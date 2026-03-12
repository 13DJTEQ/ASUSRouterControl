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


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SpeedTestProvider(Protocol):
    name: str

    async def run(self) -> ProviderResult: ...
    async def is_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# 1. Ookla — wraps speedtest-cli
# ---------------------------------------------------------------------------


class OoklaProvider:
    """Ookla speedtest.net via the speedtest-cli binary."""

    name = "ookla"

    def _find_cli(self) -> str | None:
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
                provider=self.name, error="speedtest-cli not found"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                cli, "--secure", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
            if proc.returncode != 0:
                err = stderr.decode().strip() or f"exit {proc.returncode}"
                return ProviderResult(provider=self.name, error=err)

            data = json.loads(stdout.decode())
            return ProviderResult(
                provider=self.name,
                download_bps=data.get("download"),
                upload_bps=data.get("upload"),
                ping_ms=data.get("ping"),
                server_name=data.get("server", {}).get("sponsor"),
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
_CF_DL_SIZES = [100_000, 1_000_000, 10_000_000, 25_000_000]
_CF_UL_SIZES = [100_000, 1_000_000, 10_000_000]
_CF_LATENCY_SAMPLES = 5


def _percentile(vals: list[float], p: float) -> float:
    """Simple percentile (0-100 scale)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(len(s) * p / 100 + 0.5), len(s) - 1)
    return s[idx]


def _parse_server_timing(headers: dict) -> float:
    """Extract server processing time in seconds from Server-Timing header.

    Format: ``cfRequestDuration;dur=12.345`` (milliseconds).
    Returns 0.0 if header is missing or unparseable.
    """
    raw = headers.get("Server-Timing", "")
    for part in raw.split(","):
        if "dur=" in part:
            try:
                return float(part.split("dur=")[1].strip()) / 1000.0
            except (ValueError, IndexError):
                pass
    return 0.0


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
                for _ in range(_CF_LATENCY_SAMPLES):
                    t0 = time.monotonic()
                    async with session.get(f"{_CF_DOWN}?bytes=0") as resp:
                        await resp.read()
                        srv = _parse_server_timing(dict(resp.headers))
                    elapsed = time.monotonic() - t0 - srv
                    latencies.append(max(0, elapsed * 1000))

                ping = min(latencies) if latencies else None
                avg_lat = sum(latencies) / len(latencies) if latencies else 0
                jitter = None
                if len(latencies) >= 2:
                    diffs = [
                        abs(latencies[i + 1] - latencies[i])
                        for i in range(len(latencies) - 1)
                    ]
                    jitter = sum(diffs) / len(diffs)

                # --- Download ---
                dl_bw: list[float] = []
                for size in _CF_DL_SIZES:
                    for _ in range(3 if size <= 1_000_000 else 2):
                        bw = await self._timed_download(session, size)
                        if bw:
                            dl_bw.append(bw)

                download = _percentile(dl_bw, 90) if dl_bw else None

                # --- Upload ---
                ul_bw: list[float] = []
                for size in _CF_UL_SIZES:
                    for _ in range(3 if size <= 1_000_000 else 2):
                        bw = await self._timed_upload(session, size)
                        if bw:
                            ul_bw.append(bw)

                upload = _percentile(ul_bw, 90) if ul_bw else None

                # Fetch metadata (IP, ISP, colo)
                meta = {}
                try:
                    async with session.get(
                        f"{_CF_BASE}/meta"
                    ) as resp:
                        if resp.status == 200:
                            meta = await resp.json(
                                content_type=None
                            )
                except Exception:
                    pass

                colo = meta.get("colo", "")
                server = f"Cloudflare {colo}" if colo else "Cloudflare"

                return ProviderResult(
                    provider=self.name,
                    download_bps=download,
                    upload_bps=upload,
                    ping_ms=round(ping, 2) if ping is not None else None,
                    jitter_ms=round(jitter, 2) if jitter is not None else None,
                    server_name=server,
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

    async def _timed_download(
        self, session: aiohttp.ClientSession, size: int
    ) -> float | None:
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

    async def _timed_upload(
        self, session: aiohttp.ClientSession, size: int
    ) -> float | None:
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
# 3. HTTP Download — bulk sustained-throughput measurement
# ---------------------------------------------------------------------------

_HTTP_DL_PRIMARY = f"{_CF_DOWN}?bytes=104857600"     # 100 MB
_HTTP_DL_FALLBACK = f"{_CF_DOWN}?bytes=26214400"     # 25 MB
_HTTP_DL_TIMEOUT = 120  # seconds


class HTTPDownloadProvider:
    """Raw HTTP download throughput — download-only cross-validation."""

    name = "http_download"

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.head(
                    _HTTP_DL_FALLBACK,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    return r.status == 200
        except Exception:
            return False

    async def run(self) -> ProviderResult:
        timeout = aiohttp.ClientTimeout(total=_HTTP_DL_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                bps = await self._download(session, _HTTP_DL_PRIMARY)
                if bps is None:
                    log.info("100MB timed out, falling back to 25MB")
                    bps = await self._download(session, _HTTP_DL_FALLBACK)

                if bps is None:
                    return ProviderResult(
                        provider=self.name,
                        error="download failed or timed out",
                    )
                return ProviderResult(
                    provider=self.name,
                    download_bps=bps,
                    server_name="Cloudflare CDN",
                )
        except Exception as exc:
            log.exception("HTTP download provider error")
            return ProviderResult(provider=self.name, error=str(exc))

    async def _download(
        self, session: aiohttp.ClientSession, url: str
    ) -> float | None:
        """Download URL fully, return sustained throughput in bps."""
        try:
            total_bytes = 0
            t0 = time.monotonic()
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                async for chunk in resp.content.iter_chunked(65536):
                    total_bytes += len(chunk)
            elapsed = time.monotonic() - t0
            if elapsed > 0 and total_bytes > 0:
                return total_bytes * 8 / elapsed
        except asyncio.TimeoutError:
            return None
        except Exception:
            log.warning("HTTP download failed for %s", url)
            return None
        return None
