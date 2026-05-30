"""Speed test providers — pluggable backends for multi-source testing.

Each provider implements the SpeedTestProvider protocol:
  name: str
  async def run() -> ProviderResult
  async def is_available() -> bool
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiohttp

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider result
# ---------------------------------------------------------------------------


@dataclass
class ProviderResult:
    """Result from a single speed-test provider."""

    provider: str = ""
    download_bps: float | None = None
    upload_bps: float | None = None
    ping_ms: float | None = None
    jitter_ms: float | None = None
    server_name: str | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)
    cdn_target: str | None = None
    pop_code: str | None = None
    cache_status: str | None = None


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SpeedTestProvider(Protocol):
    name: str

    async def run(self) -> ProviderResult: ...
    async def is_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# Shared helpers and CDN target model
# ---------------------------------------------------------------------------

_DEFAULT_POP_HEADERS = ("cf-ray", "x-amz-cf-pop", "x-served-by")
_DEFAULT_CACHE_HEADERS = ("cf-cache-status", "x-cache")
_DEFAULT_DOWNLOAD_CAP_BYTES = 10_000_000
_FASTLY_DOWNLOAD_CAP_BYTES = 1_000_000


def _normalize_name(value: str) -> str:
    return value.strip().lower()


def _to_str_tuple(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        v = raw.strip()
        return (v,) if v else ()
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return tuple(out)
    return ()


def _percentile(vals: list[float], p: float) -> float:
    """Simple percentile (0-100 scale)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(len(s) * p / 100 + 0.5), len(s) - 1)
    return s[idx]


def _parse_server_timing(headers: dict[str, str]) -> float:
    """Extract server processing time in seconds from Server-Timing header.

    Format: ``cfRequestDuration;dur=12.345`` (milliseconds).
    Returns 0.0 if header is missing or unparseable.
    """
    raw = headers.get("Server-Timing", "") or headers.get("server-timing", "")
    for part in raw.split(","):
        if "dur=" in part:
            try:
                return float(part.split("dur=")[1].strip()) / 1000.0
            except (ValueError, IndexError):
                pass
    return 0.0


def _extract_pop_code(headers: dict[str, str], *, candidates: tuple[str, ...]) -> str | None:
    lower = {k.lower(): v for k, v in headers.items()}
    for key in candidates:
        value = lower.get(key.lower())
        if not value:
            continue
        if key.lower() == "cf-ray":
            tail = value.rsplit("-", 1)[-1].strip()
            return tail or None
        return value.strip() or None
    return None


def _extract_cache_status(headers: dict[str, str], *, candidates: tuple[str, ...]) -> str | None:
    lower = {k.lower(): v for k, v in headers.items()}
    for key in candidates:
        value = lower.get(key.lower())
        if value:
            return value.strip()
    return None


@dataclass(frozen=True)
class CDNTargetSpec:
    """Declarative target for generic HTTP CDN measurement."""

    name: str
    download_urls: tuple[str, ...]
    latency_url: str | None = None
    upload_url: str | None = None
    metadata_url: str | None = None
    server_label: str = ""
    latency_samples: int = 5
    upload_sizes: tuple[int, ...] = (100_000, 1_000_000, 10_000_000)
    max_download_bytes: int | None = None
    pop_header_candidates: tuple[str, ...] = _DEFAULT_POP_HEADERS
    cache_header_candidates: tuple[str, ...] = _DEFAULT_CACHE_HEADERS

    @property
    def supports_upload(self) -> bool:
        return bool(self.upload_url)

    @property
    def supports_latency(self) -> bool:
        return bool(self.latency_url)

    @property
    def supports_pop_headers(self) -> bool:
        return bool(self.pop_header_candidates)


