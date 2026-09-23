"""Secure credential management with pluggable backends: Bitwarden and macOS Keychain.

Canonical naming follows the universal-keychain convention:
  Service / vault item title: universal-keychain-asusroutercontrol-{env}-{key}
  Account metadata: asusroutercontrol.{env}.{key}

The active backend is controlled by ASUSROUTERCONTROL_CREDENTIAL_BACKEND env var
("bitwarden" | "keychain").  Defaults to Bitwarden.
When the backend is unreachable, reads fall back through Bitwarden ↔ Keychain.
A 1Password backend class remains as inert scaffold only — it is never selected
by default, never used in Connect resolution, and is ignored if requested via env.
"""

from __future__ import annotations

import abc
import logging
import os
import re
import subprocess
import time
import warnings
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
_DEFAULT_BW_ROUTER_ITEM = "router.asus.com (13Maschine)"
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
    _ensure_login_keychain_env()
    with warnings.catch_warnings():
        # keyring ignores KEYCHAIN_PATH (https://github.com/jaraco/keyring/issues/623).
        warnings.filterwarnings(
            "ignore",
            message=r".*Specified keychain is ignored.*",
            category=UserWarning,
            module=r"keyring(\..*)?",
        )
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

def _login_keychain_paths() -> list[str]:
    """Prefer the user login keychain shared by Terminal CLI and DEV.app."""
    home = Path.home()
    return [
        str(home / "Library" / "Keychains" / "login.keychain-db"),
        str(home / "Library" / "Keychains" / "login.keychain"),
    ]


def _ensure_login_keychain_env() -> None:
    """Drop KEYCHAIN_PATH — keyring ignores it (jaraco/keyring#623) and warns.

    Login-keychain targeting uses ``security`` with explicit paths only.
    """
    if "KEYCHAIN_PATH" in os.environ:
        os.environ.pop("KEYCHAIN_PATH", None)



# ---------------------------------------------------------------------------
# 1Password helpers
# ---------------------------------------------------------------------------


def _op_vault() -> str | None:
    value = os.environ.get(_OP_VAULT_ENV, "").strip()
    if value:
        return value
    return os.environ.get(_OP_VAULT_ENV_FALLBACK, "").strip() or None


_op_cli_found: bool | None = None


