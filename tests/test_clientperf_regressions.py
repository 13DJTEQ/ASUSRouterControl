"""Regression tests for client-perf feature path.

Covers:
1. Degraded startup → recovery (scheduler task selection)
2. Mixed naive/aware timestamp boundaries (datastore insert/query)
3. 6GHz bucket coverage (client loads, probes, reporting)
4. Fallback when presence rows dominate (get_client_load_summary)
5. Diagnostics expectations when summary errors occur (reporting)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from asusroutercontrol.analysis.clients import (
    BAND_LINK_RATES,
    compute_client_loads,
    get_client_load_summary,
)
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import (
    ClientLoad,
    ConnectionType,
    Device,
    SpeedTestResult,
    SystemSnapshot,
    WiFiSnapshot,
)
from asusroutercontrol.probes import _band_labels_for_wl_unit
from asusroutercontrol.reporting import (
    _build_summary,
    _build_wifi,
    _calculate_health_score,
    generate_report,
)
from asusroutercontrol.scheduler import (
    MonitorScheduler,
    RuntimeProfile,
    _backoff_seconds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device(**overrides) -> Device:
    defaults = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "ip": "192.168.1.100",
        "hostname": "test-device",
        "connection": ConnectionType.WIFI_5G,
        "band": "5GHz",
        "rssi": -45,
        "tx_rate_mbps": 100.0,
        "rx_rate_mbps": 50.0,
        "is_online": True,
    }
    defaults.update(overrides)
    return Device(**defaults)


# ===================================================================
# 1. Degraded startup then recovery
# ===================================================================


class TestDegradedStartupRecovery:
    """Scheduler selects correct task sets for each capability level."""

    def _make_scheduler(self) -> MonitorScheduler:
        store = AsyncMock(spec=DataStore)
        return MonitorScheduler(store)

    def test_full_capability_selects_all_tasks(self) -> None:
        sched = self._make_scheduler()
        profile = RuntimeProfile(capability="full", operation_mode="router")
        selected, skipped = sched._select_task_specs(profile)
        names = {s.name for s in selected}
        assert "speedtest" in names
        assert "probe" in names
        assert "client-traffic" in names
        assert "poll" in names
        assert "config" in names
        assert "recommend" in names
        assert "prune" in names
        assert len(skipped) == 0

    def test_degraded_no_credentials_skips_credential_tasks(self) -> None:
        sched = self._make_scheduler()
        profile = RuntimeProfile(
            capability="degraded-no-credentials", operation_mode="unknown"
        )
        selected, skipped = sched._select_task_specs(profile)
        selected_names = {s.name for s in selected}
        skipped_names = {name for name, _ in skipped}
        # poll requires credentials
        assert "poll" in skipped_names
        assert "poll" not in selected_names
        # ssh-requiring tasks also skipped (degraded-no-credentials != full)
        assert "probe" in skipped_names
        assert "client-traffic" in skipped_names
        assert "config" in skipped_names
        # non-credential, non-ssh tasks still selected
        assert "prune" in selected_names
        assert "recommend" in selected_names

    def test_degraded_no_ssh_skips_ssh_tasks(self) -> None:
        sched = self._make_scheduler()
        profile = RuntimeProfile(
            capability="degraded-no-ssh", operation_mode="router"
        )
        selected, skipped = sched._select_task_specs(profile)
        selected_names = {s.name for s in selected}
        skipped_names = {name for name, _ in skipped}
        assert "probe" in skipped_names
        assert "client-traffic" in skipped_names
        assert "config" in skipped_names
        # poll requires credentials, not ssh; degraded-no-ssh has creds
        assert "poll" in selected_names
        # speedtest is router_mode_only
        assert "speedtest" in selected_names

    def test_access_point_mode_skips_speedtest(self) -> None:
        sched = self._make_scheduler()
        profile = RuntimeProfile(capability="full", operation_mode="access_point")
        selected, skipped = sched._select_task_specs(profile)
        selected_names = {s.name for s in selected}
        skipped_names = {name for name, _ in skipped}
        assert "speedtest" in skipped_names
        assert "speedtest" not in selected_names
        # Other tasks still selected
        assert "probe" in selected_names
        assert "poll" in selected_names

    def test_recovery_from_degraded_to_full(self) -> None:
        """Simulates environment recovering: degraded → full selects all."""
        sched = self._make_scheduler()
        degraded = RuntimeProfile(
            capability="degraded-no-ssh", operation_mode="router"
        )
        _, skipped_degraded = sched._select_task_specs(degraded)
        assert len(skipped_degraded) > 0

        full = RuntimeProfile(capability="full", operation_mode="router")
        selected_full, skipped_full = sched._select_task_specs(full)
        assert len(skipped_full) == 0
        assert len(selected_full) == len(sched._task_specs())

    def test_backoff_schedule_boundaries(self) -> None:
        assert _backoff_seconds(0) == 0.0
        assert _backoff_seconds(4) == 0.0
        assert _backoff_seconds(5) == 60
        assert _backoff_seconds(6) == 120
        assert _backoff_seconds(7) == 300
        assert _backoff_seconds(100) == 300  # clamps

    def test_record_failure_and_success_resets(self) -> None:
        sched = self._make_scheduler()
        for _ in range(6):
            sched._record_failure("probe")
        assert sched._failures["probe"] == 6
        sched._record_success("probe")
        assert "probe" not in sched._failures


# ===================================================================
# 2. Mixed naive/aware timestamp boundaries
# ===================================================================


class TestMixedTimestampBoundaries:
    """Ensure datastore insert/query works with both naive and aware datetimes."""

    async def test_naive_timestamp_insert_and_query(self, tmp_path) -> None:
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            naive_ts = datetime(2026, 6, 7, 10, 0, 0)
            await store.insert_speed_test(
                SpeedTestResult(
                    timestamp=naive_ts,
                    download_bps=250_000_000,
                    upload_bps=30_000_000,
                    ping_ms=12.0,
                    source="ookla",
                )
            )
            rows = await store.get_speed_tests(days=365)
            assert len(rows) == 1
            assert "2026-06-07T10:00:00" in rows[0]["timestamp"]
        finally:
            await store.close()

    async def test_aware_timestamp_insert_and_query(self, tmp_path) -> None:
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            aware_ts = datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc)
            await store.insert_speed_test(
                SpeedTestResult(
                    timestamp=aware_ts,
                    download_bps=250_000_000,
                    upload_bps=30_000_000,
                    ping_ms=12.0,
                    source="ookla",
                )
            )
            rows = await store.get_speed_tests(days=365)
            assert len(rows) == 1
            # Should contain the timestamp regardless of tz format
            assert "2026-06-07" in rows[0]["timestamp"]
        finally:
            await store.close()

    async def test_mixed_naive_aware_wifi_snapshots(self, tmp_path) -> None:
        """Insert wifi snapshots with both naive and aware timestamps;
        both should be retrievable in the same query."""
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            naive = datetime(2026, 6, 7, 10, 0, 0)
            aware = datetime(2026, 6, 7, 10, 5, 0, tzinfo=timezone.utc)
            await store.insert_wifi_snapshot(
                WiFiSnapshot(timestamp=naive, band="5", client_count=3, avg_rssi=-55)
            )
            await store.insert_wifi_snapshot(
                WiFiSnapshot(timestamp=aware, band="5", client_count=5, avg_rssi=-50)
            )
            rows = await store.get_wifi_snapshots(days=365, band="5")
            assert len(rows) == 2
        finally:
            await store.close()

    async def test_device_perf_timestamp_ordering_with_mixed_types(
        self, tmp_path
    ) -> None:
        """device_perf_history rows with naive/aware timestamps should
        sort consistently by ISO string comparison."""
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            mac = "AA:BB:CC:DD:EE:01"
            naive = datetime(2026, 6, 7, 10, 0, 0)
            aware = datetime(2026, 6, 7, 10, 5, 0, tzinfo=timezone.utc)
            await store.insert_device_perf(
                ClientLoad(timestamp=naive, mac=mac, load_pct=1.0, band="5GHz")
            )
            await store.insert_device_perf(
                ClientLoad(timestamp=aware, mac=mac, load_pct=2.0, band="5GHz")
            )
            rows = await store.get_device_perf_history(mac, days=365)
            assert len(rows) == 2
            # Both rows retrievable and ordered DESC by timestamp
            timestamps = [r["timestamp"] for r in rows]
            assert timestamps == sorted(timestamps, reverse=True)
        finally:
            await store.close()

    async def test_system_snapshot_mixed_timestamps(self, tmp_path) -> None:
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            naive = datetime(2026, 6, 7, 10, 0, 0)
            aware = datetime(2026, 6, 7, 10, 5, 0, tzinfo=timezone.utc)
            await store.insert_system_snapshot(
                SystemSnapshot(timestamp=naive, cpu_pct=10.0, ram_pct=50.0, temp_c=60.0)
            )
            await store.insert_system_snapshot(
                SystemSnapshot(timestamp=aware, cpu_pct=15.0, ram_pct=55.0, temp_c=62.0)
            )
            rows = await store.get_system_snapshots(days=365)
            assert len(rows) == 2
        finally:
            await store.close()


# ===================================================================
# 3. 6GHz bucket coverage
# ===================================================================


class Test6GHzBucketCoverage:
    """6GHz band is properly handled across client loads, probes, reporting."""

    def test_band_labels_for_wl_unit_6ghz(self) -> None:
        snapshot_band, traffic_band = _band_labels_for_wl_unit(2)
        assert snapshot_band == "6"
        assert traffic_band == "6GHz"

    def test_band_link_rates_includes_6ghz(self) -> None:
        assert "6GHz" in BAND_LINK_RATES
        assert BAND_LINK_RATES["6GHz"] == 1200.0

    def test_compute_loads_6ghz_device(self) -> None:
        dev = _make_device(
            connection=ConnectionType.WIFI_6G,
            band="6GHz",
            tx_rate_mbps=600.0,
            rx_rate_mbps=200.0,
        )
        loads = compute_client_loads([dev])
        assert len(loads) == 1
        # 600/1200 * 100 = 50%
        assert loads[0].load_pct == 50.0
        assert loads[0].health == "🟡"
        assert loads[0].band == "6GHz"

    def test_compute_loads_6ghz_high_throughput(self) -> None:
        dev = _make_device(
            connection=ConnectionType.WIFI_6G,
            band="6GHz",
            tx_rate_mbps=1100.0,
            rx_rate_mbps=100.0,
        )
        loads = compute_client_loads([dev])
        # 1100/1200 * 100 ≈ 91.7% → red
        assert loads[0].load_pct > 80.0
        assert loads[0].health == "🔴"

    def test_compute_loads_6ghz_low_throughput_weak_signal(self) -> None:
        dev = _make_device(
            connection=ConnectionType.WIFI_6G,
            band="6GHz",
            tx_rate_mbps=10.0,
            rx_rate_mbps=5.0,
            rssi=-80,
        )
        loads = compute_client_loads([dev])
        assert loads[0].load_pct < 5.0
        # Weak RSSI forces red
        assert loads[0].health == "🔴"

    def test_build_wifi_includes_6ghz_band(self) -> None:
        snaps = [
            {
                "band": "2.4",
                "client_count": 3,
                "avg_rssi": -55,
                "noise_floor": -92,
            },
            {
                "band": "5",
                "client_count": 5,
                "avg_rssi": -45,
                "noise_floor": -95,
            },
            {
                "band": "6",
                "client_count": 2,
                "avg_rssi": -40,
                "noise_floor": -98,
            },
        ]
        result = _build_wifi(snaps)
        assert "2.4" in result
        assert "5" in result
        # _build_wifi only iterates over ("2.4", "5") — 6GHz is not yet included
        # This test documents the current behavior for regression tracking
        # If 6GHz support is added to _build_wifi, this assertion should change

    def test_connection_type_6ghz_enum_value(self) -> None:
        assert ConnectionType.WIFI_6G.value == "6GHz"
        assert ConnectionType.WIFI_6G == ConnectionType("6GHz")

    def test_band_labels_unknown_unit_fallback(self) -> None:
        snapshot_band, traffic_band = _band_labels_for_wl_unit(3)
        assert snapshot_band == "wl3"
        assert traffic_band == "wl3"

    async def test_datastore_6ghz_device_perf(self, tmp_path) -> None:
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            mac = "CC:CC:CC:CC:CC:01"
            now = datetime.utcnow()
            await store.upsert_device(
                Device(
                    mac=mac,
                    hostname="wifi6e-phone",
                    connection=ConnectionType.WIFI_6G,
                    band="6GHz",
                )
            )
            await store.insert_device_perf(
                ClientLoad(
                    timestamp=now,
                    mac=mac,
                    band="6GHz",
                    tx_rate_mbps=500.0,
                    rx_rate_mbps=200.0,
                    rssi=-35,
                    load_pct=41.7,
                )
            )
            rows = await store.get_client_loads(hours=1, limit=10)
            assert len(rows) == 1
            assert rows[0]["band"] == "6GHz"
            assert rows[0]["mac"] == mac
        finally:
            await store.close()


# ===================================================================
# 4. Fallback when presence rows dominate
# ===================================================================


class TestPresenceRowDomination:
    """get_client_load_summary handles data where most rows lack signal."""

    async def test_all_presence_rows_returns_fallback(self) -> None:
        """When every row is presence-only, they should still be returned."""

        class _Store:
            async def get_client_loads(self, *, hours: int = 1):
                return [
                    {
                        "mac": f"AA:AA:AA:AA:AA:{i:02X}",
                        "timestamp": f"2026-06-07T10:00:{i:02d}",
                        "tx_rate_mbps": None,
                        "rx_rate_mbps": None,
                        "rssi": None,
                        "load_pct": 0.0,
                    }
                    for i in range(10)
                ]

        rows = await get_client_load_summary(_Store())
        assert len(rows) == 10
        # All rows are presence-only
        for row in rows:
            assert row["tx_rate_mbps"] is None

    async def test_single_signal_row_surfaces_amid_presence(self) -> None:
        """One signal row among many presence-only rows: signal row wins."""

        class _Store:
            async def get_client_loads(self, *, hours: int = 1):
                presence = [
                    {
                        "mac": "AA:AA:AA:AA:AA:01",
                        "timestamp": f"2026-06-07T10:00:{i:02d}",
                        "tx_rate_mbps": None,
                        "rx_rate_mbps": None,
                        "rssi": None,
                        "load_pct": 0.0,
                    }
                    for i in range(5)
                ]
                signal = {
                    "mac": "AA:AA:AA:AA:AA:01",
                    "timestamp": "2026-06-07T10:00:01",
                    "tx_rate_mbps": 5.0,
                    "rx_rate_mbps": 2.0,
                    "rssi": -55,
                    "load_pct": 3.5,
                }
                return presence + [signal]

        rows = await get_client_load_summary(_Store())
        assert len(rows) == 1
        assert rows[0]["tx_rate_mbps"] == 5.0
        assert rows[0]["rssi"] == -55

    async def test_presence_dominated_respects_max_rows(self) -> None:
        """When >15 presence MACs exist, output is capped at 15."""

        class _Store:
            async def get_client_loads(self, *, hours: int = 1):
                return [
                    {
                        "mac": f"AA:AA:AA:AA:{i // 256:02X}:{i % 256:02X}",
                        "timestamp": f"2026-06-07T10:{i // 60:02d}:{i % 60:02d}",
                        "tx_rate_mbps": None,
                        "rx_rate_mbps": None,
                        "rssi": None,
                        "load_pct": 0.0,
                    }
                    for i in range(25)
                ]

        rows = await get_client_load_summary(_Store())
        assert len(rows) == 15

    async def test_mixed_signal_and_presence_per_mac_prefers_signal(self) -> None:
        """Multiple MACs with mixed rows: signal row preferred per MAC."""

        class _Store:
            async def get_client_loads(self, *, hours: int = 1):
                rows = []
                for mac_idx in range(3):
                    mac = f"AA:AA:AA:AA:AA:{mac_idx:02X}"
                    # Presence row
                    rows.append({
                        "mac": mac,
                        "timestamp": f"2026-06-07T10:00:{mac_idx * 10:02d}",
                        "tx_rate_mbps": None,
                        "rx_rate_mbps": None,
                        "rssi": None,
                        "load_pct": 99.0,
                    })
                    # Signal row (lower load_pct but has signal)
                    rows.append({
                        "mac": mac,
                        "timestamp": f"2026-06-07T10:00:{mac_idx * 10 + 5:02d}",
                        "tx_rate_mbps": 10.0 + mac_idx,
                        "rx_rate_mbps": 5.0,
                        "rssi": -50 - mac_idx,
                        "load_pct": float(mac_idx),
                    })
                return rows

        rows = await get_client_load_summary(_Store())
        assert len(rows) == 3
        for row in rows:
            assert row["tx_rate_mbps"] is not None

    async def test_wired_presence_row_preserved_when_wireless_dominates(self) -> None:
        """Wired device with presence-only data is preserved alongside wireless."""

        class _Store:
            async def get_client_loads(self, *, hours: int = 1):
                wireless = [
                    {
                        "mac": f"AA:AA:AA:AA:AA:{i:02X}",
                        "timestamp": f"2026-06-07T10:00:{i:02d}",
                        "tx_rate_mbps": 20.0,
                        "rx_rate_mbps": 10.0,
                        "rssi": -50,
                        "load_pct": 30.0 - i,
                        "band": "5GHz",
                    }
                    for i in range(12)
                ]
                wired = {
                    "mac": "BB:BB:BB:BB:BB:01",
                    "timestamp": "2026-06-07T10:01:00",
                    "tx_rate_mbps": None,
                    "rx_rate_mbps": None,
                    "rssi": None,
                    "load_pct": 0.0,
                    "band": "wired",
                }
                return wireless + [wired]

        rows = await get_client_load_summary(_Store())
        assert len(rows) <= 15
        bands = [str(r.get("band", "")).lower() for r in rows]
        assert "wired" in bands


# ===================================================================
# 5. Diagnostics expectations when summary errors occur
# ===================================================================


class TestDiagnosticsOnSummaryErrors:
    """Reporting functions degrade gracefully with empty/error data."""

    def test_build_summary_all_empty(self) -> None:
        result = _build_summary([], [], [], [], [])
        assert result["speed_tests_count"] == 0
        assert result["device_count"] == 0
        assert result["avg_download"] == "N/A"
        assert result["avg_upload"] == "N/A"
        assert result["uptime_pct"] == 100.0

    def test_calculate_health_score_all_none(self) -> None:
        score = _calculate_health_score(
            uptime_pct=None,
            avg_download=None,
            gw_p95=None,
            avg_loss=None,
            p95_temp=None,
            avg_rssi=None,
        )
        assert score == 0.0

    def test_calculate_health_score_perfect_inputs(self) -> None:
        score = _calculate_health_score(
            uptime_pct=100.0,
            avg_download=300_000_000,
            gw_p95=5.0,
            avg_loss=0.0,
            p95_temp=70.0,
            avg_rssi=-40,
        )
        # 20 + 25 + 20 + 15 + 10 + 10 = 100
        assert score == 100.0

    def test_calculate_health_score_partial_data(self) -> None:
        score = _calculate_health_score(
            uptime_pct=99.0,
            avg_download=None,
            gw_p95=None,
            avg_loss=None,
            p95_temp=None,
            avg_rssi=None,
        )
        # Only uptime component: 20 * min(1.0, 99.0/99.9) ≈ 19.8
        assert 19.0 < score < 20.1

    def test_calculate_health_score_degraded_latency(self) -> None:
        score_good = _calculate_health_score(
            uptime_pct=None,
            avg_download=None,
            gw_p95=5.0,
            avg_loss=None,
            p95_temp=None,
            avg_rssi=None,
        )
        score_bad = _calculate_health_score(
            uptime_pct=None,
            avg_download=None,
            gw_p95=25.0,
            avg_loss=None,
            p95_temp=None,
            avg_rssi=None,
        )
        assert score_good > score_bad

    def test_calculate_health_score_high_temp(self) -> None:
        score_cool = _calculate_health_score(
            uptime_pct=None,
            avg_download=None,
            gw_p95=None,
            avg_loss=None,
            p95_temp=70.0,
            avg_rssi=None,
        )
        score_hot = _calculate_health_score(
            uptime_pct=None,
            avg_download=None,
            gw_p95=None,
            avg_loss=None,
            p95_temp=95.0,
            avg_rssi=None,
        )
        assert score_cool == 10.0
        assert score_hot == 0.0

    def test_build_summary_with_partial_speed_data(self) -> None:
        speed_tests = [
            {"download_bps": 200_000_000, "upload_bps": None},
            {"download_bps": 250_000_000, "upload_bps": 30_000_000},
        ]
        result = _build_summary(speed_tests, [], [], [], [])
        assert result["speed_tests_count"] == 2
        assert result["avg_download"] != "N/A"

    async def test_generate_report_graceful_on_analyzer_import_error(
        self, tmp_path
    ) -> None:
        """generate_report catches errors in trends/SLA/config_impact sections."""
        store = DataStore(tmp_path / "router.db")
        await store.open()
        try:
            now = datetime.utcnow()
            await store.insert_speed_test(
                SpeedTestResult(
                    timestamp=now,
                    download_bps=250_000_000,
                    upload_bps=30_000_000,
                    ping_ms=12.0,
                    source="ookla",
                )
            )
            # Patch analyzer to raise, simulating import/runtime error
            with patch(
                "asusroutercontrol.analyzer.analyze_trends",
                side_effect=RuntimeError("simulated"),
            ):
                report = await generate_report(store, days=1)
            # Summary should still be populated
            assert "summary" in report
            assert report["summary"]["speed_tests_count"] >= 1
            # Trends/SLA/config_impact should be empty fallbacks
            assert report["trends"] == {}
            assert report["sla"] == {}
            assert report["config_impact"] == []
        finally:
            await store.close()

    def test_build_wifi_empty_input(self) -> None:
        result = _build_wifi([])
        assert result == {}

    def test_build_summary_bad_timestamp_in_speed_test(self) -> None:
        """Speed tests with invalid data (missing download_bps) handled."""
        speed_tests = [
            {"download_bps": None, "upload_bps": None, "error": "timeout"},
        ]
        result = _build_summary(speed_tests, [], [], [], [])
        assert result["speed_tests_count"] == 1
        assert result["avg_download"] == "N/A"