_DEFAULT_CDN_TARGET_SPECS = {
    "cachefly": CDNTargetSpec(
        name="cachefly",
        download_urls=(
            "https://cachefly.cachefly.net/1mb.test",
            "https://cachefly.cachefly.net/10mb.test",
            "https://cachefly.cachefly.net/100mb.test",
        ),
        server_label="CacheFly",
        max_download_bytes=_DEFAULT_DOWNLOAD_CAP_BYTES,
        pop_header_candidates=(),
        cache_header_candidates=(),
    ),
    "cloudfront": CDNTargetSpec(
        name="cloudfront",
        download_urls=("https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip",),
        latency_url="https://awscli.amazonaws.com/v2/documentation/api/latest/index.html",
        server_label="CloudFront",
        max_download_bytes=_DEFAULT_DOWNLOAD_CAP_BYTES,
        pop_header_candidates=("x-amz-cf-pop", "x-served-by"),
        cache_header_candidates=("x-cache",),
    ),
    "fastly": CDNTargetSpec(
        name="fastly",
        download_urls=(
            "https://raw.githubusercontent.com/torvalds/linux/master/MAINTAINERS",
            "https://raw.githubusercontent.com/torvalds/linux/master/CREDITS",
            "https://raw.githubusercontent.com/torvalds/linux/master/Documentation/admin-guide/README.rst",
        ),
        latency_url="https://raw.githubusercontent.com/torvalds/linux/master/Documentation/admin-guide/README.rst",
        server_label="Fastly",
        max_download_bytes=_FASTLY_DOWNLOAD_CAP_BYTES,
        pop_header_candidates=("x-served-by",),
        cache_header_candidates=("x-cache",),
    ),
}


def _load_custom_cdn_target_specs() -> dict[str, CDNTargetSpec]:
    raw = os.environ.get("CDN_TARGETS_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Invalid CDN_TARGETS_JSON (not valid JSON), ignoring custom targets")
        return {}
    if not isinstance(data, list):
        log.warning("Invalid CDN_TARGETS_JSON (expected list), ignoring custom targets")
        return {}

    custom: dict[str, CDNTargetSpec] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = _normalize_name(str(entry.get("name", "")))
        if not name:
            continue
        download_urls = _to_str_tuple(entry.get("download_urls"))
        if not download_urls:
            fallback = _to_str_tuple(entry.get("download_url"))
            download_urls = fallback
        if not download_urls:
            continue

        latency_url = entry.get("latency_url")
        upload_url = entry.get("upload_url")
        metadata_url = entry.get("metadata_url")
        pop_headers = _to_str_tuple(entry.get("pop_header_candidates"))
        cache_headers = _to_str_tuple(entry.get("cache_header_candidates"))
        latency_samples = entry.get("latency_samples", 5)
        upload_sizes = entry.get("upload_sizes", (100_000, 1_000_000, 10_000_000))
        max_download_bytes = entry.get("max_download_bytes")
        if not isinstance(latency_samples, int) or latency_samples <= 0:
            latency_samples = 5
        upload_sizes_tuple: tuple[int, ...]
        if isinstance(upload_sizes, list):
            filtered = []
            for sz in upload_sizes:
                if isinstance(sz, int) and sz > 0:
                    filtered.append(sz)
            upload_sizes_tuple = tuple(filtered) or (100_000, 1_000_000, 10_000_000)
        else:
            upload_sizes_tuple = (100_000, 1_000_000, 10_000_000)
        if not isinstance(max_download_bytes, int) or max_download_bytes <= 0:
            max_download_bytes = None

        custom[name] = CDNTargetSpec(
            name=name,
            download_urls=download_urls,
            latency_url=latency_url if isinstance(latency_url, str) else None,
            upload_url=upload_url if isinstance(upload_url, str) else None,
            metadata_url=metadata_url if isinstance(metadata_url, str) else None,
            server_label=str(entry.get("server_label", name.title())),
            latency_samples=latency_samples,
            upload_sizes=upload_sizes_tuple,
            max_download_bytes=max_download_bytes,
            pop_header_candidates=pop_headers or _DEFAULT_POP_HEADERS,
            cache_header_candidates=cache_headers or _DEFAULT_CACHE_HEADERS,
        )
    return custom


def get_cdn_target_specs() -> dict[str, CDNTargetSpec]:
    specs = dict(_DEFAULT_CDN_TARGET_SPECS)
    specs.update(_load_custom_cdn_target_specs())
    return specs


