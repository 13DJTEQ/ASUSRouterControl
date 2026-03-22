"""AsusRouterMonitor — macOS menubar applet for ASUSRouterControl.

Pure PyObjC implementation (no rumps) for full macOS Sequoia compatibility.
Embeds the MonitorScheduler in a daemon thread and provides at-a-glance
status plus one-click actions from the macOS menu bar.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from datetime import datetime, timedelta
from statistics import mean

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSFont,
    NSFontAttributeName,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
    NSTimer,
    NSUnderlineStyleAttributeName,
    NSUnderlineStyleSingle,
    NSVariableStatusItemLength,
)
from PyObjCTools import AppHelper

from asusroutercontrol.analysis.clients import format_client_load_display
from asusroutercontrol.config import load_config
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.notifications import notify as _notify
from asusroutercontrol.scheduler import MonitorScheduler

log = logging.getLogger(__name__)

# Thresholds for notifications
PLAN_SPEED_DOWN = 300_000_000  # 300 Mbps
TEMP_WARN_C = 85.0
LOSS_WARN_PCT = 5.0
SPEED_DROP_RATIO = 0.70  # notify if < 70% of plan
LATENCY_WARN_MS = 50.0  # yellow if gateway latency exceeds this

REFRESH_INTERVAL = 60.0  # seconds between menu data refreshes


def _traffic_dot(current_bps: float | None, plan_bps: float | None = None) -> str:
    """Colored dot reflecting current speed vs. plan speed.

    🔴  stopped / no data
    🟡  below 70% of plan speed
    🟢  within normal range
    """
    if not current_bps:        # zero or missing — traffic has stopped
        return "🔴"
    if not plan_bps:           # no plan baseline — can't judge
        return "🟢"
    if current_bps / plan_bps < SPEED_DROP_RATIO:  # below 70% of plan
        return "🟡"
    return "🟢"


_DOT_RANK = {"🟢": 0, "🟡": 1, "🔴": 2}


def _format_band_bw(wifi_row: dict) -> str:
    """Format per-band rx/tx rates for display.  Returns '' if no data."""
    rx = wifi_row.get("rx_rate_bps")
    tx = wifi_row.get("tx_rate_bps")
    if rx is None and tx is None:
        return ""
    rx_m = f"{rx / 1_000_000:.1f}" if rx else "—"
    tx_m = f"{tx / 1_000_000:.1f}" if tx else "—"
    return f"  ↓{rx_m} ↑{tx_m} Mbps"


# Friendly labels for latency probe targets
_TARGET_LABELS: dict[str, str] = {
    "gateway": "Gateway",
    "cloudflare": "Cloudflare DNS",
    "google": "Google DNS",
}


def _friendly_target(name: str) -> str:
    return _TARGET_LABELS.get(name, name)


def _connection_status(
    dl_bps: float | None,
    ul_bps: float | None,
    gw_loss_pct: float | None = None,
    gw_latency_ms: float | None = None,
) -> str:
    """Single color-coded dot reflecting overall connection health.

    Uses plan speed as baseline (not 24h average), gateway-only
    latency/loss, and relaxed thresholds.

    🔴  no data / below plan threshold / high packet loss
    🟡  degraded vs plan speed / moderate loss / elevated latency
    🟢  healthy
    """
    worst = "🟢"

    def _elevate(dot: str) -> None:
        nonlocal worst
        if _DOT_RANK.get(dot, 0) > _DOT_RANK.get(worst, 0):
            worst = dot

    # --- per-direction speed vs plan ---
    _elevate(_traffic_dot(dl_bps, PLAN_SPEED_DOWN))
    _elevate(_traffic_dot(ul_bps))

    # --- gateway packet loss (only gateway, ignore external targets) ---
    if gw_loss_pct is not None:
        if gw_loss_pct >= LOSS_WARN_PCT:
            _elevate("🔴")
        elif gw_loss_pct >= 1.0:
            _elevate("🟡")

    # --- gateway latency ---
    if gw_latency_ms is not None and gw_latency_ms > LATENCY_WARN_MS:
        _elevate("🟡")

    return worst


def _band_bucket(band: str | None) -> str:
    """Normalise a band string to '2.4', '5', 'wired', or 'other'."""
    if band in ("2.4GHz", "2.4"):
        return "2.4"
    if band in ("5GHz", "5"):
        return "5"
    if band == "wired":
        return "wired"
    return "other"


def _add_section_header(menu, title: str):
    """Add a bold/underline section header menu item."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
    attrs = {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(13.0),
        NSUnderlineStyleAttributeName: NSUnderlineStyleSingle,
    }
    item.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(title, attrs)
    )
    item.setEnabled_(False)
    menu.addItem_(item)
    return item


