"""DHCP static reservation management over router SSH/NVRAM."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from dataclasses import dataclass

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import ConfigEvent
from asusroutercontrol.ssh import RouterSSH

_MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
_IP_RE = re.compile(r"((?:\d{1,3}\.){3}\d{1,3})")

DHCP_NVRAM_KEYS = (
    "dhcp_static_x",
    "dhcp_staticlist",
    "dhcp_hostnames",
    "lan_ipaddr",
    "lan_netmask",
    "dhcp_start",
    "dhcp_end",
)


@dataclass(frozen=True)
class DhcpReservation:
    mac: str
    ip: str
    hostname: str | None = None


@dataclass(frozen=True)
class DhcpReservationResult:
    success: bool
    dry_run: bool
    changed: bool
    action: str
    message: str
    reservation: DhcpReservation | None
    old_values: dict[str, str]
    new_values: dict[str, str]
    rollback_attempted: bool = False
    rollback_success: bool = False


@dataclass(frozen=True)
class _StaticToken:
    raw: str
    mac: str | None
    ip: str | None


@dataclass(frozen=True)
class _HostnameToken:
    raw: str
    mac: str | None
    hostname: str | None


def normalize_mac(mac: str) -> str:
    value = mac.strip().replace("-", ":").lower()
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
    int("".join(parts), 16)
    return value


def normalize_ipv4(value: str, *, field_name: str = "IP") -> str:
    try:
        ip = ipaddress.IPv4Address(value.strip())
    except Exception as exc:
        raise ValueError(f"{field_name} must be a valid IPv4 address") from exc
    return str(ip)


def _split_nvram_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [token for token in raw.split("<") if token]


def _join_nvram_list(tokens: list[str]) -> str:
    return "".join(f"<{token}" for token in tokens)


def _extract_mac(token: str) -> str | None:
    match = _MAC_RE.search(token)
    if not match:
        return None
    return normalize_mac(match.group(1))


def _extract_ipv4(token: str) -> str | None:
    for match in _IP_RE.finditer(token):
        candidate = match.group(1)
        try:
            return normalize_ipv4(candidate)
        except ValueError:
            continue
    return None


def _parse_static_tokens(raw: str) -> list[_StaticToken]:
    tokens: list[_StaticToken] = []
    for token in _split_nvram_list(raw):
        fields = token.split(">")
        mac = None
        ip = None
        if len(fields) >= 2:
            try:
                mac = normalize_mac(fields[0])
            except ValueError:
                mac = None
            try:
                ip = normalize_ipv4(fields[1], field_name="Reservation IP")
            except ValueError:
                ip = None
        mac = mac or _extract_mac(token)
        ip = ip or _extract_ipv4(token)
        tokens.append(_StaticToken(raw=token, mac=mac, ip=ip))
    return tokens


def _parse_hostname_tokens(raw: str) -> list[_HostnameToken]:
    tokens: list[_HostnameToken] = []
    for token in _split_nvram_list(raw):
        fields = token.split(">", 1)
        mac = None
        hostname = None
        if len(fields) == 2:
            try:
                mac = normalize_mac(fields[0])
            except ValueError:
                mac = None
            hostname = fields[1].strip() or None
        mac = mac or _extract_mac(token)
        tokens.append(_HostnameToken(raw=token, mac=mac, hostname=hostname))
    return tokens


def parse_reservations(raw_static: str, raw_hostnames: str = "") -> list[DhcpReservation]:
    host_map: dict[str, str] = {}
    for token in _parse_hostname_tokens(raw_hostnames):
        if token.mac and token.hostname:
            host_map[token.mac] = token.hostname
    reservations: list[DhcpReservation] = []
    for token in _parse_static_tokens(raw_static):
        if token.mac and token.ip:
            reservations.append(
                DhcpReservation(
                    mac=token.mac,
                    ip=token.ip,
                    hostname=host_map.get(token.mac),
                )
            )
    return reservations


def _validate_subnet(target_ip: str, lan_ip: str, lan_mask: str) -> None:
    try:
        network = ipaddress.IPv4Network((lan_ip, lan_mask), strict=False)
    except Exception as exc:
        raise ValueError("LAN subnet metadata is invalid on router") from exc
    ip_obj = ipaddress.IPv4Address(target_ip)
    lan_obj = ipaddress.IPv4Address(lan_ip)
    if ip_obj not in network:
        raise ValueError(f"Reservation IP {target_ip} is outside LAN subnet {network}")
    if ip_obj == lan_obj:
        raise ValueError("Reservation IP cannot equal router LAN IP")
    if ip_obj == network.network_address or ip_obj == network.broadcast_address:
        raise ValueError("Reservation IP cannot be network or broadcast address")


def _validate_pool_range(target_ip: str, start_ip: str, end_ip: str) -> None:
    if not start_ip or not end_ip:
        return
    start = ipaddress.IPv4Address(normalize_ipv4(start_ip, field_name="DHCP start"))
    end = ipaddress.IPv4Address(normalize_ipv4(end_ip, field_name="DHCP end"))
    target = ipaddress.IPv4Address(target_ip)
    if start > end:
        start, end = end, start
    if not (start <= target <= end):
        raise ValueError(
            f"Reservation IP {target_ip} must be inside DHCP range {start}-{end}"
        )


def _sanitize_hostname(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    # Keep hostnames dnsmasq-safe and shell-safe.
    allowed = re.sub(r"[^A-Za-z0-9._-]", "-", cleaned)
    return allowed[:63]


def _build_upsert_payload(
    *,
    raw_static: str,
    raw_hostnames: str,
    target_mac: str,
    target_ip: str,
    hostname: str | None,
) -> tuple[str, str]:
    static_tokens = _parse_static_tokens(raw_static)
    existing_by_ip = {
        token.ip: token.mac
        for token in static_tokens
        if token.ip and token.mac and token.mac != target_mac
    }
    owner = existing_by_ip.get(target_ip)
    if owner:
        raise ValueError(f"IP {target_ip} is already reserved for MAC {owner}")

    kept_static_raw: list[str] = []
    for token in static_tokens:
        if token.mac == target_mac:
            continue
        kept_static_raw.append(token.raw)
    kept_static_raw.append(f"{target_mac}>{target_ip}")
    new_static = _join_nvram_list(kept_static_raw)

    hostname_tokens = _parse_hostname_tokens(raw_hostnames)
    kept_hostname_raw: list[str] = []
    existing_hostname: str | None = None
    for token in hostname_tokens:
        if token.mac == target_mac:
            if token.hostname:
                existing_hostname = token.hostname
            continue
        kept_hostname_raw.append(token.raw)

    desired_hostname = existing_hostname if hostname is None else hostname
    if desired_hostname:
        kept_hostname_raw.append(f"{target_mac}>{desired_hostname}")
    new_hostnames = _join_nvram_list(kept_hostname_raw)
    return new_static, new_hostnames


def _build_remove_payload(
    *,
    raw_static: str,
    raw_hostnames: str,
    target_mac: str,
) -> tuple[str, str]:
    static_tokens = _parse_static_tokens(raw_static)
    kept_static_raw = [token.raw for token in static_tokens if token.mac != target_mac]
    new_static = _join_nvram_list(kept_static_raw)

    hostname_tokens = _parse_hostname_tokens(raw_hostnames)
    kept_hostname_raw = [token.raw for token in hostname_tokens if token.mac != target_mac]
    new_hostnames = _join_nvram_list(kept_hostname_raw)
    return new_static, new_hostnames


async def read_dhcp_nvram(ssh: RouterSSH) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in DHCP_NVRAM_KEYS:
        result = await ssh.run(f"nvram get {key}")
        values[key] = (result.stdout or "").strip() if result.ok else ""
    return values


async def get_reservations(ssh: RouterSSH) -> list[DhcpReservation]:
    values = await read_dhcp_nvram(ssh)
    return parse_reservations(values.get("dhcp_staticlist", ""), values.get("dhcp_hostnames", ""))


async def _set_nvram_value(ssh: RouterSSH, key: str, value: str) -> None:
    result = await ssh.run(f"nvram set {key}={shlex.quote(value)}")
    if not result.ok:
        raise RuntimeError(f"nvram set failed for {key}: {result.stderr}")


async def _rollback_nvram_values(ssh: RouterSSH, old_values: dict[str, str]) -> tuple[bool, str]:
    try:
        await _set_nvram_value(ssh, "dhcp_static_x", old_values["dhcp_static_x"])
        await _set_nvram_value(ssh, "dhcp_staticlist", old_values["dhcp_staticlist"])
        await _set_nvram_value(ssh, "dhcp_hostnames", old_values["dhcp_hostnames"])
        commit = await ssh.run("nvram commit")
        if not commit.ok:
            return False, f"rollback commit failed: {commit.stderr}"
        restart = await ssh.run("service restart_dnsmasq")
        if not restart.ok:
            return False, f"rollback dnsmasq restart failed: {restart.stderr}"
        return True, ""
    except Exception as exc:
        return False, str(exc)


async def _apply_payload(
    *,
    ssh: RouterSSH,
    store: DataStore,
    action: str,
    target: DhcpReservation | None,
    old_values: dict[str, str],
    new_values: dict[str, str],
    dry_run: bool,
    triggered_by: str,
) -> DhcpReservationResult:
    changed = any(old_values.get(k, "") != new_values.get(k, "") for k in new_values)
    if dry_run:
        return DhcpReservationResult(
            success=True,
            dry_run=True,
            changed=changed,
            action=action,
            message="Dry run only. No router changes applied.",
            reservation=target,
            old_values=old_values,
            new_values=new_values,
        )
    if not changed:
        return DhcpReservationResult(
            success=True,
            dry_run=False,
            changed=False,
            action=action,
            message="No changes needed.",
            reservation=target,
            old_values=old_values,
            new_values=new_values,
        )

    rollback_attempted = False
    rollback_success = False
    try:
        await _set_nvram_value(ssh, "dhcp_static_x", new_values["dhcp_static_x"])
        await _set_nvram_value(ssh, "dhcp_staticlist", new_values["dhcp_staticlist"])
        await _set_nvram_value(ssh, "dhcp_hostnames", new_values["dhcp_hostnames"])

        commit = await ssh.run("nvram commit")
        if not commit.ok:
            raise RuntimeError(f"nvram commit failed: {commit.stderr}")

        restart = await ssh.run("service restart_dnsmasq")
        if not restart.ok:
            raise RuntimeError(f"dnsmasq restart failed: {restart.stderr}")

        post = await read_dhcp_nvram(ssh)
        if post.get("dhcp_static_x") != new_values["dhcp_static_x"]:
            raise RuntimeError("Post-apply verify failed for dhcp_static_x")
        if post.get("dhcp_staticlist") != new_values["dhcp_staticlist"]:
            raise RuntimeError("Post-apply verify failed for dhcp_staticlist")
        if post.get("dhcp_hostnames", "") != new_values["dhcp_hostnames"]:
            raise RuntimeError("Post-apply verify failed for dhcp_hostnames")

        await store.insert_config_event(
            ConfigEvent(
                event_type="dhcp_reservation_apply",
                description=f"{action}: {target.mac if target else 'none'}",
                nvram_changes_json=json.dumps(
                    {
                        "dhcp_static_x": [old_values["dhcp_static_x"], new_values["dhcp_static_x"]],
                        "dhcp_staticlist": [
                            old_values["dhcp_staticlist"],
                            new_values["dhcp_staticlist"],
                        ],
                        "dhcp_hostnames": [
                            old_values["dhcp_hostnames"],
                            new_values["dhcp_hostnames"],
                        ],
                        "action": action,
                        "target": target.__dict__ if target else None,
                    },
                    sort_keys=True,
                ),
                triggered_by=triggered_by,
            )
        )
        return DhcpReservationResult(
            success=True,
            dry_run=False,
            changed=True,
            action=action,
            message="Reservation update applied successfully.",
            reservation=target,
            old_values=old_values,
            new_values=new_values,
        )
    except Exception as exc:
        rollback_attempted = True
        rollback_success, rollback_error = await _rollback_nvram_values(ssh, old_values)
        details = str(exc)
        if rollback_attempted and not rollback_success:
            details += f"; rollback failed: {rollback_error}"
        return DhcpReservationResult(
            success=False,
            dry_run=False,
            changed=changed,
            action=action,
            message=details,
            reservation=target,
            old_values=old_values,
            new_values=new_values,
            rollback_attempted=rollback_attempted,
            rollback_success=rollback_success,
        )


async def upsert_reservation(
    *,
    ssh: RouterSSH,
    store: DataStore,
    mac: str,
    ip: str,
    hostname: str | None = None,
    dry_run: bool = True,
    triggered_by: str = "dhcp_cli",
) -> DhcpReservationResult:
    target_mac = normalize_mac(mac)
    target_ip = normalize_ipv4(ip)
    target_hostname = _sanitize_hostname(hostname)

    current = await read_dhcp_nvram(ssh)
    _validate_subnet(target_ip, current["lan_ipaddr"], current["lan_netmask"])
    _validate_pool_range(target_ip, current.get("dhcp_start", ""), current.get("dhcp_end", ""))

    new_static, new_hostnames = _build_upsert_payload(
        raw_static=current["dhcp_staticlist"],
        raw_hostnames=current.get("dhcp_hostnames", ""),
        target_mac=target_mac,
        target_ip=target_ip,
        hostname=target_hostname,
    )
    new_values = {
        "dhcp_static_x": "1",
        "dhcp_staticlist": new_static,
        "dhcp_hostnames": new_hostnames,
    }
    old_values = {
        "dhcp_static_x": current["dhcp_static_x"],
        "dhcp_staticlist": current["dhcp_staticlist"],
        "dhcp_hostnames": current.get("dhcp_hostnames", ""),
    }
    target = DhcpReservation(mac=target_mac, ip=target_ip, hostname=target_hostname)
    return await _apply_payload(
        ssh=ssh,
        store=store,
        action="set",
        target=target,
        old_values=old_values,
        new_values=new_values,
        dry_run=dry_run,
        triggered_by=triggered_by,
    )


async def remove_reservation(
    *,
    ssh: RouterSSH,
    store: DataStore,
    mac: str,
    dry_run: bool = True,
    triggered_by: str = "dhcp_cli",
) -> DhcpReservationResult:
    target_mac = normalize_mac(mac)
    current = await read_dhcp_nvram(ssh)
    existing = parse_reservations(current["dhcp_staticlist"], current.get("dhcp_hostnames", ""))
    existing_target = next((entry for entry in existing if entry.mac == target_mac), None)

    new_static, new_hostnames = _build_remove_payload(
        raw_static=current["dhcp_staticlist"],
        raw_hostnames=current.get("dhcp_hostnames", ""),
        target_mac=target_mac,
    )
    new_values = {
        "dhcp_static_x": "1" if new_static else "0",
        "dhcp_staticlist": new_static,
        "dhcp_hostnames": new_hostnames,
    }
    old_values = {
        "dhcp_static_x": current["dhcp_static_x"],
        "dhcp_staticlist": current["dhcp_staticlist"],
        "dhcp_hostnames": current.get("dhcp_hostnames", ""),
    }
    return await _apply_payload(
        ssh=ssh,
        store=store,
        action="remove",
        target=existing_target,
        old_values=old_values,
        new_values=new_values,
        dry_run=dry_run,
        triggered_by=triggered_by,
    )
