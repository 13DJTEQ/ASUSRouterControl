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
from datetime import datetime
from statistics import mean

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSStatusBar,
    NSTimer,
    NSVariableStatusItemLength,
)
from PyObjCTools import AppHelper

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

REFRESH_INTERVAL = 60.0  # seconds between menu data refreshes




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
        self._sched_thread = None
        self._sched_loop = None
        self._last_speedtest_id = None
        self._last_device_count = None

        self.statusbar = NSStatusBar.systemStatusBar()
        self.statusitem = self.statusbar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.statusitem.button().setTitle_("📡 —")

        self._build_menu()
        self._start_scheduler()

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_INTERVAL, self, "refreshData:", None, True
        )
        threading.Thread(
            target=self._do_refresh, name="initial-refresh", daemon=True
        ).start()

        log.info("AsusRouterMonitor ready")

    def _build_menu(self):
        menu = NSMenu.new()
        menu.setAutoenablesItems_(False)

        self._mi_model = _add_info(menu, "Router: connecting...")
        self._mi_uptime = _add_info(menu, "Uptime: —")
        self._mi_hw = _add_info(menu, "CPU: —  RAM: —  Temp: —")
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_speed = _add_info(menu, "↓ — Mbps  ↑ — Mbps")
        self._mi_avg_24h = _add_info(menu, "  24h avg: ↓ — Mbps  ↑ — Mbps")
        self._mi_latency = _add_info(menu, "Ping: — ms  Loss: —%")
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_devices = _add_info(menu, "Devices: —")
        self._mi_wifi24 = _add_info(menu, "WiFi 2.4G: —")
        self._mi_wifi5 = _add_info(menu, "WiFi 5G: —")
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_speedtest = _add_action(menu, "▶ Run Speed Test", "runSpeedTest:", self)
        self._mi_report = _add_action(menu, "📊 Generate Report", "genReport:", self)
        _add_action(menu, "🔄 Reboot Router...", "rebootRouter:", self)
        menu.addItem_(NSMenuItem.separatorItem())

        self._mi_sched_status = _add_info(menu, "Scheduler: starting...")
        _add_action(menu, "Open Log File", "openLog:", self)
        menu.addItem_(NSMenuItem.separatorItem())

        _add_action(menu, "⛔ Kill (No Restart)", "killApp:", self)
        _add_action(menu, "Quit AsusRouterMonitor", "quitApp:", self)
        self.statusitem.setMenu_(menu)


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
                self._sched = MonitorScheduler(store, self._cfg)
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

    def _stop_scheduler(self):
        if self._sched:
            self._sched.stop()
        if self._sched_loop:
            self._sched_loop.call_soon_threadsafe(self._sched_loop.stop)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    @objc.typedSelector(b"v@:@")
    def refreshData_(self, timer):
        threading.Thread(
            target=self._do_refresh, name="refresh", daemon=True
        ).start()

    def _do_refresh(self):
        try:
            data = asyncio.run(self._fetch_latest())
            # Store data on self to avoid PyObjC NSDictionary bridging
            # issues with None values in nested dicts.
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
            gw = [r for r in lat_rows if r.get("target") == "gateway"]
            result["latency"] = gw[0] if gw else None

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
        self._mi_model.setTitle_("Router: RT-AC68U")
        health = data.get("health", 0)
        self.statusitem.button().setTitle_(f"📡 {health:.0f}")

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
            self._mi_avg_24h.setTitle_(
                f"  24h avg: ↓ {dl_avg} Mbps  ↑ {ul_avg} Mbps ({avg_24h['count']} tests)"
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
            avg_s = f"{avg_ms:.1f}" if avg_ms else "—"
            loss_s = f"{loss:.1f}" if loss is not None else "—"
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
        if w24:
            ch = w24.get("channel", "?")
            clients = w24.get("client_count", 0)
            self._mi_wifi24.setTitle_(f"WiFi 2.4G: {clients} clients (ch {ch})")
        if w5:
            ch = w5.get("channel", "?")
            clients = w5.get("client_count", 0)
            self._mi_wifi5.setTitle_(f"WiFi 5G: {clients} clients (ch {ch})")

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

            result = asyncio.run(run_speed_test())

            if result.error:
                _notify("Speed Test Failed", "", result.error or "Unknown error")
            else:
                dl = (result.download_bps or 0) / 1_000_000
                ul = (result.upload_bps or 0) / 1_000_000
                ping = result.ping_ms or 0
                asyncio.run(self._store_speedtest(result))
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
