"""Secure credential management with pluggable backends: Bitwarden, 1Password, macOS Keychain.

Canonical naming follows the universal-keychain convention:
  Service / vault item title: universal-keychain-asusroutercontrol-{env}-{key}
  Account metadata: asusroutercontrol.{env}.{key}

The active backend is controlled by ASUSROUTERCONTROL_CREDENTIAL_BACKEND env var
("bitwarden" | "1password" | "keychain").  Defaults to Bitwarden.
When the backend is unreachable, reads fall back through the remaining backends
in priority order.
"""

from __future__ import annotations

import abc
import logging
import os
import subprocess
from json import JSONDecodeError
from json import dumps as json_dumps
from json import loads as json_loads
from pathlib import Path

import keyring

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Naming constants
# ---------------------------------------------------------------------------
PROJECT = "asusroutercontrol"
DEFAULT_ENV = "prod"
_LEGACY_SERVICE = "com.asusroutercontrol"

_CREDENTIAL_BACKEND_ENV = "ASUSROUTERCONTROL_CREDENTIAL_BACKEND"
_BW_ROUTER_ITEM_ENV = "ASUSROUTERCONTROL_BW_ROUTER_ITEM"
_OP_VAULT_ENV = "ASUSROUTERCONTROL_1PASSWORD_VAULT"
_OP_VAULT_ENV_FALLBACK = "OP_VAULT"
_BW_SESSION_ENV = "BW_SESSION"

# ---------------------------------------------------------------------------
# Backend detection helpers
# ---------------------------------------------------------------------------


def _backend_name(backend: object) -> str:
    cls = type(backend)
    return f"{cls.__module__}.{cls.__name__}"


def _is_fail_backend(backend: object) -> bool:
    return _backend_name(backend) == "keyring.backends.fail.Keyring"


def _build_macos_keyring():
    from keyring.backends.macOS import Keyring as MacOSKeyring

    return MacOSKeyring()


def _ensure_secure_keyring_backend() -> bool:
    backend = keyring.get_keyring()
    if not _is_fail_backend(backend):
        return True
    try:
        keyring.set_keyring(_build_macos_keyring())
    except Exception as exc:
        log.error(
            "Secure keychain backend unavailable (active backend=%s): %s",
            _backend_name(backend),
            exc,
        )
        return False
    active = keyring.get_keyring()
    if _is_fail_backend(active):
        log.error(
            "Secure keychain backend unavailable (still active backend=%s)",
            _backend_name(active),
        )
        return False
    log.info("Activated secure keychain backend: %s", _backend_name(active))
    return True


# ---------------------------------------------------------------------------
# Canonical name builders
# ---------------------------------------------------------------------------


def _service_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"universal-keychain-{PROJECT}-{env}-{key}"


def _account_name(key: str, env: str = DEFAULT_ENV) -> str:
    return f"{PROJECT}.{env}.{key}"


# ---------------------------------------------------------------------------
# 1Password helpers
# ---------------------------------------------------------------------------


def _op_vault() -> str | None:
    value = os.environ.get(_OP_VAULT_ENV, "").strip()
    if value:
        return value
    return os.environ.get(_OP_VAULT_ENV_FALLBACK, "").strip() or None


