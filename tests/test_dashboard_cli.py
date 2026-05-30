from __future__ import annotations

import json

from click.testing import CliRunner

from asusroutercontrol.cli import cli
from asusroutercontrol.config import Config


def test_dashboard_cli_json_and_export(monkeypatch, tmp_path) -> None:
    async def _fake_dashboard(
        _store,
        *,
        hours: int = 24,
        clients: int = 10,
        timeline_points: int = 6,
    ) -> dict:
        assert hours == 24
        assert clients == 10
        assert timeline_points == 6
        return {
            "generated_at": "2026-04-03T00:00:00",
            "window": {
                "hours": 24,
                "start": "2026-04-02T00:00:00",
                "end": "2026-04-03T00:00:00",
            },
            "isp_performance": {
                "tests_total": 2,
                "quality_counts": {"ok": 2, "suspect": 0, "error": 0, "other": 0},
                "source_counts": {"composite": 2},
                "avg_download_mbps": 200.0,
                "avg_upload_mbps": 30.0,
                "avg_ping_ms": 10.0,
                "avg_jitter_ms": 2.0,
                "avg_confidence": 80.0,
                "latest_test": {"timestamp": "2026-04-03T00:00:00"},
            },
            "client_speed_load": {
                "clients_total": 1,
                "clients_with_signal": 1,
                "clients_placeholder_only": 0,
                "top_clients": [
                    {
                        "mac": "AA:AA:AA:AA:AA:99",
                        "hostname": "lab-client",
                        "band": "5GHz",
                        "timestamp": "2026-04-03T00:00:00",
                        "has_signal": True,
                        "sample_count": 2,
                        "signal_samples": 2,
                        "placeholder_samples": 0,
                        "latest_load_pct": 5.0,
                        "avg_load_pct": 4.5,
                        "peak_load_pct": 6.0,
                        "tx_rate_mbps": 20.0,
                        "rx_rate_mbps": 15.0,
                        "rssi": -52,
                    }
                ],
            },
            "isp_client_timeline": [],
        }

    monkeypatch.setattr(
        "asusroutercontrol.analysis.dashboard.build_isp_client_dashboard",
        _fake_dashboard,
    )
    monkeypatch.setattr(
        "asusroutercontrol.cli.load_config",
        lambda: Config(data_dir=tmp_path),
    )

    export_path = tmp_path / "dashboard.json"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dashboard", "--json", "--export", str(export_path)],
    )
    assert result.exit_code == 0
    assert "\"isp_performance\"" in result.output
    assert export_path.exists()

    payload = json.loads(export_path.read_text())
    assert payload["isp_performance"]["tests_total"] == 2
