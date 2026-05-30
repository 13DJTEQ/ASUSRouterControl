from __future__ import annotations

import json

import pytest

from asusroutercontrol.speedtest_providers import (
    CDNHttpProvider,
    CDNTargetSpec,
    build_cdn_http_providers,
)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._body), size):
            yield self._body[i : i + size]


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self._json_body = json_body or {}
        self.content = _FakeContent(body)

    async def read(self) -> bytes:
        return self._body

    async def json(self, *, content_type=None):  # noqa: ANN001
        return self._json_body


class _FakeRequestCtx:
    def __init__(self, payload: _FakeResponse | Exception) -> None:
        self._payload = payload

    async def __aenter__(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _FakeSession:
    def __init__(self, plan: dict[tuple[str, str], list[_FakeResponse | Exception]]) -> None:
        self._plan = plan

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def _next(self, method: str, url: str) -> _FakeResponse | Exception:
        key = (method, url)
        queue = self._plan.get(key, [])
        if not queue:
            raise RuntimeError(f"unexpected request: {method} {url}")
        return queue.pop(0)

    def get(self, url: str, timeout=None, headers=None):  # noqa: ANN001
        return _FakeRequestCtx(self._next("GET", url))

    def head(self, url: str, timeout=None):  # noqa: ANN001
        return _FakeRequestCtx(self._next("HEAD", url))

    def post(self, url: str, data=None):  # noqa: ANN001
        return _FakeRequestCtx(self._next("POST", url))


def _client_session_factory(
    plan: dict[tuple[str, str], list[_FakeResponse | Exception]],
):
    def _factory(*_args, **_kwargs):  # noqa: ANN001
        copied = {k: list(v) for k, v in plan.items()}
        return _FakeSession(copied)

    return _factory


@pytest.mark.asyncio
async def test_cdn_http_provider_collects_pop_and_cache_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CDNTargetSpec(
        name="examplecdn",
        download_urls=("https://cdn.example/file.bin",),
        server_label="Example CDN",
        pop_header_candidates=("x-amz-cf-pop",),
        cache_header_candidates=("x-cache",),
    )
    plan = {
        ("GET", "https://cdn.example/file.bin"): [
            _FakeResponse(
                status=200,
                body=b"x" * 1024,
                headers={
                    "X-Amz-Cf-Pop": "IAD12-P4",
                    "X-Cache": "Hit from cloudfront",
                },
            )
        ]
    }
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.aiohttp.ClientSession",
        _client_session_factory(plan),
    )
    clock = {"t": 0.0}

    def _fake_monotonic() -> float:
        clock["t"] += 1.0
        return clock["t"]
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.time.monotonic",
        _fake_monotonic,
    )

    result = await CDNHttpProvider(spec).run()
    assert result.error is None
    assert result.provider == "examplecdn"
    assert result.download_bps == 8192.0
    assert result.pop_code == "IAD12-P4"
    assert result.cache_status == "Hit from cloudfront"


@pytest.mark.asyncio
async def test_cdn_http_provider_is_available_with_get_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CDNTargetSpec(name="edge", download_urls=("https://edge.example/file",))
    plan = {
        ("HEAD", "https://edge.example/file"): [RuntimeError("head unsupported")],
        ("GET", "https://edge.example/file"): [_FakeResponse(status=200)],
    }
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.aiohttp.ClientSession",
        _client_session_factory(plan),
    )

    assert await CDNHttpProvider(spec).is_available() is True


@pytest.mark.asyncio
async def test_cdn_http_provider_returns_error_when_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CDNTargetSpec(name="broken", download_urls=("https://broken.example/file",))
    plan = {
        ("GET", "https://broken.example/file"): [_FakeResponse(status=503)],
    }
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.aiohttp.ClientSession",
        _client_session_factory(plan),
    )
    clock = {"t": 0.0}

    def _fake_monotonic() -> float:
        clock["t"] += 1.0
        return clock["t"]
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.time.monotonic",
        _fake_monotonic,
    )

    result = await CDNHttpProvider(spec).run()
    assert result.download_bps is None
    assert result.error == "download failed or timed out"


@pytest.mark.asyncio
async def test_cdn_http_provider_respects_max_download_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CDNTargetSpec(
        name="capped",
        download_urls=("https://capped.example/file",),
        max_download_bytes=4,
    )
    plan = {
        ("GET", "https://capped.example/file"): [
            _FakeResponse(status=200, body=b"x" * 1024)
        ]
    }
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.aiohttp.ClientSession",
        _client_session_factory(plan),
    )
    clock = {"t": 0.0}

    def _fake_monotonic() -> float:
        clock["t"] += 1.0
        return clock["t"]
    monkeypatch.setattr(
        "asusroutercontrol.speedtest_providers.time.monotonic",
        _fake_monotonic,
    )

    result = await CDNHttpProvider(spec).run()
    assert result.error is None
    assert result.download_bps == 32.0


def test_build_cdn_http_providers_supports_multiple_builtin_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDN_TARGETS_JSON", raising=False)
    providers = build_cdn_http_providers(("cachefly", "cloudfront", "fastly"))
    assert [p.name for p in providers] == ["cachefly", "cloudfront", "fastly"]


def test_build_cdn_http_providers_accepts_custom_target_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CDN_TARGETS_JSON",
        json.dumps(
            [
                {
                    "name": "fastly_probe",
                    "download_urls": ["https://fastly.example/100mb.bin"],
                    "latency_url": "https://fastly.example/ping",
                    "server_label": "Fastly Probe",
                    "max_download_bytes": 2048,
                }
            ]
        ),
    )

    providers = build_cdn_http_providers(("fastly_probe",))
    assert len(providers) == 1
    assert providers[0].name == "fastly_probe"
    assert providers[0]._target.max_download_bytes == 2048
