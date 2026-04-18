"""Optimization executor — applies NVRAM changes with safety guardrails.

Workflow: snapshot config → apply → restart relevant service → record ConfigEvent.
All changes are validated against the TRACKED_NVRAM_KEYS whitelist.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from asusroutercontrol.datastore import DataStore
from asusroutercontrol.models import ConfigEvent
from asusroutercontrol.probes import TRACKED_NVRAM_KEYS, diff_config_snapshots, probe_config
from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)

# Map NVRAM key prefixes to the service that must restart after change.
SERVICE_RESTART_MAP: dict[str, str] = {
    "qos_": "restart_qos",
    "wl0_": "restart_wireless",
    "wl1_": "restart_wireless",
    "wan0_dns": "restart_dnsmasq",
    "wan_dns": "restart_dnsmasq",
    "misc_http": "restart_httpd",
    "enable_webdav": "restart_webdav",
    "amas_": "restart_amas",
    "apps_analysis": "restart_wrs",
    "wrs_": "restart_wrs",
    "ctf_": "reboot",  # CTF changes require reboot
}


@dataclass
class ApplyResult:
    key: str
    old_value: str
    new_value: str
    success: bool
    service_restarted: str
    error: str = ""


def _service_for_key(key: str) -> str:
    """Determine which service to restart for a given NVRAM key."""
    for prefix, service in SERVICE_RESTART_MAP.items():
        if key.startswith(prefix):
            return service
    return ""


async def apply_nvram_setting(
    ssh: RouterSSH,
    store: DataStore,
    key: str,
    value: str,
    *,
    triggered_by: str = "user",
    dry_run: bool = False,
) -> ApplyResult:
    """Apply a single NVRAM setting with safety validation.

    1. Validate key is in TRACKED_NVRAM_KEYS whitelist.
    2. Read current value.
    3. If dry_run, return without applying.
    4. nvram set + nvram commit.
    5. Restart relevant service.
    6. Record ConfigEvent in datastore.
    """
    # Safety: only allow whitelisted keys
    if key not in TRACKED_NVRAM_KEYS:
        return ApplyResult(
            key=key, old_value="", new_value=value,
            success=False, service_restarted="",
            error=f"Key '{key}' not in TRACKED_NVRAM_KEYS whitelist",
        )

    # Read current value
    r = await ssh.run(f"nvram get {key}")
    old_value = r.stdout if r.ok else ""

    if old_value == value:
        return ApplyResult(
            key=key, old_value=old_value, new_value=value,
            success=True, service_restarted="",
            error="Already set to target value",
        )

    if dry_run:
        service = _service_for_key(key)
        return ApplyResult(
            key=key, old_value=old_value, new_value=value,
            success=True, service_restarted=f"(would restart: {service})" if service else "",
        )

    # Apply change
    try:
        r = await ssh.run(f"nvram set {key}={value}")
        if not r.ok:
            return ApplyResult(
                key=key, old_value=old_value, new_value=value,
                success=False, service_restarted="",
                error=f"nvram set failed: {r.stderr}",
            )

        r = await ssh.run("nvram commit")
        if not r.ok:
            return ApplyResult(
                key=key, old_value=old_value, new_value=value,
                success=False, service_restarted="",
                error=f"nvram commit failed: {r.stderr}",
            )

        # Restart relevant service
        service = _service_for_key(key)
        if service and service != "reboot":
            r = await ssh.run(f"service {service}")
            log.info("Service restart: %s (exit %d)", service, r.exit_code)

        # Record config event
        try:
            await store.insert_config_event(ConfigEvent(
                event_type="nvram_apply",
                description=f"{key}: {old_value!r} → {value!r}",
                nvram_changes_json=json.dumps({key: [old_value, value]}),
                triggered_by=triggered_by,
            ))
        except Exception:
            log.exception("Failed to record config event")

        return ApplyResult(
            key=key, old_value=old_value, new_value=value,
            success=True, service_restarted=service,
        )

    except Exception as e:
        return ApplyResult(
            key=key, old_value=old_value, new_value=value,
            success=False, service_restarted="",
            error=str(e),
        )


async def apply_optimization_batch(
    ssh: RouterSSH,
    store: DataStore,
    suggestions: list[dict],
    *,
    triggered_by: str = "optimizer",
    dry_run: bool = False,
) -> list[ApplyResult]:
    """Apply a batch of suggest_settings() results.

    Takes pre-change config snapshot, applies all changes,
    then takes post-change snapshot.
    """
    results: list[ApplyResult] = []

    if not dry_run:
        # Pre-change snapshot
        try:
            pre_snap = await probe_config(ssh, source="pre-change")
            await store.insert_config_snapshot(pre_snap)
        except Exception:
            log.exception("Pre-change snapshot failed")

    # Group by service to minimize restarts
    by_service: dict[str, list[dict]] = {}
    for s in suggestions:
        key = s.get("key", "")
        service = _service_for_key(key)
        by_service.setdefault(service, []).append(s)

    for _service, group in by_service.items():
        for s in group:
            key = s.get("key", "")
            proposed = s.get("proposed", "")
            # Extract clean value from proposed (e.g. "0 (disable WAN admin access)" -> "0")
            clean_value = proposed.split("(")[0].strip().split()[0] if proposed else ""
            if not clean_value:
                results.append(ApplyResult(
                    key=key, old_value="", new_value=proposed,
                    success=False, service_restarted="",
                    error="Could not parse proposed value",
                ))
                continue

            result = await apply_nvram_setting(
                ssh, store, key, clean_value,
                triggered_by=triggered_by,
                dry_run=dry_run,
            )
            results.append(result)

    if not dry_run:
        # Post-change snapshot
        try:
            post_snap = await probe_config(ssh, source="post-change")
            prev = await store.get_latest_config_snapshot()
            if prev:
                post_snap.diff_summary = diff_config_snapshots(
                    post_snap.nvram_json, prev["nvram_json"]
                )
            await store.insert_config_snapshot(post_snap)
        except Exception:
            log.exception("Post-change snapshot failed")

    return results


async def verify_deep_dive_findings(ssh: RouterSSH) -> list[dict]:
    """Check execution status of all Deep Dive findings."""
    findings: list[dict] = []

    checks = [
        {
            "id": 1, "title": "aaews killed",
            "cmd": "ps w | grep -v grep | grep aaews | wc -l",
            "want": "0", "compare": "eq",
        },
        {
            "id": 2, "title": "DNS override disabled",
            "cmd": "nvram get wan0_dnsenable_x",
            "want": "0", "compare": "eq",
        },
        {
            "id": 3, "title": "QoS ibw ~305000",
            "cmd": "nvram get qos_ibw",
            "want": "305000", "compare": "eq",
        },
        {
            "id": 4, "title": "WAN Web UI disabled",
            "cmd": "nvram get misc_http_x",
            "want": "0", "compare": "eq",
        },
        {
            "id": 6, "title": "AiMesh disabled",
            "cmd": "ps w | grep -v grep | grep mastiff | wc -l",
            "want": "0", "compare": "eq",
        },
        {
            "id": 7, "title": "5GHz TurboQAM enabled",
            "cmd": "nvram get wl1_turbo_qam",
            "want": "1", "compare": "eq",
        },
        {
            "id": 8, "title": "TCP tuning applied",
            "cmd": "cat /proc/sys/net/core/rmem_max",
            "want": "4194304", "compare": "eq",
        },
    ]

    for check in checks:
        r = await ssh.run(check["cmd"])
        actual = r.stdout.strip()
        passed = actual == check["want"]
        findings.append({
            "finding": check["id"],
            "title": check["title"],
            "expected": check["want"],
            "actual": actual,
            "passed": passed,
            "status": "✅" if passed else "❌",
        })

    return findings
