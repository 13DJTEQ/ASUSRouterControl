"""Async scheduler — orchestrates speed tests, probes, and device polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from asusroutercontrol.config import Config, load_config
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.probes import (
    diff_config_snapshots,
    probe_config,
    probe_latency,
    probe_system,
    probe_wifi,
)
from asusroutercontrol.speedtest import run_speed_test
from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)


def _next_speedtest_time(cfg: Config) -> datetime:
    """Calculate the next clock-aligned speed test time."""
    now = datetime.now()
    today_hours = sorted(cfg.speedtest_times)

    for hour in today_hours:
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate

    # All today's times have passed — next is first hour tomorrow
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=today_hours[0], minute=0, second=0, microsecond=0)


class MonitorScheduler:
    """Runs three concurrent task loops: speed tests, probes, and device polls."""

    def __init__(self, store: DataStore, cfg: Config | None = None) -> None:
        self._store = store
        self._cfg = cfg or load_config()
        self._running = False

    async def run(self) -> None:
        """Start all task loops concurrently."""
        self._running = True
        log.info(
            "Scheduler started — speed tests at %s, probes every %ds, polls every %ds",
            self._cfg.speedtest_times,
            self._cfg.probe_interval,
            self._cfg.poll_interval,
        )
        try:
            await asyncio.gather(
                self._speedtest_loop(),
                self._probe_loop(),
                self._poll_loop(),
                self._prune_loop(),
                self._config_snapshot_loop(),
                self._recommendation_loop(),
            )
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    # --- Speed Test Loop ---

    async def _speedtest_loop(self) -> None:
        while self._running:
            next_run = _next_speedtest_time(self._cfg)
            wait_secs = (next_run - datetime.now()).total_seconds()
            log.info(
                "Next speed test at %s (in %.0f min)",
                next_run.strftime("%H:%M"), wait_secs / 60,
            )

            await self._interruptible_sleep(wait_secs)
            if not self._running:
                break

            try:
                result = await run_speed_test()
                await self._store.insert_speed_test(result)
                if result.download_bps:
                    log.info(
                        "Speed test stored: %.1f/%.1f Mbps (source=%s)",
                        result.download_bps / 1_000_000,
                        (result.upload_bps or 0) / 1_000_000,
                        result.source,
                    )
                else:
                    log.warning("Speed test failed: %s", result.error)
            except Exception:
                log.exception("Speed test task error")

    # --- Probe Loop (latency + system + WiFi) ---

    async def _probe_loop(self) -> None:
        while self._running:
            try:
                async with RouterSSH() as ssh:
                    # Latency
                    latency_results = await probe_latency(ssh)
                    for probe in latency_results:
                        await self._store.insert_latency_probe(probe, commit=False)

                    # System health
                    sys_snap = await probe_system(ssh)
                    await self._store.insert_system_snapshot(sys_snap, commit=False)

                    # WiFi health
                    wifi_snaps = await probe_wifi(ssh)
                    for ws in wifi_snaps:
                        await self._store.insert_wifi_snapshot(ws, commit=False)
                await self._store.commit()

                log.info(
                    "Probes stored: %d latency, 1 system, %d wifi",
                    len(latency_results), len(wifi_snaps),
                )
            except Exception:
                await self._store.rollback()
                log.exception("Probe task error")

            await self._interruptible_sleep(self._cfg.probe_interval)

    # --- Device + Traffic Poll Loop ---

    async def _poll_loop(self) -> None:
        from asusroutercontrol.backends.merlin import MerlinBackend
        from asusroutercontrol.credentials import get_router_credentials

        cfg = self._cfg
        username, password = get_router_credentials()
        if not username or not password:
            log.error("No router credentials — poll loop disabled")
            return

        backend = MerlinBackend(
            hostname=cfg.router_host,
            username=username,
            password=password,
            use_ssl=cfg.use_ssl,
            port=cfg.router_port,
        )

        while self._running:
            try:
                await backend.connect()
                devices = await backend.get_connected_devices()
                for dev in devices:
                    await self._store.upsert_device(dev, commit=False)

                traffic = await backend.get_traffic_stats()
                await self._store.insert_traffic(traffic, commit=False)
                await self._store.commit()

                log.info("Poll: %d devices, RX=%.0f bps", len(devices), traffic.rx_rate_bps or 0)
            except Exception:
                await self._store.rollback()
                log.exception("Poll task error")
            finally:
                try:
                    await backend.disconnect()
                except Exception:
                    pass

            await self._interruptible_sleep(self._cfg.poll_interval)

    # --- Daily Data Pruning ---

    async def _prune_loop(self) -> None:
        """Run data retention pruning once per day."""
        while self._running:
            # Sleep until ~03:00 local to avoid interfering with speed tests
            now = datetime.now()
            next_prune = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_prune <= now:
                next_prune += timedelta(days=1)
            await self._interruptible_sleep((next_prune - now).total_seconds())
            if not self._running:
                break

            try:
                pruned = await self._store.prune_old_data(retention_days=90)
                total = sum(pruned.values())
                if total:
                    log.info("Pruned %d old rows: %s", total, pruned)
            except Exception:
                log.exception("Prune task error")

    # --- Config Snapshot Loop ---

    async def _config_snapshot_loop(self) -> None:
        """Capture NVRAM config snapshot every 6 hours."""
        CONFIG_INTERVAL = 6 * 3600  # 6 hours
        while self._running:
            try:
                async with RouterSSH() as ssh:
                    snap = await probe_config(ssh)
                    # Compute diff against previous snapshot
                    prev = await self._store.get_latest_config_snapshot()
                    if prev:
                        snap.diff_summary = diff_config_snapshots(
                            snap.nvram_json, prev["nvram_json"]
                        )
                    await self._store.insert_config_snapshot(snap)
                    if snap.diff_summary:
                        log.info("Config change detected: %s", snap.diff_summary)
                        # Record as event
                        from asusroutercontrol.models import ConfigEvent

                        await self._store.insert_config_event(ConfigEvent(
                            event_type="config_change",
                            description=snap.diff_summary,
                            nvram_changes_json=snap.diff_summary,
                            triggered_by="auto",
                        ))
                    else:
                        log.info("Config snapshot stored (no changes)")
            except Exception:
                log.exception("Config snapshot task error")

            await self._interruptible_sleep(CONFIG_INTERVAL)

    # --- Recommendation Loop ---

    RECOMMENDATION_INTERVAL = 12 * 3600  # every 12 hours
    RECOMMENDATION_COOLDOWN = 24 * 3600  # suppress repeat for same issue

    async def _recommendation_loop(self) -> None:
        """Generate recommendations periodically and notify for actionable ones."""
        # Initial delay — wait 5 min for data loops to populate fresh data.
        await self._interruptible_sleep(300)

        while self._running:
            try:
                await self._evaluate_recommendations()
            except Exception:
                log.exception("Recommendation task error")

            await self._interruptible_sleep(self.RECOMMENDATION_INTERVAL)

    async def _evaluate_recommendations(self) -> None:
        from asusroutercontrol.optimizer import generate_recommendations

        recs = await generate_recommendations(self._store, days=30)
        actionable = [r for r in recs if r.get("priority") in ("high", "medium")]
        if not actionable:
            log.info("Recommendations: no actionable items")
            return

        now = datetime.now()
        notified = 0
        for rec in actionable:
            key = f"{rec['category']}:{rec['priority']}"
            last = await self._store.get_notification_last_sent(key)
            if last and (now - last).total_seconds() < self.RECOMMENDATION_COOLDOWN:
                continue  # already notified recently
            notified += 1
            emoji = "🔴" if rec["priority"] == "high" else "🟡"
            from asusroutercontrol.notifications import notify

            notify(
                f"{emoji} {rec['category'].title()} Advisory",
                rec["description"][:120],
                rec.get("action", "")[:100],
            )
            await self._store.set_notification_last_sent(key, sent_at=now)

        log.info(
            "Recommendations: %d actionable, %d notified",
            len(actionable), notified,
        )

    # --- Helpers ---

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in 5-second chunks so stop() takes effect quickly."""
        remaining = max(0.0, seconds)
        while remaining > 0 and self._running:
            await asyncio.sleep(min(5.0, remaining))
            remaining -= 5.0
