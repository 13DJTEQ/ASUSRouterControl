"""Async scheduler — orchestrates speed tests, probes, and device polling."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from time import perf_counter

from asusroutercontrol.config import Config, load_config
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import SpeedTestResult
from asusroutercontrol.probes import (
    diff_config_snapshots,
    probe_client_traffic,
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


def _percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    rank = (len(samples) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(samples) - 1)
    weight = rank - lower
    return samples[lower] + (samples[upper] - samples[lower]) * weight


# Timeout constants (seconds)
PROBE_CYCLE_TIMEOUT = 120.0
CLIENT_TRAFFIC_CYCLE_TIMEOUT = 120.0
POLL_CYCLE_TIMEOUT = 120.0
CONFIG_CYCLE_TIMEOUT = 120.0
SPEEDTEST_CYCLE_TIMEOUT = 300.0

# Backoff: after N consecutive failures, sleep longer before retrying
MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_SCHEDULE = (60, 120, 300)  # seconds


def _backoff_seconds(consecutive_failures: int) -> float:
    """Return backoff sleep duration based on failure count."""
    if consecutive_failures < MAX_CONSECUTIVE_FAILURES:
        return 0.0
    idx = min(consecutive_failures - MAX_CONSECUTIVE_FAILURES, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[idx]


class MonitorScheduler:
    """Runs concurrent task loops: speed tests, probes, and device polls."""

    def __init__(
        self,
        store: DataStore,
        cfg: Config | None = None,
        on_speedtest_complete: Callable[[SpeedTestResult], None] | None = None,
    ) -> None:
        self._store = store
        self._cfg = cfg or load_config()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self.on_speedtest_complete = on_speedtest_complete
        # In-memory cache for per-client byte counter deltas
        # {mac: (rx_bytes, tx_bytes, timestamp)}
        self._client_prev: dict[str, tuple[int, int, datetime]] = {}
        self._perf_samples: dict[str, deque[float]] = {}
        self._perf_counts: dict[str, int] = {}
        self._perf_log_every = 12
        # Per-loop consecutive failure counters
        self._failures: dict[str, int] = {}

    async def run(self) -> None:
        """Start all task loops as cancellable Tasks."""
        self._running = True
        log.info(
            "Scheduler started — speed tests at %s, probes every %ds, "
            "client traffic every %ds, polls every %ds",
            self._cfg.speedtest_times,
            self._cfg.probe_interval,
            self._cfg.client_traffic_interval,
            self._cfg.poll_interval,
        )
        self._tasks = [
            asyncio.create_task(self._speedtest_loop(), name="speedtest"),
            asyncio.create_task(self._probe_loop(), name="probe"),
            asyncio.create_task(self._client_traffic_loop(), name="client-traffic"),
            asyncio.create_task(self._poll_loop(), name="poll"),
            asyncio.create_task(self._prune_loop(), name="prune"),
            asyncio.create_task(self._config_snapshot_loop(), name="config"),
            asyncio.create_task(self._recommendation_loop(), name="recommend"),
            asyncio.create_task(self._lan_probe_loop(), name="lan-probe"),
            asyncio.create_task(self._switch_probe_loop(), name="switch-probe"),
        ]
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._running = False
            self._tasks = []

    async def stop(self) -> None:
        """Cancel all tasks and wait for them to finish."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def _record_perf_sample(
        self,
        metric: str,
        duration_seconds: float,
        *,
        context: str = "",
    ) -> None:
        sample_ms = max(0.0, duration_seconds) * 1000.0
        samples = self._perf_samples.setdefault(metric, deque(maxlen=120))
        samples.append(sample_ms)
        count = self._perf_counts.get(metric, 0) + 1
        self._perf_counts[metric] = count
        if count % self._perf_log_every:
            return

        ordered = sorted(samples)
        mean_ms = sum(ordered) / len(ordered)
        total_s = sum(ordered) / 1000.0
        throughput = (len(ordered) / total_s) if total_s > 0 else 0.0
        suffix = f", {context}" if context else ""
        log.info(
            "baseline[%s] n=%d p50=%.2fms p95=%.2fms mean=%.2fms throughput=%.2f/s%s",
            metric,
            len(ordered),
            _percentile(ordered, 0.50),
            _percentile(ordered, 0.95),
            mean_ms,
            throughput,
            suffix,
        )

    def _record_failure(self, loop_name: str) -> float:
        """Increment failure counter for a loop; return backoff seconds."""
        count = self._failures.get(loop_name, 0) + 1
        self._failures[loop_name] = count
        backoff = _backoff_seconds(count)
        if backoff > 0:
            log.warning(
                "%s: %d consecutive failures, backing off %.0fs",
                loop_name, count, backoff,
            )
        return backoff

    def _record_success(self, loop_name: str) -> None:
        self._failures.pop(loop_name, None)

    # --- Speed Test Loop ---

    async def _speedtest_loop(self) -> None:
        try:
            while self._running:
                next_run = _next_speedtest_time(self._cfg)
                wait_secs = (next_run - datetime.now()).total_seconds()
                log.info(
                    "Next speed test at %s (in %.0f min)",
                    next_run.strftime("%H:%M"), wait_secs / 60,
                )

                await asyncio.sleep(max(0.0, wait_secs))
                if not self._running:
                    break
                cycle_start = perf_counter()

                try:
                    result = await asyncio.wait_for(
                        run_speed_test(), timeout=SPEEDTEST_CYCLE_TIMEOUT,
                    )
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
                    if self.on_speedtest_complete:
                        try:
                            self.on_speedtest_complete(result)
                        except Exception:
                            log.exception("on_speedtest_complete callback error")
                    self._record_perf_sample("speedtest.cycle", perf_counter() - cycle_start)
                    self._record_success("speedtest")
                except asyncio.TimeoutError:
                    log.error("Speed test timed out after %.0fs", SPEEDTEST_CYCLE_TIMEOUT)
                    backoff = self._record_failure("speedtest")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    log.exception("Speed test task error")
                    backoff = self._record_failure("speedtest")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            log.info("Speed test loop cancelled")

    # --- Probe Loop (latency + system + WiFi) ---

    async def _probe_loop(self) -> None:
        try:
            while self._running:
                cycle_start = perf_counter()
                try:
                    await asyncio.wait_for(
                        self._run_probes_cycle(),
                        timeout=PROBE_CYCLE_TIMEOUT,
                    )
                    self._record_perf_sample("probe.cycle", perf_counter() - cycle_start)
                    self._record_success("probe")
                except asyncio.TimeoutError:
                    log.error("Probe cycle timed out after %.0fs", PROBE_CYCLE_TIMEOUT)
                    await self._store.rollback()
                    backoff = self._record_failure("probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    await self._store.rollback()
                    log.exception("Probe task error")
                    backoff = self._record_failure("probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)

                await asyncio.sleep(self._cfg.probe_interval)
        except asyncio.CancelledError:
            log.info("Probe loop cancelled")

    async def _run_probes_cycle(self) -> None:
        """Single probe cycle — extracted so it can be wrapped in wait_for."""
        async with RouterSSH() as ssh:
            latency_results = await probe_latency(ssh)
            for probe in latency_results:
                await self._store.insert_latency_probe(probe, commit=False)

            sys_snap = await probe_system(ssh)
            await self._store.insert_system_snapshot(sys_snap, commit=False)

            wifi_snaps = await probe_wifi(ssh)
            for ws in wifi_snaps:
                prev = await self._store.get_latest_wifi_snapshot(ws.band)
                if prev and prev.get("rx_bytes") is not None:
                    from asusroutercontrol.probes import compute_wifi_rates

                    prev_bytes = (prev["rx_bytes"], prev["tx_bytes"])
                    prev_ts = datetime.fromisoformat(prev["timestamp"])
                    compute_wifi_rates(ws, prev_bytes, prev_ts)
                await self._store.insert_wifi_snapshot(ws, commit=False)

        await self._store.commit()
        log.info(
            "Probes stored: %d latency, 1 system, %d wifi",
            len(latency_results), len(wifi_snaps),
        )

    # --- Client Traffic Loop (high-frequency deltas) ---

    async def _client_traffic_loop(self) -> None:
        try:
            while self._running:
                cycle_start = perf_counter()
                try:
                    await asyncio.wait_for(
                        self._run_client_traffic_cycle(),
                        timeout=CLIENT_TRAFFIC_CYCLE_TIMEOUT,
                    )
                    self._record_perf_sample(
                        "client_traffic.cycle",
                        perf_counter() - cycle_start,
                    )
                    self._record_success("client_traffic")
                except asyncio.TimeoutError:
                    log.error(
                        "Client traffic cycle timed out after %.0fs",
                        CLIENT_TRAFFIC_CYCLE_TIMEOUT,
                    )
                    await self._store.rollback()
                    backoff = self._record_failure("client_traffic")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    await self._store.rollback()
                    log.exception("Client traffic task error")
                    backoff = self._record_failure("client_traffic")
                    if backoff > 0:
                        await asyncio.sleep(backoff)

                await asyncio.sleep(self._cfg.client_traffic_interval)
        except asyncio.CancelledError:
            log.info("Client traffic loop cancelled")

    async def _run_client_traffic_cycle(self) -> None:
        async with RouterSSH() as ssh:
            await self._collect_client_traffic(ssh)
        await self._store.commit()

    async def _collect_client_traffic(self, ssh: RouterSSH) -> None:
        """Probe per-client byte counters, compute rate deltas, store loads."""
        from asusroutercontrol.analysis.clients import BAND_LINK_RATES, DEFAULT_LINK_RATE
        from asusroutercontrol.models import ClientLoad

        try:
            snapshots = await probe_client_traffic(ssh)
        except Exception:
            log.exception("Client traffic probe failed")
            return

        now = datetime.utcnow()
        devices_start = perf_counter()
        devices = await self._store.get_all_devices()
        self._record_perf_sample(
            "probe.device_lookup",
            perf_counter() - devices_start,
        )
        hostname_map = {d["mac"]: d.get("hostname") for d in devices}
        probed = len(snapshots)
        with_rates = 0
        inserted_valid = 0
        inserted_placeholder = 0
        for snap in snapshots:
            mac = snap["mac"]
            rx_bytes = snap["rx_bytes"]
            tx_bytes = snap["tx_bytes"]
            prev = self._client_prev.get(mac)

            self._client_prev[mac] = (rx_bytes, tx_bytes, now)

            if prev is None:
                continue
            prev_rx, prev_tx, prev_ts = prev
            dt = (now - prev_ts).total_seconds()
            if dt <= 0:
                continue

            drx = rx_bytes - prev_rx
            dtx = tx_bytes - prev_tx
            if drx < 0 or dtx < 0:
                continue
            with_rates += 1

            rx_mbps = (drx * 8) / dt / 1_000_000
            tx_mbps = (dtx * 8) / dt / 1_000_000

            band = snap.get("band", "")
            link_rate = BAND_LINK_RATES.get(band, DEFAULT_LINK_RATE)
            peak = max(rx_mbps, tx_mbps)
            load_pct = min(100.0, (peak / link_rate) * 100.0) if link_rate > 0 else 0.0

            rssi = snap.get("rssi")
            health = "\U0001f7e2"
            if rssi is not None and rssi < -75:
                health = "\U0001f534"
            elif load_pct >= 80:
                health = "\U0001f534"
            elif load_pct >= 50:
                health = "\U0001f7e1"

            cl = ClientLoad(
                timestamp=now,
                mac=mac,
                hostname=hostname_map.get(mac),
                band=band,
                rssi=rssi,
                tx_rate_mbps=round(tx_mbps, 2),
                rx_rate_mbps=round(rx_mbps, 2),
                load_pct=round(load_pct, 1),
                health=health,
            )
            await self._store.insert_client_load(cl, commit=False)
            await self._store.insert_device_perf(cl, commit=False)
            inserted_valid += 1

        log.info(
            "Client traffic cycle: probed=%d with_rates=%d "
            "inserted_valid=%d inserted_placeholder=%d",
            probed,
            with_rates,
            inserted_valid,
            inserted_placeholder,
        )

    # --- Device + Traffic Poll Loop ---

    async def _poll_loop(self) -> None:
        from asusroutercontrol.backends.factory import create_backend
        from asusroutercontrol.credentials import get_router_credentials

        try:
            cfg = self._cfg
            username, password = get_router_credentials()
            if not username or not password:
                log.error("No router credentials — poll loop disabled")
                return

            backend = create_backend(cfg, username=username, password=password)

            while self._running:
                cycle_start = perf_counter()
                try:
                    await asyncio.wait_for(
                        self._run_poll_cycle(backend),
                        timeout=POLL_CYCLE_TIMEOUT,
                    )
                    self._record_perf_sample("poll.cycle", perf_counter() - cycle_start)
                    self._record_success("poll")
                except asyncio.TimeoutError:
                    log.error("Poll cycle timed out after %.0fs", POLL_CYCLE_TIMEOUT)
                    await self._store.rollback()
                    backoff = self._record_failure("poll")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    await self._store.rollback()
                    log.exception("Poll task error")
                    backoff = self._record_failure("poll")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                finally:
                    try:
                        await backend.disconnect()
                    except Exception:
                        pass

                await asyncio.sleep(self._cfg.poll_interval)
        except asyncio.CancelledError:
            log.info("Poll loop cancelled")

    async def _run_poll_cycle(self, backend) -> None:
        """Single poll cycle — extracted so it can be wrapped in wait_for."""
        from asusroutercontrol.models import ClientLoad, ConnectionType

        await backend.connect()
        devices = await backend.get_connected_devices()
        now = datetime.utcnow()
        wired_count = wifi_count = unknown_count = 0
        for dev in devices:
            await self._store.upsert_device(dev, commit=False)
            if not dev.is_online:
                continue
            # Write a presence row for every online device so all client submenus
            # populate even when SSH telemetry is unavailable.  tx/rx rates are
            # stored as None (presence-only); the SSH client_traffic_loop enriches
            # these with actual throughput deltas and wins via _row_rank ordering.
            if dev.connection == ConnectionType.WIRED:
                band = "wired"
                wired_count += 1
            elif dev.connection in (
                ConnectionType.WIFI_2G,
                ConnectionType.WIFI_5G,
                ConnectionType.WIFI_6G,
            ):
                band = dev.connection.value  # "2.4GHz", "5GHz", "6GHz"
                wifi_count += 1
            else:
                # UNKNOWN connection type — use stored band hint if available
                band = dev.band or ""
                unknown_count += 1
                if not band or band.lower() in ("unknown", "none", "null"):
                    log.debug(
                        "Skipping presence row: unknown band for %s (%s)",
                        dev.hostname or dev.mac, dev.mac,
                    )
                    continue
            cl = ClientLoad(
                timestamp=now,
                mac=dev.mac,
                hostname=dev.hostname,
                band=band,
                rssi=dev.rssi,      # API RSSI elevates row rank; SSH enriches further
                tx_rate_mbps=None,  # API link-speed != throughput; SSH loop fills this
                rx_rate_mbps=None,
                load_pct=0.0,
                health="\U0001f7e2",
            )
            await self._store.insert_device_perf(cl, commit=False)
        traffic = await backend.get_traffic_stats()
        await self._store.insert_traffic(traffic, commit=False)
        await self._store.commit()
        log.info(
            "Poll: %d devices (%d wired, %d wifi, %d unknown-band), RX=%.0f bps",
            len(devices), wired_count, wifi_count, unknown_count,
            traffic.rx_rate_bps or 0,
        )

    # --- Daily Data Pruning ---

    async def _prune_loop(self) -> None:
        """Run data retention pruning once per day."""
        try:
            while self._running:
                now = datetime.now()
                next_prune = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if next_prune <= now:
                    next_prune += timedelta(days=1)
                await asyncio.sleep((next_prune - now).total_seconds())
                if not self._running:
                    break

                try:
                    pruned = await self._store.prune_old_data(retention_days=90)
                    total = sum(pruned.values())
                    if total:
                        log.info("Pruned %d old rows: %s", total, pruned)
                except Exception:
                    log.exception("Prune task error")
        except asyncio.CancelledError:
            log.info("Prune loop cancelled")

    # --- Config Snapshot Loop ---

    async def _config_snapshot_loop(self) -> None:
        """Capture NVRAM config snapshot every 6 hours."""
        CONFIG_INTERVAL = 6 * 3600  # 6 hours
        try:
            while self._running:
                try:
                    await asyncio.wait_for(
                        self._run_config_cycle(),
                        timeout=CONFIG_CYCLE_TIMEOUT,
                    )
                    self._record_success("config")
                except asyncio.TimeoutError:
                    log.error("Config snapshot timed out after %.0fs", CONFIG_CYCLE_TIMEOUT)
                    backoff = self._record_failure("config")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    log.exception("Config snapshot task error")
                    backoff = self._record_failure("config")
                    if backoff > 0:
                        await asyncio.sleep(backoff)

                await asyncio.sleep(CONFIG_INTERVAL)
        except asyncio.CancelledError:
            log.info("Config snapshot loop cancelled")

    async def _run_config_cycle(self) -> None:
        """Single config snapshot cycle."""
        async with RouterSSH() as ssh:
            snap = await probe_config(ssh)
            prev = await self._store.get_latest_config_snapshot()
            if prev:
                snap.diff_summary = diff_config_snapshots(
                    snap.nvram_json, prev["nvram_json"]
                )
            await self._store.insert_config_snapshot(snap)
            if snap.diff_summary:
                log.info("Config change detected: %s", snap.diff_summary)
                from asusroutercontrol.models import ConfigEvent

                await self._store.insert_config_event(ConfigEvent(
                    event_type="config_change",
                    description=snap.diff_summary,
                    nvram_changes_json=snap.diff_summary,
                    triggered_by="auto",
                ))
            else:
                log.info("Config snapshot stored (no changes)")

    # --- LAN Probe Loop ---

    LAN_PROBE_CYCLE_TIMEOUT = 120.0
    SWITCH_PROBE_CYCLE_TIMEOUT = 60.0
    AUTO_DISABLE_THRESHOLD_S = 60.0
    AUTO_DISABLE_CONSECUTIVE = 3

    async def _lan_probe_loop(self) -> None:
        """Ping RFC1918 LAN hosts and track bridge throughput."""
        from asusroutercontrol.probes_lan import (
            probe_bridge_throughput,
            probe_lan_clients,
        )

        overrun_count = 0
        try:
            while self._running:
                if not self._cfg.lan_probes_enabled:
                    log.debug("LAN probes disabled, sleeping 60s")
                    await asyncio.sleep(60)
                    continue

                cycle_start = perf_counter()
                try:
                    async with RouterSSH() as ssh:
                        # LAN client latency probes
                        probes = await asyncio.wait_for(
                            probe_lan_clients(
                                ssh,
                                ping_count=self._cfg.lan_probe_ping_count,
                                concurrency=self._cfg.lan_probe_concurrency,
                            ),
                            timeout=self.LAN_PROBE_CYCLE_TIMEOUT,
                        )
                        for p in probes:
                            await self._store.insert_lan_client_probe(
                                p, commit=False
                            )

                        # Bridge throughput
                        bridge = await probe_bridge_throughput(ssh)
                        if bridge:
                            prev = await self._store.get_latest_bridge_throughput()
                            if prev and prev.get("rx_bytes") is not None:
                                dt = (
                                    bridge.timestamp
                                    - datetime.fromisoformat(prev["timestamp"])
                                ).total_seconds()
                                if dt > 0:
                                    drx = bridge.rx_bytes - prev["rx_bytes"]
                                    dtx = bridge.tx_bytes - prev["tx_bytes"]
                                    if drx >= 0 and dtx >= 0:
                                        bridge.rx_rate_bps = (drx * 8) / dt
                                        bridge.tx_rate_bps = (dtx * 8) / dt
                            await self._store.insert_bridge_throughput(
                                bridge, commit=False
                            )

                    await self._store.commit()
                    elapsed = perf_counter() - cycle_start
                    self._record_perf_sample("lan_probe.cycle", elapsed)
                    self._record_success("lan_probe")
                    log.info(
                        "LAN probe cycle: %d clients in %.1fs",
                        len(probes), elapsed,
                    )

                    # Auto-disable check
                    if elapsed > self.AUTO_DISABLE_THRESHOLD_S:
                        overrun_count += 1
                        if overrun_count >= self.AUTO_DISABLE_CONSECUTIVE:
                            log.warning(
                                "LAN probes auto-disabled: %d consecutive cycles "
                                "> %.0fs",
                                overrun_count,
                                self.AUTO_DISABLE_THRESHOLD_S,
                            )
                            # Cannot mutate frozen dataclass; log and stop loop
                            break
                    else:
                        overrun_count = 0

                except asyncio.TimeoutError:
                    log.error(
                        "LAN probe cycle timed out after %.0fs",
                        self.LAN_PROBE_CYCLE_TIMEOUT,
                    )
                    await self._store.rollback()
                    backoff = self._record_failure("lan_probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    await self._store.rollback()
                    log.exception("LAN probe task error")
                    backoff = self._record_failure("lan_probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)

                await asyncio.sleep(self._cfg.lan_probe_interval)
        except asyncio.CancelledError:
            log.info("LAN probe loop cancelled")

    async def _switch_probe_loop(self) -> None:
        """Read switch port error/drop counters."""
        from asusroutercontrol.probes_lan import probe_switch_ports

        try:
            while self._running:
                if not self._cfg.lan_probes_enabled:
                    await asyncio.sleep(60)
                    continue

                cycle_start = perf_counter()
                try:
                    async with RouterSSH() as ssh:
                        stats = await asyncio.wait_for(
                            probe_switch_ports(ssh),
                            timeout=self.SWITCH_PROBE_CYCLE_TIMEOUT,
                        )
                        for s in stats:
                            await self._store.insert_switch_port_stats(
                                s, commit=False
                            )
                    await self._store.commit()
                    self._record_perf_sample(
                        "switch_probe.cycle", perf_counter() - cycle_start
                    )
                    self._record_success("switch_probe")
                    log.info("Switch probe: %d ports", len(stats))

                except asyncio.TimeoutError:
                    log.error(
                        "Switch probe timed out after %.0fs",
                        self.SWITCH_PROBE_CYCLE_TIMEOUT,
                    )
                    await self._store.rollback()
                    backoff = self._record_failure("switch_probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)
                except Exception:
                    await self._store.rollback()
                    log.exception("Switch probe task error")
                    backoff = self._record_failure("switch_probe")
                    if backoff > 0:
                        await asyncio.sleep(backoff)

                await asyncio.sleep(self._cfg.switch_probe_interval)
        except asyncio.CancelledError:
            log.info("Switch probe loop cancelled")

    # --- Recommendation Loop ---

    RECOMMENDATION_INTERVAL = 12 * 3600  # every 12 hours
    RECOMMENDATION_COOLDOWN = 24 * 3600  # suppress repeat for same issue

    async def _recommendation_loop(self) -> None:
        """Generate recommendations periodically and notify for actionable ones."""
        try:
            await asyncio.sleep(300)  # initial delay for data loops

            while self._running:
                try:
                    await self._evaluate_recommendations()
                except Exception:
                    log.exception("Recommendation task error")

                await asyncio.sleep(self.RECOMMENDATION_INTERVAL)
        except asyncio.CancelledError:
            log.info("Recommendation loop cancelled")

    async def _evaluate_recommendations(self) -> None:
        from asusroutercontrol.optimizer import generate_recommendations
        eval_start = perf_counter()

        recs = await generate_recommendations(self._store, days=30)
        actionable = [r for r in recs if r.get("priority") in ("high", "medium")]
        if not actionable:
            log.info("Recommendations: no actionable items")
            self._record_perf_sample(
                "recommendations.evaluate",
                perf_counter() - eval_start,
                context="actionable=0",
            )
            return

        now = datetime.now()
        notified = 0
        for rec in actionable:
            key = f"{rec['category']}:{rec['priority']}"
            last = await self._store.get_notification_last_sent(key)
            if last and (now - last).total_seconds() < self.RECOMMENDATION_COOLDOWN:
                continue  # already notified recently
            notified += 1
            emoji = "\U0001f534" if rec["priority"] == "high" else "\U0001f7e1"
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
        self._record_perf_sample(
            "recommendations.evaluate",
            perf_counter() - eval_start,
            context=f"actionable={len(actionable)} notified={notified}",
        )