def _op_run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    global _op_cli_found  # noqa: PLW0603
    if _op_cli_found is False:
        return None
    try:
        result = subprocess.run(
            ["op", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        _op_cli_found = True
        return result
    except FileNotFoundError:
        # Scaffold-only path: never prompt operators to install `op`.
        if _op_cli_found is not False:
            log.debug("1Password CLI ('op') not found; scaffold backend inert")
        _op_cli_found = False
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


def _bw_session_file_candidates() -> list[Path]:
    return [
        Path.home() / ".asusroutercontrol.dev" / "bw_session",
        Path.home() / ".asusroutercontrol" / "bw_session",
        Path.home() / ".config" / "asusroutercontrol" / "bw_session",
        Path.cwd() / ".bw_session",
    ]


def _bw_session_env_file_candidates() -> list[Path]:
    """`.env` paths that may hold BW_SESSION for GUI / LaunchServices launches."""
    runtime = (
        os.environ.get("ASUSROUTERCONTROL_RUNTIME_ENV", "prod").strip().lower() or "prod"
    )
    paths: list[Path] = []
    env_override = os.environ.get("ASUSROUTERCONTROL_ENV_FILE", "").strip()
    if env_override:
        paths.append(Path(env_override).expanduser())
    if runtime != "prod":
        paths.append(Path.home() / f".asusroutercontrol.{runtime}" / ".env")
    paths.extend(
        [
            Path.home() / ".asusroutercontrol.dev" / ".env",
            Path.home() / ".asusroutercontrol" / ".env",
            Path.home() / ".config" / "asusroutercontrol" / ".env",
        ]
    )
    return paths


def _read_bw_session_from_env_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            key = key.strip()
            if key not in {_BW_SESSION_ENV, "BITWARDEN_SESSION"}:
                continue
            value = raw.strip().strip("'").strip('"')
            if value:
                return value
    except OSError:
        return None
    return None


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
    for path in _bw_session_file_candidates():
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    os.environ[_BW_SESSION_ENV] = value
                    log.debug("Loaded BW_SESSION from %s", path)
                    return
        except OSError:
            continue
    # Last resort: read synced runtime .env (Finder/.app often has empty env).
    for env_path in _bw_session_env_file_candidates():
        value = _read_bw_session_from_env_file(env_path.expanduser())
        if value:
            os.environ[_BW_SESSION_ENV] = value
            log.debug("Loaded BW_SESSION from %s", env_path)
            return


# Keychain-only secret used to unlock Bitwarden without an interactive prompt.
# Never stored in the Bitwarden vault itself (chicken-and-egg).
_BW_MASTER_PASSWORD_KEY = "bw_master_password"
# Earlier experiments / docs may have used alternate key or env names.
_BW_MASTER_PASSWORD_FALLBACK_KEYS = (
    "bw_master_password",
    "bitwarden_master_password",
    "master_password",
    "bw_password",
    "bw-master-password",
)
_BW_MASTER_PASSWORD_FALLBACK_ENVS = ("prod", "shared", "dev", "test")
# Pre-universal-keychain or ad-hoc Keychain locations (service, account).
_BW_MASTER_PASSWORD_LEGACY_LOCATIONS: tuple[tuple[str, str], ...] = (
    (f"{_LEGACY_SERVICE}.bw_master_password", "default"),
    (f"{_LEGACY_SERVICE}.bitwarden_master_password", "default"),
    (f"{_LEGACY_SERVICE}.master_password", "default"),
    (f"{_LEGACY_SERVICE}.bw_password", "default"),
    (PROJECT, "bw_master_password"),
    (PROJECT, "bitwarden_master_password"),
    (PROJECT, "master_password"),
    (PROJECT, "bw_password"),
    ("Bitwarden", "master_password"),
    ("bw", "master_password"),
    ("bitwarden-cli", "master_password"),
)
_BW_PASSWORD_ENV = "BW_PASSWORD"
_BW_SESSION_TOKEN_RE = re.compile(r'BW_SESSION="?([^"\s]+)"?')

# Last auto-unlock failure reason (never includes the master password itself).
_last_bw_unlock_error: str | None = None
# Last successful Keychain lookup path, e.g. "keyring:svc/acct" or "security:svc/acct".
_last_bw_mp_matched_path: str | None = None
# Last lookup attempt labels (no secrets) for diagnostics.
_last_bw_mp_lookups_tried: list[str] = []
# Dedupe repeated "vault locked / no MP" INFO lines during one Connect dialog.
_bw_unlock_miss_logged: bool = False

# Wrong / corrupt Keychain master password: cooldown to stop unlock spam.
_BW_WRONG_MP_COOLDOWN_SECONDS = 15 * 60
_BW_WRONG_MP_OPERATOR_HINT = (
    "Keychain master password rejected by Bitwarden (wrong or corrupt). "
    "Run: asusrouter credentials bw-master --force --set"
)
_bw_wrong_mp_cooldown_until: float = 0.0
_bw_wrong_mp_quarantined_path: str | None = None
_bw_wrong_mp_fail_logged: bool = False


def _set_bw_unlock_error(message: str | None) -> None:
    global _last_bw_unlock_error  # noqa: PLW0603
    _last_bw_unlock_error = (message or "").strip() or None


def get_last_bitwarden_unlock_error() -> str | None:
    """Return the most recent Keychain auto-unlock failure reason, if any."""
    return _last_bw_unlock_error


def get_last_bitwarden_master_password_match() -> str | None:
    """Return the Keychain path that last satisfied a master-password read."""
    return _last_bw_mp_matched_path


def get_bitwarden_master_password_quarantine() -> str | None:
    """Return the Keychain path marked bad after a wrong-MP unlock failure, if any.

    Quarantine is advisory only — the item is not deleted until the operator
    runs ``bw-master --delete`` or replaces it with ``--force --set``.
    """
    return _bw_wrong_mp_quarantined_path


def wrong_mp_cooldown_active() -> bool:
    """True while unlock retries with the same Keychain MP are suppressed."""
    return time.monotonic() < _bw_wrong_mp_cooldown_until


def _clear_wrong_mp_cooldown() -> None:
    global _bw_wrong_mp_cooldown_until, _bw_wrong_mp_quarantined_path  # noqa: PLW0603
    global _bw_wrong_mp_fail_logged  # noqa: PLW0603
    _bw_wrong_mp_cooldown_until = 0.0
    _bw_wrong_mp_quarantined_path = None
    _bw_wrong_mp_fail_logged = False


def _is_wrong_or_corrupt_mp_failure(blob: str) -> bool:
    """Detect BW crypto / wrong-password failures that should enter cooldown."""
    text = (blob or "").lower()
    if not text:
        return False
    if "decryption operation failed" in text:
        return True
    if "bitwarden_crypto" in text or "master_key" in text:
        return True
    if "invalid master password" in text or "incorrect master password" in text:
        return True
    if "wrong master password" in text or "wrong or corrupt" in text:
        return True
    if "rejected by bitwarden" in text:
        return True
    return False


def _enter_wrong_mp_cooldown(*, matched_path: str | None) -> str:
    """Quarantine the matched Keychain path and suppress unlock retries."""
    global _bw_wrong_mp_cooldown_until, _bw_wrong_mp_quarantined_path  # noqa: PLW0603
    global _bw_wrong_mp_fail_logged  # noqa: PLW0603
    path = (matched_path or _last_bw_mp_matched_path or "").strip() or None
    _bw_wrong_mp_quarantined_path = path
    _bw_wrong_mp_cooldown_until = time.monotonic() + _BW_WRONG_MP_COOLDOWN_SECONDS
    message = _BW_WRONG_MP_OPERATOR_HINT
    if path:
        message = f"{message} (matched_path={path}; not deleted — use --delete or --force --set)"
    _set_bw_unlock_error(message)
    if not _bw_wrong_mp_fail_logged:
        log.warning(
            "Bitwarden unlock via Keychain master password failed: %s "
            "(suppressing retries for %d min)",
            message,
            _BW_WRONG_MP_COOLDOWN_SECONDS // 60,
        )
        _bw_wrong_mp_fail_logged = True
    else:
        log.debug(
            "Bitwarden wrong-MP cooldown active until monotonic=%.0f path=%s",
            _bw_wrong_mp_cooldown_until,
            path,
        )
    return message


def _bw_mp_canonical_service() -> str:
    return _service_name(_BW_MASTER_PASSWORD_KEY, DEFAULT_ENV)


def _bw_mp_canonical_account() -> str:
    return _account_name(_BW_MASTER_PASSWORD_KEY, DEFAULT_ENV)



def _security_bin() -> str | None:
    fixed = Path("/usr/bin/security")
    if fixed.is_file():
        return str(fixed)
    return None


def _format_key_path(source: str, service: str, account: str | None) -> str:
    acct = "*" if account is None else account
    return f"{source}:{service}/{acct}"


def _bw_master_password_candidate_pairs() -> list[tuple[str, str]]:
    """Plausible (service, account) pairs used historically by this project / keyring."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(service: str, account: str) -> None:
        key = (service, account)
        if key in seen:
            return
        seen.add(key)
        pairs.append(key)

    # 1) Canonical universal-keychain location (prod).
    add(_bw_mp_canonical_service(), _bw_mp_canonical_account())

    # 2) Alternate env + key combinations (incl. shared/dev from earlier stores).
    for try_env in _BW_MASTER_PASSWORD_FALLBACK_ENVS:
        for try_key in _BW_MASTER_PASSWORD_FALLBACK_KEYS:
            svc = _service_name(try_key, try_env)
            add(svc, _account_name(try_key, try_env))
            add(svc, "default")
            add(svc, try_key)

    # 3) Legacy / ad-hoc pairs.
    for service, account in _BW_MASTER_PASSWORD_LEGACY_LOCATIONS:
        add(service, account)

    # 4) Common accidental variants (bare key as service, macOS username as account).
    user = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").strip()
    for try_key in _BW_MASTER_PASSWORD_FALLBACK_KEYS:
        add(try_key, "default")
        add(f"{_LEGACY_SERVICE}.{try_key}", "default")
        add(PROJECT, try_key)
        if user:
            add(_service_name(try_key, DEFAULT_ENV), user)
            add(PROJECT, user)

    return pairs


def _keyring_get_password(service: str, account: str) -> str | None:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*Specified keychain is ignored.*",
                category=UserWarning,
            )
            found = keyring.get_password(service, account)
    except Exception as exc:
        log.debug("keyring get_password %s/%s failed: %s", service, account or '""', exc)
        return None
    if isinstance(found, str) and found.strip():
        return found.strip()
    return None


def _security_get_generic_password(service: str, account: str | None) -> str | None:
    """Read via `security find-generic-password`, preferring the login keychain.

    Using the security(1) CLI reaches the same login keychain Terminal uses and
    can succeed when Python keyring misses due to ACL / app-identity differences.
    """
    security = _security_bin()
    if security is None:
        return None
    if not Path("/usr/bin/security").is_file() and security == "security":
        # Non-macOS CI: avoid spawning a missing binary repeatedly.
        return None

    base = [security, "find-generic-password", "-s", service]
    if account is not None:
        base.extend(["-a", account])
    base.append("-w")

    keychain_targets: list[str | None] = []
    for path in _login_keychain_paths():
        if Path(path).expanduser().is_file():
            keychain_targets.append(path)
    keychain_targets.append(None)  # default search list

    for keychain in keychain_targets:
        cmd = list(base)
        if keychain:
            cmd.append(keychain)
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            log.debug("security find-generic-password failed for %s: %s", service, exc)
            return None
        if result.returncode == 0:
            secret = (result.stdout or "").strip()
            if secret:
                return secret
    return None


def _security_set_generic_password(service: str, account: str, password: str) -> bool:
    """Write/update a generic password on the login keychain, allow-all apps (-A).

    -A ensures Terminal CLI and DEV.app share the same Keychain item without
    per-app ACL denials that make get_password return None.
    """
    security = _security_bin()
    if security is None or not Path("/usr/bin/security").is_file():
        return False
    keychains = [p for p in _login_keychain_paths() if Path(p).expanduser().is_file()]
    targets: list[str | None] = keychains or [None]
    ok = False
    for keychain in targets:
        del_cmd = [security, "delete-generic-password", "-s", service, "-a", account]
        add_cmd = [
            security,
            "add-generic-password",
            "-U",
            "-A",
            "-s",
            service,
            "-a",
            account,
            "-w",
            password,
        ]
        if keychain:
            del_cmd.append(keychain)
            add_cmd.append(keychain)
        try:
            subprocess.run(
                del_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                stdin=subprocess.DEVNULL,
            )
            result = subprocess.run(
                add_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            log.debug("security add-generic-password failed for %s/%s: %s", service, account, exc)
            continue
        if result.returncode == 0:
            ok = True
            break
        err = f"{result.stdout}\n{result.stderr}".strip()
        log.debug("security add-generic-password %s/%s: %s", service, account, err)
    return ok


def _normalize_bw_master_password(secret: str) -> bool:
    """Write the master password to the canonical Keychain location (security -A)."""
    backend = _BACKENDS.get("keychain")
    wrote = False
    if backend is not None:
        wrote = bool(backend.store(_BW_MASTER_PASSWORD_KEY, secret, env=DEFAULT_ENV))
    svc = _bw_mp_canonical_service()
    acct = _bw_mp_canonical_account()
    if _security_set_generic_password(svc, acct, secret):
        wrote = True
        log.info(
            "Persisted Bitwarden master password to login keychain via security at %s/%s",
            svc,
            acct,
        )
    elif _security_cli_available() and not wrote:
        log.warning(
            "Bitwarden master password normalize: security -A write failed"
        )
        return False
    return wrote


def store_bitwarden_master_password(password: str) -> bool:
    """Persist the Bitwarden master password in macOS Keychain (Keychain backend only).

    Always writes the canonical universal-keychain prod path via ``security -A``
    (required on macOS so DEV.app can unlock). Keyring is optional secondary.
    """
    secret = (password or "").strip()
    if not secret:
        return False
    _ensure_login_keychain_env()
    backend = _BACKENDS.get("keychain")
    if backend is None:
        log.error("Keychain backend unavailable — cannot store Bitwarden master password")
        return False
    # KeychainBackend.store requires security -A for bw_master_password on macOS.
    wrote_backend = bool(backend.store(_BW_MASTER_PASSWORD_KEY, secret, env=DEFAULT_ENV))
    # Also pin the known canonical pair explicitly (covers legacy path variants).
    wrote_security = _security_set_generic_password(
        _bw_mp_canonical_service(),
        _bw_mp_canonical_account(),
        secret,
    )
    security_available = _security_cli_available()
    if security_available and not (wrote_security or wrote_backend):
        log.warning(
            "Bitwarden master password security -A write failed; "
            "refusing keyring-only success"
        )
        return False
    if wrote_backend or wrote_security:
        global _last_bw_mp_matched_path  # noqa: PLW0603
        _last_bw_mp_matched_path = _format_key_path(
            "security" if wrote_security else "keyring",
            _bw_mp_canonical_service(),
            _bw_mp_canonical_account(),
        )
        _clear_wrong_mp_cooldown()
        log.info(
            "Stored Bitwarden master password at canonical %s/%s "
            "(backend=%s security=%s)",
            _bw_mp_canonical_service(),
            _bw_mp_canonical_account(),
            wrote_backend,
            wrote_security,
        )
        return True
    return False


def _discover_bitwarden_master_password() -> tuple[str | None, str | None]:
    """Search Keychain for the BW master password across historical locations.

    Returns (secret, matched_path). matched_path is like ``keyring:svc/acct`` or
    ``security:svc/acct`` (account may be ``*`` when omitted for security).
    """
    global _last_bw_mp_matched_path, _last_bw_mp_lookups_tried  # noqa: PLW0603

    _ensure_login_keychain_env()
    keyring_ok = _ensure_secure_keyring_backend()

    tried: list[str] = []
    pairs = _bw_master_password_candidate_pairs()
    log.info(
        "Bitwarden master password Keychain lookup starting (%d candidate pairs, keyring=%s)",
        len(pairs),
        "ok" if keyring_ok else "unavailable",
    )

    for service, account in pairs:
        acct_label = account if account != "" else '""'
        label = f"{service}/{acct_label}"
        if keyring_ok:
            tried.append(f"keyring:{label}")
            secret = _keyring_get_password(service, account)
            if secret:
                path = _format_key_path("keyring", service, account)
                _last_bw_mp_matched_path = path
                _last_bw_mp_lookups_tried = tried
                log.info(
                    "Bitwarden master password Keychain hit via keyring at %s/%s after %d tries",
                    service,
                    acct_label,
                    len(tried),
                )
                return secret, path

        tried.append(f"security:{label}")
        secret = _security_get_generic_password(service, account)
        if secret:
            path = _format_key_path("security", service, account)
            _last_bw_mp_matched_path = path
            _last_bw_mp_lookups_tried = tried
            log.info(
                "Bitwarden master password Keychain hit via security at %s/%s after %d tries",
                service,
                acct_label,
                len(tried),
            )
            return secret, path

    # Service-only security probes (any account) for unique services already tried.
    seen_services: set[str] = set()
    for service, _account in pairs:
        if service in seen_services:
            continue
        seen_services.add(service)
        tried.append(f"security:{service}/*")
        secret = _security_get_generic_password(service, None)
        if secret:
            path = _format_key_path("security", service, None)
            _last_bw_mp_matched_path = path
            _last_bw_mp_lookups_tried = tried
            log.info(
                "Bitwarden master password Keychain hit via security at %s/* after %d tries",
                service,
                len(tried),
            )
            return secret, path

    _last_bw_mp_matched_path = None
    _last_bw_mp_lookups_tried = tried
    preview = "; ".join(tried[:24])
    more = f" (+{len(tried) - 24} more)" if len(tried) > 24 else ""
    log.info(
        "Bitwarden master password Keychain lookup miss after %d tries: %s%s",
        len(tried),
        preview,
        more,
    )
    return None, None


def get_bitwarden_master_password() -> str | None:
    """Read the Bitwarden master password from macOS Keychain, if present.

    Searches the canonical universal-keychain location first, then alternate
    key/env names, legacy Keychain pairs, and ``security find-generic-password``
    on the login keychain. When found under a non-canonical location, normalizes
    by writing the canonical entry for future reads (CLI and DEV.app).
    """
    secret, matched = _discover_bitwarden_master_password()
    if not secret:
        return None

    canonical = _format_key_path(
        "keyring",
        _bw_mp_canonical_service(),
        _bw_mp_canonical_account(),
    )
    # Normalize when the hit was not already the canonical keyring path, or when
    # security found it (so keyring + allow-all ACL are updated for DEV.app).
    needs_normalize = matched != canonical or (matched or "").startswith("security:")
    if needs_normalize:
        if _normalize_bw_master_password(secret):
            log.info(
                "Normalized Bitwarden master password Keychain entry "
                "from %s → canonical %s/%s",
                matched,
                _bw_mp_canonical_service(),
                _bw_mp_canonical_account(),
            )
            global _last_bw_mp_matched_path  # noqa: PLW0603
            # Prefer reporting the original hit so status explains the discovery.
            _last_bw_mp_matched_path = matched
    return secret


def delete_bitwarden_master_password() -> bool:
    """Remove the Bitwarden master password from macOS Keychain (canonical + fallbacks)."""
    global _last_bw_mp_matched_path  # noqa: PLW0603
    backend = _BACKENDS.get("keychain")
    if backend is None:
        return False
    _ensure_login_keychain_env()
    keyring_ok = _ensure_secure_keyring_backend()
    removed = False
    security = _security_bin()
    pairs = _bw_master_password_candidate_pairs()
    if keyring_ok:
        for service, account in pairs:
            try:
                keyring.delete_password(service, account)
                removed = True
            except Exception:
                continue
    if security and Path("/usr/bin/security").is_file():
        for service, account in pairs:
            for keychain in [p for p in _login_keychain_paths() if Path(p).is_file()] + [None]:
                cmd = [security, "delete-generic-password", "-s", service, "-a", account]
                if keychain:
                    cmd.append(keychain)
                try:
                    result = subprocess.run(
                        cmd,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        stdin=subprocess.DEVNULL,
                    )
                except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0:
                    removed = True
    if removed:
        global _last_bw_mp_matched_path  # noqa: PLW0603
        _last_bw_mp_matched_path = None
        _clear_wrong_mp_cooldown()
    return removed


def bitwarden_unlock_status(*, attempt_unlock: bool = False) -> dict[str, str | bool | int | None]:
    """Diagnostics for Keychain MP + vault state.

    When attempt_unlock is True, runs ensure_bitwarden_unlocked() first so
    last_unlock_error reflects the latest auto-unlock attempt.
    """
    vault: str
    if attempt_unlock:
        vault = ensure_bitwarden_unlocked()
    else:
        backend = _BACKENDS.get("bitwarden")
        if backend is None or not isinstance(backend, _BitwardenBackend):
            vault = "unknown"
        else:
            vault = backend.login_check()

    secret = get_bitwarden_master_password()
    matched = get_last_bitwarden_master_password_match() if secret else None
    quarantined = get_bitwarden_master_password_quarantine()
    cooldown = wrong_mp_cooldown_active()
    remaining = 0
    if cooldown:
        remaining = max(0, int(_bw_wrong_mp_cooldown_until - time.monotonic()))
    return {
        "master_password_stored": secret is not None,
        "master_password_matched_path": matched,
        "master_password_quarantined_path": quarantined,
        "wrong_mp_cooldown_active": cooldown,
        "wrong_mp_cooldown_remaining_seconds": remaining if cooldown else 0,
        "master_password_canonical": (
            f"{_bw_mp_canonical_service()}/{_bw_mp_canonical_account()}"
        ),
        "lookups_tried": len(_last_bw_mp_lookups_tried),
        "vault_status": vault,
        "last_unlock_error": get_last_bitwarden_unlock_error(),
        "bw_session_present": bool(os.environ.get(_BW_SESSION_ENV, "").strip()),
        # KEYCHAIN_PATH is intentionally unused (keyring#623); security -A only.
        "keychain_path": None,
    }


def _persist_bw_session(session: str) -> None:
    """Write BW_SESSION into process env, session files, and runtime .env files."""
    token = (session or "").strip()
    if not token:
        return
    os.environ[_BW_SESSION_ENV] = token
    # Persist under home dirs only — never pollute the process cwd.
    for session_path in _bw_session_file_candidates():
        try:
            if session_path.resolve() == (Path.cwd() / ".bw_session").resolve():
                continue
        except OSError:
            if session_path.name == ".bw_session" and session_path.parent == Path.cwd():
                continue
        try:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(token + "\n", encoding="utf-8")
            try:
                session_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            log.debug(
                "Could not persist BW_SESSION file %s", session_path, exc_info=True
            )
    targets = [
        Path.home() / ".asusroutercontrol.dev" / ".env",
        Path.home() / ".asusroutercontrol" / ".env",
    ]
    for env_path in targets:
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = []
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    key = line.split("=", 1)[0].strip() if "=" in line else ""
                    if key in {_BW_SESSION_ENV, "BITWARDEN_SESSION"}:
                        continue
                    lines.append(line)
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(f"{_BW_SESSION_ENV}={token}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            log.debug("Could not persist BW_SESSION to %s", env_path, exc_info=True)


def _parse_bw_session_token(stdout: str, stderr: str = "") -> str:
    """Extract a BW_SESSION token from `bw unlock --raw` (or export-style) output."""
    lines = [line.strip() for line in (stdout or "").strip().splitlines() if line.strip()]
    token = lines[-1] if lines else ""
    match = _BW_SESSION_TOKEN_RE.search(token)
    if match:
        return match.group(1)
    # Bare --raw token: no whitespace. Reject multi-word prompt/help lines.
    if token and " " not in token:
        return token
    match = _BW_SESSION_TOKEN_RE.search(f"{stdout}\n{stderr}")
    return match.group(1) if match else ""


def _classify_bw_unlock_failure(stderr: str, stdout: str = "") -> str:
    """Map bw unlock stderr/stdout into a short operator-facing reason."""
    blob = f"{stdout}\n{stderr}".strip()
    lower = blob.lower()
    if not blob:
        return "bw unlock failed with no error output"
    if _is_wrong_or_corrupt_mp_failure(blob):
        return _BW_WRONG_MP_OPERATOR_HINT
    if ("keychain" in lower and "locked" in lower) or "mac os keychain" in lower:
        return "macOS Keychain locked or unavailable"
    if "session key is invalid" in lower or "invalid session" in lower:
        return "invalid BW_SESSION"
    if "not logged in" in lower or "unauthenticated" in lower:
        return "Bitwarden not logged in (run: bw login)"
    # Keep a truncated raw snippet for unexpected failures (never the password).
    raw = blob.replace("\n", " ")
    return f"bw unlock failed: {raw[:180]}"


def ensure_bitwarden_unlocked() -> str:
    """Unlock Bitwarden using Keychain master password when the vault is locked.

    Never prompts on stdin. Returns the post-attempt vault status string from
    login_check(). Records a human-readable failure via get_last_bitwarden_unlock_error().

    Always reloads synced runtime ``.env`` / session files first so DEV.app Connect
    picks up a Terminal ``bw_sync`` session even when LaunchServices has no env.

    After a wrong/corrupt Keychain master password (crypto decryption failure),
    skips further unlock attempts for ``_BW_WRONG_MP_COOLDOWN_SECONDS`` and
    deduplicates the warning log. Connect can still proceed via Keychain-mirrored
    router credentials while the vault stays locked.
    """
    # load_runtime_env_files is defined later; resolve at call time.
    try:
        load_runtime_env_files()
    except Exception:  # noqa: BLE001 — unlock must stay non-fatal
        _ensure_bw_session_env()

    backend = _BACKENDS.get("bitwarden")
    if backend is None or not isinstance(backend, _BitwardenBackend):
        _set_bw_unlock_error("Bitwarden backend unavailable")
        return "unknown"
    global _bw_unlock_miss_logged  # noqa: PLW0603

    state = backend.login_check()
    if state == "unlocked":
        _set_bw_unlock_error(None)
        _bw_unlock_miss_logged = False
        _clear_wrong_mp_cooldown()
        return state
    if state != "locked":
        if state == "cli_not_found":
            _set_bw_unlock_error("Bitwarden CLI ('bw') not found on PATH")
        elif state == "unauthenticated":
            _set_bw_unlock_error("Bitwarden not logged in (run: bw login)")
        else:
            _set_bw_unlock_error(f"Bitwarden vault status: {state}")
        return state

    if wrong_mp_cooldown_active():
        msg = get_last_bitwarden_unlock_error() or _BW_WRONG_MP_OPERATOR_HINT
        _set_bw_unlock_error(msg)
        log.debug(
            "Skipping Bitwarden unlock (wrong-MP cooldown; vault locked). %s",
            msg,
        )
        return "locked"

    master = get_bitwarden_master_password()
    if not master:
        msg = (
            "Bitwarden vault locked and no master password in Keychain "
            "(or Keychain denied this app). "
            "In Terminal: asusrouter credentials bw-master --set && "
            "bash scripts/bw_sync_router_env.sh"
        )
        _set_bw_unlock_error(msg)
        # Connect / get_credential can call this many times; log once at INFO.
        if not _bw_unlock_miss_logged:
            log.info(msg)
            _bw_unlock_miss_logged = True
        else:
            log.debug(msg)
        return state

    result = _bw_run(
        ["unlock", "--passwordenv", _BW_PASSWORD_ENV, "--raw"],
        extra_env={_BW_PASSWORD_ENV: master},
    )
    if result is None:
        _set_bw_unlock_error("Bitwarden CLI ('bw') not found on PATH")
        return "cli_not_found"
    if result.returncode != 0:
        raw_blob = f"{result.stdout or ''}\n{result.stderr or ''}"
        reason = _classify_bw_unlock_failure(result.stderr or "", result.stdout or "")
        if _is_wrong_or_corrupt_mp_failure(raw_blob) or _is_wrong_or_corrupt_mp_failure(
            reason
        ):
            _enter_wrong_mp_cooldown(matched_path=_last_bw_mp_matched_path)
        else:
            _set_bw_unlock_error(reason)
            log.warning(
                "Bitwarden unlock via Keychain master password failed: %s", reason
            )
        return "locked"
    token = _parse_bw_session_token(result.stdout or "", result.stderr or "")
    if not token:
        reason = "bw unlock succeeded but no session token was parsed"
        _set_bw_unlock_error(reason)
        log.warning(reason)
        return "locked"
    _persist_bw_session(token)
    state = backend.login_check()
    if state == "unlocked":
        _set_bw_unlock_error(None)
        _bw_unlock_miss_logged = False
        _clear_wrong_mp_cooldown()
        log.info("Bitwarden vault unlocked via Keychain master password")
    else:
        _set_bw_unlock_error(
            f"bw unlock produced a session but vault still reports: {state}"
        )
    return state


def _bw_run(
    arguments: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Execute a Bitwarden CLI command, returning None if bw is unavailable.

    stdin is always DEVNULL so menubar/GUI never blocks on interactive prompts
    (e.g. accidental `bw unlock` without --passwordenv).
    """
    global _bw_cli_found  # noqa: PLW0603
    _ensure_bw_session_env()
    env = os.environ.copy()
    # Ensure Homebrew paths exist for menubar / .app launches.
    path_parts = env.get("PATH", "").split(":") if env.get("PATH") else []
    for extra in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")):
        if extra not in path_parts:
            path_parts.insert(0, extra)
    env["PATH"] = ":".join(p for p in path_parts if p)
    if extra_env:
        env.update(extra_env)

    last_error: Exception | None = None
    for binary in _bw_binaries():
        try:
            result = subprocess.run(
                [binary, *arguments],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                stdin=subprocess.DEVNULL,
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


# Secrets that DEV.app must read via security(1) ACL (-A). Keyring-only writes
# silently strand the GUI with a blank password / locked vault.
_KEYS_REQUIRE_SECURITY = frozenset({"router_password", "bw_master_password"})


def _security_cli_available() -> bool:
    return bool(_security_bin() and Path("/usr/bin/security").is_file())


class _KeychainBackend(_CredentialBackend):
    @property
    def name(self) -> str:
        return "keychain"

    def get(self, key: str, *, env: str = DEFAULT_ENV) -> str | None:
        """Read via keyring, then ``security find-generic-password``.

        DEV.app often cannot read Python-keyring ACL items that Terminal wrote;
        the security(1) path reaches the same login keychain Terminal uses.
        """
        _ensure_login_keychain_env()
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        if _ensure_secure_keyring_backend():
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r".*Specified keychain is ignored.*",
                        category=UserWarning,
                    )
                    found = keyring.get_password(svc, acct)
            except Exception as exc:
                log.error("Failed to read keychain entry %s/%s: %s", svc, acct, exc)
                found = None
            if isinstance(found, str) and found.strip():
                return found.strip()
        secret = _security_get_generic_password(svc, acct)
        if secret:
            log.info(
                "Keychain credential %s resolved via security CLI at %s/%s (env=%s)",
                key,
                svc,
                acct,
                env,
            )
            return secret
        return None

    def store(self, key: str, value: str, *, env: str = DEFAULT_ENV) -> bool:
        """Write via ``security -A`` first (required for passwords), keyring secondary.

        For ``router_password`` / ``bw_master_password`` on macOS, success requires
        the security(1) write — keyring-only ACL items are invisible to DEV.app.
        """
        _ensure_login_keychain_env()
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        require_security = key in _KEYS_REQUIRE_SECURITY
        security_available = _security_cli_available()

        # Prefer security delete → add -A first so Terminal + DEV.app share ACL.
        wrote_security = _security_set_generic_password(svc, acct, value)
        if wrote_security:
            log.info(
                "Persisted Keychain credential %s via security -A at %s/%s (env=%s)",
                key,
                svc,
                acct,
                env,
            )
        elif require_security and security_available:
            log.warning(
                "Keychain security -A write failed for %s (env=%s); "
                "refusing keyring-only success (DEV.app would not read it)",
                key,
                env,
            )

        wrote_keyring = False
        if _ensure_secure_keyring_backend():
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r".*Specified keychain is ignored.*",
                        category=UserWarning,
                    )
                    keyring.set_password(svc, acct, value)
                wrote_keyring = True
            except Exception as exc:
                log.error("Failed to write keychain entry %s/%s: %s", svc, acct, exc)

        if require_security and security_available:
            return bool(wrote_security)

        if wrote_security or wrote_keyring:
            return True
        log.error("Failed to store Keychain credential %s (env=%s)", key, env)
        return False

    def delete(self, key: str, *, env: str = DEFAULT_ENV) -> bool:
        _ensure_login_keychain_env()
        svc = _service_name(key, env)
        acct = _account_name(key, env)
        removed = False
        if _ensure_secure_keyring_backend():
            try:
                keyring.delete_password(svc, acct)
                removed = True
            except Exception as exc:
                log.error("Failed to delete keychain entry %s/%s: %s", svc, acct, exc)
        security = _security_bin()
        if security and Path("/usr/bin/security").is_file():
            for keychain in [p for p in _login_keychain_paths() if Path(p).is_file()] + [
                None
            ]:
                cmd = [
                    security,
                    "delete-generic-password",
                    "-s",
                    svc,
                    "-a",
                    acct,
                ]
                if keychain:
                    cmd.append(keychain)
                try:
                    result = subprocess.run(
                        cmd,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        stdin=subprocess.DEVNULL,
                    )
                except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0:
                    removed = True
        return removed


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
        state = ensure_bitwarden_unlocked()
        if state == "unlocked":
            return True
        if state == "cli_not_found":
            log.info("Bitwarden backend unhealthy: CLI not found.")
        elif state == "locked":
            if wrong_mp_cooldown_active():
                log.debug(
                    "Bitwarden backend unhealthy: vault locked (wrong-MP cooldown). %s",
                    get_last_bitwarden_unlock_error() or _BW_WRONG_MP_OPERATOR_HINT,
                )
            else:
                log.info(
                    "Bitwarden backend unhealthy: vault is locked. "
                    "Store the master password with: asusrouter credentials bw-master --set"
                )
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
        state = ensure_bitwarden_unlocked()
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

# Live credential backends used by Connect / CLI / menubar.
_ACTIVE_BACKENDS = frozenset({"bitwarden", "keychain"})

# 1Password remains registered only as inert scaffold (tests / dead code path).
_SCAFFOLD_BACKENDS = frozenset({"1password"})

_BACKENDS: dict[str, _CredentialBackend] = {
    "1password": _OnePasswordBackend(),
    "keychain": _KeychainBackend(),
    "bitwarden": _BitwardenBackend(),
}

_READ_FALLBACK_ORDER = ["bitwarden", "keychain"]
_WRITE_FALLBACK_ORDER = ["bitwarden", "keychain"]


def _active_backend_name() -> str:
    raw = os.environ.get(_CREDENTIAL_BACKEND_ENV, "bitwarden").strip().lower()
    if raw in _ACTIVE_BACKENDS:
        return raw
    if raw in _SCAFFOLD_BACKENDS:
        log.warning(
            "Credential backend '%s' is scaffold-only and not used; "
            "falling back to bitwarden",
            raw,
        )
        return "bitwarden"
    if raw:
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

    # Legacy keychain fallback (read-only) — keyring then security CLI.
    legacy_key = key.replace("_", ".")
    legacy_svc = f"{_LEGACY_SERVICE}.{legacy_key}"
    if _ensure_secure_keyring_backend():
        try:
            val = keyring.get_password(legacy_svc, "default")
            if val:
                log.debug("Credential '%s' resolved from legacy keychain entry", key)
                return val
        except Exception:
            pass
    legacy_via_security = _security_get_generic_password(legacy_svc, "default")
    if legacy_via_security:
        log.info(
            "Credential '%s' resolved from legacy Keychain via security CLI",
            key,
        )
        return legacy_via_security

    # Environment variable fallback (non-secret)
    return os.environ.get(key.upper())


def store_credential(
    key: str,
    value: str,
    *,
    env: str = DEFAULT_ENV,
    backend: str | None = None,
) -> bool:
    """Store credential in the active backend (or an explicit live backend)."""
    if backend is None:
        target = _active_backend()
    else:
        name = backend.strip().lower()
        if name in _SCAFFOLD_BACKENDS:
            log.error(
                "Credential backend '%s' is scaffold-only and cannot be used for writes",
                name,
            )
            return False
        if name not in _ACTIVE_BACKENDS:
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
    1. ASUSROUTERCONTROL_BW_ROUTER_ITEM exact title/id (defaults to lab item)
    2. Search/list match against host hints (title or URI)
    3. Broad search for titles containing router.asus.com that have SSH Port
    """
    ensure_bitwarden_unlocked()

    explicit = (
        os.environ.get(_BW_ROUTER_ITEM_ENV, "").strip() or _DEFAULT_BW_ROUTER_ITEM
    )
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
        # Also try search by the stable host fragment.
        for candidate in _bw_search_items("router.asus.com"):
            name = str(candidate.get("name", ""))
            if "13maschine" in name.lower() or name.lower() == explicit.lower():
                item_id = candidate.get("id")
                if isinstance(item_id, str) and item_id:
                    full = _bw_get_item_json(item_id)
                    if full is not None:
                        log.info("Resolved BW router item via search: %s", full.get("name"))
                        return full
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


def _env_router_username() -> str | None:
    return (
        os.environ.get("ASUSROUTERCONTROL_ROUTER_USERNAME", "").strip()
        or os.environ.get("ROUTER_USERNAME", "").strip()
        or None
    )


def _env_router_ssh_port() -> int | None:
    """SSH port from env (bw_sync / .env), if set and valid."""
    for key in ("ASUSROUTERCONTROL_ROUTER_SSH_PORT", "SSH_PORT"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            log.warning("Ignoring non-integer %s=%r", key, raw)
            continue
        if 1 <= port <= 65535:
            return port
        log.warning("Ignoring out-of-range %s=%s", key, port)
    return None


def _runtime_env_file_candidates() -> list[Path]:
    """Ordered .env paths for GUI/CLI — runtime-scoped data dir beats prod."""
    runtime = (
        os.environ.get("ASUSROUTERCONTROL_RUNTIME_ENV", "prod").strip().lower() or "prod"
    )
    candidates: list[Path] = []
    env_override = os.environ.get("ASUSROUTERCONTROL_ENV_FILE", "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())
    # Prefer the active runtime's data-dir .env so prod cannot overwrite DEV.
    if runtime == "prod":
        candidates.append(Path.home() / ".asusroutercontrol" / ".env")
    else:
        candidates.append(Path.home() / f".asusroutercontrol.{runtime}" / ".env")
        if runtime != "dev":
            candidates.append(Path.home() / ".asusroutercontrol.dev" / ".env")
    candidates.extend(
        [
            Path.cwd() / ".env",
            Path.home() / "ASUSRouterControl" / ".env",
            Path.home() / ".asusroutercontrol.dev" / ".env",
            Path.home() / ".asusroutercontrol" / ".env",
            Path.home() / ".config" / "asusroutercontrol" / ".env",
        ]
    )
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        try:
            key = path.expanduser().resolve()
        except OSError:
            key = path.expanduser()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path.expanduser())
    return ordered


def load_runtime_env_files() -> None:
    """Load .env for GUI launches (non-repo cwd) and refresh BW_SESSION.

    Higher-priority files win for forced keys (session, router username, SSH
    port, BW item). Later prod ``~/.asusroutercontrol/.env`` must not clobber
    values already taken from ``~/.asusroutercontrol.dev/.env``.
    """
    try:
        from dotenv import dotenv_values, load_dotenv

        forced_keys = (
            "BW_SESSION",
            "BITWARDEN_SESSION",
            "ASUSROUTERCONTROL_ROUTER_USERNAME",
            "ROUTER_USERNAME",
            "ASUSROUTERCONTROL_ROUTER_SSH_PORT",
            "SSH_PORT",
            "ASUSROUTERCONTROL_BW_ROUTER_ITEM",
        )
        applied: set[str] = set()
        loaded = False
        for candidate in _runtime_env_file_candidates():
            try:
                if not candidate.is_file():
                    continue
                load_dotenv(dotenv_path=str(candidate), override=False)
                values = dotenv_values(candidate)
                for key in forced_keys:
                    if key in applied:
                        continue
                    value = (values.get(key) or "").strip()
                    if not value:
                        continue
                    if key in ("BW_SESSION", "BITWARDEN_SESSION"):
                        os.environ[_BW_SESSION_ENV] = value
                        applied.add("BW_SESSION")
                        applied.add("BITWARDEN_SESSION")
                    else:
                        os.environ[key] = value
                        applied.add(key)
                # Mirror lab sync key into SSH_PORT for Config overlay.
                if (
                    "ASUSROUTERCONTROL_ROUTER_SSH_PORT" in applied
                    and "SSH_PORT" not in applied
                    and not os.environ.get("SSH_PORT", "").strip()
                ):
                    os.environ["SSH_PORT"] = os.environ[
                        "ASUSROUTERCONTROL_ROUTER_SSH_PORT"
                    ]
                    applied.add("SSH_PORT")
                loaded = True
            except OSError:
                continue
        if not loaded:
            load_dotenv(override=False)
    except Exception:  # noqa: BLE001
        pass
    _ensure_bw_session_env()


def get_router_credentials(*, host_hint: str | None = None) -> tuple[str | None, str | None]:
    """Return (username, password) for router access using the runtime env.

    When *host_hint* is provided, prefer a matching human Bitwarden login item
    (e.g. ``router.asus.com (13Maschine)``) over stale canonical keys.
    ``ASUSROUTERCONTROL_ROUTER_USERNAME`` always overrides a stored username
    (avoids stale Keychain ``admin`` after the router login name was renamed).
    """
    load_runtime_env_files()
    env = _runtime_credential_env()
    username: str | None = None
    password: str | None = None
    source = "none"
    if host_hint:
        item = lookup_bitwarden_router_item(host_hint=host_hint)
        if item is not None:
            bw_user, bw_pass = _credentials_from_bitwarden_item(item)
            if bw_user and bw_pass:
                username, password = bw_user, bw_pass
                source = f"bitwarden-item:{item.get('name') or 'unknown'}"
            else:
                # Partial BW item — fill gaps from canonical store.
                username = bw_user or get_credential("router_username", env=env)
                password = bw_pass or get_credential("router_password", env=env)
                source = "bitwarden-item+store"
    if username is None and password is None:
        username = get_credential("router_username", env=env)
        password = get_credential("router_password", env=env)
        if username or password:
            source = f"credential-store(env={env})"
        if not (username and password):
            item = lookup_bitwarden_router_item(host_hint=host_hint)
            if item is not None:
                bw_user, bw_pass = _credentials_from_bitwarden_item(item)
                username = username or bw_user
                password = password or bw_pass
                if bw_user or bw_pass:
                    source = f"bitwarden-item-fallback:{item.get('name') or 'unknown'}"

    env_user = _env_router_username()
    if env_user:
        if username and username != env_user:
            log.info(
                "Overriding stored username %r with env router username %r",
                username,
                env_user,
            )
        username = env_user
        if source == "none":
            source = "env-username"
        else:
            source = f"{source}+env-username"

    log.info(
        "Router credentials resolved: source=%s user=%r password=%s",
        source,
        username or None,
        "present" if password else "missing",
    )
    return username, password


def resolve_blank_connect_password(
    *,
    host: str,
    username: str,
    password: str,
) -> tuple[str, str, str]:
    """Fill blank Connect password/username from BW/Keychain after unlock.

    Returns (username, password, source_label). Never logs secret values.
    Also replaces a stale ``admin`` username when env/store has the lab login name.
    """
    load_runtime_env_files()
    resolved_user = (username or "").strip()
    resolved_pass = (password or "").strip()
    source = "dialog"

    store_user, store_pass = get_router_credentials(host_hint=host or None)
    env_user = _env_router_username()

    if not resolved_pass and store_pass:
        resolved_pass = store_pass
        source = "store"
        log.info("Connect password filled from credential store (host=%s)", host)

    if env_user and (
        not resolved_user or resolved_user.lower() in {"admin", "asus", "root"}
    ):
        if resolved_user and resolved_user != env_user:
            log.info(
                "Connect replacing stale username %r with env %r",
                resolved_user,
                env_user,
            )
        resolved_user = env_user
        source = f"{source}+env-username"
    elif not resolved_user and store_user:
        resolved_user = store_user
        source = f"{source}+store-username"

    return resolved_user, resolved_pass, source


def missing_mirrored_password_message(*, bw_status: str | None = None) -> str:
    """Explicit fail-fast copy when vault is locked and Keychain mirror is empty."""
    status = (bw_status or bitwarden_vault_status() or "unknown").strip()
    return (
        f"Bitwarden vault is {status} and no Keychain-mirrored router password "
        "was found. Do not retry LOGIN with a blank password (Captcha risk). "
        "Run: bash scripts/bw_sync_router_env.sh && "
        "asusrouter credentials bw-master --set"
    )


def assert_connect_password_ready(
    password: str,
    *,
    host: str | None = None,
) -> None:
    """Fail fast before HTTP LOGIN when vault is locked and mirror is missing.

    Prevents blank-password Captcha bait against the router admin endpoint.
    """
    if (password or "").strip():
        return
    load_runtime_env_files()
    bw_status = bitwarden_vault_status()
    if bw_status == "unlocked":
        # Vault unlocked but password still empty — store/item miss, not bait.
        raise ConnectionError(
            "Router password is empty after credential resolve. "
            "Enter the admin password or set ASUSROUTERCONTROL_BW_ROUTER_ITEM."
        )
    _, store_pass = get_router_credentials(host_hint=host)
    if store_pass:
        # Caller should have filled from store; treat as programming/order bug.
        raise ConnectionError(
            "Router password is empty but Keychain/store has a mirrored secret — "
            "retry Connect (or leave password blank to reuse the mirror)."
        )
    raise ConnectionError(missing_mirrored_password_message(bw_status=bw_status))


def get_router_ssh_port(*, host_hint: str | None = None) -> int | None:
    """Return SSH port from Bitwarden item, env, or canonical store.

    Precedence: host-matched BW ``SSH Port`` field →
    ``ASUSROUTERCONTROL_ROUTER_SSH_PORT`` / ``SSH_PORT`` env →
    Keychain/BW canonical ``router_ssh_port`` → BW item without host hint.

    Human BW items such as ``router.asus.com (13Maschine)`` are preferred when
    *host_hint* is set, so a stale Keychain ``router_ssh_port=22`` cannot mask
    the item's ``SSH Port`` custom field.
    """
    load_runtime_env_files()
    if host_hint:
        item = lookup_bitwarden_router_item(host_hint=host_hint)
        if item is not None:
            from_item = _ssh_port_from_bitwarden_item(item)
            if from_item is not None:
                return from_item

    env_port = _env_router_ssh_port()
    if env_port is not None:
        return env_port

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


def format_connect_failure_detail(
    exc: BaseException,
    *,
    host: str | None = None,
    username: str | None = None,
    ssh_port: int | None = None,
    password_present: bool | None = None,
) -> str:
    """Augment Connect exceptions with Bitwarden / Keychain context for the UI."""
    detail = str(exc).strip() or exc.__class__.__name__
    extras: list[str] = []
    if host:
        extras.append(f"host={host}")
    if username:
        extras.append(f"user={username!r}")
    if ssh_port is not None:
        extras.append(f"ssh_port={ssh_port}")
    if password_present is not None:
        extras.append(f"password={'set' if password_present else 'missing'}")
    try:
        vault = bitwarden_vault_status()
    except Exception:  # noqa: BLE001
        vault = "unknown"
    if vault != "unlocked":
        extras.append(f"Bitwarden vault: {vault}")
    unlock_err = get_last_bitwarden_unlock_error()
    if unlock_err:
        extras.append(f"Unlock: {unlock_err}")
    mp_match = get_last_bitwarden_master_password_match()
    if vault == "locked" and not unlock_err:
        extras.append(
            "Keychain master password missing — run: "
            "asusrouter credentials bw-master --set"
        )
    elif mp_match and vault == "locked":
        extras.append(f"MP Keychain path={mp_match}")
    if extras:
        detail = f"{detail}\n" + " | ".join(extras)
    return detail


def log_connect_event(message: str, *args: object) -> None:
    """INFO log for Connect diagnostics (never pass secrets as args)."""
    log.info(message, *args)


def mirror_router_login_to_keychain(
    username: str,
    password: str,
    *,
    ssh_port: int | None = None,
    env: str | None = None,
) -> str:
    """Copy BW-sourced login into Keychain for GUI Connect when vault unlock fails.

    Used by ``bw_sync_router_env.sh`` so DEV.app can reuse the synced password
    without needing an interactive Bitwarden unlock inside the GUI process.

    Writes the active/runtime env and also ``prod`` so a mismatched
    ``ASUSROUTERCONTROL_RUNTIME_ENV`` cannot strand the mirrored password.
    Stores via KeychainBackend (``security -A`` required for password) so DEV.app
    can read the mirrored secret. Raises ``RuntimeError`` on any store failure.
    """
    if not (username or "").strip() or not (password or "").strip():
        raise RuntimeError(
            "Cannot mirror empty router username/password to Keychain"
        )
    resolved_env = (env or _runtime_credential_env() or DEFAULT_ENV).strip().lower()
    targets: list[str] = []
    for candidate in (resolved_env, "prod", "shared"):
        if candidate and candidate not in targets:
            targets.append(candidate)
    last_backend = "keychain"
    for target_env in targets:
        try:
            last_backend = store_router_credentials(
                username,
                password,
                ssh_port=ssh_port,
                env=target_env,
                backend="keychain",
            )
        except Exception as exc:
            log.warning(
                "Keychain mirror failed for env=%s user=%r: %s",
                target_env,
                username,
                exc.__class__.__name__,
            )
            raise RuntimeError(
                f"Keychain mirror failed for env={target_env}: {exc}"
            ) from exc
        log.info(
            "Mirrored router login to Keychain env=%s user=%r ssh_port=%s "
            "(password present, length omitted)",
            target_env,
            username,
            ssh_port,
        )
    return last_backend


def resolve_connect_login_defaults(
    *,
    suggested_host: str,
    config_ssh_port: int = 22,
    preferred_backend: str | None = None,
) -> dict[str, str | int | None]:
    """Defaults for Connect Router UI / CLI, sourced from BW/Keychain when present."""
    load_runtime_env_files()
    # Allow a later unlock to recover after an earlier "bw missing" sticky miss.
    global _bw_cli_found  # noqa: PLW0603
    if _bw_cli_found is False:
        _bw_cli_found = None

    bw_status = ensure_bitwarden_unlocked()
    item = lookup_bitwarden_router_item(host_hint=suggested_host)
    item_name = str(item.get("name")) if isinstance(item, dict) and item.get("name") else None
    item_ssh_port = _ssh_port_from_bitwarden_item(item) if item is not None else None

    username, password = get_router_credentials(host_hint=suggested_host)
    stored_port = get_router_ssh_port(host_hint=suggested_host)
    # Env override already applied inside get_router_credentials.
    resolved_username = username or ""
    if preferred_backend in _GUI_CREDENTIAL_BACKENDS:
        backend = preferred_backend
    elif bw_status == "unlocked":
        backend = "bitwarden"
    else:
        # Vault locked / CLI missing / unauthenticated: never default Store-in
        # to bitwarden — typed + Keychain-mirrored logins must land in Keychain.
        backend = "keychain"
    ssh_port = stored_port if stored_port is not None else int(config_ssh_port or 22)

    # Always surface vault status — SSH port prefills depend on it even when
    # the user stores new credentials to Keychain.
    detail: str | None = None
    if (
        bw_status == "unlocked"
        and item_name
        and resolved_username
        and password
        and item_ssh_port is not None
    ):
        detail = (
            f"Bitwarden: loaded login + SSH port {item_ssh_port} from '{item_name}'"
        )
    elif bw_status == "unlocked" and item_name and resolved_username and password:
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
    elif bw_status == "locked" and password and resolved_username:
        unlock_err = get_last_bitwarden_unlock_error()
        detail = (
            f"Bitwarden vault locked — using Keychain-mirrored login "
            f"user={resolved_username!r} ssh_port={ssh_port}. "
            "Leave password blank to reuse the mirrored admin password."
        )
        if unlock_err:
            detail = f"{detail} ({unlock_err})"
    elif bw_status == "locked":
        unlock_err = get_last_bitwarden_unlock_error()
        detail = (
            "Bitwarden vault is locked — Connect needs an unlocked vault or a "
            "Keychain password synced via: bash scripts/bw_sync_router_env.sh. "
            "One-time: asusrouter credentials bw-master --set. "
            "Router Login Name may not be 'admin'."
        )
        if unlock_err:
            detail = f"{detail} ({unlock_err})"
    elif bw_status == "cli_not_found":
        detail = (
            "Bitwarden CLI ('bw') not found for this app launch PATH — "
            "SSH port cannot be read from the vault."
        )
        if password and resolved_username:
            detail = (
                f"{detail} Using Keychain-mirrored login "
                f"user={resolved_username!r} ssh_port={ssh_port}."
            )
    elif bw_status == "unauthenticated":
        detail = "Bitwarden not logged in. Run: bw login"
    else:
        detail = f"Bitwarden status: {bw_status}"

    if not resolved_username:
        extra = (
            "Enter the Router Login Name from Administration → System "
            "(not always 'admin')."
        )
        detail = f"{detail} {extra}" if detail else extra

    log.info(
        "Connect defaults: bw_status=%s item=%r ssh_port=%s user=%r "
        "password=%s backend=%s",
        bw_status,
        item_name,
        ssh_port,
        resolved_username or None,
        "present" if password else "missing",
        backend,
    )

    return {
        "host": suggested_host,
        "username": resolved_username,
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
    if target_name in _SCAFFOLD_BACKENDS or target_name not in _ACTIVE_BACKENDS:
        target_name = "bitwarden"
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