def get_cdn_target_spec(target_name: str) -> CDNTargetSpec | None:
    name = _normalize_name(target_name)
    return get_cdn_target_specs().get(name)


def list_cdn_http_provider_names(target_names: tuple[str, ...] | None = None) -> list[str]:
    return [p.name for p in build_cdn_http_providers(target_names)]


def build_cdn_http_providers(
    target_names: tuple[str, ...] | None = None,
) -> list["CDNHttpProvider"]:
    specs = get_cdn_target_specs()
    if not target_names:
        target_names = tuple(specs.keys())
    providers: list[CDNHttpProvider] = []
    for raw_name in target_names:
        name = _normalize_name(raw_name)
        if not name or name == "cloudflare":
            # Cloudflare uses a specialized provider in this module.
            continue
        spec = specs.get(name)
        if not spec:
            log.warning("Unknown CDN target '%s' in CDN_TARGETS, skipping", raw_name)
            continue
        providers.append(CDNHttpProvider(spec))
    return providers


# ---------------------------------------------------------------------------
# 1. Ookla — wraps official speedtest binary (preferred) or speedtest-cli
# ---------------------------------------------------------------------------

# Official Ookla binary (brew tap teamookla/speedtest && brew install speedtest)
# outputs bandwidth in bytes/s under download.bandwidth / upload.bandwidth,
# while the community speedtest-cli outputs bits/s directly in download / upload.
_OFFICIAL_OOKLA_ARGS = ("--format=json", "--accept-license", "--accept-gdpr")
_COMMUNITY_CLI_ARGS = ("--secure", "--json")


def _is_official_ookla(cli_path: str) -> bool:
    """Return True if cli_path is the official Ookla binary (not speedtest-cli)."""
    return not cli_path.endswith("speedtest-cli")


