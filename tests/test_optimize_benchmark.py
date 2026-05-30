from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from asusroutercontrol.benchmark import run_datastore_benchmark
from asusroutercontrol.cli import cli
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import Device, LatencyProbe, SpeedTestResult, WiFiSnapshot


@pytest.mark.asyncio
async def test_run_datastore_benchmark_returns_expected_metrics(tmp_path) -> None:
    store = DataStore(tmp_path / "router.db")
    await store.open()
    try:
        await store.insert_speed_test(
            SpeedTestResult(
                download_bps=250_000_000,
                upload_bps=35_000_000,
                ping_ms=12.0,
                source="composite",
            )
        )
        await store.insert_latency_probe(
            LatencyProbe(
                target="gateway",
                min_ms=1.5,
                avg_ms=2.0,
                max_ms=3.5,
                jitter_ms=0.4,
                samples=6,
            )
        )
        await store.insert_wifi_snapshot(
            WiFiSnapshot(
                band="5",
                client_count=2,
                avg_rssi=-56.0,
                min_rssi=-65.0,
            )
        )
        await store.upsert_device(
            Device(
                mac="aa:bb:cc:dd:ee:ff",
                ip="192.168.1.10",
                hostname="benchmark-device",
            )
        )

        payload = await run_datastore_benchmark(store, iterations=3, days=7)
    finally:
        await store.close()

    assert payload["iterations"] == 3
    assert payload["days"] == 7

    metrics = payload["metrics"]
    for metric_name in (
        "query_speed_tests_ms",
        "query_latency_probes_ms",
        "query_wifi_snapshots_ms",
        "query_devices_ms",
        "write_temp_insert_ms",
        "write_commit_ms",
    ):
        summary = metrics[metric_name]
        assert summary["count"] == 3
        assert "p50_ms" in summary
        assert "p95_ms" in summary
        assert "ops_per_sec" in summary

    sample_sizes = payload["sample_sizes"]
    assert sample_sizes["speed_tests_rows"] >= 1
    assert sample_sizes["latency_probes_rows"] >= 1
    assert sample_sizes["wifi_snapshots_rows"] >= 1
    assert sample_sizes["devices_rows"] >= 1


def test_optimize_benchmark_cli_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDataStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def open(self) -> None:
            return None

        async def close(self) -> None:
            return None

    async def _fake_run_datastore_benchmark(_store, *, iterations: int, days: int):
        return {
            "generated_at": "2026-03-15T00:00:00+00:00",
            "iterations": iterations,
            "days": days,
            "sample_sizes": {
                "speed_tests_rows": 4,
                "latency_probes_rows": 2,
                "wifi_snapshots_rows": 3,
                "devices_rows": 5,
            },
            "metrics": {
                "query_speed_tests_ms": {
                    "count": iterations,
                    "min_ms": 1.0,
                    "p50_ms": 2.0,
                    "p95_ms": 3.0,
                    "mean_ms": 2.1,
                    "max_ms": 3.2,
                    "ops_per_sec": 400.0,
                },
                "query_latency_probes_ms": {
                    "count": iterations,
                    "min_ms": 1.1,
                    "p50_ms": 2.1,
                    "p95_ms": 3.1,
                    "mean_ms": 2.2,
                    "max_ms": 3.3,
                    "ops_per_sec": 350.0,
                },
                "query_wifi_snapshots_ms": {
                    "count": iterations,
                    "min_ms": 1.2,
                    "p50_ms": 2.2,
                    "p95_ms": 3.2,
                    "mean_ms": 2.3,
                    "max_ms": 3.4,
                    "ops_per_sec": 320.0,
                },
                "query_devices_ms": {
                    "count": iterations,
                    "min_ms": 1.3,
                    "p50_ms": 2.3,
                    "p95_ms": 3.3,
                    "mean_ms": 2.4,
                    "max_ms": 3.5,
                    "ops_per_sec": 300.0,
                },
                "write_temp_insert_ms": {
                    "count": iterations,
                    "min_ms": 0.8,
                    "p50_ms": 1.2,
                    "p95_ms": 1.7,
                    "mean_ms": 1.3,
                    "max_ms": 1.9,
                    "ops_per_sec": 700.0,
                },
                "write_commit_ms": {
                    "count": iterations,
                    "min_ms": 0.7,
                    "p50_ms": 1.0,
                    "p95_ms": 1.4,
                    "mean_ms": 1.1,
                    "max_ms": 1.6,
                    "ops_per_sec": 750.0,
                },
            },
        }

    monkeypatch.setattr("asusroutercontrol.cli.DataStore", _FakeDataStore)
    monkeypatch.setattr(
        "asusroutercontrol.benchmark.run_datastore_benchmark",
        _fake_run_datastore_benchmark,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["optimize", "benchmark", "-n", "4", "-d", "2", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["iterations"] == 4
    assert payload["days"] == 2
    assert payload["metrics"]["query_speed_tests_ms"]["count"] == 4
