"""Live router model resolution for UI (no hardcoded SKUs)."""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_MODEL_RE = re.compile(r"^RT-[A-Z0-9]+", re.IGNORECASE)


def normalize_router_model(raw: str | None) -> str | None:
    """Normalize a router model string; return None if empty/unknown."""
    if raw is None:
        return None
    text = str(raw).strip().strip("\"'")
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"unknown", "none", "n/a", "na", "—", "-"}:
        return None
    # Prefer a clean RT-* token when vendors embed extra firmware text.
    match = _MODEL_RE.search(text.replace("_", "-"))
    if match:
        return match.group(0).upper()
    return text


def format_router_model_menu_title(model: str | None, health: float | None = None) -> str:
    """Menubar Router line: live model (or unknown) plus optional health score."""
    label = normalize_router_model(model) or "unknown"
    if health is None:
        return f"Router: {label}"
    try:
        score = float(health)
    except (TypeError, ValueError):
        return f"Router: {label}"
    return f"Router: {label}  ·  Health: {score:.0f}/100"


def model_from_firmware_payload(fw_data: dict[str, Any] | None) -> str | None:
    """Extract model from asusrouter FIRMWARE payload keys."""
    if not fw_data or not isinstance(fw_data, dict):
        return None
    for key in ("model", "productid", "odmpid", "product_id", "model_name"):
        value = normalize_router_model(fw_data.get(key))
        if value:
            return value
    return None


async def fetch_model_via_ssh(ssh: Any) -> str | None:
    """Read productid/model from NVRAM over an existing SSH session."""
    for key in ("productid", "odmpid", "model"):
        try:
            result = await ssh.run(f"nvram get {key} 2>/dev/null")
        except Exception:
            log.debug("SSH nvram get %s failed", key, exc_info=True)
            continue
        if getattr(result, "ok", False) and getattr(result, "stdout", None):
            value = normalize_router_model(result.stdout)
            if value:
                return value
    return None


async def fetch_live_router_model(
    cfg: Any,
    *,
    username: str | None = None,
    password: str | None = None,
    prefer_ssh: bool = True,
) -> str | None:
    """Resolve the live router model via SSH and/or HTTP backend.

    SSH is preferred on refresh paths to avoid hammering HTTP LOGIN.
    On Connect / first discovery, callers may set prefer_ssh=False to try API first.
    """
    model: str | None = None

    async def _from_ssh() -> str | None:
        from asusroutercontrol.ssh import RouterSSH

        ssh = RouterSSH(connect_timeout=8.0)
        try:
            await ssh.connect()
            return await fetch_model_via_ssh(ssh)
        except Exception:
            log.debug("SSH model probe failed", exc_info=True)
            return None
        finally:
            try:
                await ssh.disconnect()
            except Exception:
                log.debug("SSH disconnect after model probe failed", exc_info=True)

    async def _from_api() -> str | None:
        from asusroutercontrol.backends.factory import create_backend
        from asusroutercontrol.credentials import get_router_credentials

        user = username
        pw = password
        if not user or not pw:
            host = getattr(cfg, "router_host", None)
            user, pw = get_router_credentials(host_hint=host)
        if not user or not pw:
            return None
        backend = create_backend(cfg, username=user, password=pw)
        try:
            await backend.connect()
            info = await backend.get_system_info()
            return normalize_router_model(getattr(info, "model", None))
        except Exception:
            log.debug("API model probe failed", exc_info=True)
            return None
        finally:
            try:
                await backend.disconnect()
            except Exception:
                log.debug("Backend disconnect after model probe failed", exc_info=True)

    order = (_from_ssh, _from_api) if prefer_ssh else (_from_api, _from_ssh)
    for probe in order:
        model = await probe()
        if model:
            return model
    return None