class OoklaProvider:
    """Ookla speedtest.net via the official speedtest binary or speedtest-cli fallback."""

    name = "ookla"

    def _find_cli(self) -> str | None:
        # Prefer official Ookla binary over community speedtest-cli
        for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(extra) / "speedtest"
            if candidate.exists():
                return str(candidate)
        official = shutil.which("speedtest")
        if official:
            return official
        # Fall back to community speedtest-cli
        venv_bin = Path(sys.executable).parent / "speedtest-cli"
        if venv_bin.exists():
            return str(venv_bin)
        return shutil.which("speedtest-cli")

    async def is_available(self) -> bool:
        return self._find_cli() is not None

    async def run(self) -> ProviderResult:
        cli = self._find_cli()
        if not cli:
            return ProviderResult(
                provider=self.name,
                error="no Ookla binary found (install: brew tap teamookla/speedtest && brew install speedtest)",
            )
        official = _is_official_ookla(cli)
        args = _OFFICIAL_OOKLA_ARGS if official else _COMMUNITY_CLI_ARGS
        try:
            proc = await asyncio.create_subprocess_exec(
                cli,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                err = stderr.decode().strip() or f"exit {proc.returncode}"
                return ProviderResult(provider=self.name, error=err)

            data = json.loads(stdout.decode())

            if official:
                # Official binary: bandwidth in bytes/s → convert to bits/s
                dl_bps = (data.get("download", {}).get("bandwidth") or 0) * 8 or None
                ul_bps = (data.get("upload", {}).get("bandwidth") or 0) * 8 or None
                ping_ms = data.get("ping", {}).get("latency")
                jitter_ms = data.get("ping", {}).get("jitter")
                server_name = (
                    data.get("server", {}).get("name")
                    or data.get("server", {}).get("sponsor")
                )
            else:
                # Community speedtest-cli: values already in bits/s
                dl_bps = data.get("download")
                ul_bps = data.get("upload")
                ping_ms = data.get("ping")
                jitter_ms = None
                server_name = data.get("server", {}).get("sponsor")

            return ProviderResult(
                provider=self.name,
                download_bps=dl_bps,
                upload_bps=ul_bps,
                ping_ms=ping_ms,
                jitter_ms=jitter_ms,
                server_name=server_name,
                raw=data,
            )
        except asyncio.TimeoutError:
            return ProviderResult(provider=self.name, error="timeout (120s)")
        except Exception as exc:
            log.exception("Ookla provider error")
            return ProviderResult(provider=self.name, error=str(exc))


# ---------------------------------------------------------------------------
# 2. Cloudflare — native aiohttp against speed.cloudflare.com
# ---------------------------------------------------------------------------

_CF_BASE = "https://speed.cloudflare.com"
_CF_DOWN = f"{_CF_BASE}/__down"
_CF_UP = f"{_CF_BASE}/__up"

# Escalating payload sizes for bandwidth measurement
# Large sizes needed to saturate high-bandwidth connections (400+ Mbps)
_CF_DL_SIZES = [1_000_000, 10_000_000, 25_000_000, 100_000_000]
_CF_UL_SIZES = [1_000_000, 10_000_000, 25_000_000]
_CF_LATENCY_SAMPLES = 5
# Number of parallel streams for download/upload — mimics multi-stream methodology
# used by the native Ookla app to saturate high-bandwidth links
_CF_PARALLEL_STREAMS = 4


class CloudflareProvider:
    """Speed test against Cloudflare's edge via speed.cloudflare.com."""

    name = "cloudflare"

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{_CF_DOWN}?bytes=0", timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    return r.status == 200
        except Exception:
            return False

    async def run(self) -> ProviderResult:
        timeout = aiohttp.ClientTimeout(total=180)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # --- Latency + jitter ---
                latencies: list[float] = []
                latency_headers: dict[str, str] = {}
                for _ in range(_CF_LATENCY_SAMPLES):
                    t0 = time.monotonic()
                    async with session.get(f"{_CF_DOWN}?bytes=0") as resp:
                        await resp.read()
                        latency_headers = dict(resp.headers)
                        srv = _parse_server_timing(latency_headers)
                    elapsed = time.monotonic() - t0 - srv
                    latencies.append(max(0, elapsed * 1000))

                ping = min(latencies) if latencies else None
                avg_lat = sum(latencies) / len(latencies) if latencies else 0
                jitter = None
                if len(latencies) >= 2:
                    diffs = [
                        abs(latencies[i + 1] - latencies[i]) for i in range(len(latencies) - 1)
                    ]
                    jitter = sum(diffs) / len(diffs)

                # --- Download (parallel streams) ---
                # Run _CF_PARALLEL_STREAMS concurrent requests per size to saturate
                # high-bandwidth connections, matching native Ookla app methodology.
                dl_bw: list[float] = []
                for size in _CF_DL_SIZES:
                    repeats = 2 if size >= 25_000_000 else 3
                    for _ in range(repeats):
                        tasks = [
                            self._timed_download(session, size)
                            for _ in range(_CF_PARALLEL_STREAMS)
                        ]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        stream_bws = [r for r in results if isinstance(r, float) and r > 0]
                        if stream_bws:
                            # Sum parallel streams — total throughput, not per-stream
                            dl_bw.append(sum(stream_bws))

                download = _percentile(dl_bw, 90) if dl_bw else None

                # --- Upload (parallel streams) ---
                ul_bw: list[float] = []
                for size in _CF_UL_SIZES:
                    repeats = 2 if size >= 10_000_000 else 3
                    for _ in range(repeats):
                        tasks = [
                            self._timed_upload(session, size)
                            for _ in range(_CF_PARALLEL_STREAMS)
                        ]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        stream_bws = [r for r in results if isinstance(r, float) and r > 0]
                        if stream_bws:
                            ul_bw.append(sum(stream_bws))

                upload = _percentile(ul_bw, 90) if ul_bw else None

                # Fetch metadata (IP, ISP, colo)
                meta = {}
                try:
                    async with session.get(f"{_CF_BASE}/meta") as resp:
                        if resp.status == 200:
                            meta = await resp.json(content_type=None)
                except Exception:
                    pass

                colo = str(meta.get("colo", "")).strip()
                server = f"Cloudflare {colo}" if colo else "Cloudflare"
                pop_code = colo or _extract_pop_code(
                    latency_headers,
                    candidates=("cf-ray", "x-served-by", "x-amz-cf-pop"),
                )
                cache_status = _extract_cache_status(
                    latency_headers, candidates=("cf-cache-status", "x-cache")
                )

                return ProviderResult(
                    provider=self.name,
                    cdn_target=self.name,
                    download_bps=download,
                    upload_bps=upload,
                    ping_ms=round(ping, 2) if ping is not None else None,
                    jitter_ms=round(jitter, 2) if jitter is not None else None,
                    server_name=server,
                    pop_code=pop_code,
                    cache_status=cache_status,
                    raw={
                        "latencies_ms": [round(v, 2) for v in latencies],
                        "avg_latency_ms": round(avg_lat, 2),
                        "dl_samples": len(dl_bw),
                        "ul_samples": len(ul_bw),
                        "meta": meta,
                    },
                )
        except Exception as exc:
            log.exception("Cloudflare provider error")
            return ProviderResult(provider=self.name, error=str(exc))

    async def _timed_download(self, session: aiohttp.ClientSession, size: int) -> float | None:
        """Download `size` bytes and return bandwidth in bps."""
        try:
            t0 = time.monotonic()
            async with session.get(f"{_CF_DOWN}?bytes={size}") as resp:
                data = await resp.read()
                srv = _parse_server_timing(dict(resp.headers))
            elapsed = time.monotonic() - t0 - srv
            if elapsed > 0:
                return len(data) * 8 / elapsed
        except Exception:
            pass
        return None

    async def _timed_upload(self, session: aiohttp.ClientSession, size: int) -> float | None:
        """Upload `size` bytes of random data and return bandwidth in bps."""
        try:
            payload = b"\x00" * size
            t0 = time.monotonic()
            async with session.post(_CF_UP, data=payload) as resp:
                await resp.read()
                srv = _parse_server_timing(dict(resp.headers))
            elapsed = time.monotonic() - t0 - srv
            if elapsed > 0:
                return size * 8 / elapsed
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# 3. Generic HTTP CDN provider
# ---------------------------------------------------------------------------


class CDNHttpProvider:
    """Generic CDN HTTP provider with capability-aware metrics."""

    def __init__(self, target: CDNTargetSpec) -> None:
        self._target = target
        self.name = target.name

    async def is_available(self) -> bool:
        url = self._target.download_urls[0]
        try:
            async with aiohttp.ClientSession() as s:
                async with s.head(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return r.status < 500
        except Exception:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        return r.status < 500
            except Exception:
                return False

    async def run(self) -> ProviderResult:
        timeout = aiohttp.ClientTimeout(total=180)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                latencies = await self._measure_latency(session)

                dl_bw: list[float] = []
                sample_headers: dict[str, str] = {}
                for url in self._target.download_urls:
                    bw, headers = await self._download_url(session, url)
                    if bw is not None:
                        dl_bw.append(bw)
                        if headers:
                            sample_headers = headers

                if not dl_bw:
                    return ProviderResult(
                        provider=self.name,
                        cdn_target=self._target.name,
                        error="download failed or timed out",
                    )

                ul_bw = await self._measure_upload(session)
                meta = await self._fetch_metadata(session)
                pop_code = _extract_pop_code(
                    sample_headers, candidates=self._target.pop_header_candidates
                )
                cache_status = _extract_cache_status(
                    sample_headers, candidates=self._target.cache_header_candidates
                )

                if not pop_code and isinstance(meta, dict):
                    colo = str(meta.get("colo", "")).strip()
                    if colo:
                        pop_code = colo

                server_label = self._target.server_label or self._target.name.title()
                server_name = server_label
                if pop_code and self._target.supports_pop_headers:
                    server_name = f"{server_label} {pop_code}"

                ping = min(latencies) if latencies else None
                jitter = None
                if len(latencies) >= 2:
                    diffs = [
                        abs(latencies[i + 1] - latencies[i]) for i in range(len(latencies) - 1)
                    ]
                    jitter = sum(diffs) / len(diffs)

                return ProviderResult(
                    provider=self.name,
                    cdn_target=self._target.name,
                    download_bps=_percentile(dl_bw, 90),
                    upload_bps=_percentile(ul_bw, 90) if ul_bw else None,
                    ping_ms=round(ping, 2) if ping is not None else None,
                    jitter_ms=round(jitter, 2) if jitter is not None else None,
                    server_name=server_name,
                    pop_code=pop_code,
                    cache_status=cache_status,
                    raw={
                        "latencies_ms": [round(v, 2) for v in latencies],
                        "dl_samples": len(dl_bw),
                        "ul_samples": len(ul_bw),
                        "meta": meta,
                    },
                )
        except Exception as exc:
            log.exception("CDN HTTP provider error (%s)", self._target.name)
            return ProviderResult(provider=self.name, cdn_target=self._target.name, error=str(exc))

    async def _measure_latency(self, session: aiohttp.ClientSession) -> list[float]:
        if not self._target.latency_url:
            return []
        vals: list[float] = []
        for _ in range(self._target.latency_samples):
            try:
                t0 = time.monotonic()
                async with session.get(self._target.latency_url) as resp:
                    await resp.read()
                    srv = _parse_server_timing(dict(resp.headers))
                elapsed = time.monotonic() - t0 - srv
                vals.append(max(0, elapsed * 1000))
            except Exception:
                continue
        return vals

    async def _measure_upload(self, session: aiohttp.ClientSession) -> list[float]:
        if not self._target.upload_url:
            return []
        vals: list[float] = []
        for size in self._target.upload_sizes:
            repeat = 3 if size <= 1_000_000 else 2
            for _ in range(repeat):
                bw = await self._upload_payload(session, size)
                if bw is not None:
                    vals.append(bw)
        return vals

    async def _fetch_metadata(self, session: aiohttp.ClientSession) -> dict:
        if not self._target.metadata_url:
            return {}
        try:
            async with session.get(self._target.metadata_url) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
        except Exception:
            pass
        return {}

    async def _download_url(
        self, session: aiohttp.ClientSession, url: str
    ) -> tuple[float | None, dict[str, str]]:
        total_bytes = 0
        headers: dict[str, str] = {}
        max_bytes = self._target.max_download_bytes
        request_headers: dict[str, str] | None = None
        if isinstance(max_bytes, int) and max_bytes > 0:
            request_headers = {"Range": f"bytes=0-{max_bytes - 1}"}
        else:
            max_bytes = None
        try:
            t0 = time.monotonic()
            async with session.get(url, headers=request_headers) as resp:
                if resp.status not in (200, 206):
                    return None, {}
                headers = dict(resp.headers)
                async for chunk in resp.content.iter_chunked(65536):
                    if not chunk:
                        continue
                    if max_bytes is None:
                        total_bytes += len(chunk)
                        continue
                    remaining = max_bytes - total_bytes
                    if remaining <= 0:
                        break
                    total_bytes += min(len(chunk), remaining)
                    if total_bytes >= max_bytes:
                        break
            elapsed = time.monotonic() - t0
            if elapsed > 0 and total_bytes > 0:
                return (total_bytes * 8 / elapsed), headers
        except asyncio.TimeoutError:
            return None, {}
        except Exception:
            log.warning("CDN HTTP download failed for %s", url)
            return None, {}
        return None, {}

    async def _upload_payload(self, session: aiohttp.ClientSession, size: int) -> float | None:
        if not self._target.upload_url:
            return None
        try:
            payload = b"\x00" * size
            t0 = time.monotonic()
            async with session.post(self._target.upload_url, data=payload) as resp:
                await resp.read()
                srv = _parse_server_timing(dict(resp.headers))
            elapsed = time.monotonic() - t0 - srv
            if elapsed > 0:
                return size * 8 / elapsed
        except Exception:
            return None
        return None


# ---------------------------------------------------------------------------
# 4. HTTP Download alias — backward compatibility
# ---------------------------------------------------------------------------


class HTTPDownloadProvider(CDNHttpProvider):
    """Backward-compatible alias to a configured HTTP CDN provider."""

    name = "http_download"

    def __init__(self, target_name: str = "cachefly") -> None:
        target = get_cdn_target_spec(target_name)
        if target is None:
            target = _DEFAULT_CDN_TARGET_SPECS["cachefly"]
        super().__init__(target)
        self.name = "http_download"