def _op_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["op", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        log.error("1Password CLI ('op') not found. Install it to manage project credentials.")
        return None
    except OSError as exc:
        log.error("Failed to execute 1Password CLI command: %s", exc)
        return None


def _op_item_not_found(message: str) -> bool:
    normalized = message.lower()
    return any(
        candidate in normalized
        for candidate in (
            "isn't an item",
            "could not find",
            "not found",
            "doesn't exist",
            "no item",
        )
    )


def _extract_op_secret(item: dict) -> str | None:
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        fields = []
    fallback_value: str | None = None
    for field in fields:
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if value in (None, ""):
            continue
        value_text = str(value)
        if fallback_value is None:
            fallback_value = value_text
        field_id = str(field.get("id", "")).lower()
        purpose = str(field.get("purpose", "")).lower()
        field_type = str(field.get("type", "")).lower()
        label = str(field.get("label", "")).lower()
        if (
            field_id == "password"
            or purpose == "password"
            or field_type == "concealed"
            or label in {"password", "value", "secret"}
        ):
            return value_text
    explicit_password = item.get("password")
    if isinstance(explicit_password, str) and explicit_password:
        return explicit_password
    return fallback_value


# ---------------------------------------------------------------------------
# Bitwarden helpers
# ---------------------------------------------------------------------------


# Sentinel to avoid repeatedly probing for the bw binary.
_bw_cli_found: bool | None = None


def _bw_binaries() -> list[str]:
    """Candidate bw executable paths (GUI apps often lack Homebrew PATH)."""
    home = Path.home()
    candidates = [
        "bw",
        "/opt/homebrew/bin/bw",
        "/usr/local/bin/bw",
        str(home / ".local" / "bin" / "bw"),
        str(home / "bin" / "bw"),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _ensure_bw_session_env() -> None:
    """Load BW_SESSION for GUI launches that don't inherit a Terminal unlock."""
    if os.environ.get(_BW_SESSION_ENV, "").strip():
        return
    # dotenv / process env alternate names
    for key in ("BW_SESSION", "BITWARDEN_SESSION"):
        value = os.environ.get(key, "").strip()
        if value:
            os.environ[_BW_SESSION_ENV] = value
            return
    session_files = [
        Path.home() / ".config" / "asusroutercontrol" / "bw_session",
        Path.home() / ".asusroutercontrol" / "bw_session",
        Path.cwd() / ".bw_session",
    ]
    for path in session_files:
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    os.environ[_BW_SESSION_ENV] = value
                    log.debug("Loaded BW_SESSION from %s", path)
                    return
        except OSError:
            continue


def _bw_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Execute a Bitwarden CLI command, returning None if bw is unavailable."""
    global _bw_cli_found  # noqa: PLW0603
    _ensure_bw_session_env()
    env = os.environ.copy()
    # Ensure Homebrew paths exist for menubar / .app launches.
    path_parts = env.get("PATH", "").split(":") if env.get("PATH") else []
    for extra in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")):
        if extra not in path_parts:
            path_parts.insert(0, extra)
    env["PATH"] = ":".join(p for p in path_parts if p)

    last_error: Exception | None = None
    for binary in _bw_binaries():
        try:
            result = subprocess.run(
                [binary, *arguments],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            _bw_cli_found = True
            return result
        except FileNotFoundError as exc:
            last_error = exc
            continue
        except OSError as exc:
            last_error = exc
            continue
    _bw_cli_found = False
    if last_error is not None:
        log.warning(
            "Bitwarden CLI ('bw') not found on PATH. Install it or unlock via Terminal."
        )
    return None


def _bw_item_not_found(message: str) -> bool:
    normalized = message.lower()
    return any(
        candidate in normalized
        for candidate in (
            "not found",
            "no objects found",
            "search does not match",
            "not found in this vault",
            "we didn't find an item",
            "more than one item was found",
        )
    )


def _extract_bw_secret(item: dict) -> str | None:
    login = item.get("login", {})
    if isinstance(login, dict):
        password = login.get("password")
        if isinstance(password, str) and password:
            return password
    fields = item.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                name = str(field.get("name", "")).lower()
                if name in {"password", "value", "secret"}:
                    value = field.get("value")
                    if isinstance(value, str) and value:
                        return value
    notes = item.get("notes")
    if isinstance(notes, str) and notes:
        return notes
    return None


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class _CredentialBackend(abc.ABC):
    """Pluggable credential backend."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @abc.abstractmethod
    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        ...

    @abc.abstractmethod
    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        ...

    @abc.abstractmethod
    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        ...

    def healthy(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# 1Password backend
# ---------------------------------------------------------------------------


class _OnePasswordBackend(_CredentialBackend):
    @property
    def name(self) -> str:
        return "1password"

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        title = _service_name(key, env)
        args = ["item", "get", title, "--format", "json"]
        vault = _op_vault()
        if vault:
            args.extend(["--vault", vault])
        result = _op_run(args)
        if result is None:
            return None
        if result.returncode != 0:
            error_blob = f"{result.stdout}\n{result.stderr}".strip()
            if _op_item_not_found(error_blob):
                return None
            log.error("Failed to read 1Password item '%s': %s", title, error_blob)
            return None
        try:
            payload = json_loads(result.stdout or "{}")
        except JSONDecodeError as exc:
            log.error("Invalid JSON received from 1Password for '%s': %s", title, exc)
            return None
        if not isinstance(payload, dict):
            log.error("Unexpected 1Password response payload type for '%s'", title)
            return None
        return _extract_op_secret(payload)

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        title = _service_name(key, env)
        account = _account_name(key, env)
        vault = _op_vault()
        edit_args = [
            "item",
            "edit",
            title,
            f"username={account}",
            f"password={value}",
        ]
        if vault:
            edit_args.extend(["--vault", vault])
        edit_result = _op_run(edit_args)
        if edit_result is None:
            return False
        if edit_result.returncode == 0:
            return True
        edit_error = f"{edit_result.stdout}\n{edit_result.stderr}".strip()
        if not _op_item_not_found(edit_error):
            log.error("Failed to update 1Password item '%s': %s", title, edit_error)
            return False
        create_args = [
            "item",
            "create",
            "--category",
            "password",
            "--title",
            title,
            f"username={account}",
            f"password={value}",
        ]
        if vault:
            create_args.extend(["--vault", vault])
        create_result = _op_run(create_args)
        if create_result is None:
            return False
        if create_result.returncode != 0:
            create_error = f"{create_result.stdout}\n{create_result.stderr}".strip()
            log.error("Failed to create 1Password item '%s': %s", title, create_error)
            return False
        return True

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        title = _service_name(key, env)
        args = ["item", "delete", title, "--archive"]
        vault = _op_vault()
        if vault:
            args.extend(["--vault", vault])
        result = _op_run(args)
        if result is None:
            return False
        if result.returncode == 0:
            return True
        error_blob = f"{result.stdout}\n{result.stderr}".strip()
        if _op_item_not_found(error_blob):
            return False
        log.error("Failed to delete 1Password item '%s': %s", title, error_blob)
        return False


# ---------------------------------------------------------------------------
# macOS Keychain backend
# ---------------------------------------------------------------------------


class _KeychainBackend(_CredentialBackend):
    @property
    def name(self) -> str:
        return "keychain"

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        if not _ensure_secure_keyring_backend():
            return None
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            return keyring.get_password(svc, acct)
        except Exception as exc:
            log.error("Failed to read keychain entry %s/%s: %s", svc, acct, exc)
            return None

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        if not _ensure_secure_keyring_backend():
            return False
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            keyring.set_password(svc, acct, value)
            return True
        except Exception as exc:
            log.error("Failed to write keychain entry %s/%s: %s", svc, acct, exc)
            return False

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        if not _ensure_secure_keyring_backend():
            return False
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        try:
            keyring.delete_password(svc, acct)
            return True
        except Exception as exc:
            log.error("Failed to delete keychain entry %s/%s: %s", svc, acct, exc)
            return False


# ---------------------------------------------------------------------------
# Bitwarden backend
# ---------------------------------------------------------------------------


class _BitwardenBackend(_CredentialBackend):
    """Bitwarden CLI backend with graceful handling of missing CLI,
    login state checks, and clear error messages."""

    @property
    def name(self) -> str:
        return "bitwarden"

    def login_check(self) -> str:
        """Verify Bitwarden vault state and return a descriptive status string.

        Returns:
            "unlocked" if the vault is unlocked and ready for use.
            "locked" if the user is logged in but the vault is locked.
            "unauthenticated" if the user is not logged in.
            "cli_not_found" if the `bw` binary is not available.
            "unknown" for any other state (parse errors, etc.).
        """
        result = _bw_run(["status"])
        if result is None:
            return "cli_not_found"
        if result.returncode != 0:
            error = f"{result.stdout}\n{result.stderr}".strip()
            log.debug("Bitwarden status command failed: %s", error)
            return "unknown"
        try:
            status = json_loads(result.stdout or "{}")
        except JSONDecodeError:
            log.debug("Bitwarden status returned invalid JSON")
            return "unknown"
        if not isinstance(status, dict):
            return "unknown"
        bw_status = str(status.get("status", "")).lower()
        if bw_status == "unlocked":
            return "unlocked"
        if bw_status == "locked":
            return "locked"
        if bw_status in {"unauthenticated", "not logged in", "not authenticated"}:
            return "unauthenticated"
        return "unknown"

    def healthy(self) -> bool:
        """Return True only when the bw CLI is present AND the vault is unlocked."""
        if _bw_cli_found is False:
            log.debug("Bitwarden CLI was previously detected as missing; skipping health check.")
            return False
        state = self.login_check()
        if state == "unlocked":
            return True
        if state == "cli_not_found":
            log.info("Bitwarden backend unhealthy: CLI not found.")
        elif state == "locked":
            log.info("Bitwarden backend unhealthy: vault is locked. Run 'bw unlock'.")
        elif state == "unauthenticated":
            log.info("Bitwarden backend unhealthy: not logged in. Run 'bw login'.")
        else:
            log.info("Bitwarden backend unhealthy: unknown vault state.")
        return False

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
        return _bw_run(arguments)

    def _item_title(self, key: str, env: str = DEFAULT_ENV) -> str:
        return _service_name(key, env)

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        title = self._item_title(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden get skipped: CLI not installed.")
            return None
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden get skipped: vault %s.", state)
            return None
        result = self._run(["get", "item", title, "--raw"])
        if result is None:
            return None
        if result.returncode != 0:
            error_blob = f"{result.stdout}\n{result.stderr}".strip()
            if _bw_item_not_found(error_blob):
                log.debug("Bitwarden item '%s' not found.", title)
                return None
            log.error("Failed to read Bitwarden item '%s': %s", title, error_blob)
            return None
        try:
            payload = json_loads(result.stdout or "{}")
        except JSONDecodeError as exc:
            log.error("Invalid JSON received from Bitwarden for '%s': %s", title, exc)
            return None
        if not isinstance(payload, dict):
            log.error("Unexpected Bitwarden response payload type for '%s'", title)
            return None
        return _extract_bw_secret(payload)

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        title = self._item_title(key, env)
        account = _account_name(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden store skipped: CLI not installed.")
            return False
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden store skipped: vault %s.", state)
            return False

        # Determine if the item already exists.
        get_result = self._run(["get", "item", title, "--raw"])
        if get_result is not None and get_result.returncode == 0:
            # Item exists — update via `bw edit item` using JSON stdin.
            edit_payload = {
                "login": {
                    "username": account,
                    "password": value,
                },
            }
            edit_json = json_dumps(edit_payload)
            edit_result = self._run(["edit", "item", title, edit_json])
            if edit_result is None:
                return False
            if edit_result.returncode == 0:
                return True
            error_blob = f"{edit_result.stdout}\n{edit_result.stderr}".strip()
            log.error("Failed to update Bitwarden item '%s': %s", title, error_blob)
            return False

        # Item does not exist — create via `bw create item login` using JSON stdin.
        create_payload = {
            "type": 1,  # Login
            "name": title,
            "login": {
                "username": account,
                "password": value,
            },
        }
        create_json = json_dumps(create_payload)
        create_result = self._run(["create", "item", "login", create_json, "--raw"])
        if create_result is None:
            return False
        if create_result.returncode != 0:
            error_blob = f"{create_result.stdout}\n{create_result.stderr}".strip()
            log.error("Failed to create Bitwarden item '%s': %s", title, error_blob)
            return False
        return True

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        title = self._item_title(key, env)
        state = self.login_check()
        if state == "cli_not_found":
            log.debug("Bitwarden delete skipped: CLI not installed.")
            return False
        if state in ("locked", "unauthenticated"):
            log.debug("Bitwarden delete skipped: vault %s.", state)
            return False
        result = self._run(["delete", "item", title])
        if result is None:
            return False
        if result.returncode == 0:
            return True
        error_blob = f"{result.stdout}\n{result.stderr}".strip()
        if _bw_item_not_found(error_blob):
            log.debug("Bitwarden item '%s' not found for delete.", title)
            return False
        log.error("Failed to delete Bitwarden item '%s': %s", title, error_blob)
        return False


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, _CredentialBackend] = {
    "1password": _OnePasswordBackend(),
    "keychain": _KeychainBackend(),
    "bitwarden": _BitwardenBackend(),
}

_READ_FALLBACK_ORDER = ["bitwarden", "1password", "keychain"]
_WRITE_FALLBACK_ORDER = ["bitwarden", "1password", "keychain"]


def _active_backend_name() -> str:
    raw = os.environ.get(_CREDENTIAL_BACKEND_ENV, "bitwarden").strip().lower()
    if raw in _BACKENDS:
        return raw
    log.warning("Unknown credential backend '%s'; falling back to bitwarden", raw)
    return "bitwarden"


def _active_backend() -> _CredentialBackend:
    return _BACKENDS[_active_backend_name()]


# ---------------------------------------------------------------------------
# Public CRUD (env-aware, with shared fallback)
# ---------------------------------------------------------------------------

def _fallback_envs(env: str) -> list[str]:
    """Return env search chain: specific env → shared → prod."""
    if env == "prod":
        return ["prod"]
    return [env, "shared", "prod"]


def get_credential(key: str, *, env: str = DEFAULT_ENV) -> str | None:
    """Retrieve credential from the active backend, with cross-backend and env fallbacks."""
    # Try the active backend first across env fallback chain
    active = _active_backend()
    for try_env in _fallback_envs(env):
        val = active.get(key, env=try_env)
        if val:
            log.debug("Credential '%s' resolved from %s (env=%s)", key, active.name, try_env)
            return val

    # Fall back through remaining backends (reads only)
    for fallback_name in _READ_FALLBACK_ORDER:
        if fallback_name == active.name:
            continue
        backend = _BACKENDS[fallback_name]
        for try_env in _fallback_envs(env):
            val = backend.get(key, env=try_env)
            if val:
                log.debug(
                    "Credential '%s' resolved from %s fallback (env=%s)",
                    key,
                    fallback_name,
                    try_env,
                )
                return val

    # Legacy keychain fallback (read-only)
    legacy_key = key.replace("_", ".")
    if _ensure_secure_keyring_backend():
        try:
            val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
            if val:
                log.debug("Credential '%s' resolved from legacy keychain entry", key)
                return val
        except Exception:
            pass

    # Environment variable fallback (non-secret)
    return os.environ.get(key.upper())


def store_credential(
    key: str,
    value: str,
    *,
    env: str = DEFAULT_ENV,
    backend: str | None = None,
) -> bool:
    """Store credential in the active backend (or an explicit backend)."""
    if backend is None:
        target = _active_backend()
    else:
        name = backend.strip().lower()
        if name not in _BACKENDS:
            log.error("Unknown credential backend '%s'", backend)
            return False
        target = _BACKENDS[name]
    if target.store(key, value, env=env):
        log.info("Stored %s in %s (env=%s)", key, target.name, env)
        return True
    log.error("Failed to store %s in %s", key, target.name)
    return False


def delete_credential(key: str, *, env: str = DEFAULT_ENV) -> bool:
    """Remove credential from the active backend."""
    return _active_backend().delete(key, env=env)




def _bw_custom_field_value(item: dict, field_names: set[str]) -> str | None:
    """Return the first custom-field value whose name matches field_names."""
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        return None
    wanted = {n.lower() for n in field_names}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip().lower()
        if name not in wanted:
            continue
        value = field.get("value")
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            return text_value
    return None


def _bw_fuzzy_ssh_port_field(item: dict) -> str | None:
    """Match custom fields whose names look like SSH port (e.g. 'Router SSH Port')."""
    fields = item.get("fields", [])
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip().lower()
        normalized = name.replace("_", " ").replace("-", " ")
        if "ssh" in normalized and "port" in normalized:
            value = field.get("value")
            if value is None:
                continue
            text_value = str(value).strip()
            if text_value:
                return text_value
    return None


def _bw_uri_ssh_port(item: dict) -> str | None:
    """Parse ssh://user@host:port URIs on the login item."""
    import re

    login = item.get("login")
    if not isinstance(login, dict):
        return None
    uris = login.get("uris", [])
    if not isinstance(uris, list):
        return None
    for entry in uris:
        if isinstance(entry, dict):
            uri = str(entry.get("uri", ""))
        else:
            uri = str(entry)
        match = re.search(r"(?i)^ssh://[^/]*:(\d{1,5})(?:/|$)", uri.strip())
        if match:
            return match.group(1)
    return None


def _bw_notes_ssh_port(notes: str | None) -> str | None:
    if not notes:
        return None
    import re

    patterns = (
        r"(?im)^\s*ssh[\s_-]*port\s*[:=]\s*(\d{1,5})\s*$",
        r"(?im)^\s*ssh\s*[:=]\s*(\d{1,5})\s*$",
        r"(?im)^\s*port\s*[:=]\s*(\d{1,5})\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, notes)
        if match:
            return match.group(1)
    return None


def _bw_item_host_hints(host_hint: str | None = None) -> list[str]:
    hints: list[str] = []
    for candidate in (
        host_hint,
        os.environ.get("ROUTER_HOST", "").strip() or None,
        "router.asus.com",
    ):
        if candidate and candidate not in hints:
            hints.append(candidate)
    return hints


def _bw_item_matches_host(item: dict, host: str) -> bool:
    host_l = host.strip().lower()
    if not host_l:
        return False
    name = str(item.get("name", "")).lower()
    if host_l in name:
        return True
    login = item.get("login")
    if not isinstance(login, dict):
        return False
    uris = login.get("uris", [])
    if isinstance(uris, list):
        for entry in uris:
            if isinstance(entry, dict):
                uri = str(entry.get("uri", ""))
            else:
                uri = str(entry)
            if host_l in uri.lower():
                return True
    return False


def _bw_get_item_json(title_or_id: str) -> dict | None:
    backend = _BACKENDS.get("bitwarden")
    if backend is None or not isinstance(backend, _BitwardenBackend):
        return None
    if not backend.healthy():
        return None
    result = backend._run(["get", "item", title_or_id, "--raw"])
    if result is None or result.returncode != 0:
        return None
    try:
        payload = json_loads(result.stdout or "{}")
    except JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bw_search_items(query: str) -> list[dict]:
    backend = _BACKENDS.get("bitwarden")
    if backend is None or not isinstance(backend, _BitwardenBackend):
        return []
    if not backend.healthy():
        return []
    result = backend._run(["list", "items", "--search", query])
    if result is None or result.returncode != 0:
        return []
    try:
        payload = json_loads(result.stdout or "[]")
    except JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def lookup_bitwarden_router_item(*, host_hint: str | None = None) -> dict | None:
    """Find the human Bitwarden router login item (e.g. 'router.asus.com (13Maschine)').

    Preference order:
    1. ASUSROUTERCONTROL_BW_ROUTER_ITEM exact title/id
    2. Search/list match against host hints (title or URI)
    3. Broad search for titles containing router.asus.com that have SSH Port
    """
    explicit = os.environ.get(_BW_ROUTER_ITEM_ENV, "").strip()
    if explicit:
        item = _bw_get_item_json(explicit)
        if item is not None:
            return item
        # Title search when exact get fails (common with parentheses in names).
        for candidate in _bw_search_items(explicit):
            name = str(candidate.get("name", ""))
            if name.lower() == explicit.lower() or explicit.lower() in name.lower():
                item_id = candidate.get("id")
                if isinstance(item_id, str) and item_id:
                    full = _bw_get_item_json(item_id)
                    if full is not None:
                        return full
                return candidate
        log.info("Configured BW router item %r was not found", explicit)

    for hint in _bw_item_host_hints(host_hint):
        matches: list[dict] = []
        # Search first — titles like "router.asus.com (13Maschine)" won't exact-get.
        for candidate in _bw_search_items(hint):
            if _bw_item_matches_host(candidate, hint):
                item_id = candidate.get("id")
                if isinstance(item_id, str) and item_id:
                    full = _bw_get_item_json(item_id)
                    if full is None:
                        log.warning(
                            "Bitwarden search hit id=%s name=%r but get item failed; "
                            "SSH Port custom fields may be missing from search payload",
                            item_id,
                            candidate.get("name"),
                        )
                        matches.append(candidate)
                    else:
                        matches.append(full)
                else:
                    matches.append(candidate)
        # Exact title get as secondary path
        exact = _bw_get_item_json(hint)
        if exact is not None:
            matches.append(exact)

        # Prefer an item that actually has an SSH Port custom field.
        for candidate in matches:
            if _ssh_port_from_bitwarden_item(candidate) is not None:
                return candidate
        if matches:
            return matches[0]

    # Last resort: any item titled like router.asus.com that carries SSH Port.
    for candidate in _bw_search_items("router.asus.com"):
        name = str(candidate.get("name", "")).lower()
        if "router.asus.com" not in name and "asusrouter" not in name:
            continue
        item_id = candidate.get("id")
        full = (
            _bw_get_item_json(item_id)
            if isinstance(item_id, str) and item_id
            else candidate
        ) or candidate
        if _ssh_port_from_bitwarden_item(full) is not None:
            return full
    return None


def _ssh_port_from_bitwarden_item(item: dict) -> int | None:
    raw = _bw_custom_field_value(
        item,
        {
            "ssh port",
            "ssh_port",
            "ssh-port",
            "sshport",
            "router ssh port",
            "ssh",
            # Intentionally omit bare "port" — too ambiguous on human items.
        },
    )
    if raw is None:
        raw = _bw_fuzzy_ssh_port_field(item)
    if raw is None:
        raw = _bw_uri_ssh_port(item)
    if raw is None:
        raw = _bw_notes_ssh_port(item.get("notes") if isinstance(item.get("notes"), str) else None)
    if raw is None:
        return None
    try:
        port = int(str(raw).strip())
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _credentials_from_bitwarden_item(item: dict) -> tuple[str | None, str | None]:
    login = item.get("login")
    if not isinstance(login, dict):
        return None, None
    username = login.get("username")
    password = login.get("password")
    user = str(username).strip() if isinstance(username, str) and username.strip() else None
    # Strip accidental whitespace/newlines from vault paste — they break router login.
    pw = (
        str(password).strip()
        if isinstance(password, str) and password.strip()
        else None
    )
    return user, pw


# ---------------------------------------------------------------------------
# Router-specific helpers
# ---------------------------------------------------------------------------
_ROUTER_KEYS = ("router_username", "router_password", "router_ssh_port")
_GUI_CREDENTIAL_BACKENDS = ("bitwarden", "keychain")


def _runtime_credential_env() -> str:
    return os.environ.get("ASUSROUTERCONTROL_RUNTIME_ENV", "prod")


def get_router_credentials(*, host_hint: str | None = None) -> tuple[str | None, str | None]:
    """Return (username, password) for router access using the runtime env.

    When *host_hint* is provided, prefer a matching human Bitwarden login item
    (e.g. ``router.asus.com (13Maschine)``) over stale canonical keys.
    """
    env = _runtime_credential_env()
    if host_hint:
        item = lookup_bitwarden_router_item(host_hint=host_hint)
        if item is not None:
            bw_user, bw_pass = _credentials_from_bitwarden_item(item)
            if bw_user and bw_pass:
                return bw_user, bw_pass
            # Partial BW item — fill gaps from canonical store.
            username = get_credential("router_username", env=env)
            password = get_credential("router_password", env=env)
            return bw_user or username, bw_pass or password
    username = get_credential("router_username", env=env)
    password = get_credential("router_password", env=env)
    if username and password:
        return username, password
    item = lookup_bitwarden_router_item(host_hint=host_hint)
    if item is None:
        return username, password
    bw_user, bw_pass = _credentials_from_bitwarden_item(item)
    return username or bw_user, password or bw_pass


def get_router_ssh_port(*, host_hint: str | None = None) -> int | None:
    """Return SSH port from the host-matched Bitwarden item, else canonical store.

    Human BW items such as ``router.asus.com (13Maschine)`` are preferred when
    *host_hint* is set, so a stale Keychain ``router_ssh_port=22`` cannot mask
    the item's ``SSH Port`` custom field.
    """
    if host_hint:
        item = lookup_bitwarden_router_item(host_hint=host_hint)
        if item is not None:
            from_item = _ssh_port_from_bitwarden_item(item)
            if from_item is not None:
                return from_item

    raw = get_credential("router_ssh_port", env=_runtime_credential_env())
    if raw is not None and str(raw).strip():
        try:
            port = int(str(raw).strip())
        except ValueError:
            log.warning("Ignoring non-integer router_ssh_port value from credential store")
            port = None
        else:
            if 1 <= port <= 65535:
                return port
            log.warning("Ignoring out-of-range router_ssh_port=%s from credential store", port)

    if not host_hint:
        item = lookup_bitwarden_router_item(host_hint=host_hint)
        if item is None:
            return None
        return _ssh_port_from_bitwarden_item(item)
    return None


def bitwarden_vault_status() -> str:
    """Return unlocked|locked|unauthenticated|cli_not_found|unknown for UI messaging."""
    backend = _BACKENDS.get("bitwarden")
    if backend is None or not isinstance(backend, _BitwardenBackend):
        return "unknown"
    return backend.login_check()


def resolve_connect_login_defaults(
    *,
    suggested_host: str,
    config_ssh_port: int = 22,
    preferred_backend: str | None = None,
) -> dict[str, str | int | None]:
    """Defaults for Connect Router UI / CLI, sourced from BW/Keychain when present."""
    # Ensure .env values (including BW_SESSION) are visible before vault calls.
    # GUI .app launches often have a non-repo cwd, so probe known locations.
    try:
        from dotenv import dotenv_values, load_dotenv

        candidates = [
            Path.cwd() / ".env",
            Path.home() / "ASUSRouterControl" / ".env",
            Path.home() / ".asusroutercontrol" / ".env",
            Path.home() / ".config" / "asusroutercontrol" / ".env",
        ]
        env_override = os.environ.get("ASUSROUTERCONTROL_ENV_FILE", "").strip()
        if env_override:
            candidates.insert(0, Path(env_override).expanduser())
        loaded = False
        for candidate in candidates:
            try:
                if candidate.is_file():
                    load_dotenv(dotenv_path=str(candidate), override=False)
                    # BW_SESSION must refresh after `bw unlock` without relaunch quirks.
                    values = dotenv_values(candidate)
                    session = (
                        values.get("BW_SESSION")
                        or values.get("BITWARDEN_SESSION")
                        or ""
                    ).strip()
                    if session:
                        os.environ[_BW_SESSION_ENV] = session
                    loaded = True
            except OSError:
                continue
        if not loaded:
            load_dotenv(override=False)
    except Exception:  # noqa: BLE001
        pass
    _ensure_bw_session_env()
    # Allow a later unlock to recover after an earlier "bw missing" sticky miss.
    global _bw_cli_found  # noqa: PLW0603
    if _bw_cli_found is False:
        _bw_cli_found = None

    bw_status = bitwarden_vault_status()
    item = lookup_bitwarden_router_item(host_hint=suggested_host)
    item_name = str(item.get("name")) if isinstance(item, dict) and item.get("name") else None
    item_ssh_port = _ssh_port_from_bitwarden_item(item) if item is not None else None

    username, password = get_router_credentials(host_hint=suggested_host)
    stored_port = get_router_ssh_port(host_hint=suggested_host)
    active = _active_backend_name()
    if preferred_backend in _GUI_CREDENTIAL_BACKENDS:
        backend = preferred_backend
    elif bw_status == "unlocked":
        backend = "bitwarden"
    elif active in _GUI_CREDENTIAL_BACKENDS:
        backend = active
    else:
        backend = "keychain"
    ssh_port = stored_port if stored_port is not None else int(config_ssh_port or 22)

    # Always surface vault status — SSH port prefills depend on it even when
    # the user stores new credentials to Keychain.
    detail: str | None = None
    if (
        bw_status == "unlocked"
        and item_name
        and username
        and password
        and item_ssh_port is not None
    ):
        detail = (
            f"Bitwarden: loaded login + SSH port {item_ssh_port} from '{item_name}'"
        )
    elif bw_status == "unlocked" and item_name and username and password:
        detail = (
            f"Bitwarden: loaded login from '{item_name}' "
            f"but no SSH Port custom field — using {ssh_port}. "
            "Add a custom field named 'SSH Port'."
        )
    elif bw_status == "unlocked" and item_name and item_ssh_port is not None:
        detail = f"Bitwarden: loaded SSH port {item_ssh_port} from '{item_name}'"
    elif bw_status == "unlocked" and item_name:
        detail = (
            f"Bitwarden: found '{item_name}' but no SSH Port custom field; "
            f"using port {ssh_port}"
        )
    elif bw_status == "unlocked":
        detail = (
            f"Bitwarden unlocked, but no login matched host '{suggested_host}'. "
            "Set ASUSROUTERCONTROL_BW_ROUTER_ITEM to the exact item title "
            "(e.g. router.asus.com (13Maschine))."
        )
    elif bw_status == "locked":
        detail = (
            "Bitwarden vault is locked — SSH port will stay at 22 until unlocked. "
            "In Terminal: bw unlock  → put BW_SESSION=... into .env → relaunch app"
        )
    elif bw_status == "cli_not_found":
        detail = (
            "Bitwarden CLI ('bw') not found for this app launch PATH — "
            "SSH port cannot be read from the vault."
        )
    elif bw_status == "unauthenticated":
        detail = "Bitwarden not logged in. Run: bw login"
    else:
        detail = f"Bitwarden status: {bw_status}"

    log.info(
        "Connect defaults: bw_status=%s item=%r ssh_port=%s user=%r backend=%s",
        bw_status,
        item_name,
        ssh_port,
        username,
        backend,
    )

    return {
        "host": suggested_host,
        "username": username or "admin",
        "password": password or "",
        "ssh_port": ssh_port,
        "credential_backend": backend,
        "password_from_store": bool(password),
        "bw_status": bw_status,
        "bw_item_name": item_name,
        "bw_ssh_port": item_ssh_port,
        "store_detail": detail,
    }


def store_router_credentials(
    username: str,
    password: str,
    *,
    ssh_port: int | None = None,
    env: str | None = None,
    backend: str | None = None,
) -> str:
    """Store router username/password and optional SSH port.

    Returns the backend name used. Connect UI may pass ``backend="keychain"`` or
    ``backend="bitwarden"``; CLI setup keeps the process default (Bitwarden) unless
    overridden.
    """
    resolved_env = env or _runtime_credential_env()
    target_name = (backend or _active_backend_name()).strip().lower()
    ok_user = store_credential(
        "router_username", username, env=resolved_env, backend=target_name
    )
    ok_pass = store_credential(
        "router_password", password, env=resolved_env, backend=target_name
    )
    ok_port = True
    if ssh_port is not None:
        port = int(ssh_port)
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid ssh_port: {port}")
        ok_port = store_credential(
            "router_ssh_port", str(port), env=resolved_env, backend=target_name
        )
    if not (ok_user and ok_pass and ok_port):
        raise RuntimeError(f"Failed to store router credentials in {target_name}")
    return target_name


# ---------------------------------------------------------------------------
# Migration from legacy entries
# ---------------------------------------------------------------------------

_LEGACY_KEY_MAP: dict[str, str] = {
    "router_username": "router.username",
    "router_password": "router.password",
}


def migrate_legacy_credentials(*, env: str = DEFAULT_ENV, dry_run: bool = False) -> list[str]:
    """Migrate keychain fallback entries into canonical storage.

    Returns list of migrated key names.
    """
    migrated: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        # Skip if canonical entry already exists in active backend
        if _active_backend().get(new_key, env=env):
            log.info("Skip %s — already exists in canonical store", new_key)
            continue
        # Prefer canonical keychain entry, then old legacy service.
        val = None
        if _ensure_secure_keyring_backend():
            try:
                val = keyring.get_password(_service_name(new_key, env), _account_name(new_key, env))
            except Exception:
                pass
        source = _service_name(new_key, env)
        if not val:
            if _ensure_secure_keyring_backend():
                try:
                    val = keyring.get_password(f"{_LEGACY_SERVICE}.{legacy_key}", "default")
                    source = f"{_LEGACY_SERVICE}.{legacy_key}"
                except Exception:
                    pass
        if not val:
            log.info("Skip %s — no keychain fallback entry found", new_key)
            continue
        if dry_run:
            log.info("Would migrate %s → %s", source, _service_name(new_key, env))
            migrated.append(new_key)
            continue
        if store_credential(new_key, val, env=env):
            migrated.append(new_key)
            log.info("Migrated %s → %s", source, _service_name(new_key, env))
    return migrated


def delete_legacy_credentials() -> list[str]:
    """Remove keychain fallback entries after migration to canonical storage."""
    if not _ensure_secure_keyring_backend():
        return []
    removed: list[str] = []
    for new_key, legacy_key in _LEGACY_KEY_MAP.items():
        canonical_service = _service_name(new_key)
        canonical_account = _account_name(new_key)
        try:
            keyring.delete_password(canonical_service, canonical_account)
            removed.append(canonical_service)
        except (keyring.errors.PasswordDeleteError, keyring.errors.KeyringError):
            pass
        legacy_service = f"{_LEGACY_SERVICE}.{legacy_key}"
        try:
            keyring.delete_password(legacy_service, "default")
            removed.append(legacy_service)
        except (keyring.errors.PasswordDeleteError, keyring.errors.KeyringError):
            pass
    return removed