def _add_info(menu, title: str):
    """Add a non-clickable info menu item."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    item.setEnabled_(False)
    menu.addItem_(item)
    return item


def _add_action(menu, title: str, selector: str, target):
    """Add a clickable action menu item."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
    item.setTarget_(target)
    menu.addItem_(item)
    return item


class AppDelegate(NSObject):
    """Main application delegate — owns the status item and scheduler."""

    statusbar = None
    statusitem = None
    _pending_data = None  # refresh data stored here to avoid ObjC bridging issues

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def applicationDidFinishLaunching_(self, notification):
        log.info("AsusRouterMonitor starting")

        self._cfg = load_config()
        self._cfg.ensure_dirs()
        self._db_path = self._cfg.data_dir / "router.db"
        self._log_path = self._cfg.data_dir / "scheduler.log"

        self._sched = None
        self._sched_store = None
        self._sched_thread = None
        self._sched_loop = None
        self._last_speedtest_id = None
        self._last_device_count = None
        self._last_saturation_notify = None
        self._degraded = False

        self.statusbar = NSStatusBar.systemStatusBar()
        self.statusitem = self.statusbar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.statusitem.button().setTitle_("📡 —")

        self._build_menu()

        # Phase 4: Health check before starting scheduler
        threading.Thread(
            target=self._startup_health_check, name="health-check", daemon=True
        ).start()

        log.info("AsusRouterMonitor starting (health check in progress)")

    def _startup_health_check(self):
        """Check router backend reachability before starting scheduler."""
        from asusroutercontrol.backends.factory import create_backend
        from asusroutercontrol.credentials import get_router_credentials
        from asusroutercontrol.ssh import RouterSSH

        async def _check_backend():
            username, password = get_router_credentials()
            if not username or not password:
                raise RuntimeError("Missing router credentials")
            backend = create_backend(
                self._cfg,
                username=username,
                password=password,
            )
            try:
                await backend.connect()
            finally:
                try:
                    await backend.disconnect()
                except Exception:
                    pass

        async def _check_ssh():
            ssh = RouterSSH(connect_timeout=10.0)
            await ssh.connect()
            await ssh.disconnect()

        try:
            asyncio.run(_check_backend())
        except Exception as exc:
            log.warning(
                "Router backend unreachable: %s — entering degraded mode",
                exc,
            )
            self._degraded = True
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "enterDegradedMode:", None, False
            )
            return

        if (self._cfg.router_backend or "").strip().lower() == "merlin":
            try:
                asyncio.run(_check_ssh())
            except Exception as exc:
                log.warning(
                    "SSH unavailable at startup: %s — starting scheduler with limited data",
                    exc,
                )

        log.info("Router reachable — starting scheduler")
        self._degraded = False
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "startAfterHealthCheck:", None, False
        )

    @objc.typedSelector(b"v@:@")
    def startAfterHealthCheck_(self, _):
        """Called on main thread after successful health check."""
        if self._degraded:
            self.statusitem.button().setTitle_("📡 ⚠️")
            self._mi_sched_status.setTitle_("Scheduler: ● Running (degraded)")
        else:
            self.statusitem.button().setTitle_("📡 —")
        self._start_scheduler()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_INTERVAL, self, "refreshData:", None, True
        )
        threading.Thread(
            target=self._do_refresh, name="initial-refresh", daemon=True
        ).start()
        log.info("AsusRouterMonitor ready")

    @objc.typedSelector(b"v@:@")
    def enterDegradedMode_(self, _):
        """Router backend unreachable — show offline status and retry in 60s."""
        self.statusitem.button().setTitle_("📡 ⚠️ Offline")
        self._mi_sched_status.setTitle_("Scheduler: ○ Offline (retrying in 60s)")
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            60.0, self, "retryHealthCheck:", None, False
        )

    @objc.typedSelector(b"v@:@")
    def retryHealthCheck_(self, _):
        """Retry the health check."""
        threading.Thread(
            target=self._startup_health_check, name="health-retry", daemon=True
        ).start()

    def applicationWillTerminate_(self, notification):
        """Safety net: ensure scheduler is stopped on app termination."""
        self._stop_scheduler()

    def _build_menu(self):
        menu = NSMenu.new()
        menu.setAutoenablesItems_(False)

        _add_section_header(menu, "Router")
        self._mi_model = _add_info(menu, "Router: connecting...")
        self._mi_uptime = _add_info(menu, "Uptime: —")
        self._mi_hw = _add_info(menu, "CPU: —  RAM: —  Temp: —")
        menu.addItem_(NSMenuItem.separatorItem())

        _add_section_header(menu, "SpeedHealth")
        self._mi_speed = _add_info(menu, "↓ — Mbps  ↑ — Mbps")
        self._mi_avg_24h = _add_info(menu, "  24h avg: ↓ — Mbps  ↑ — Mbps")
        self._mi_latency = _add_info(menu, "Ping: — ms  Loss: —%")
        menu.addItem_(NSMenuItem.separatorItem())

        _add_section_header(menu, "Network Devices")
        self._mi_devices = _add_info(menu, "Devices: —")
        self._mi_lan_total = _add_info(menu, "LAN Wired: ↓— ↑— Mbps")
        self._mi_wifi24 = _add_info(menu, "WiFi 2.4G: —")
        self._mi_wifi5 = _add_info(menu, "WiFi 5G: —")
        menu.addItem_(NSMenuItem.separatorItem())

        # WiFi 2.4GHz Clients submenu
        self._mi_clients_wifi24 = _add_info(menu, "📶 WiFi 2.4GHz Clients")
        self._clients_wifi24_submenu = NSMenu.new()
        self._clients_wifi24_submenu.setAutoenablesItems_(False)
        self._mi_clients_wifi24.setSubmenu_(self._clients_wifi24_submenu)
        self._mi_clients_wifi24.setEnabled_(True)

        # WiFi 5GHz Clients submenu
        self._mi_clients_wifi5 = _add_info(menu, "📶 WiFi 5GHz Clients")
        self._clients_wifi5_submenu = NSMenu.new()
        self._clients_wifi5_submenu.setAutoenablesItems_(False)
        self._mi_clients_wifi5.setSubmenu_(self._clients_wifi5_submenu)
        self._mi_clients_wifi5.setEnabled_(True)

        # LAN Wired Clients submenu
        self._mi_clients_lan = _add_info(menu, "🔌 LAN Clients")
        self._clients_lan_submenu = NSMenu.new()
        self._clients_lan_submenu.setAutoenablesItems_(False)
        self._mi_clients_lan.setSubmenu_(self._clients_lan_submenu)
        self._mi_clients_lan.setEnabled_(True)
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_speedtest = _add_action(menu, "▶ Run Speed Test", "runSpeedTest:", self)
        self._mi_report = _add_action(menu, "📊 Generate Report", "genReport:", self)
        _add_action(menu, "🔄 Reboot Router...", "rebootRouter:", self)
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_sched_status = _add_info(menu, "Scheduler: starting...")
        _add_action(menu, "Open Log File", "openLog:", self)
        menu.addItem_(NSMenuItem.separatorItem())

        _add_action(menu, "⛔ Shutdown", "killApp:", self)
        _add_action(menu, "🔄 Restart AsusRouterMonitor", "quitApp:", self)
        self.statusitem.setMenu_(menu)


    # ------------------------------------------------------------------
    # Client submenu population
    # ------------------------------------------------------------------

    def _populate_client_submenu(
        self,
        submenu,
        clients: list[dict],
        client_trends: dict,
    ) -> list[str]:
        """Fill a client submenu; return list of saturated client names."""
        submenu.removeAllItems()
        saturated: list[str] = []
        if not clients:
            _add_info(submenu, "No clients")
            return saturated
        for cl in clients:
            name = cl.get("hostname") or cl.get("mac", "?")
            mac = cl.get("mac", "")
            tx = cl.get("tx_rate_mbps")
            rx = cl.get("rx_rate_mbps")
            raw_load = cl.get("load_pct")
            load = float(raw_load) if raw_load is not None else 0.0
            rssi = cl.get("rssi")
            health = "🟢"
            if rssi is not None and rssi < -75:
                health = "🔴"
            elif load >= 80:
                health = "🔴"
            elif load >= 50:
                health = "🟡"
            tx_s = f"{tx:.0f}" if tx else "—"
            rx_s = f"{rx:.0f}" if rx else "—"
            trend_avg = client_trends.get(mac)
            if trend_avg is not None and load > 0:
                diff = load - trend_avg
                trend = "↑" if diff > 5 else "↓" if diff < -5 else "—"
            else:
                trend = ""
            trend_s = f" {trend}" if trend else ""
            load_s = format_client_load_display(raw_load)
            rssi_s = f"  {rssi} dBm" if rssi is not None else ""
            title = f"{health} {name}  ↓{rx_s} ↑{tx_s} Mbps  ({load_s}{trend_s}){rssi_s}"
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, None, ""
            )
            item.setEnabled_(False)
            submenu.addItem_(item)
            if load >= 80:
                saturated.append(name)
        return saturated

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _start_scheduler(self):
        def _run():
            loop = asyncio.new_event_loop()
            self._sched_loop = loop
            asyncio.set_event_loop(loop)
            try:
                store = DataStore(self._db_path)
                loop.run_until_complete(store.open())
                self._sched_store = store
                self._sched = MonitorScheduler(
                    store,
                    self._cfg,
                    on_speedtest_complete=self._on_scheduled_speedtest,
                )
                log.info("Scheduler started from menubar app")
                loop.run_until_complete(self._sched.run())
            except Exception:
                log.exception("Scheduler thread crashed")
            finally:
                self._sched_loop = None

        self._sched_thread = threading.Thread(
            target=_run, name="scheduler", daemon=True
        )
        self._sched_thread.start()

    def _on_scheduled_speedtest(self, result):
        """Called from the scheduler thread when a speed test finishes."""
        if not self._cfg.notify_on_speedtest:
            return
        if result.error:
            _notify(
                "Scheduled Speed Test Failed",
                "",
                result.error or "Unknown error",
            )
        else:
            dl = (result.download_bps or 0) / 1_000_000
            ul = (result.upload_bps or 0) / 1_000_000
            ping = result.ping_ms or 0
            _notify(
                "\U0001f4ca Scheduled Speed Test",
                f"\u2193 {dl:.1f} Mbps  \u2191 {ul:.1f} Mbps",
                f"Ping: {ping:.1f} ms",
            )
        # Trigger a UI refresh on the main thread
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "refreshData:", None, False
        )

    def _stop_scheduler(self):
        if self._sched and self._sched_loop:
            # Schedule async stop on the scheduler's event loop
            future = asyncio.run_coroutine_threadsafe(
                self._sched.stop(), self._sched_loop
            )
            try:
                future.result(timeout=10.0)  # hard deadline
            except Exception:
                log.warning("Scheduler stop timed out or errored; forcing loop stop")
                self._sched_loop.call_soon_threadsafe(self._sched_loop.stop)
        elif self._sched:
            # Fallback: no loop available, just set the flag
            self._sched._running = False
        # Join thread with timeout — daemon flag ensures it dies with process
        if self._sched_thread and self._sched_thread.is_alive():
            self._sched_thread.join(timeout=10.0)
            if self._sched_thread.is_alive():
                log.error("Scheduler thread did not stop within 10s")

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    @objc.typedSelector(b"v@:@")
    def refreshData_(self, timer):
        threading.Thread(
            target=self._do_refresh, name="refresh", daemon=True
        ).start()

    def _submit_to_sched(self, coro, timeout=30.0):
        """Submit a coroutine to the scheduler event loop; fallback to asyncio.run()."""
        loop = self._sched_loop
        if loop and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=timeout)
        return asyncio.run(coro)

    def _do_refresh(self):
        try:
            data = self._submit_to_sched(self._fetch_latest())
            self._pending_data = data
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyData:", None, False
            )
        except Exception:
            log.exception("Refresh failed")

    async def _fetch_latest(self) -> dict:
        store = DataStore(self._db_path)
        await store.open()
        try:
            result = {}

            sys_rows = await store.get_system_snapshots(days=1)
            result["system"] = sys_rows[0] if sys_rows else None

            speed_rows = await store.get_speed_tests(days=1)
            result["speed"] = speed_rows[0] if speed_rows else None
            result["speed_id"] = speed_rows[0].get("id") if speed_rows else None
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            result["speed_tests_last_hour"] = 0
            for row in speed_rows:
                if not row.get("download_bps"):
                    continue
                ts = row.get("timestamp")
                if not ts:
                    continue
                try:
                    if datetime.fromisoformat(ts) >= one_hour_ago:
                        result["speed_tests_last_hour"] += 1
                except ValueError:
                    continue
            # Compute single rolling 24h average across timestamped speed tests.
            window = [s for s in speed_rows if s.get("download_bps")]
            if window:
                ul_vals = [s["upload_bps"] for s in window if s.get("upload_bps")]
                result["avg_24h"] = {
                    "dl": mean(s["download_bps"] for s in window),
                    "ul": mean(ul_vals) if ul_vals else None,
                    "count": len(window),
                }
            else:
                result["avg_24h"] = None

            lat_rows = await store.get_latency_probes(days=1)
            latest_by_target: dict[str, dict] = {}
            for row in lat_rows:
                target = row.get("target")
                if target and target not in latest_by_target:
                    latest_by_target[target] = row
            result["latency_by_target"] = latest_by_target
            result["latency"] = latest_by_target.get("gateway")
            loss_items = [
                (t, r.get("loss_pct"))
                for t, r in latest_by_target.items()
                if r.get("loss_pct") is not None and r.get("loss_pct", 0) > 0
            ]
            if loss_items:
                target, value = max(loss_items, key=lambda item: item[1] or 0.0)
                result["loss_max_target"] = target
                result["loss_max_pct"] = value
            else:
                result["loss_max_target"] = None
                result["loss_max_pct"] = None

            devs = await store.get_all_devices()
            result["device_count"] = len(devs)

            wifi_rows = await store.get_wifi_snapshots(days=1)
            by_band: dict[str, dict] = {}
            for w in wifi_rows:
                band = w.get("band", "")
                if band not in by_band:
                    by_band[band] = w
            result["wifi"] = by_band

            result["health"] = self._calc_health(sys_rows, speed_rows, lat_rows)

            # Client loads
            try:
                from asusroutercontrol.analysis.clients import get_client_load_summary
                result["client_loads"] = await get_client_load_summary(store)
            except Exception:
                result["client_loads"] = []

            # Per-client load trends (1h avg from device_perf_history)
            try:
                result["client_trends"] = await store.get_client_load_trends(hours=1)
            except Exception:
                result["client_trends"] = {}

            # Trend arrows (best-effort, skip if not enough data)
            try:
                from asusroutercontrol.analyzer import analyze_trends
                trends = await analyze_trends(store, days=7)
                result["trends"] = trends
            except Exception:
                result["trends"] = {}

            return result
        finally:
            await store.close()

    def _calc_health(self, sys_rows, speed_rows, lat_rows) -> float:
        score = 0.0

        dl = [s["download_bps"] for s in speed_rows if s.get("download_bps")]
        if dl:
            score += 25 * min(1.0, mean(dl) / PLAN_SPEED_DOWN)

        gw_avg = [
            r["avg_ms"] for r in lat_rows
            if r.get("target") == "gateway" and r.get("avg_ms")
        ]
        if gw_avg:
            avg = mean(gw_avg)
            if avg <= 10:
                score += 25
            elif avg <= 30:
                score += 25 * (1.0 - (avg - 10) / 20)

        loss = [r["loss_pct"] for r in lat_rows if r.get("loss_pct") is not None]
        if loss:
            avg_loss = mean(loss)
            if avg_loss == 0:
                score += 20
            elif avg_loss <= 2:
                score += 20 * (1.0 - avg_loss / 2)

        if sys_rows:
            latest = sys_rows[0]
            cpu = latest.get("cpu_pct")
            ram = latest.get("ram_pct")
            if cpu is not None and cpu < 80:
                score += 7.5
            if ram is not None and ram < 80:
                score += 7.5

        temps = [s["temp_c"] for s in sys_rows if s.get("temp_c")]
        if temps:
            t = temps[0]
            if t <= 80:
                score += 15
            elif t <= 90:
                score += 15 * (1.0 - (t - 80) / 10)

        return round(score, 1)

    @objc.typedSelector(b"v@:@")
    def applyData_(self, _ignored):
        """Update menu items — called on main thread."""
        data = self._pending_data
        if not data:
            return

        # --- menubar title: traffic dot meters ---
        health = data.get("health", 0)
        _spd = data.get("speed")
        _dl_bps = _spd.get("download_bps") if _spd else None
        _ul_bps = _spd.get("upload_bps") if _spd else None
        _lat = data.get("latency")  # gateway latency probe
        _gw_loss = _lat.get("loss_pct") if _lat else None
        _gw_lat_ms = _lat.get("avg_ms") if _lat else None
        status_dot = _connection_status(
            _dl_bps, _ul_bps,
            gw_loss_pct=_gw_loss, gw_latency_ms=_gw_lat_ms,
        )
        self.statusitem.button().setTitle_(f"📡 {status_dot}")
        self._mi_model.setTitle_(f"Router: RT-AC68U  ·  Health: {health:.0f}/100")

        sys_snap = data.get("system")
        if sys_snap:
            uptime_s = sys_snap.get("uptime_s")
            if uptime_s:
                days = uptime_s // 86400
                hours = (uptime_s % 86400) // 3600
                self._mi_uptime.setTitle_(f"Uptime: {days}d {hours}h")

            parts = []
            cpu = sys_snap.get("cpu_pct")
            ram = sys_snap.get("ram_pct")
            temp = sys_snap.get("temp_c")
            if cpu is not None:
                parts.append(f"CPU: {cpu:.1f}%")
            if ram is not None:
                parts.append(f"RAM: {ram:.1f}%")
            if temp is not None:
                parts.append(f"{temp:.0f}°C")
                if temp > TEMP_WARN_C:
                    _notify(
                        "⚠️ Router Temperature Warning",
                        f"Temperature: {temp:.0f}°C",
                        f"Exceeds {TEMP_WARN_C:.0f}°C threshold",
                    )
            if parts:
                self._mi_hw.setTitle_("  ".join(parts))

        trends = data.get("trends", {})
        dl_arrow = trends.get("download", {}).get("arrow", "")
        ul_arrow = trends.get("upload", {}).get("arrow", "")

        speed = data.get("speed")
        if speed:
            dl = speed.get("download_bps")
            ul = speed.get("upload_bps")
            dl_s = f"{dl / 1_000_000:.1f}" if dl else "—"
            ul_s = f"{ul / 1_000_000:.1f}" if ul else "—"
            # Confidence indicator from provider_details_json
            conf_dot = ""
            pdj = speed.get("provider_details_json") or "{}"
            if pdj != "{}":
                try:
                    import json as _json
                    conf = _json.loads(pdj).get("confidence", 0)
                    conf_dot = (
                        " 🟢" if conf >= 80
                        else " 🟡" if conf >= 50
                        else " 🔴"
                    )
                except Exception:
                    pass
            self._mi_speed.setTitle_(
                f"↓ {dl_s} Mbps {dl_arrow}  ↑ {ul_s} Mbps {ul_arrow}{conf_dot}".strip()
            )

        avg_24h = data.get("avg_24h")
        if avg_24h:
            dl_avg = f"{avg_24h['dl'] / 1_000_000:.1f}"
            ul_val = avg_24h.get("ul")
            ul_avg = f"{ul_val / 1_000_000:.1f}" if ul_val is not None else "—"
            one_hour_tests = int(data.get("speed_tests_last_hour") or 0)
            self._mi_avg_24h.setTitle_(
                f"  24h avg: ↓ {dl_avg} Mbps  ↑ {ul_avg} Mbps "
                f"({avg_24h['count']} tests, 1h: {one_hour_tests})"
            )
        else:
            self._mi_avg_24h.setTitle_("  24h avg: no data")

        if speed:
            sid = data.get("speed_id")
            if sid != self._last_speedtest_id and dl:
                self._last_speedtest_id = sid
                if dl < PLAN_SPEED_DOWN * SPEED_DROP_RATIO:
                    threshold = PLAN_SPEED_DOWN * SPEED_DROP_RATIO
                    _notify(
                        "⚠️ Speed Drop Detected",
                        f"Download: {dl / 1_000_000:.1f} Mbps",
                        f"Below {threshold / 1_000_000:.0f} Mbps",
                    )

        lat = data.get("latency")
        if lat:
            avg_ms = lat.get("avg_ms")
            loss = lat.get("loss_pct", 0)
            max_loss = data.get("loss_max_pct")
            max_target = data.get("loss_max_target")
            avg_s = f"{avg_ms:.1f}" if avg_ms else "—"
            loss_s = f"{loss:.1f}" if loss is not None else "—"
            if max_loss is not None and max_loss > 0:
                max_loss_s = f"{max_loss:.1f}"
                target_s = _friendly_target(max_target) if max_target else "any"
                self._mi_latency.setTitle_(
                    f"Ping: {avg_s} ms  Loss: {loss_s}% (max {max_loss_s}% {target_s})"
                )
            else:
                self._mi_latency.setTitle_(f"Ping: {avg_s} ms  Loss: {loss_s}%")

            if loss is not None and loss > LOSS_WARN_PCT:
                _notify(
                    "⚠️ Packet Loss Alert",
                    f"Gateway loss: {loss:.1f}%",
                    f"Exceeds {LOSS_WARN_PCT:.0f}% threshold",
                )

        dev_count = data.get("device_count", 0)
        self._mi_devices.setTitle_(f"Devices: {dev_count} connected")

        if self._last_device_count is not None and dev_count > self._last_device_count:
            new = dev_count - self._last_device_count
            _notify(
                "🆕 New Device Detected",
                f"{new} new device(s) on network",
                f"Total: {dev_count} devices",
            )
        self._last_device_count = dev_count

        wifi = data.get("wifi", {})
        w24 = wifi.get("2.4")
        w5 = wifi.get("5")
        w_wired = wifi.get("wired")

        # --- Wired LAN bandwidth (vlan1) ---
        if w_wired:
            wrx = w_wired.get("rx_rate_bps") or 0
            wtx = w_wired.get("tx_rate_bps") or 0
            if wrx or wtx:
                self._mi_lan_total.setTitle_(
                    f"LAN Wired: ↓{wrx / 1_000_000:.1f} ↑{wtx / 1_000_000:.1f} Mbps"
                )
            else:
                self._mi_lan_total.setTitle_("LAN Wired: awaiting data")
        else:
            self._mi_lan_total.setTitle_("LAN Wired: no data")
        if w24:
            ch = w24.get("channel", "?")
            clients = w24.get("client_count", 0)
            bw = _format_band_bw(w24)
            self._mi_wifi24.setTitle_(f"WiFi 2.4G: {clients} clients (ch {ch}){bw}")
        if w5:
            ch = w5.get("channel", "?")
            clients = w5.get("client_count", 0)
            bw = _format_band_bw(w5)
            self._mi_wifi5.setTitle_(f"WiFi 5G: {clients} clients (ch {ch}){bw}")

        # --- Client submenus (split by connectivity type) ---
        client_loads = data.get("client_loads", [])
        client_trends = data.get("client_trends", {})

        wifi24 = [c for c in client_loads if _band_bucket(c.get("band")) == "2.4"]
        wifi5  = [c for c in client_loads if _band_bucket(c.get("band")) == "5"]
        lan    = [c for c in client_loads if _band_bucket(c.get("band")) == "wired"]

        saturated = self._populate_client_submenu(
            self._clients_wifi24_submenu, wifi24, client_trends
        )
        saturated += self._populate_client_submenu(
            self._clients_wifi5_submenu, wifi5, client_trends
        )
        saturated += self._populate_client_submenu(
            self._clients_lan_submenu, lan, client_trends
        )

        self._mi_clients_wifi24.setTitle_(
            f"📶 WiFi 2.4GHz ({len(wifi24)})" if wifi24 else "📶 WiFi 2.4GHz Clients"
        )
        self._mi_clients_wifi5.setTitle_(
            f"📶 WiFi 5GHz ({len(wifi5)})" if wifi5 else "📶 WiFi 5GHz Clients"
        )
        self._mi_clients_lan.setTitle_(
            f"🔌 LAN ({len(lan)})" if lan else "🔌 LAN Clients"
        )

        # Saturation notification (cooldown: max once per 10 min)
        if saturated:
            now_ts = datetime.now()
            if (
                self._last_saturation_notify is None
                or (now_ts - self._last_saturation_notify).total_seconds() > 600
            ):
                self._last_saturation_notify = now_ts
                _notify(
                    "🔴 Client Saturation",
                    f"{len(saturated)} client(s) above 80% load",
                    ", ".join(saturated[:3]),
                )

        alive = self._sched_thread and self._sched_thread.is_alive()
        dot = "●" if alive else "○"
        status = "Running" if alive else "Stopped"
        self._mi_sched_status.setTitle_(f"Scheduler: {dot} {status}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @objc.typedSelector(b"v@:@")
    def runSpeedTest_(self, sender):
        self._mi_speedtest.setTitle_("▶ Running Speed Test...")
        threading.Thread(
            target=self._do_speedtest, name="speedtest", daemon=True
        ).start()

    def _do_speedtest(self):
        try:
            from asusroutercontrol.speedtest import run_speed_test

            result = self._submit_to_sched(run_speed_test(), timeout=300.0)

            if result.error:
                _notify("Speed Test Failed", "", result.error or "Unknown error")
            else:
                dl = (result.download_bps or 0) / 1_000_000
                ul = (result.upload_bps or 0) / 1_000_000
                ping = result.ping_ms or 0
                self._submit_to_sched(self._store_speedtest(result))
                _notify(
                    "✅ Speed Test Complete",
                    f"↓ {dl:.1f} Mbps  ↑ {ul:.1f} Mbps",
                    f"Ping: {ping:.1f} ms",
                )
        except Exception as exc:
            log.exception("Speed test action failed")
            _notify("Speed Test Error", "", str(exc)[:100])
        finally:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "resetSpeedTestTitle:", None, False
            )

    @objc.typedSelector(b"v@:@")
    def resetSpeedTestTitle_(self, _):
        self._mi_speedtest.setTitle_("▶ Run Speed Test")

    async def _store_speedtest(self, result):
        store = DataStore(self._db_path)
        await store.open()
        try:
            await store.insert_speed_test(result)
        finally:
            await store.close()

    @objc.typedSelector(b"v@:@")
    def genReport_(self, sender):
        self._mi_report.setTitle_("📊 Generating Report...")
        threading.Thread(
            target=self._do_report, name="report", daemon=True
        ).start()

    def _do_report(self):
        try:
            from asusroutercontrol.reporting import export_report_json, generate_report

            async def _gen():
                store = DataStore(self._db_path)
                await store.open()
                try:
                    return await generate_report(store, days=7)
                finally:
                    await store.close()

            data = asyncio.run(_gen())

            report_dir = self._cfg.data_dir / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = report_dir / f"report_{ts}.json"
            export_report_json(data, report_path)

            health = data.get("summary", {}).get("health_score", "?")
            _notify(
                "📊 Report Generated",
                f"Health Score: {health}",
                f"Saved to {report_path.name}",
            )
        except Exception as exc:
            log.exception("Report generation failed")
            _notify("Report Error", "", str(exc)[:100])
        finally:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "resetReportTitle:", None, False
            )

    @objc.typedSelector(b"v@:@")
    def resetReportTitle_(self, _):
        self._mi_report.setTitle_("📊 Generate Report")

    @objc.typedSelector(b"v@:@")
    def rebootRouter_(self, sender):
        alert = NSAlert.new()
        alert.setMessageText_("Reboot Router?")
        alert.setInformativeText_("This will temporarily disconnect all devices.")
        alert.addButtonWithTitle_("Reboot")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() == NSAlertFirstButtonReturn:
            threading.Thread(
                target=self._do_reboot, name="reboot", daemon=True
            ).start()

    def _do_reboot(self):
        try:
            from asusroutercontrol.backends.merlin import MerlinBackend
            from asusroutercontrol.credentials import get_router_credentials

            username, password = get_router_credentials()
            if not username or not password:
                _notify("Reboot Failed", "", "No credentials configured")
                return

            async def _reboot():
                backend = MerlinBackend(
                    hostname=self._cfg.router_host,
                    username=username,
                    password=password,
                    use_ssl=self._cfg.use_ssl,
                    port=self._cfg.router_port,
                )
                await backend.connect()
                try:
                    return await backend.set_state("reboot")
                finally:
                    await backend.disconnect()

            ok = asyncio.run(_reboot())
            if ok:
                _notify("🔄 Router Rebooting", "", "Allow 2-3 min to reconnect")
            else:
                _notify("Reboot Failed", "", "Router did not accept command")
        except Exception as exc:
            log.exception("Reboot action failed")
            _notify("Reboot Error", "", str(exc)[:100])

    @objc.typedSelector(b"v@:@")
    def openLog_(self, sender):
        if self._log_path.exists():
            subprocess.Popen(["open", "-a", "Console", str(self._log_path)])
        else:
            _notify("No Log File", "", f"Expected at {self._log_path}")

    @objc.typedSelector(b"v@:@")
    def killApp_(self, sender):
        """Stop scheduler, unload launchd agent, then terminate — no restart."""
        log.info("killApp_ invoked — shutting down")
        self._stop_scheduler()
        from pathlib import Path

        plist = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            log.info("Unloaded launchd agent — will not restart")
        NSApplication.sharedApplication().terminate_(sender)

    @objc.typedSelector(b"v@:@")
    def quitApp_(self, sender):
        """Stop scheduler and terminate — launchd KeepAlive will restart."""
        log.info("quitApp_ invoked — restarting")
        self._stop_scheduler()
        from pathlib import Path

        plist = Path.home() / "Library" / "LaunchAgents" / "com.asusroutermonitor.plist"
        if plist.exists():
            # Ensure agent is loaded so KeepAlive can respawn after terminate
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
            log.info("Ensured launchd agent is loaded — KeepAlive will restart")
        NSApplication.sharedApplication().terminate_(sender)


def main() -> None:
    """Entry point for the menubar app."""
    cfg = load_config()
    cfg.ensure_dirs()
    log_path = cfg.data_dir / "scheduler.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.FileHandler(str(log_path))],
    )

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
