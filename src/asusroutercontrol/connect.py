"""Router connection testing and setup orchestration.

HTTP admin API is required. SSH is optional and never blocks a successful setup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from asusroutercontrol.backends.factory import create_backend
from asusroutercontrol.config import Config, load_config
from asusroutercontrol.credentials import store_router_credentials
from asusroutercontrol.discovery import discover_router_candidates, pick_default_host
from asusroutercontrol.profile import (
    ProfileStore,
    RouterProfile,
    apply_profile,
    load_profiles,
    new_profile_id,
    save_profiles,
    update_capability_flags,
)
from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)


def format_http_probe_error(exc: BaseException, *, host: str) -> str:
    """Turn asusrouter/network failures into actionable Connect UI text."""
    raw = str(exc).strip() or exc.__class__.__name__
    low = raw.lower()
    if "cannot access" in low and "login" in low:
        return (
            f"Cannot reach HTTP admin login on {host}. "
            "Check you are on the router LAN/Wi‑Fi, username/password are the "
            "router admin credentials (not the Bitwarden item label), and try "
            "the gateway IP if router.asus.com fails."
        )
    if "ssl" in low and "certificate" in low:
        return (
            f"TLS/SSL certificate error talking to {host}. "
            "Retry with HTTP or install/trust the router certificate."
        )
    if "timeout" in low or "timed out" in low:
        return f"Timed out reaching {host}. Confirm the host/IP and local network."
    if "name or service not known" in low or "nodename nor servname" in low:
        return f"Could not resolve host {host}. Use the router LAN IP instead."
    if "connection refused" in low:
        return f"Connection refused by {host}. Check HTTP(S) admin port."
    return f"{raw}"



@dataclass(frozen=True)
class ConnectionProbeResult:
    http_ok: bool
    ssh_ok: bool | None
    http_error: str | None = None
    ssh_error: str | None = None

    @property
    def ok(self) -> bool:
        """Setup succeeds when HTTP works — SSH is optional."""
        return self.http_ok


@dataclass(frozen=True)
class SetupResult:
    profile: RouterProfile
    probe: ConnectionProbeResult
    credentials_backend: str
    profiles_path: Path


async def probe_http(cfg: Config, username: str, password: str) -> tuple[bool, str | None]:
    backend = create_backend(cfg, username=username, password=password)
    try:
        await backend.connect()
        return True, None
    except Exception as exc:  # noqa: BLE001 — surface any connect failure to setup UX
        return False, format_http_probe_error(exc, host=cfg.router_host)
    finally:
        try:
            await backend.disconnect()
        except Exception:  # noqa: BLE001
            log.debug("HTTP backend disconnect failed during probe", exc_info=True)


async def probe_ssh(cfg: Config, username: str, password: str) -> tuple[bool, str | None]:
    ssh = RouterSSH(
        hostname=cfg.router_host,
        username=username,
        password=password,
        port=cfg.ssh_port,
        connect_timeout=8.0,
    )
    try:
        await ssh.connect()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        try:
            await ssh.disconnect()
        except Exception:  # noqa: BLE001
            log.debug("SSH disconnect failed during probe", exc_info=True)


async def probe_connection(
    cfg: Config,
    username: str,
    password: str,
    *,
    try_ssh: bool = True,
) -> ConnectionProbeResult:
    http_ok, http_error = await probe_http(cfg, username, password)
    if not try_ssh:
        return ConnectionProbeResult(http_ok=http_ok, ssh_ok=None, http_error=http_error)
    ssh_ok, ssh_error = await probe_ssh(cfg, username, password)
    return ConnectionProbeResult(
        http_ok=http_ok,
        ssh_ok=ssh_ok,
        http_error=http_error,
        ssh_error=ssh_error,
    )


async def setup_router_connection(
    *,
    host: str | None = None,
    username: str,
    password: str,
    http_port: int = 80,
    use_ssl: bool = False,
    backend: str = "merlin",
    ssh_enabled: bool = True,
    ssh_port: int = 22,
    display_name: str | None = None,
    credential_backend: str = "keychain",
    credential_namespace: str | None = None,
    cfg: Config | None = None,
    data_dir: Path | None = None,
    discover: bool = True,
) -> SetupResult:
    """Test HTTP (required) + optional SSH, then persist profile + credentials."""
    base = cfg or load_config()
    resolved_host = host
    if not resolved_host and discover:
        resolved_host = pick_default_host(
            discover_router_candidates(http_port=http_port, probe=True)
        )
    resolved_host = resolved_host or base.router_host

    namespace = credential_namespace or base.runtime_env
    backend_kind = backend if backend in ("merlin", "freshtomato") else "merlin"
    profile = RouterProfile(
        id=new_profile_id(display_name or resolved_host),
        display_name=display_name or resolved_host,
        host=resolved_host,
        http_port=http_port,
        use_ssl=use_ssl,
        backend=backend_kind,  # type: ignore[arg-type]
        ssh_enabled=ssh_enabled,
        ssh_port=ssh_port,
        ssh_trust_mode=base.ssh_trust_mode,
        ssh_host_key_fingerprint=base.ssh_host_key_fingerprint,
        credential_namespace=namespace,
    )
    effective = apply_profile(base, profile)
    probe = await probe_connection(
        effective,
        username,
        password,
        try_ssh=ssh_enabled,
    )
    # Retry alternate transports when the first HTTP probe fails.
    if not probe.http_ok:
        attempts: list[tuple[str, int, bool]] = []
        if not use_ssl and http_port == 80:
            attempts.append((resolved_host, 443, True))
        # router.asus.com only works on-LAN via ASUS DNS — also try gateway IP.
        host_l = resolved_host.strip().lower()
        if host_l in {"router.asus.com", "www.asusrouter.com", "router.asus.com."}:
            try:
                from asusroutercontrol.discovery import default_gateway_ipv4

                gateway = default_gateway_ipv4()
            except Exception:  # noqa: BLE001
                gateway = None
            if gateway and gateway != resolved_host:
                attempts.append((gateway, http_port, use_ssl))
                if not use_ssl and http_port == 80:
                    attempts.append((gateway, 443, True))

        last_error = probe.http_error
        for attempt_host, attempt_port, attempt_ssl in attempts:
            log.info(
                "HTTP probe failed for %s (%s); retrying %s:%s ssl=%s",
                resolved_host,
                last_error,
                attempt_host,
                attempt_port,
                attempt_ssl,
            )
            profile = replace(
                profile,
                host=attempt_host,
                http_port=attempt_port,
                use_ssl=attempt_ssl,
            )
            effective = apply_profile(base, profile)
            probe = await probe_connection(
                effective,
                username,
                password,
                try_ssh=ssh_enabled,
            )
            last_error = probe.http_error
            if probe.http_ok:
                resolved_host = attempt_host
                break

    if not probe.http_ok:
        raise ConnectionError(
            f"HTTP admin login failed for {resolved_host}: {probe.http_error}"
        )

    profile = update_capability_flags(
        profile,
        http_ok=probe.http_ok,
        ssh_ok=probe.ssh_ok,
    )
    if ssh_enabled and probe.ssh_ok is False:
        profile = replace(profile, ssh_enabled=False, ssh_ok=False)

    target_dir = data_dir or effective.data_dir
    store = load_profiles(target_dir, runtime_env=namespace)
    store.upsert(profile, make_active=True)
    path = save_profiles(store, target_dir, runtime_env=namespace)

    stored_backend = credential_backend
    try:
        stored_backend = store_router_credentials(
            username,
            password,
            ssh_port=ssh_port,
            env=namespace,
            backend=credential_backend,
        )
    except Exception as store_exc:  # noqa: BLE001 — connection already succeeded
        log.warning(
            "Credential store in %s failed after successful HTTP login: %s",
            credential_backend,
            store_exc,
        )
        if credential_backend != "keychain":
            try:
                stored_backend = store_router_credentials(
                    username,
                    password,
                    ssh_port=ssh_port,
                    env=namespace,
                    backend="keychain",
                )
                log.info("Fell back to keychain credential store")
            except Exception as keychain_exc:  # noqa: BLE001
                log.warning("Keychain credential fallback failed: %s", keychain_exc)
                stored_backend = f"unstored:{credential_backend}"
        else:
            stored_backend = f"unstored:{credential_backend}"
    return SetupResult(
        profile=profile,
        probe=probe,
        credentials_backend=stored_backend,
        profiles_path=path,
    )


def active_profile(cfg: Config | None = None) -> RouterProfile | None:
    base = cfg or load_config()
    store = load_profiles(base.data_dir, runtime_env=base.runtime_env)
    return store.active


def load_profile_store(cfg: Config | None = None) -> ProfileStore:
    base = cfg or load_config()
    return load_profiles(base.data_dir, runtime_env=base.runtime_env)
