"""Safe staged rollout engine for optimization settings."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from asusroutercontrol.backends.base import FirmwareBackend
from asusroutercontrol.backends.factory import create_backend
from asusroutercontrol.config import load_config
from asusroutercontrol.credentials import get_router_credentials
from asusroutercontrol.datastore import DataStore
from asusroutercontrol.executor import SERVICE_RESTART_MAP, apply_nvram_setting
from asusroutercontrol.models import ConfigEvent, ConnectionType
from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)

DISRUPTIVE_SERVICES = {"restart_wireless", "reboot"}
QUICK_LOSS_TARGETS: dict[str, str] = {
    "cloudflare": "1.1.1.1",
    "google": "8.8.8.8",
}

_ASSOC_RE = re.compile(r"assoclist\s+([0-9A-Fa-f:]{17})")
_DHCP_LOG_RE = re.compile(
    r"dnsmasq-dhcp\[\d+\]:\s+"
    r"(DHCPDISCOVER|DHCPOFFER|DHCPREQUEST|DHCPACK|DHCPNAK)\(br0\)"
)
_AUTH_LOG_RE = re.compile(
    r"wlceventd_proc_event\(\d+\):\s+"
    r"(eth\d):\s+(Auth|Assoc|Disassoc|Deauth)\s+([0-9A-F:]{17}),\s+status:\s+([^,]+)"
)


@dataclass(frozen=True)
class RolloutStep:
    key: str
    target: str
    rationale: str = ""


@dataclass
class HealthSnapshot:
    timestamp: float
    wan_connected: bool
    loss_by_target: dict[str, float]
    max_loss_pct: float
    wifi_assoc_macs: set[str]
    wired_online_macs: set[str]
    watch_mac_state: dict[str, bool] = field(default_factory=dict)

    def payload(self) -> dict:
        return {
            "wan_connected": self.wan_connected,
            "loss_by_target": self.loss_by_target,
            "max_loss_pct": round(self.max_loss_pct, 2),
            "wifi_assoc_count": len(self.wifi_assoc_macs),
            "wired_online_count": len(self.wired_online_macs),
            "watch_mac_state": self.watch_mac_state,
        }


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    observed_max_loss_pct: float = 0.0
    dropped_wifi_macs: list[str] = field(default_factory=list)
    dropped_wired_macs: list[str] = field(default_factory=list)
    last_snapshot: HealthSnapshot | None = None

    def payload(self) -> dict:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "observed_max_loss_pct": round(self.observed_max_loss_pct, 2),
            "dropped_wifi_macs": self.dropped_wifi_macs,
            "dropped_wired_macs": self.dropped_wired_macs,
            "last_snapshot": self.last_snapshot.payload() if self.last_snapshot else None,
        }


@dataclass
class StepRunResult:
    key: str
    target: str
    previous: str
    service: str
    status: str  # pass / fail / skipped / rolled_back
    reason: str = ""
    gate_payload: dict | None = None
    rollback_ok: bool = False


@dataclass
class RolloutRunResult:
    profile: str
    completed: bool
    aborted_reason: str = ""
    step_results: list[StepRunResult] = field(default_factory=list)
@dataclass
class BaselineAssessment:
    snapshot: HealthSnapshot
    unstable_wifi_macs: list[str] = field(default_factory=list)
    unstable_wired_macs: list[str] = field(default_factory=list)


ROLLOUT_PROFILES: dict[str, list[RolloutStep]] = {
    # DNS is ordered to prevent resolver cut-over before custom resolvers are populated.
    "last-rollback": [
        RolloutStep("upnp_enable", "0", "Disable UPnP exposure after baseline recovers."),
        RolloutStep("qos_ibw", "440000", "Restore tuned QoS inbound bandwidth cap."),
        RolloutStep("qos_obw", "42000", "Restore tuned QoS outbound bandwidth cap."),
        RolloutStep("wan_dns1_x", "1.1.1.1", "Set custom primary resolver first."),
        RolloutStep("wan_dns2_x", "1.0.0.1", "Set custom secondary resolver."),
        RolloutStep("wan0_dnsenable_x", "0", "Switch DNS mode to use custom resolvers."),
        RolloutStep("amas_enable", "0", "Disable AiMesh control plane on standalone unit."),
        RolloutStep("wl1_turbo_qam", "1", "Enable 5GHz TurboQAM (wireless restart required)."),
    ]
}


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    value = mac.strip().replace("-", ":").lower()
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
    int("".join(parts), 16)
    return value


def service_for_key(key: str) -> str:
    for prefix, service in SERVICE_RESTART_MAP.items():
        if key.startswith(prefix):
            return service
    return ""


def profile_steps(profile: str) -> list[RolloutStep]:
    if profile not in ROLLOUT_PROFILES:
        known = ", ".join(sorted(ROLLOUT_PROFILES))
        raise ValueError(f"Unknown profile '{profile}'. Known: {known}")
    return ROLLOUT_PROFILES[profile]


async def _record_rollout_event(
    store: DataStore,
    *,
    profile: str,
    step_key: str,
    phase: str,
    triggered_by: str,
    payload: dict,
) -> None:
    event_payload = {
        "profile": profile,
        "step": step_key,
        "phase": phase,
        **payload,
    }
    event = ConfigEvent(
        event_type="rollout_step",
        description=f"{step_key} {phase}",
        nvram_changes_json=json.dumps(event_payload, sort_keys=True),
        triggered_by=triggered_by,
    )
    await store.insert_config_event(event)


def _backend_from_local_config() -> FirmwareBackend:
    cfg = load_config()
    username, password = get_router_credentials()
    if not username or not password:
        raise RuntimeError("Router credentials not configured. Run `asusrouter setup`.")
    return create_backend(cfg, username=username, password=password)


async def _current_nvram_value(ssh: RouterSSH, key: str) -> str:
    result = await ssh.run(f"nvram get {key}")
    return (result.stdout or "").strip() if result.ok else ""


async def _wifi_assoc_macs(ssh: RouterSSH) -> set[str]:
    output: list[str] = []
    for iface in ("eth1", "eth2"):
        result = await ssh.run(f"wl -i {iface} assoclist 2>/dev/null || true")
        output.extend((result.stdout or "").splitlines())
    macs: set[str] = set()
    for line in output:
        match = _ASSOC_RE.search(line)
        if match:
            macs.add(match.group(1).lower())
    return macs


async def _wired_online_macs(backend: FirmwareBackend) -> set[str]:
    devices = await backend.get_connected_devices()
    return {
        dev.mac.lower()
        for dev in devices
        if dev.is_online and dev.connection == ConnectionType.WIRED
    }


async def _watch_mac_state(ssh: RouterSSH, watch_mac: str | None) -> dict[str, bool]:
    if not watch_mac:
        return {}
    result = await ssh.run(f"grep -Ei '{watch_mac}' /tmp/syslog.log | tail -n 120")
    state = {
        "seen": False,
        "discover": False,
        "offer": False,
        "request": False,
        "ack": False,
        "nak": False,
        "assoc": False,
        "disassoc": False,
    }
    for line in (result.stdout or "").splitlines():
        if watch_mac not in line.lower():
            continue
        state["seen"] = True
        dhcp = _DHCP_LOG_RE.search(line)
        if dhcp:
            state[dhcp.group(1).replace("DHCP", "").lower()] = True
        auth = _AUTH_LOG_RE.search(line)
        if auth:
            event = auth.group(2)
            if event == "Assoc":
                state["assoc"] = True
            if event in {"Disassoc", "Deauth"}:
                state["disassoc"] = True
    return state

async def _quick_loss_probe(
    ssh: RouterSSH,
    target: str,
    *,
    count: int = 3,
    timeout: int = 2,
) -> float:
    result = await ssh.run(f"ping -c {count} -W {timeout} {target} 2>&1")
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(r"(\d+)% packet loss", output)
    if match:
        return float(match.group(1))
    # If ping failed without parseable output, treat as hard failure.
    return 100.0 if not result.ok else 0.0


async def _quick_loss_by_target(ssh: RouterSSH) -> dict[str, float]:
    losses: dict[str, float] = {}
    for name, ip in QUICK_LOSS_TARGETS.items():
        try:
            losses[name] = await _quick_loss_probe(ssh, ip)
        except Exception:
            losses[name] = 100.0
    return losses


async def collect_health_snapshot(
    ssh: RouterSSH,
    backend: FirmwareBackend,
    *,
    watch_mac: str | None,
) -> HealthSnapshot:
    wan = await backend.get_wan_status()
    loss_by_target = await _quick_loss_by_target(ssh)
    max_loss_pct = max(loss_by_target.values()) if loss_by_target else 0.0
    return HealthSnapshot(
        timestamp=time.time(),
        wan_connected=(wan.status or "").lower() == "connected",
        loss_by_target=loss_by_target,
        max_loss_pct=max_loss_pct,
        wifi_assoc_macs=await _wifi_assoc_macs(ssh),
        wired_online_macs=await _wired_online_macs(backend),
        watch_mac_state=await _watch_mac_state(ssh, watch_mac),
    )
async def build_stable_baseline(
    ssh: RouterSSH,
    backend: FirmwareBackend,
    *,
    watch_mac: str | None,
    samples: int = 3,
    sample_interval: float = 2.0,
) -> BaselineAssessment:
    samples = max(1, samples)
    history: list[HealthSnapshot] = []
    for idx in range(samples):
        history.append(await collect_health_snapshot(ssh, backend, watch_mac=watch_mac))
        if idx < samples - 1:
            await asyncio.sleep(max(0.5, sample_interval))

    stable_wifi = set.intersection(*(snap.wifi_assoc_macs for snap in history))
    stable_wired = set.intersection(*(snap.wired_online_macs for snap in history))
    observed_wifi = set.union(*(snap.wifi_assoc_macs for snap in history))
    observed_wired = set.union(*(snap.wired_online_macs for snap in history))

    baseline = history[-1]
    baseline.wifi_assoc_macs = stable_wifi
    baseline.wired_online_macs = stable_wired
    return BaselineAssessment(
        snapshot=baseline,
        unstable_wifi_macs=sorted(observed_wifi - stable_wifi),
        unstable_wired_macs=sorted(observed_wired - stable_wired),
    )


def _drop_delta(baseline: HealthSnapshot, current: HealthSnapshot) -> tuple[list[str], list[str]]:
    dropped_wifi = sorted(baseline.wifi_assoc_macs - current.wifi_assoc_macs)
    dropped_wired = sorted(baseline.wired_online_macs - current.wired_online_macs)
    return dropped_wifi, dropped_wired


async def monitor_hold_window(
    ssh: RouterSSH,
    backend: FirmwareBackend,
    *,
    baseline: HealthSnapshot,
    hold_seconds: int,
    poll_seconds: float,
    max_loss_pct: float,
    watch_mac: str | None,
    strict_drops: bool = True,
) -> GateResult:
    deadline = time.monotonic() + max(1, hold_seconds)
    observed_max = 0.0
    while time.monotonic() < deadline:
        current = await collect_health_snapshot(ssh, backend, watch_mac=watch_mac)
        observed_max = max(observed_max, current.max_loss_pct)
        dropped_wifi, dropped_wired = _drop_delta(baseline, current)
        if not current.wan_connected:
            return GateResult(
                ok=False,
                reason="WAN disconnected during hold window.",
                observed_max_loss_pct=observed_max,
                dropped_wifi_macs=dropped_wifi,
                dropped_wired_macs=dropped_wired,
                last_snapshot=current,
            )
        if current.max_loss_pct > max_loss_pct:
            return GateResult(
                ok=False,
                reason=(
                    f"Packet loss threshold exceeded: {current.max_loss_pct:.1f}% > "
                    f"{max_loss_pct:.1f}%."
                ),
                observed_max_loss_pct=observed_max,
                dropped_wifi_macs=dropped_wifi,
                dropped_wired_macs=dropped_wired,
                last_snapshot=current,
            )
        if strict_drops and (dropped_wifi or dropped_wired):
            reason_parts: list[str] = []
            if dropped_wifi:
                reason_parts.append(f"Wi-Fi drops: {', '.join(dropped_wifi)}")
            if dropped_wired:
                reason_parts.append(f"Wired drops: {', '.join(dropped_wired)}")
            return GateResult(
                ok=False,
                reason="; ".join(reason_parts),
                observed_max_loss_pct=observed_max,
                dropped_wifi_macs=dropped_wifi,
                dropped_wired_macs=dropped_wired,
                last_snapshot=current,
            )
        await asyncio.sleep(max(0.5, poll_seconds))

    final = await collect_health_snapshot(ssh, backend, watch_mac=watch_mac)
    observed_max = max(observed_max, final.max_loss_pct)
    dropped_wifi, dropped_wired = _drop_delta(baseline, final)
    if not final.wan_connected:
        return GateResult(
            ok=False,
            reason="WAN disconnected at end of hold window.",
            observed_max_loss_pct=observed_max,
            dropped_wifi_macs=dropped_wifi,
            dropped_wired_macs=dropped_wired,
            last_snapshot=final,
        )
    if final.max_loss_pct > max_loss_pct:
        return GateResult(
            ok=False,
            reason=(
                f"Packet loss threshold exceeded: {final.max_loss_pct:.1f}% > "
                f"{max_loss_pct:.1f}%."
            ),
            observed_max_loss_pct=observed_max,
            dropped_wifi_macs=dropped_wifi,
            dropped_wired_macs=dropped_wired,
            last_snapshot=final,
        )
    if strict_drops and (dropped_wifi or dropped_wired):
        reason_parts: list[str] = []
        if dropped_wifi:
            reason_parts.append(f"Wi-Fi drops: {', '.join(dropped_wifi)}")
        if dropped_wired:
            reason_parts.append(f"Wired drops: {', '.join(dropped_wired)}")
        return GateResult(
            ok=False,
            reason="; ".join(reason_parts),
            observed_max_loss_pct=observed_max,
            dropped_wifi_macs=dropped_wifi,
            dropped_wired_macs=dropped_wired,
            last_snapshot=final,
        )
    return GateResult(
        ok=True,
        reason="",
        observed_max_loss_pct=observed_max,
        dropped_wifi_macs=dropped_wifi,
        dropped_wired_macs=dropped_wired,
        last_snapshot=final,
    )


async def get_rollout_plan_rows(
    profile: str,
    *,
    no_disconnect: bool = True,
    allow_disruptive: bool = False,
) -> list[dict]:
    steps = profile_steps(profile)
    rows: list[dict] = []
    async with RouterSSH() as ssh:
        for step in steps:
            current = await _current_nvram_value(ssh, step.key)
            service = service_for_key(step.key)
            disruptive = service in DISRUPTIVE_SERVICES
            blocked = no_disconnect and disruptive and not allow_disruptive
            rows.append(
                {
                    "key": step.key,
                    "current": current,
                    "target": step.target,
                    "service": service or "-",
                    "disruptive": disruptive,
                    "blocked": blocked,
                    "action": "skip" if blocked else "apply",
                }
            )
    return rows


async def run_rollout_profile(
    store: DataStore,
    *,
    profile: str,
    max_loss_pct: float,
    hold_seconds: int,
    poll_seconds: float,
    watch_mac: str | None,
    no_disconnect: bool = True,
    allow_disruptive: bool = False,
    dry_run: bool = False,
) -> RolloutRunResult:
    watch_mac = normalize_mac(watch_mac)
    steps = profile_steps(profile)
    backend = _backend_from_local_config()
    triggered_by = f"rollout:{profile}"
    run_result = RolloutRunResult(profile=profile, completed=False)
    await _record_rollout_event(
        store,
        profile=profile,
        step_key="__run__",
        phase="start",
        triggered_by=triggered_by,
        payload={
            "max_loss_pct": max_loss_pct,
            "hold_seconds": hold_seconds,
            "poll_seconds": poll_seconds,
            "watch_mac": watch_mac,
            "no_disconnect": no_disconnect,
            "allow_disruptive": allow_disruptive,
            "dry_run": dry_run,
        },
    )

    try:
        await backend.connect()
        async with RouterSSH() as ssh:
            for step in steps:
                service = service_for_key(step.key)
                disruptive = service in DISRUPTIVE_SERVICES
                current = await _current_nvram_value(ssh, step.key)
                if current == step.target:
                    result = StepRunResult(
                        key=step.key,
                        target=step.target,
                        previous=current,
                        service=service,
                        status="skipped",
                        reason="Already at target value.",
                    )
                    run_result.step_results.append(result)
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="skip_already_target",
                        triggered_by=triggered_by,
                        payload={"current": current, "target": step.target, "service": service},
                    )
                    continue

                if no_disconnect and disruptive and not allow_disruptive:
                    result = StepRunResult(
                        key=step.key,
                        target=step.target,
                        previous=current,
                        service=service,
                        status="skipped",
                        reason="Blocked by strict no-disconnect policy.",
                    )
                    run_result.step_results.append(result)
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="skip_blocked_disruptive",
                        triggered_by=triggered_by,
                        payload={"current": current, "target": step.target, "service": service},
                    )
                    continue
                if dry_run:
                    apply_result = await apply_nvram_setting(
                        ssh,
                        store,
                        step.key,
                        step.target,
                        triggered_by=triggered_by,
                        dry_run=True,
                    )
                    if not apply_result.success:
                        reason = f"Dry-run apply failed: {apply_result.error}"
                        run_result.step_results.append(
                            StepRunResult(
                                key=step.key,
                                target=step.target,
                                previous=current,
                                service=service,
                                status="fail",
                                reason=reason,
                            )
                        )
                        run_result.aborted_reason = reason
                        await _record_rollout_event(
                            store,
                            profile=profile,
                            step_key=step.key,
                            phase="dry_run_fail",
                            triggered_by=triggered_by,
                            payload={"reason": reason, "current": current, "target": step.target},
                        )
                        return run_result
                    run_result.step_results.append(
                        StepRunResult(
                            key=step.key,
                            target=step.target,
                            previous=current,
                            service=service,
                            status="pass",
                            reason="Dry run only.",
                        )
                    )
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="dry_run_pass",
                        triggered_by=triggered_by,
                        payload={"current": current, "target": step.target},
                    )
                    continue

                assessed = await build_stable_baseline(
                    ssh,
                    backend,
                    watch_mac=watch_mac,
                    samples=3,
                    sample_interval=poll_seconds,
                )
                baseline = assessed.snapshot
                await _record_rollout_event(
                    store,
                    profile=profile,
                    step_key=step.key,
                    phase="baseline",
                    triggered_by=triggered_by,
                    payload={
                        "current": current,
                        "target": step.target,
                        "service": service,
                        "baseline": baseline.payload(),
                        "unstable_wifi_excluded": assessed.unstable_wifi_macs,
                        "unstable_wired_excluded": assessed.unstable_wired_macs,
                    },
                )
                if not baseline.wan_connected:
                    reason = "WAN not connected before step."
                    run_result.step_results.append(
                        StepRunResult(
                            key=step.key,
                            target=step.target,
                            previous=current,
                            service=service,
                            status="fail",
                            reason=reason,
                        )
                    )
                    run_result.aborted_reason = reason
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="abort_precheck",
                        triggered_by=triggered_by,
                        payload={"reason": reason},
                    )
                    return run_result

                apply_result = await apply_nvram_setting(
                    ssh,
                    store,
                    step.key,
                    step.target,
                    triggered_by=triggered_by,
                    dry_run=dry_run,
                )
                if not apply_result.success:
                    reason = f"Apply failed: {apply_result.error}"
                    run_result.step_results.append(
                        StepRunResult(
                            key=step.key,
                            target=step.target,
                            previous=current,
                            service=service,
                            status="fail",
                            reason=reason,
                        )
                    )
                    run_result.aborted_reason = reason
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="apply_fail",
                        triggered_by=triggered_by,
                        payload={"reason": reason, "current": current, "target": step.target},
                    )
                    return run_result

                gate = await monitor_hold_window(
                    ssh,
                    backend,
                    baseline=baseline,
                    hold_seconds=hold_seconds,
                    poll_seconds=poll_seconds,
                    max_loss_pct=max_loss_pct,
                    watch_mac=watch_mac,
                    strict_drops=no_disconnect,
                )
                if gate.ok:
                    run_result.step_results.append(
                        StepRunResult(
                            key=step.key,
                            target=step.target,
                            previous=current,
                            service=service,
                            status="pass",
                            reason="Post-step connectivity gate passed.",
                            gate_payload=gate.payload(),
                        )
                    )
                    await _record_rollout_event(
                        store,
                        profile=profile,
                        step_key=step.key,
                        phase="pass",
                        triggered_by=triggered_by,
                        payload={
                            "current": current,
                            "target": step.target,
                            "gate": gate.payload(),
                        },
                    )
                    continue

                rollback = await apply_nvram_setting(
                    ssh,
                    store,
                    step.key,
                    apply_result.old_value,
                    triggered_by=f"{triggered_by}:rollback",
                    dry_run=False,
                )
                rollback_gate = await monitor_hold_window(
                    ssh,
                    backend,
                    baseline=baseline,
                    hold_seconds=hold_seconds,
                    poll_seconds=poll_seconds,
                    max_loss_pct=max_loss_pct,
                    watch_mac=watch_mac,
                    strict_drops=False,
                )
                rollback_ok = rollback.success and rollback_gate.ok
                run_result.step_results.append(
                    StepRunResult(
                        key=step.key,
                        target=step.target,
                        previous=current,
                        service=service,
                        status="rolled_back" if rollback_ok else "fail",
                        reason=(
                            f"Gate failed after apply: {gate.reason}"
                            if rollback_ok
                            else (
                                "Gate failed and rollback did not fully recover: "
                                f"{gate.reason}; rollback_ok={rollback.success}; "
                                f"rollback_gate_ok={rollback_gate.ok}"
                            )
                        ),
                        gate_payload=gate.payload(),
                        rollback_ok=rollback_ok,
                    )
                )
                run_result.aborted_reason = (
                    f"Step {step.key} failed connectivity gate and was rolled back."
                    if rollback_ok
                    else f"Step {step.key} failed and rollback recovery failed."
                )
                await _record_rollout_event(
                    store,
                    profile=profile,
                    step_key=step.key,
                    phase="gate_fail_rollback" if rollback_ok else "gate_fail_rollback_fail",
                    triggered_by=triggered_by,
                    payload={
                        "current": current,
                        "target": step.target,
                        "gate": gate.payload(),
                        "rollback_success": rollback.success,
                        "rollback_gate": rollback_gate.payload(),
                    },
                )
                return run_result

        run_result.completed = True
        await _record_rollout_event(
            store,
            profile=profile,
            step_key="__run__",
            phase="completed",
            triggered_by=triggered_by,
            payload={"completed": True},
        )
        return run_result
    finally:
        await backend.disconnect()


async def rollout_status(
    store: DataStore,
    *,
    profile: str,
    days: int = 30,
) -> dict:
    events = [
        ev
        for ev in await store.get_config_events(days=days)
        if ev.get("event_type") == "rollout_step"
        and ev.get("triggered_by", "").startswith(f"rollout:{profile}")
    ]
    steps = profile_steps(profile)
    latest_phase_by_step: dict[str, dict] = {}
    for event in reversed(events):  # oldest -> newest
        try:
            payload = json.loads(event.get("nvram_changes_json") or "{}")
        except json.JSONDecodeError:
            continue
        step = payload.get("step")
        if not step:
            continue
        latest_phase_by_step[step] = {
            "timestamp": event.get("timestamp"),
            "phase": payload.get("phase"),
            "payload": payload,
        }

    rows: list[dict] = []
    for step in steps:
        row = latest_phase_by_step.get(step.key)
        rows.append(
            {
                "key": step.key,
                "target": step.target,
                "phase": row["phase"] if row else "not_started",
                "timestamp": row["timestamp"] if row else None,
            }
        )
    run_row = latest_phase_by_step.get("__run__")
    return {
        "profile": profile,
        "rows": rows,
        "run_phase": run_row["phase"] if run_row else "not_started",
        "run_timestamp": run_row["timestamp"] if run_row else None,
    }

