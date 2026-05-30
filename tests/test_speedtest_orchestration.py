from __future__ import annotations

import json

import pytest

from asusroutercontrol.speedtest import MultiSourceSpeedTest
from asusroutercontrol.speedtest_providers import ProviderResult


class _Provider:
    def __init__(
        self,
        name: str,
        result: ProviderResult,
        *,
        available: bool = True,
    ) -> None:
        self.name = name
        self._result = result
        self._available = available

    async def is_available(self) -> bool:
        return self._available

    async def run(self) -> ProviderResult:
        return self._result


@pytest.mark.asyncio
async def test_legacy_http_download_alias_maps_to_cachefly_provider() -> None:
    cachefly = ProviderResult(
        provider="cachefly",
        download_bps=120_000_000,
        server_name="CacheFly",
    )
    multi = MultiSourceSpeedTest(
        providers=[_Provider("cachefly", cachefly)],
        cooldown=0,
    )

    composite, rows = await multi.run(source_filter="http_download")
    assert composite.error is None
    assert len(rows) == 1
    assert rows[0].provider == "cachefly"


@pytest.mark.asyncio
async def test_unknown_provider_returns_explicit_error() -> None:
    multi = MultiSourceSpeedTest(
        providers=[
            _Provider(
                "ookla",
                ProviderResult(provider="ookla", download_bps=200_000_000),
            )
        ],
        cooldown=0,
    )

    composite, rows = await multi.run(source_filter="not_a_provider")
    assert rows == []
    assert composite.error is not None
    assert "unknown provider: not_a_provider" in composite.error


@pytest.mark.asyncio
async def test_confidence_uses_only_comparable_download_samples() -> None:
    multi = MultiSourceSpeedTest(
        providers=[
            _Provider(
                "cloudflare",
                ProviderResult(provider="cloudflare", download_bps=250_000_000),
            ),
            _Provider(
                "latency_only",
                ProviderResult(provider="latency_only", ping_ms=11.5),
            ),
        ],
        cooldown=0,
    )

    composite, _rows = await multi.run()
    details = json.loads(composite.provider_details_json)
    assert details["download_samples"] == 1
    assert details["confidence"] == 45


@pytest.mark.asyncio
async def test_provider_details_include_pop_and_cache_metadata() -> None:
    multi = MultiSourceSpeedTest(
        providers=[
            _Provider(
                "cachefly",
                ProviderResult(
                    provider="cachefly",
                    download_bps=150_000_000,
                    pop_code="IAD12-P4",
                    cache_status="HIT",
                ),
            )
        ],
        cooldown=0,
    )

    composite, _rows = await multi.run()
    details = json.loads(composite.provider_details_json)
    provider_entry = details["providers"]["cachefly"]
    assert provider_entry["pop"] == "IAD12-P4"
    assert provider_entry["cache_status"] == "HIT"


@pytest.mark.asyncio
async def test_outliers_reported_when_provider_deviation_exceeds_threshold() -> None:
    multi = MultiSourceSpeedTest(
        providers=[
            _Provider(
                "a",
                ProviderResult(provider="a", download_bps=100_000_000),
            ),
            _Provider(
                "b",
                ProviderResult(provider="b", download_bps=200_000_000),
            ),
        ],
        cooldown=0,
    )

    composite, _rows = await multi.run()
    details = json.loads(composite.provider_details_json)
    outliers = details["outliers"]
    assert any(item.startswith("a ") for item in outliers)
    assert any(item.startswith("b ") for item in outliers)
