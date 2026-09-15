"""Non-secret router connection profiles.

Profiles store host/port/backend/capability flags only — never passwords.
Secrets stay in Bitwarden / Keychain via :mod:`asusroutercontrol.credentials`.

v1 keeps a single active profile; the on-disk shape supports multiple later.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from asusroutercontrol.config import (
    Config,
    default_data_dir_for_runtime,
    normalize_runtime_environment,
)

log = logging.getLogger(__name__)

_PROFILE_FILE = "profiles.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

BackendKind = Literal["merlin", "freshtomato"]


@dataclass
class RouterProfile:
    """Customer/lab connection settings for one router."""

    id: str
    display_name: str
    host: str = "router.asus.com"
    http_port: int = 80
    use_ssl: bool = False
    backend: BackendKind = "merlin"
    ssh_enabled: bool = True
    ssh_port: int = 22
    ssh_trust_mode: str = "tofu_confirm"
    ssh_host_key_fingerprint: str | None = None
    credential_namespace: str = "prod"
    http_ok: bool | None = None
    ssh_ok: bool | None = None

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.id):
            raise ValueError(
                f"Profile id {self.id!r} must match [a-z0-9][a-z0-9._-]{{0,63}}"
            )
        if self.backend not in ("merlin", "freshtomato"):
            raise ValueError(f"Unsupported backend: {self.backend!r}")
        if not 1 <= self.http_port <= 65535:
            raise ValueError(f"Invalid http_port: {self.http_port}")
        if not 1 <= self.ssh_port <= 65535:
            raise ValueError(f"Invalid ssh_port: {self.ssh_port}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouterProfile:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ProfileStore:
    """JSON-backed profile collection with one active profile."""

    profiles: list[RouterProfile] = field(default_factory=list)
    active_id: str | None = None

    @property
    def active(self) -> RouterProfile | None:
        if not self.active_id:
            return None
        for profile in self.profiles:
            if profile.id == self.active_id:
                return profile
        return None

    def get(self, profile_id: str) -> RouterProfile | None:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def upsert(self, profile: RouterProfile, *, make_active: bool = True) -> None:
        for idx, existing in enumerate(self.profiles):
            if existing.id == profile.id:
                self.profiles[idx] = profile
                break
        else:
            self.profiles.append(profile)
        if make_active or self.active_id is None:
            self.active_id = profile.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_id": self.active_id,
            "profiles": [p.to_dict() for p in self.profiles],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileStore:
        profiles = [RouterProfile.from_dict(p) for p in data.get("profiles", [])]
        active_id = data.get("active_id")
        if active_id is not None and not any(p.id == active_id for p in profiles):
            active_id = profiles[0].id if profiles else None
        return cls(profiles=profiles, active_id=active_id)


def profiles_path(
    data_dir: Path | None = None,
    *,
    runtime_env: str | None = None,
) -> Path:
    if data_dir is not None:
        return Path(data_dir).expanduser() / _PROFILE_FILE
    env = normalize_runtime_environment(runtime_env)
    return default_data_dir_for_runtime(env) / _PROFILE_FILE


def load_profiles(
    data_dir: Path | None = None,
    *,
    runtime_env: str | None = None,
) -> ProfileStore:
    path = profiles_path(data_dir, runtime_env=runtime_env)
    if not path.exists():
        return ProfileStore()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to read profiles from %s: %s", path, exc)
        return ProfileStore()
    if not isinstance(raw, dict):
        log.warning("Invalid profiles file shape in %s", path)
        return ProfileStore()
    try:
        return ProfileStore.from_dict(raw)
    except (TypeError, ValueError) as exc:
        log.warning("Invalid profile entries in %s: %s", path, exc)
        return ProfileStore()


def save_profiles(
    store: ProfileStore,
    data_dir: Path | None = None,
    *,
    runtime_env: str | None = None,
) -> Path:
    path = profiles_path(data_dir, runtime_env=runtime_env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(store.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def new_profile_id(display_name: str | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (display_name or "router").strip().lower()).strip("-")
    base = (base or "router")[:48]
    suffix = uuid.uuid4().hex[:8]
    candidate = f"{base}-{suffix}"
    return candidate if _ID_RE.fullmatch(candidate) else f"router-{suffix}"


def profile_from_config(
    cfg: Config,
    *,
    profile_id: str | None = None,
    display_name: str | None = None,
) -> RouterProfile:
    backend: BackendKind
    if cfg.router_backend in ("merlin", "freshtomato"):
        backend = cfg.router_backend  # type: ignore[assignment]
    else:
        backend = "merlin"
    return RouterProfile(
        id=profile_id or new_profile_id(display_name or cfg.router_host),
        display_name=display_name or cfg.router_host,
        host=cfg.router_host,
        http_port=cfg.router_port,
        use_ssl=cfg.use_ssl,
        backend=backend,
        ssh_enabled=True,
        ssh_port=cfg.ssh_port,
        ssh_trust_mode=cfg.ssh_trust_mode,
        ssh_host_key_fingerprint=cfg.ssh_host_key_fingerprint,
        credential_namespace=cfg.runtime_env,
    )


def apply_profile(cfg: Config, profile: RouterProfile | None) -> Config:
    """Overlay non-secret profile fields onto Config."""
    if profile is None:
        return cfg
    return replace(
        cfg,
        router_host=profile.host,
        router_port=profile.http_port,
        use_ssl=profile.use_ssl,
        router_backend=profile.backend,
        ssh_port=profile.ssh_port,
        ssh_trust_mode=profile.ssh_trust_mode or cfg.ssh_trust_mode,
        ssh_host_key_fingerprint=(
            profile.ssh_host_key_fingerprint
            if profile.ssh_host_key_fingerprint is not None
            else cfg.ssh_host_key_fingerprint
        ),
        runtime_env=profile.credential_namespace or cfg.runtime_env,
    )


def update_capability_flags(
    profile: RouterProfile,
    *,
    http_ok: bool | None = None,
    ssh_ok: bool | None = None,
) -> RouterProfile:
    return replace(
        profile,
        http_ok=profile.http_ok if http_ok is None else http_ok,
        ssh_ok=profile.ssh_ok if ssh_ok is None else ssh_ok,
    )
