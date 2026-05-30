"""Shared pytest fixtures for ASUSRouterControl tests.

Provides:
- tmp_data_dir       — isolated temp directory per test
- datastore          — in-memory SQLite DataStore (no file I/O)
- fake_backend       — FirmwareBackend test double with controllable responses
- mock_ssh           — RouterSSH stub for NVRAM / probe tests
- env_clean          — clears relevant env vars before each test
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from asusroutercontrol.backends.base import BackendOperationUnsupported, FirmwareBackend
from asusroutercontrol.models import (
    Device,
    PortRule,
    SystemInfo,
    TrafficSnapshot,
    WANStatus,
    WiFiClient,
)

# ---------------------------------------------------------------------------
# Directory / path fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Isolated data directory for a single test."""
    d = tmp_path / "asusroutercontrol"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# DataStore fixture (in-memory SQLite)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def datastore(tmp_data_dir: Path):
    """Fully initialised async DataStore backed by an on-disk temp file.

    An in-memory ``:memory:`` path is not used because aiosqlite opens a
    separate connection per call, losing shared state between queries.
    The fixture uses a tmp file instead, which is cleaned up automatically.
    """
    from asusroutercontrol.datastore import DataStore

    db_path = tmp_data_dir / "router.db"
    store = DataStore(db_path)
    await store.open()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Fake FirmwareBackend
# ---------------------------------------------------------------------------


class FakeBackend(FirmwareBackend):
    """Controllable FirmwareBackend for unit tests.

    Default responses are empty / benign.  Tests can override attributes
    (e.g. ``fake_backend.devices = [...]``) before calling the method.
    """

    def __init__(self) -> None:
        self.connected = False
        self.devices: list[Device] = []
        self.traffic: TrafficSnapshot = TrafficSnapshot()
        self.system_info: SystemInfo = SystemInfo()
        self.wan: WANStatus = WANStatus()
        self.wifi_clients: list[WiFiClient] = []
        self.port_rules: list[PortRule] = []
        self.set_state_results: dict[str, bool] = {}  # action -> return value

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_connected_devices(self) -> list[Device]:
        return list(self.devices)

    async def get_traffic_stats(self) -> TrafficSnapshot:
        return self.traffic

    async def get_system_info(self) -> SystemInfo:
        return self.system_info

    async def get_wan_status(self) -> WANStatus:
        return self.wan

    async def get_wifi_clients(self) -> list[WiFiClient]:
        return list(self.wifi_clients)

    async def set_state(self, action: str, **kwargs: Any) -> bool:
        if action not in self.set_state_results:
            raise BackendOperationUnsupported(f"FakeBackend: unsupported action {action!r}")
        return self.set_state_results[action]

    async def get_port_forwarding(self) -> list[PortRule]:
        return list(self.port_rules)

    async def set_port_forwarding(self, rules: list[PortRule]) -> bool:
        self.port_rules = list(rules)
        return True


@pytest.fixture
def fake_backend() -> FakeBackend:
    """A pre-constructed FakeBackend instance."""
    return FakeBackend()


# ---------------------------------------------------------------------------
# SSH stub
# ---------------------------------------------------------------------------


class SSHResult:
    """Minimal mock of asusroutercontrol.ssh.SSHResult."""

    def __init__(self, stdout: str = "", stderr: str = "", ok: bool = True) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.ok = ok


class FakeSSH:
    """RouterSSH test double.

    Pre-seed ``nvram`` dict to control ``nvram get <key>`` responses.
    All other commands return an empty-stdout success result unless
    overridden via ``responses``.
    """

    def __init__(self) -> None:
        self.nvram: dict[str, str] = {}
        self.responses: dict[str, SSHResult] = {}
        self.calls: list[str] = []

    async def run(self, cmd: str) -> SSHResult:
        self.calls.append(cmd)
        # Handle `nvram get <key>`
        if cmd.startswith("nvram get "):
            key = cmd.split(" ", 2)[2].strip()
            return SSHResult(stdout=self.nvram.get(key, ""), ok=True)
        # Handle `nvram set <key>=<value>`
        if cmd.startswith("nvram set "):
            rest = cmd[len("nvram set "):]
            # Strip any shell quoting (simplified)
            rest = rest.strip("'\"")
            if "=" in rest:
                k, v = rest.split("=", 1)
                self.nvram[k.strip()] = v.strip().strip("'\"")
            return SSHResult(ok=True)
        # Handle `nvram commit`
        if cmd.strip() == "nvram commit":
            return SSHResult(ok=True)
        # Handle `service restart_dnsmasq`
        if "restart_dnsmasq" in cmd:
            return SSHResult(ok=True)
        # Custom override
        if cmd in self.responses:
            return self.responses[cmd]
        return SSHResult(ok=True)


@pytest.fixture
def fake_ssh() -> FakeSSH:
    """A pre-constructed FakeSSH instance with empty NVRAM."""
    return FakeSSH()


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

_ENV_KEYS = [
    "ROUTER_BACKEND",
    "ROUTER_HOST",
    "ROUTER_PORT",
    "USE_SSL",
    "POLLING_INTERVAL",
    "DATA_DIR",
    "SSH_PORT",
    "SSH_TRUST_MODE",
    "SSH_HOST_KEY_FINGERPRINT",
    "SSH_KNOWN_HOSTS_PATH",
    "SOUNDSHIELD_EXPORT_PATH",
    "SPEEDTEST_TIMES",
    "CDN_TARGETS",
    "PEAK_START",
    "PEAK_END",
    "PROBE_INTERVAL",
    "CLIENT_TRAFFIC_INTERVAL",
    "POLL_INTERVAL",
    "NOTIFY_ON_SPEEDTEST",
    "ASUSROUTERCONTROL_ENV_FILE",
]


@pytest.fixture
def env_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all ASUSRouterControl env vars for the duration of the test."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
