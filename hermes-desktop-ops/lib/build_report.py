#!/usr/bin/env python3
"""Build plan-pipeable report.json + report.md from collector staging dirs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main() -> None:
    collect = Path(os.environ["COLLECT_DIR"])
    report_dir = Path(os.environ["REPORT_DIR"])
    report_dir.mkdir(parents=True, exist_ok=True)

    incident_id = os.environ["INCIDENT_ID"]
    host = os.environ["HOST_SHORT"]
    ts = os.environ["TS"]
    os_name = os.environ["OS_NAME"]
    arch = os.environ["ARCH_NAME"]
    hermes_bin = os.environ.get("HERMES_BIN") or ""
    port = os.environ.get("PORT_9130") or "unknown"
    auth_status = os.environ.get("AUTH_STATUS") or "missing"
    nous = os.environ.get("NOUS_PRESENT") or "false"
    rt_null = os.environ.get("RT_NULL") or "unknown"
    quarantine = os.environ.get("QUARANTINE_HINT") or "unknown"

    python_probe = read_text(collect / "install" / "python-probe.txt")
    doctor = read_text(collect / "doctor" / "doctor.txt")
    gateway = read_text(collect / "gateway" / "status.txt")
    launchd_lines = read_text(collect / "launchd" / "hermes-lines.txt")

    hypotheses: list[dict] = []

    # H1: Desktop wrong Python / gateway :9130
    h1_score = 0
    h1_evidence: list[str] = []
    if port == "closed":
        h1_score += 3
        h1_evidence.append("port_9130_closed")
    if "hermes_cli_import_system: FAIL" in python_probe or "hermes_cli_import: FAIL" in python_probe:
        h1_score += 2
        h1_evidence.append("system_python_cannot_import_hermes_cli")
    if "hermes_cli_import_venv: OK" in python_probe and port == "closed":
        h1_score += 2
        h1_evidence.append("venv_ok_but_backend_down_suggests_launcher_uses_wrong_python")
    if not hermes_bin:
        h1_score += 1
        h1_evidence.append("hermes_not_on_path")
    hypotheses.append(
        {
            "id": "H1_desktop_python_gateway",
            "title": "Desktop launcher / wrong Python → gateway never binds :9130",
            "score": h1_score,
            "upstream_refs": ["https://github.com/NousResearch/hermes-agent/issues/43913"],
            "evidence": h1_evidence,
            "plan_section": "Desktop/runtime",
            "repair_hint": "Use venv Python for Desktop/gateway; kickstart ai.hermes.gateway; recheck :9130",
        }
    )

    # H2: Nous Portal OAuth
    h2_score = 0
    h2_evidence: list[str] = []
    if rt_null == "true":
        h2_score += 4
        h2_evidence.append("refresh_token_null")
    if quarantine == "true":
        h2_score += 3
        h2_evidence.append("quarantine_or_revoke_hint")
    if nous == "true":
        h2_score += 1
        h2_evidence.append("nous_mentioned_in_auth")
    for blob in (doctor, gateway):
        if re.search(r"invalid_grant|revoked|Refresh session|nous portal", blob, re.I):
            h2_score += 2
            h2_evidence.append("doctor_or_gateway_mentions_portal_auth_failure")
            break
    hypotheses.append(
        {
            "id": "H2_nous_oauth_rt",
            "title": "Nous Portal OAuth refresh-token rotation / revocation",
            "score": h2_score,
            "upstream_refs": [
                "https://github.com/NousResearch/hermes-agent/issues/15099",
                "https://github.com/NousResearch/hermes-agent/issues/81410",
            ],
            "evidence": h2_evidence,
            "plan_section": "Auth/Portal",
            "repair_hint": "hermes auth add nous --type oauth; do not run external OAuth token health-checks",
        }
    )

    # H3: Gateway ops
    h3_score = 0
    h3_evidence: list[str] = []
    if os_name == "Darwin" and not launchd_lines.strip():
        h3_score += 2
        h3_evidence.append("no_hermes_launchd_jobs_listed")
    if re.search(r"not running|stopped|failed|crash", gateway, re.I):
        h3_score += 2
        h3_evidence.append("gateway_status_unhealthy")
    hypotheses.append(
        {
            "id": "H3_gateway_ops",
            "title": "Gateway crash loop / launchd / ops instability",
            "score": h3_score,
            "upstream_refs": [],
            "evidence": h3_evidence,
            "plan_section": "Ops/stability",
            "repair_hint": "hermes gateway status; launchctl kickstart gui/$UID/ai.hermes.gateway",
        }
    )

    # H4: Resource
    h4_score = 0
    h4_evidence: list[str] = []
    logs_dir = collect / "logs"
    if logs_dir.is_dir():
        for p in logs_dir.iterdir():
            if not p.is_file():
                continue
            text = read_text(p)
            if re.search(r"oom|out of memory|killed|memoryerror|swap", text, re.I):
                h4_score += 3
                h4_evidence.append(f"oom_hint_in_{p.name}")
                break
    hypotheses.append(
        {
            "id": "H4_resource_oom",
            "title": "RAM / OOM pressure (chat+face budget)",
            "score": h4_score,
            "upstream_refs": [],
            "evidence": h4_evidence,
            "plan_section": "Resource",
            "repair_hint": "Unload heavy local models / disable face render before chat; check Activity Monitor",
        }
    )

    ranked = sorted(hypotheses, key=lambda h: h["score"], reverse=True)
    primary = ranked[0]["id"] if ranked else None

    report = {
        "format": "hermes-desktop-ops/report-v1",
        "incident_id": incident_id,
        "hostname": host,
        "collected_at_utc": ts,
        "os": os_name,
        "arch": arch,
        "signals": {
            "hermes_bin": hermes_bin or None,
            "port_9130": port,
            "auth_json": auth_status,
            "nous_mentioned": nous,
            "refresh_token_null": rt_null,
            "quarantine_or_revoke_hint": quarantine,
        },
        "hypotheses_ranked": ranked,
        "primary_hypothesis": primary,
        "plan_pipe": {
            "format": "hermes-desktop-ops/report-v1",
            "instructions": (
                "Paste report.md into the MOE RCA plan under "
                "'Findings from collectors'. Merge hypotheses_ranked "
                "evidence into the MOE expert table."
            ),
            "section_anchor": "Findings from collectors",
        },
    }

    (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Hermes Desktop Incident Report",
        "",
        "```",
        f"incident_id: {incident_id}",
        f"hostname: {host}",
        f"collected_at_utc: {ts}",
        f"os: {os_name}",
        f"arch: {arch}",
        f"hermes_home: {os.environ.get('HERMES_HOME', '')}",
        "```",
        "",
        "## Plan pipe",
        "",
        "- Format: `hermes-desktop-ops/report-v1`",
        "- Paste this file into the MOE RCA plan under **Findings from collectors**.",
        f"- Primary hypothesis: **{primary or 'unclassified'}**",
        "",
        "## Signals",
        "",
        "| Signal | Value |",
        "|--------|-------|",
        f"| hermes on PATH | {'yes' if hermes_bin else 'no'} |",
        f"| hermes bin | `{hermes_bin or 'none'}` |",
        f"| port 9130 | {port} |",
        f"| auth.json | {auth_status} |",
        f"| nous mentioned | {nous} |",
        f"| refresh_token null | {rt_null} |",
        f"| quarantine/revoke hint | {quarantine} |",
        "",
        "## Ranked hypotheses",
        "",
    ]
    for i, h in enumerate(ranked, 1):
        ev = ", ".join(h.get("evidence") or []) or "(none yet)"
        refs = ", ".join(h.get("upstream_refs") or []) or "—"
        lines.extend(
            [
                f"### {i}. `{h['id']}` (score={h['score']})",
                f"- **Title:** {h['title']}",
                f"- **Plan section:** {h['plan_section']}",
                f"- **Evidence:** {ev}",
                f"- **Upstream:** {refs}",
                f"- **Repair hint:** {h.get('repair_hint', '—')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Recommended next steps",
            "",
            "1. Keep this zip + `.report.md` on Desktop; run the same collector on the other Mac.",
            "2. Share both Desktop incident packs for confirmatory MOE RCA.",
            "3. From `~/Desktop/hermes-desktop-ops`: `./hermes-desktop-repair.sh` (dry-run), then `--apply` if agreed.",
            "4. If H2 leads: `hermes auth add nous --type oauth`.",
            "5. If H1 leads: ensure Desktop uses venv Python; restart gateway; recheck `:9130`.",
            "",
            "## Bundle contents",
            "",
            "See `collect/` inside the zip for doctor, gateway, install, launchd, ports, logs, crashes, auth fingerprint.",
            "",
        ]
    )
    (report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
