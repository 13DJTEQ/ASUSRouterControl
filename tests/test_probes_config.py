from __future__ import annotations

import json

import pytest

from asusroutercontrol.probes import probe_config


class _Result:
    def __init__(self, ok: bool, stdout: str) -> None:
        self.ok = ok
        self.stdout = stdout


class _FakeSSH:
    def __init__(
        self,
        *,
        batch_ok: bool,
        batch_out: str,
        per_key: dict[str, str] | None = None,
    ) -> None:
        self.batch_ok = batch_ok
        self.batch_out = batch_out
        self.per_key = per_key or {}
        self.calls: list[str] = []

    async def run(self, cmd: str) -> _Result:
        self.calls.append(cmd)
        if cmd.startswith("nvram show"):
            return _Result(self.batch_ok, self.batch_out)
        if cmd.startswith("nvram get "):
            key = cmd.split()[2]
            return _Result(True, self.per_key.get(key, ""))
        return _Result(False, "")


@pytest.mark.asyncio
async def test_probe_config_uses_batch_when_available() -> None:
    ssh = _FakeSSH(
        batch_ok=True,
        batch_out="qos_enable=1\nmisc_http_x=0\n",
    )
    snap = await probe_config(ssh)
    nvram = json.loads(snap.nvram_json)
    assert nvram["qos_enable"] == "1"
    assert nvram["misc_http_x"] == "0"
    assert len(ssh.calls) == 1


@pytest.mark.asyncio
async def test_probe_config_falls_back_to_per_key_calls() -> None:
    ssh = _FakeSSH(
        batch_ok=False,
        batch_out="",
        per_key={"qos_enable": "1", "misc_http_x": "0"},
    )
    snap = await probe_config(ssh)
    nvram = json.loads(snap.nvram_json)
    assert nvram["qos_enable"] == "1"
    assert nvram["misc_http_x"] == "0"
    assert any(cmd.startswith("nvram get ") for cmd in ssh.calls)
