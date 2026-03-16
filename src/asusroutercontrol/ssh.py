"""Async SSH client for router shell access.

Uses the same Keychain credentials as the HTTP backend.
Merlin firmware enables SSH by default on port 22.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import asyncssh

from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import get_router_credentials

log = logging.getLogger(__name__)
_TRUST_MODES = {"strict", "tofu_confirm", "tofu_auto"}


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

@dataclass(frozen=True)
class HostKeyDetails:
    host_token: str
    algorithm: str
    key_blob: str  # "<algorithm> <base64>"
    sha256: str
    md5: str


class UnknownHostKeyError(RuntimeError):
    def __init__(self, details: HostKeyDetails, trust_mode: str) -> None:
        self.details = details
        self.trust_mode = trust_mode
        super().__init__(
            f"Unknown SSH host key for {details.host_token} "
            f"(SHA256={details.sha256}, MD5={details.md5}, mode={trust_mode})"
        )


class HostKeyMismatchError(RuntimeError):
    def __init__(
        self,
        *,
        host_token: str,
        expected_sha256: str,
        presented_sha256: str,
        expected_md5: str,
        presented_md5: str,
    ) -> None:
        self.host_token = host_token
        self.expected_sha256 = expected_sha256
        self.presented_sha256 = presented_sha256
        self.expected_md5 = expected_md5
        self.presented_md5 = presented_md5
        super().__init__(
            f"SSH host key mismatch for {host_token}: "
            f"expected SHA256={expected_sha256} (MD5={expected_md5}), "
            f"presented SHA256={presented_sha256} (MD5={presented_md5})"
        )


def _normalize_fingerprint(fp: str | None) -> str | None:
    return fp.strip() if fp and fp.strip() else None


def _fingerprints_from_key_blob(key_blob: str) -> tuple[str, str]:
    parts = key_blob.split()
    if len(parts) < 2:
        raise ValueError(f"Invalid key blob: {key_blob!r}")
    raw = base64.b64decode(parts[1] + "===")
    sha = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    md5_hex = hashlib.md5(raw).hexdigest()
    md5 = "MD5:" + ":".join(md5_hex[i : i + 2] for i in range(0, len(md5_hex), 2))
    return sha, md5


# Default timeouts (seconds)
SSH_CONNECT_TIMEOUT = 15.0
SSH_COMMAND_TIMEOUT = 30.0


class RouterSSH:
    """Async SSH connection to the router."""

    def __init__(
        self,
        hostname: str | None = None,
        username: str | None = None,
        password: str | None = None,
        port: int | None = None,
        *,
        connect_timeout: float = SSH_CONNECT_TIMEOUT,
        command_timeout: float = SSH_COMMAND_TIMEOUT,
    ) -> None:
        cfg = load_config()
        stored_user, stored_pass = get_router_credentials()
        self._hostname = hostname or cfg.router_host
        self._username = username or stored_user
        self._password = password or stored_pass
        self._port = port or cfg.ssh_port
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._trust_mode = (
            cfg.ssh_trust_mode if cfg.ssh_trust_mode in _TRUST_MODES else "tofu_confirm"
        )
        self._expected_fingerprint = _normalize_fingerprint(cfg.ssh_host_key_fingerprint)
        self._known_hosts_path = cfg.ssh_known_hosts_path or (cfg.data_dir / "known_hosts")
        self._conn: asyncssh.SSHClientConnection | None = None

    async def connect(self) -> None:
        self._known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
        conn, presented = await self._probe_server_key()
        try:
            pinned = self._get_pinned_host_key(self._known_hosts_path)

            # Validate against pinned key in known_hosts if present.
            if pinned and pinned.key_blob != presented.key_blob:
                raise HostKeyMismatchError(
                    host_token=self._host_token(),
                    expected_sha256=pinned.sha256,
                    presented_sha256=presented.sha256,
                    expected_md5=pinned.md5,
                    presented_md5=presented.md5,
                )

            # Validate against explicit configured fingerprint, if provided.
            if self._expected_fingerprint:
                expected = self._expected_fingerprint
                if expected not in (presented.sha256, presented.md5):
                    exp_sha = expected if expected.startswith("SHA256:") else "(not provided)"
                    exp_md5 = expected if expected.startswith("MD5:") else "(not provided)"
                    raise HostKeyMismatchError(
                        host_token=self._host_token(),
                        expected_sha256=exp_sha,
                        presented_sha256=presented.sha256,
                        expected_md5=exp_md5,
                        presented_md5=presented.md5,
                    )

            # Unknown host key policy.
            if not pinned:
                if self._trust_mode == "strict":
                    raise UnknownHostKeyError(presented, self._trust_mode)
                if self._trust_mode == "tofu_confirm":
                    raise UnknownHostKeyError(presented, self._trust_mode)
                # tofu_auto
                self._upsert_pinned_host_key(self._known_hosts_path, presented)
                log.warning(
                    "Pinned SSH host key automatically for %s (TOFU auto): %s",
                    self._host_token(),
                    presented.sha256,
                )

            self._conn = conn
            conn = None
        finally:
            if conn:
                conn.close()
                await conn.wait_closed()
        log.info("SSH connected to %s:%d", self._hostname, self._port)

    def _host_token(self) -> str:
        if self._port and self._port != 22:
            return f"[{self._hostname}]:{self._port}"
        return self._hostname

    @property
    def known_hosts_path(self) -> Path:
        return self._known_hosts_path

    async def _probe_server_key(self) -> tuple[asyncssh.SSHClientConnection, HostKeyDetails]:
        import asyncio as _aio

        conn = await _aio.wait_for(
            asyncssh.connect(
                self._hostname,
                port=self._port,
                username=self._username,
                password=self._password,
                known_hosts=None,
            ),
            timeout=self._connect_timeout,
        )
        host_key = conn.get_server_host_key()
        key_blob = host_key.export_public_key().decode().strip()
        algorithm = key_blob.split()[0]
        sha256, md5 = _fingerprints_from_key_blob(key_blob)
        return conn, HostKeyDetails(
            host_token=self._host_token(),
            algorithm=algorithm,
            key_blob=key_blob,
            sha256=sha256,
            md5=md5,
        )

    def _get_pinned_host_key(self, known_hosts_path: Path) -> HostKeyDetails | None:
        if not known_hosts_path.exists():
            return None
        token = self._host_token()
        for line in known_hosts_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 3:
                continue
            hosts = parts[0].split(",")
            if token not in hosts:
                continue
            key_blob = f"{parts[1]} {parts[2]}"
            sha256, md5 = _fingerprints_from_key_blob(key_blob)
            return HostKeyDetails(
                host_token=token,
                algorithm=parts[1],
                key_blob=key_blob,
                sha256=sha256,
                md5=md5,
            )
        return None

    def _upsert_pinned_host_key(self, known_hosts_path: Path, details: HostKeyDetails) -> None:
        token = details.host_token
        lines: list[str] = []
        if known_hosts_path.exists():
            for raw in known_hosts_path.read_text(encoding="utf-8").splitlines():
                stripped = raw.strip()
                if not stripped or stripped.startswith("#"):
                    lines.append(raw)
                    continue
                parts = stripped.split()
                if len(parts) < 3:
                    lines.append(raw)
                    continue
                hosts = parts[0].split(",")
                if token in hosts:
                    continue  # drop replaced entry
                lines.append(raw)
        lines.append(f"{token} {details.key_blob}")
        self._write_known_hosts_atomic(known_hosts_path, lines)

    def _remove_pinned_host_key(self, known_hosts_path: Path, host: str, port: int) -> bool:
        token = self._format_host_token(host, port)
        if not known_hosts_path.exists():
            return False
        removed = False
        lines: list[str] = []
        for raw in known_hosts_path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(raw)
                continue
            parts = stripped.split()
            if len(parts) < 3:
                lines.append(raw)
                continue
            hosts = parts[0].split(",")
            if token in hosts:
                removed = True
                continue
            lines.append(raw)
        if removed:
            self._write_known_hosts_atomic(known_hosts_path, lines)
        return removed

    def _write_known_hosts_atomic(self, known_hosts_path: Path, lines: list[str]) -> None:
        known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = known_hosts_path.with_suffix(".tmp")
        payload = "\n".join(lines).rstrip("\n")
        tmp.write_text((payload + "\n") if payload else "", encoding="utf-8")
        os.replace(tmp, known_hosts_path)
        known_hosts_path.chmod(0o600)

    @staticmethod
    def _format_host_token(host: str, port: int) -> str:
        return f"[{host}]:{port}" if port != 22 else host

    def list_pinned_hosts(self) -> list[HostKeyDetails]:
        results: list[HostKeyDetails] = []
        if not self._known_hosts_path.exists():
            return results
        for line in self._known_hosts_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 3:
                continue
            token = parts[0]
            key_blob = f"{parts[1]} {parts[2]}"
            sha256, md5 = _fingerprints_from_key_blob(key_blob)
            results.append(
                HostKeyDetails(
                    host_token=token,
                    algorithm=parts[1],
                    key_blob=key_blob,
                    sha256=sha256,
                    md5=md5,
                )
            )
        return results

    async def rotate_pinned_host_key(
        self, host: str | None = None, port: int | None = None
    ) -> HostKeyDetails:
        target_host = host or self._hostname
        target_port = port or self._port
        old_host, old_port = self._hostname, self._port
        self._hostname, self._port = target_host, target_port
        conn, details = await self._probe_server_key()
        conn.close()
        await conn.wait_closed()
        self._upsert_pinned_host_key(self._known_hosts_path, details)
        self._hostname, self._port = old_host, old_port
        return details

    def revoke_pinned_host_key(self, host: str | None = None, port: int | None = None) -> bool:
        target_host = host or self._hostname
        target_port = port or self._port
        return self._remove_pinned_host_key(self._known_hosts_path, target_host, target_port)

    async def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None
            log.info("SSH disconnected")

    async def run(
        self,
        command: str,
        *,
        check: bool = False,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute a command and return structured result."""
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        import asyncio as _aio

        effective_timeout = timeout if timeout is not None else self._command_timeout
        result = await _aio.wait_for(
            self._conn.run(command),
            timeout=effective_timeout,
        )
        cmd_result = CommandResult(
            stdout=(result.stdout or "").strip(),
            stderr=(result.stderr or "").strip(),
            exit_code=result.exit_status or 0,
        )
        if check and not cmd_result.ok:
            raise RuntimeError(
                f"Command failed (exit {cmd_result.exit_code}): {command}\n{cmd_result.stderr}"
            )
        log.debug("SSH cmd: %s -> exit %d", command, cmd_result.exit_code)
        return cmd_result

    async def read_file(self, path: str) -> str | None:
        """Read a remote file, return None if not found."""
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        safe_path = str(PurePosixPath(path))
        try:
            async with self._conn.start_sftp_client() as sftp:
                async with sftp.open(safe_path, "r") as f:
                    return await f.read()
        except asyncssh.SFTPNoSuchFile:
            return None

    async def write_file(self, path: str, content: str) -> bool:
        """Write content to a remote file."""
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        safe_path = str(PurePosixPath(path))
        try:
            async with self._conn.start_sftp_client() as sftp:
                async with sftp.open(safe_path, "w") as f:
                    await f.write(content)
            return True
        except Exception:
            log.exception("Failed to write remote file: %s", safe_path)
            return False

    async def file_exists(self, path: str) -> bool:
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")
        safe_path = str(PurePosixPath(path))
        try:
            async with self._conn.start_sftp_client() as sftp:
                await sftp.stat(safe_path)
                return True
        except asyncssh.SFTPNoSuchFile:
            return False
        except Exception:
            log.exception("Failed to stat remote file: %s", safe_path)
            return False

    async def __aenter__(self) -> RouterSSH:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()
