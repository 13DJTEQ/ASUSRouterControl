"""Multi-source speed test runner.

Orchestrates multiple providers (Ookla, Cloudflare, configurable CDN HTTP),
produces a cross-validated composite result with confidence scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from statistics import median

from asusroutercontrol.config import load_config
from asusroutercontrol.models import SpeedTestResult
from asusroutercontrol.speedtest_providers import (
    CloudflareProvider,
    OoklaProvider,
    ProviderResult,
    SpeedTestProvider,
    build_cdn_http_providers,
)

log = logging.getLogger(__name__)

_DEFAULT_COOLDOWN = 10  # seconds between providers
_OUTLIER_THRESHOLD = 0.25  # 25% deviation from median = outlier
_LEGACY_HTTP_ALIAS = "http_download"


def _is_peak_hour(cfg) -> bool:
    """Check if current local time falls within peak hours."""
    hour = datetime.now().hour
    if cfg.peak_start <= cfg.peak_end:
        return cfg.peak_start <= hour < cfg.peak_end
    return hour >= cfg.peak_start or hour < cfg.peak_end


def _build_default_providers() -> list[SpeedTestProvider]:
    cfg = load_config()
    providers: list[SpeedTestProvider] = [
        OoklaProvider(),
        CloudflareProvider(),
        *build_cdn_http_providers(cfg.cdn_targets),
    ]
    # Preserve order while deduplicating by provider name.
    seen: set[str] = set()
    out: list[SpeedTestProvider] = []
    for provider in providers:
        if provider.name in seen:
            continue
        seen.add(provider.name)
        out.append(provider)
    return out


def _build_source_aliases(providers: list[SpeedTestProvider]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    names = [p.name for p in providers]
    if "cachefly" in names:
        aliases[_LEGACY_HTTP_ALIAS] = "cachefly"
        return aliases
    for name in names:
        if name not in {"ookla", "cloudflare"}:
            aliases[_LEGACY_HTTP_ALIAS] = name
            break
    return aliases


# ---------------------------------------------------------------------------
# Multi-source orchestrator
# ---------------------------------------------------------------------------


class MultiSourceSpeedTest:
    """Run multiple speed-test providers and produce a composite result."""

    def __init__(
        self,
        providers: list[SpeedTestProvider] | None = None,
        cooldown: float = _DEFAULT_COOLDOWN,
    ) -> None:
        self._providers = providers or _build_default_providers()
        self._source_aliases = _build_source_aliases(self._providers)
        self._cooldown = cooldown

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    @property
    def source_aliases(self) -> dict[str, str]:
        return dict(self._source_aliases)

    async def run(
        self, *, source_filter: str | None = None
    ) -> tuple[SpeedTestResult, list[ProviderResult]]:
        """Execute providers and return (composite, individual_results)."""
        cfg = load_config()
        now = datetime.utcnow()
        is_peak = _is_peak_hour(cfg)
        session_id = uuid.uuid4().hex[:12]

        providers = self._providers
        if source_filter:
            requested = source_filter.strip().lower()
            resolved = self._source_aliases.get(requested, requested)
            providers = [p for p in providers if p.name == resolved]
            if not providers:
                known = sorted(set(self.provider_names) | set(self._source_aliases.keys()))
                known_str = ", ".join(known)
                return (
                    SpeedTestResult(
                        timestamp=now,
                        is_peak=is_peak,
                        error=f"unknown provider: {source_filter} (available: {known_str})",
                    ),
                    [],
                )

        # Check availability, run sequentially with cooldown
        results: list[ProviderResult] = []
        for i, provider in enumerate(providers):
            if not await provider.is_available():
                log.warning("%s not available, skipping", provider.name)
                results.append(
                    ProviderResult(
                        provider=provider.name,
                        error="not available",
                    )
                )
                continue

            log.info("Running %s speed test...", provider.name)
            result = await provider.run()
            results.append(result)

            if result.error:
                log.warning("%s failed: %s", provider.name, result.error)
            else:
                dl = (result.download_bps or 0) / 1_000_000
                ul = (result.upload_bps or 0) / 1_000_000
                log.info(
                    "%s: %.1f/%.1f Mbps, ping=%s ms",
                    provider.name,
                    dl,
                    ul,
                    f"{result.ping_ms:.1f}" if result.ping_ms else "N/A",
                )

            # Cooldown between providers (not after last)
            if i < len(providers) - 1:
                await asyncio.sleep(self._cooldown)

        composite = self._compute_composite(
            results, now=now, is_peak=is_peak, session_id=session_id
        )
        return composite, results

    def _compute_composite(
        self,
        results: list[ProviderResult],
        *,
        now: datetime,
        is_peak: bool,
        session_id: str,
    ) -> SpeedTestResult:
        """Merge provider results into one composite SpeedTestResult."""
        ok = [r for r in results if not r.error]
        if not ok:
            errors = "; ".join(f"{r.provider}: {r.error}" for r in results if r.error)
            return SpeedTestResult(
                timestamp=now,
                is_peak=is_peak,
                source="composite",
                session_id=session_id,
                error=f"all providers failed ({errors})",
            )

        # Median download
        dl_vals = [r.download_bps for r in ok if r.download_bps]
        dl = median(dl_vals) if dl_vals else None

        # Median upload
        ul_vals = [r.upload_bps for r in ok if r.upload_bps]
        ul = median(ul_vals) if ul_vals else None

        # Min ping (closest to true latency)
        ping_vals = [r.ping_ms for r in ok if r.ping_ms is not None]
        ping = min(ping_vals) if ping_vals else None

        jitter_vals = [r.jitter_ms for r in ok if r.jitter_ms is not None]
        jitter = median(jitter_vals) if jitter_vals else None

        # Server name: use the first successful provider's server
        server = None
        for r in ok:
            if r.server_name:
                server = r.server_name
                break

        # Confidence + outliers
        outliers = self._detect_outliers(dl_vals, results)
        confidence = self._compute_confidence(dl_vals)

        # Build provider details JSON
        provider_details = {"providers": {}, "composite_method": "median"}
        for r in results:
            entry: dict = {}
            if r.download_bps is not None:
                entry["download_bps"] = round(r.download_bps)
            if r.upload_bps is not None:
                entry["upload_bps"] = round(r.upload_bps)
            if r.ping_ms is not None:
                entry["ping_ms"] = round(r.ping_ms, 2)
            if r.jitter_ms is not None:
                entry["jitter_ms"] = round(r.jitter_ms, 2)
            if r.error:
                entry["error"] = r.error
            if r.server_name:
                entry["server"] = r.server_name
            if r.cdn_target:
                entry["cdn"] = r.cdn_target
            if r.pop_code:
                entry["pop"] = r.pop_code
            if r.cache_status:
                entry["cache_status"] = r.cache_status
            provider_details["providers"][r.provider] = entry

        provider_details["confidence"] = confidence
        provider_details["outliers"] = outliers
        provider_details["download_samples"] = len(dl_vals)

        composite = SpeedTestResult(
            timestamp=now,
            download_bps=dl,
            upload_bps=ul,
            ping_ms=round(ping, 2) if ping is not None else None,
            jitter_ms=round(jitter, 2) if jitter is not None else None,
            server_name=server,
            is_peak=is_peak,
            source="composite",
            session_id=session_id,
            provider_details_json=json.dumps(provider_details),
        )

        log.info(
            "Composite: %.1f/%.1f Mbps, ping=%s ms, jitter=%s ms, confidence=%d, outliers=%s",
            (dl or 0) / 1_000_000,
            (ul or 0) / 1_000_000,
            f"{ping:.1f}" if ping else "N/A",
            f"{jitter:.1f}" if jitter else "N/A",
            confidence,
            outliers or "none",
        )
        return composite

    def _detect_outliers(
        self, dl_vals: list[float], results: list[ProviderResult]
    ) -> list[str]:
        """Flag providers whose download deviates >25% from median."""
        if len(dl_vals) < 2:
            return []
        med = median(dl_vals)
        if med == 0:
            return []
        outliers: list[str] = []
        for r in results:
            if r.download_bps and abs(r.download_bps - med) / med > _OUTLIER_THRESHOLD:
                pct = (r.download_bps - med) / med * 100
                outliers.append(f"{r.provider} ({pct:+.0f}%)")
        return outliers

    def _compute_confidence(self, dl_vals: list[float]) -> int:
        """Score 0-100 based on provider count and agreement."""
        if not dl_vals:
            return 0
        score = 0
        # More comparable download providers = more confidence (up to 40 pts)
        score += min(40, len(dl_vals) * 15)
        # Agreement: lower spread = higher confidence (up to 60 pts)
        if len(dl_vals) >= 2:
            med = median(dl_vals)
            if med > 0:
                max_dev = max(abs(v - med) / med for v in dl_vals)
                if max_dev <= 0.10:
                    score += 60  # All within 10%
                elif max_dev <= 0.15:
                    score += 50
                elif max_dev <= 0.25:
                    score += 35
                else:
                    score += 15
        else:
            score += 30  # Single provider, moderate confidence
        return min(100, score)


# ---------------------------------------------------------------------------
# Public API — backward-compatible
# ---------------------------------------------------------------------------


async def run_speed_test(*, source: str | None = None) -> SpeedTestResult:
    """Run a multi-source speed test and return the composite result.

    Pass ``source='ookla'`` (or 'cloudflare' / 'cachefly' / 'cloudfront'
    / 'fastly' / 'http_download') to run only a single provider.
    """
    multi = MultiSourceSpeedTest()
    composite, _individual = await multi.run(source_filter=source)
    return composite


async def run_speed_test_detailed(
    *, source: str | None = None
) -> tuple[SpeedTestResult, list[ProviderResult]]:
    """Like run_speed_test but also returns per-provider results."""
    multi = MultiSourceSpeedTest()
    return await multi.run(source_filter=source)


def available_speedtest_sources() -> list[str]:
    """Return provider names plus source aliases accepted by the speedtest command."""
    multi = MultiSourceSpeedTest()
    names = set(multi.provider_names)
    names.update(multi.source_aliases.keys())
    return sorted(names)
