from __future__ import annotations

from click.testing import CliRunner

from asusroutercontrol.cli import cli
from asusroutercontrol.config import Config
from asusroutercontrol.models import SpeedTestResult
from asusroutercontrol.speedtest_providers import ProviderResult


def test_speedtest_cli_accepts_cachefly_source(
    monkeypatch,
    tmp_path,
) -> None:
    async def _fake_run_speed_test_detailed(*, source: str | None = None):
        assert source == "cachefly"
        return (
            SpeedTestResult(
                download_bps=250_000_000,
                upload_bps=35_000_000,
                ping_ms=12.0,
                provider_details_json='{"providers": {}, "confidence": 80, "outliers": []}',
            ),
            [ProviderResult(provider="cachefly", download_bps=250_000_000)],
        )

    monkeypatch.setattr(
        "asusroutercontrol.speedtest.run_speed_test_detailed",
        _fake_run_speed_test_detailed,
    )
    monkeypatch.setattr(
        "asusroutercontrol.cli.load_config",
        lambda: Config(data_dir=tmp_path),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["speedtest", "--no-store", "-s", "cachefly"])
    assert result.exit_code == 0
    assert "Composite Result" in result.output


def test_speedtest_cli_accepts_legacy_http_download_alias(
    monkeypatch,
    tmp_path,
) -> None:
    seen: list[str | None] = []

    async def _fake_run_speed_test_detailed(*, source: str | None = None):
        seen.append(source)
        return (
            SpeedTestResult(
                download_bps=200_000_000,
                upload_bps=20_000_000,
                ping_ms=14.0,
                provider_details_json='{"providers": {}, "confidence": 75, "outliers": []}',
            ),
            [ProviderResult(provider="cachefly", download_bps=200_000_000)],
        )

    monkeypatch.setattr(
        "asusroutercontrol.speedtest.run_speed_test_detailed",
        _fake_run_speed_test_detailed,
    )
    monkeypatch.setattr(
        "asusroutercontrol.cli.load_config",
        lambda: Config(data_dir=tmp_path),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["speedtest", "--no-store", "-s", "http_download"])
    assert result.exit_code == 0
    assert seen == ["http_download"]


def test_speedtest_cli_reports_unknown_provider_error(
    monkeypatch,
    tmp_path,
) -> None:
    async def _fake_run_speed_test_detailed(*, source: str | None = None):
        return (
            SpeedTestResult(error=f"unknown provider: {source}"),
            [],
        )

    monkeypatch.setattr(
        "asusroutercontrol.speedtest.run_speed_test_detailed",
        _fake_run_speed_test_detailed,
    )
    monkeypatch.setattr(
        "asusroutercontrol.cli.load_config",
        lambda: Config(data_dir=tmp_path),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["speedtest", "--no-store", "-s", "nope"])
    assert result.exit_code == 0
    assert "Speed test failed: unknown provider: nope" in result.output
