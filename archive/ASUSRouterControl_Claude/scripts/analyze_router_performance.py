#!/usr/bin/env python3
"""Comprehensive RT-AC68U performance analysis.

Downloads the full NVRAM dump, runs all live probes, pulls historical
optimizer recommendations, and cross-references everything against a
curated knowledge base of Merlin-firmware performance settings.

Usage:
    PYTHONPATH=src python scripts/analyze_router_performance.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── colour helpers ──────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

def _h(title: str) -> str:
    bar = "─" * (60 - len(title) - 2)
    return f"\n{BOLD}{CYAN}┌─ {title} {bar}{RESET}"

def _ok(msg: str)   -> str: return f"  {GREEN}✔{RESET}  {msg}"
def _warn(msg: str) -> str: return f"  {YELLOW}⚠{RESET}  {msg}"
def _bad(msg: str)  -> str: return f"  {RED}✖{RESET}  {msg}"
def _info(msg: str) -> str: return f"  {DIM}·{RESET}  {msg}"


# ── RT-AC68U Merlin performance knowledge base ──────────────────────────────

@dataclass
class NvramRule:
    key: str
    optimal: str | list[str]          # value(s) considered optimal
    label: str                         # short display name
    description: str                   # why this matters
    impact: str                        # "high" | "medium" | "low"
    reversible: bool = True
    condition: str = ""                # e.g. "only if qos_enable=1"

NVRAM_RULES: list[NvramRule] = [
    # ── CTF / Hardware NAT ─────────────────────────────────────────────────
    NvramRule("ctf_disable", "0", "Hardware NAT (CTF)",
              "Cut-Through Forwarding offloads NAT to hardware. Disabled = software NAT, "
              "significantly lower WAN throughput at gigabit speeds.", "high"),
    NvramRule("ctf_fa_mode", "2", "CTF Flow Accelerator",
              "FA mode 2 enables both CTF and Flow Accelerator for maximum NAT offload.", "high"),

    # ── WiFi 2.4 GHz ───────────────────────────────────────────────────────
    NvramRule("wl0_turbo_qam", "1", "2.4 GHz 256-QAM (TurboQAM)",
              "Enables 256-QAM on 2.4 GHz, raising theoretical peak from 150 → 200 Mbps "
              "for close-range clients on supported devices.", "medium"),
    NvramRule("wl0_txbf", "1", "2.4 GHz Explicit Beamforming",
              "Focuses radio energy toward clients, improving SNR and range.", "medium"),
    NvramRule("wl0_itxbf", "1", "2.4 GHz Implicit Beamforming",
              "Beamforming for clients that don't advertise beamforming support.", "low"),
    NvramRule("wl0_frameburst", "1", "2.4 GHz Frame Burst",
              "Reduces inter-frame gaps; improves throughput when only one client active.", "low"),
    NvramRule("wl0_obss_coex", "0", "2.4 GHz OBSS Coexistence (disable)",
              "When enabled, router falls back to 20 MHz if neighbouring APs detected on "
              "overlapping channels. Disable to hold 40 MHz if channel is stable.", "medium"),
    NvramRule("wl0_bw", ["0", "2"], "2.4 GHz Bandwidth",
              "0=auto or 2=40 MHz. 20 MHz (1) halves 2.4 GHz throughput.", "medium"),

    # ── WiFi 5 GHz ─────────────────────────────────────────────────────────
    NvramRule("wl1_turbo_qam", "1", "5 GHz TurboQAM",
              "Enables 1024-QAM on 5 GHz for close-range throughput boost on AC Wave 2 clients.",
              "medium"),
    NvramRule("wl1_txbf", "1", "5 GHz Explicit Beamforming",
              "Steers 5 GHz signal toward each client for better range and throughput.", "medium"),
    NvramRule("wl1_itxbf", "1", "5 GHz Implicit Beamforming",
              "Beamforming for legacy clients.", "low"),
    NvramRule("wl1_frameburst", "1", "5 GHz Frame Burst",
              "Throughput improvement for lightly loaded 5 GHz band.", "low"),
    NvramRule("wl1_mumimo", "1", "5 GHz MU-MIMO",
              "Serves multiple 5 GHz clients simultaneously instead of round-robin. "
              "RT-AC68U supports 3x3 MU-MIMO on 5 GHz.", "high"),
    NvramRule("wl1_bw", "2", "5 GHz Bandwidth (80 MHz)",
              "5 GHz should run 80 MHz (bw=2) for AC. 40 MHz (bw=1) or 20 MHz halves throughput.",
              "high"),

    # ── Services / CPU+RAM impact ───────────────────────────────────────────
    NvramRule("wrs_enable", "0", "AiProtect / WebAdvisor (disable if unused)",
              "Trend Micro engine performs deep-packet inspection on every packet. "
              "Costs ~30-60 MB RAM and measurable CPU on the RT-AC68U BCM4708A0.", "high",
              condition="only beneficial if AiProtect not actively used"),
    NvramRule("wrs_protect_enable", "0", "AiProtect network protection (disable if unused)",
              "Companion to wrs_enable; same Trend Micro DPI engine.", "high",
              condition="pair with wrs_enable"),
    NvramRule("TM_EULA", "0", "Trend Micro EULA / cloud telemetry",
              "Disabling stops telemetry callbacks to Trend Micro cloud; reduces background traffic.",
              "low"),
    NvramRule("amas_enable", "0", "AiMesh controller (disable on standalone)",
              "AiMesh control plane daemons (mastiff, cfg_server) consume ~15-25 MB RAM "
              "on routers not part of a mesh.", "medium",
              condition="only if no AiMesh satellite nodes are connected"),
    NvramRule("smart_connect_x", "0", "Band Steering (disable if not needed)",
              "Smart Connect steers clients between bands but adds latency during steering "
              "events and can cause reconnection drops.", "low",
              condition="disable if clients manage their own band selection reliably"),
    NvramRule("apps_analysis", "0", "DPI Traffic Analysis (bwdpi)",
              "Required for Adaptive QoS classification. Disable only if switching to "
              "Traditional QoS or disabling QoS entirely — reclaims ~10-20 MB RAM.", "medium",
              condition="only if qos_type≠1 (Adaptive QoS)"),
    NvramRule("enable_webdav", "0", "WebDAV / DDNS service",
              "WebDAV exposes USB storage over HTTPS; disable if unused to close attack surface "
              "and reduce background connections.", "low"),
    NvramRule("VPNServer_enable", "0", "OpenVPN Server",
              "VPN server daemon costs ~5-10 MB RAM when idle; disable if not actively used.",
              "low"),
    NvramRule("enable_samba", "0", "Samba / CIFS file sharing",
              "Samba daemon costs memory; disable if no USB storage sharing needed.", "low"),
    NvramRule("enable_ftp", "0", "FTP server",
              "FTP exposes storage with weak security model; disable if unused.", "low"),
    NvramRule("upnp_enable", "0", "UPnP (disable if unused)",
              "UPnP allows devices to open firewall ports automatically — security risk. "
              "Disable and manually configure port forwarding if possible.", "medium",
              reversible=True),

    # ── QoS ────────────────────────────────────────────────────────────────
    NvramRule("qos_enable", ["0", "1"], "QoS State",
              "QoS adds per-packet classification CPU overhead. Disable entirely if all "
              "devices have symmetric access needs. If enabled, Adaptive QoS (type=1) is "
              "more accurate than Traditional.", "medium"),

    # ── Admin surface ───────────────────────────────────────────────────────
    NvramRule("misc_http_x", "0", "WAN remote admin access (disable)",
              "Exposes router admin UI to the internet. Disable unless remote access required; "
              "use VPN instead.", "high", reversible=True),

    # ── IPv6 ───────────────────────────────────────────────────────────────
    NvramRule("ipv6_service", ["", "disabled"], "IPv6 service",
              "If ISP does not provide IPv6, disable to prevent unnecessary RA/DHCPv6 traffic "
              "and reduce connection-time overhead.", "low"),
]

# sysctl optimal values for Merlin / Linux 2.6.36 kernel
SYSCTL_RULES: dict[str, dict[str, Any]] = {
    "net.core.rmem_max": {
        "optimal_min": 4_194_304,
        "label": "Socket receive buffer max",
        "desc": "Low values throttle high-throughput TCP flows. ≥4 MB recommended.",
        "impact": "high",
    },
    "net.core.wmem_max": {
        "optimal_min": 4_194_304,
        "label": "Socket send buffer max",
        "desc": "Low values throttle upload TCP flows. ≥4 MB recommended.",
        "impact": "high",
    },
    "net.ipv4.tcp_fastopen": {
        "optimal_min": 3,
        "label": "TCP Fast Open",
        "desc": "Reduces connection setup RTT by piggybacking data on SYN. "
                "Value 3 = both client and server mode.",
        "impact": "medium",
    },
    "net.core.netdev_max_backlog": {
        "optimal_min": 5000,
        "label": "NIC receive queue depth",
        "desc": "Prevents packet drops at high throughput. ≥5000 recommended.",
        "impact": "medium",
    },
    "net.ipv4.tcp_congestion_control": {
        "optimal_values": ["bbr", "cubic"],
        "label": "TCP congestion algorithm",
        "desc": "BBR or CUBIC outperform legacy RENO especially over lossy links.",
        "impact": "medium",
    },
}

# Services known to consume CPU/RAM with no user-facing benefit on standalone setups
BLOAT_SERVICES = {
    "aaews":       "AiCloud relay daemon — disable AiCloud to stop",
    "mastiff":     "AiMesh controller — disable amas_enable to stop",
    "cfg_server":  "AiMesh config sync — disable amas_enable to stop",
    "awsiot":      "AWS IoT cloud push — disable in Administration → Cloud Sync",
    "bwdpi":       "DPI engine (Adaptive QoS/AiProtect) — disable apps_analysis",
    "wred":        "bwdpi child process",
    "conn_diag":   "Connection diagnostics cloud uploader",
    "dcd":         "Device classification daemon",
}


# ── Analysis engine ─────────────────────────────────────────────────────────

@dataclass
class Finding:
    category: str
    impact: str        # high / medium / low / info
    title: str
    detail: str
    current: str = ""
    proposed: str = ""


async def run_analysis() -> None:
    from asusroutercontrol.config import load_config
    from asusroutercontrol.credentials import get_router_credentials
    from asusroutercontrol.datastore import DataStore
    from asusroutercontrol.optimizer import generate_recommendations, suggest_settings
    from asusroutercontrol.probes import (
        probe_config,
        probe_services,
        probe_sysctl,
        probe_system,
        probe_wifi_channels,
    )
    from asusroutercontrol.ssh import RouterSSH

    cfg = load_config()
    username, password = get_router_credentials()
    if not username or not password:
        print(_bad("No router credentials found. Run: arc credentials set"))
        sys.exit(1)

    print(f"\n{BOLD}{'━'*62}{RESET}")
    print(f"{BOLD}  RT-AC68U Performance Analysis{RESET}")
    print(f"{BOLD}{'━'*62}{RESET}")
    print(f"  Host : {cfg.router_host}:{cfg.ssh_port}")
    print(f"  User : {username}")

    # ── 1. SSH connection ────────────────────────────────────────────────────
    print(_h("Connecting"))
    # RouterSSH reads trust_mode / known_hosts / fingerprint from load_config() internally
    ssh = RouterSSH(
        hostname=cfg.router_host,
        username=username,
        password=password,
        port=cfg.ssh_port,
    )
    await ssh.connect()
    print(_ok("SSH connection established"))

    findings: list[Finding] = []

    try:
        # ── 2. Full NVRAM dump ───────────────────────────────────────────────
        print(_h("Downloading full NVRAM"))
        result = await ssh.run("nvram show 2>/dev/null")
        nvram: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                nvram[k.strip()] = v.strip()
        print(_ok(f"{len(nvram):,} NVRAM keys downloaded"))

        # ── 3. Live probes ───────────────────────────────────────────────────
        print(_h("Running live probes"))

        sys_snap = await probe_system(ssh)
        print(_ok(f"System: CPU {sys_snap.cpu_pct:.1f}%  RAM {sys_snap.ram_pct:.1f}%  "
                  f"Temp {sys_snap.temp_c or '?'}°C"))

        sysctl_snap = await probe_sysctl(ssh)
        print(_ok(f"sysctl: {len(sysctl_snap.entries)} keys read  "
                  f"({sysctl_snap.optimal_count}/{sysctl_snap.total_count} optimal)"))

        svc_snap = await probe_services(ssh)
        print(_ok(f"Services: {len(svc_snap.services)} processes  "
                  f"bloat RSS {svc_snap.bloat_rss_kb / 1024:.0f} MB  "
                  f"({svc_snap.bloat_count} bloat daemons)"))

        wifi_surveys = await probe_wifi_channels(ssh)
        print(_ok(f"WiFi channels: {len(wifi_surveys)} band(s) surveyed"))

        config_snap = await probe_config(ssh)
        tracked = json.loads(config_snap.nvram_json)
        print(_ok(f"Config snapshot: {len(tracked)} tracked keys"))

        # ── 4. NVRAM knowledge-base analysis ────────────────────────────────
        print(_h("Analysing NVRAM settings"))

        for rule in NVRAM_RULES:
            current = nvram.get(rule.key, tracked.get(rule.key, ""))
            optimal = rule.optimal if isinstance(rule.optimal, list) else [rule.optimal]
            if current in optimal:
                continue  # optimal — no finding
            if current == "" and "" in optimal:
                continue

            detail = rule.description
            if rule.condition:
                detail += f"\n       Condition: {rule.condition}"

            findings.append(Finding(
                category="nvram",
                impact=rule.impact,
                title=rule.label,
                detail=detail,
                current=current or "(not set)",
                proposed=optimal[0],
            ))

        # ── 5. sysctl analysis — entries is list[SysctlEntry] with .key/.current ─
        sysctl_by_key = {e.key: e for e in sysctl_snap.entries}
        for skey, rule in SYSCTL_RULES.items():
            entry = sysctl_by_key.get(skey)
            if entry is None:
                continue
            current_val = entry.current
            if "optimal_min" in rule:
                try:
                    numeric = int(str(current_val).split()[0])
                    if numeric >= rule["optimal_min"]:
                        continue
                    findings.append(Finding(
                        category="sysctl",
                        impact=rule["impact"],
                        title=rule["label"],
                        detail=rule["desc"],
                        current=str(current_val),
                        proposed=f"≥ {rule['optimal_min']:,}",
                    ))
                except (ValueError, IndexError):
                    pass
            elif "optimal_values" in rule:
                if str(current_val).strip() in rule["optimal_values"]:
                    continue
                findings.append(Finding(
                    category="sysctl",
                    impact=rule["impact"],
                    title=rule["label"],
                    detail=rule["desc"],
                    current=str(current_val),
                    proposed=" or ".join(rule["optimal_values"]),
                ))

        # ── 6. Service bloat analysis — services is list[ServiceEntry] ─────────
        running_names = {s.name.lower() for s in svc_snap.services}
        for svc_name, explanation in BLOAT_SERVICES.items():
            if svc_name in running_names:
                rss_kb = next(
                    (s.rss_kb for s in svc_snap.services if s.name.lower() == svc_name),
                    0,
                )
                findings.append(Finding(
                    category="services",
                    impact="medium",
                    title=f"Bloat service running: {svc_name}",
                    detail=explanation,
                    current=f"running ({rss_kb} KB RSS)" if rss_kb else "running",
                    proposed="stopped",
                ))

        # ── 7. WiFi channel analysis — ChannelSurvey has .entries list ─────────
        for survey in wifi_surveys:
            if survey.best_channel and survey.best_channel != survey.current_channel:
                cur_entry  = next((e for e in survey.entries if e.is_current), None)
                best_entry = next((e for e in survey.entries
                                   if e.channel == survey.best_channel), None)
                cur_util  = cur_entry.utilization_pct  if cur_entry  else 0.0
                best_util = best_entry.utilization_pct if best_entry else 0.0
                util_diff = cur_util - best_util
                findings.append(Finding(
                    category="wifi",
                    impact="medium" if util_diff > 20 else "low",
                    title=f"WiFi {survey.band} GHz channel not optimal",
                    detail=(
                        f"Current ch {survey.current_channel} utilisation {cur_util:.0f}%. "
                        f"Channel {survey.best_channel} has {best_util:.0f}% utilisation "
                        f"({util_diff:.0f}pp improvement available). {survey.best_reason}"
                    ),
                    current=f"ch {survey.current_channel}",
                    proposed=f"ch {survey.best_channel}",
                ))

        # ── 8. System health findings ────────────────────────────────────────
        if sys_snap.temp_c and sys_snap.temp_c > 85:
            findings.append(Finding(
                category="thermal",
                impact="high",
                title="Router temperature elevated",
                detail=f"Temperature {sys_snap.temp_c}°C exceeds 85°C warning threshold. "
                       "Check ventilation; sustained high temps shorten component lifespan.",
                current=f"{sys_snap.temp_c}°C",
                proposed="< 80°C",
            ))
        elif sys_snap.temp_c and sys_snap.temp_c > 75:
            findings.append(Finding(
                category="thermal",
                impact="medium",
                title="Router temperature warm",
                detail=f"Temperature {sys_snap.temp_c}°C is elevated. Ensure adequate airflow.",
                current=f"{sys_snap.temp_c}°C",
                proposed="< 75°C",
            ))

        if sys_snap.ram_pct and sys_snap.ram_pct > 85:
            findings.append(Finding(
                category="memory",
                impact="high",
                title="RAM utilisation critical",
                detail=f"{sys_snap.ram_pct:.0f}% RAM used. Router may start dropping "
                       "connections or become unresponsive. Disable unused services.",
                current=f"{sys_snap.ram_pct:.0f}%",
                proposed="< 80%",
            ))
        elif sys_snap.ram_pct and sys_snap.ram_pct > 70:
            findings.append(Finding(
                category="memory",
                impact="medium",
                title="RAM utilisation elevated",
                detail=f"{sys_snap.ram_pct:.0f}% RAM used. Consider disabling unused services.",
                current=f"{sys_snap.ram_pct:.0f}%",
                proposed="< 70%",
            ))

        # ── 9. Historical optimizer (DataStore) ──────────────────────────────
        print(_h("Historical analysis (DataStore)"))
        store = DataStore(cfg.data_dir / "router.db")
        await store.open()
        try:
            hist_recs  = await generate_recommendations(store, days=30)
            nvram_sugg = await suggest_settings(store)
        finally:
            await store.close()
        print(_ok(f"{len(hist_recs)} trend recommendations, {len(nvram_sugg)} NVRAM suggestions"))

    finally:
        await ssh.disconnect()

    # ── Output report ────────────────────────────────────────────────────────
    _print_report(findings, hist_recs, nvram_sugg, nvram, sys_snap, sysctl_snap, svc_snap)


def _print_report(
    findings: list[Finding],
    hist_recs: list[dict],
    nvram_sugg: list[dict],
    nvram: dict[str, str],
    sys_snap: Any,
    sysctl_snap: Any,
    svc_snap: Any,
) -> None:
    impact_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: impact_order.get(f.impact, 9))

    # ── Live snapshot ────────────────────────────────────────────────────────
    print(_h("Live Snapshot"))
    cpu  = f"{sys_snap.cpu_pct:.1f}%" if sys_snap.cpu_pct else "?"
    ram  = f"{sys_snap.ram_pct:.1f}%" if sys_snap.ram_pct else "?"
    temp = f"{sys_snap.temp_c}°C"    if sys_snap.temp_c  else "?"
    uptime_h = (sys_snap.uptime_s or 0) / 3600
    print(_info(f"CPU {cpu}   RAM {ram}   Temp {temp}   "
                f"Uptime {uptime_h:.0f}h"))
    if sys_snap.conntrack_count:
        pct = (sys_snap.conntrack_count / sys_snap.conntrack_max * 100
               if sys_snap.conntrack_max else 0)
        colour = RED if pct > 80 else YELLOW if pct > 60 else GREEN
        print(_info(f"Conntrack {sys_snap.conntrack_count:,} / {sys_snap.conntrack_max:,} "
                    f"({colour}{pct:.0f}%{RESET})"))

    # ── Key NVRAM values ─────────────────────────────────────────────────────
    print(_h("Key NVRAM Values"))
    key_groups = [
        ("CTF/NAT",   ["ctf_disable", "ctf_fa_mode"]),
        ("2.4 GHz",   ["wl0_bw", "wl0_chanspec", "wl0_turbo_qam", "wl0_txbf",
                        "wl0_itxbf", "wl0_frameburst", "wl0_obss_coex"]),
        ("5 GHz",     ["wl1_bw", "wl1_chanspec", "wl1_turbo_qam", "wl1_txbf",
                        "wl1_itxbf", "wl1_frameburst", "wl1_mumimo"]),
        ("Services",  ["wrs_enable", "amas_enable", "apps_analysis", "upnp_enable",
                        "misc_http_x", "ipv6_service", "qos_enable", "qos_type"]),
        ("TCP/sysctl",["net.core.rmem_max", "net.ipv4.tcp_fastopen",
                        "net.ipv4.tcp_congestion_control"]),
    ]
    sysctl_dict = {e.key: e.current for e in sysctl_snap.entries}
    for group, keys in key_groups:
        vals = []
        for k in keys:
            v = nvram.get(k) or sysctl_dict.get(k, "")
            vals.append(f"{DIM}{k}{RESET}={BOLD}{v or '—'}{RESET}")
        print(f"  {CYAN}{group:<10}{RESET}  {'   '.join(vals)}")

    # ── NVRAM + probe findings ────────────────────────────────────────────────
    high   = [f for f in findings if f.impact == "high"]
    medium = [f for f in findings if f.impact == "medium"]
    low    = [f for f in findings if f.impact == "low"]

    def _print_findings(items: list[Finding], colour: str, label: str) -> None:
        if not items:
            return
        print(_h(f"{label} ({len(items)})"))
        for f in items:
            icon = _bad if colour == RED else (_warn if colour == YELLOW else _info)
            print(icon(f"{BOLD}{f.title}{RESET}  [{f.category}]"))
            for line in f.detail.splitlines():
                print(f"       {line}")
            if f.current:
                print(f"       {DIM}Current:{RESET} {f.current}  "
                      f"{DIM}→  Proposed:{RESET} {BOLD}{f.proposed}{RESET}")

    _print_findings(high,   RED,    "🔴 High Impact")
    _print_findings(medium, YELLOW, "🟡 Medium Impact")
    _print_findings(low,    "",     "🔵 Low Impact")

    # ── Historical trend recommendations ─────────────────────────────────────
    if hist_recs and not (len(hist_recs) == 1 and hist_recs[0].get("priority") == "info"):
        print(_h("📈 Trend Recommendations (30-day)"))
        for rec in hist_recs:
            pri = rec.get("priority", "info")
            fn = _bad if pri == "high" else (_warn if pri == "medium" else _info)
            print(fn(f"{BOLD}{rec.get('description', '')}{RESET}"))
            if rec.get("action"):
                print(f"       → {rec['action']}")
    else:
        print(_h("📈 Trend Recommendations"))
        print(_ok("All 30-day trends within normal ranges"))

    # ── NVRAM suggestions from optimizer ────────────────────────────────────
    if nvram_sugg and not (len(nvram_sugg) == 1 and "message" in nvram_sugg[0]):
        print(_h("⚙️  Data-Backed NVRAM Suggestions"))
        for s in nvram_sugg:
            print(_warn(f"{BOLD}{s.get('key', '')}{RESET}  "
                        f"risk={s.get('risk', '?')}  reversible={s.get('reversible')}"))
            print(f"       {s.get('rationale', '')} ")
            print(f"       {DIM}Current:{RESET} {s.get('current', '')}  "
                  f"{DIM}→  Proposed:{RESET} {BOLD}{s.get('proposed', '')}{RESET}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'━'*62}{RESET}")
    total = len(findings)
    print(f"  Findings:  {RED}{len(high)} high{RESET}  "
          f"{YELLOW}{len(medium)} medium{RESET}  "
          f"{len(low)} low   ({total} total)")
    print(f"  NVRAM keys analysed : {len(NVRAM_RULES)}")
    print(f"  Sysctl rules        : {len(SYSCTL_RULES)}")
    print(f"  Trend recommendations: {len(hist_recs)}")
    print(f"{BOLD}{'━'*62}{RESET}\n")

    if not findings and not nvram_sugg:
        print(_ok("Router is optimally configured — no changes recommended.\n"))


if __name__ == "__main__":
    asyncio.run(run_analysis())
