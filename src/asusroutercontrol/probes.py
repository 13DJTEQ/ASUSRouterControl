"""Network and system health probes — all run via SSH to the router."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from asusroutercontrol._time import utcnow
from asusroutercontrol.models import (
    ChannelSurvey,
    ChannelSurveyEntry,
    ConfigSnapshot,
    LatencyProbe,
    ServiceAudit,
    ServiceEntry,
    SysctlEntry,
    SysctlSnapshot,
    SystemSnapshot,
    WiFiSnapshot,
)
from asusroutercontrol.ssh import RouterSSH

log = logging.getLogger(__name__)

# Targets for latency probes
LATENCY_TARGETS = {
    "gateway": "76.94.96.1",
    "cloudflare": "1.1.1.1",
    "google": "8.8.8.8",
}

PING_COUNT = 20


async def probe_latency(ssh: RouterSSH) -> list[LatencyProbe]:
    """Ping multiple targets from the router and return latency stats."""
    now = utcnow()
    results: list[LatencyProbe] = []

    for name, ip in LATENCY_TARGETS.items():
        try:
            r = await ssh.run(f"ping -c {PING_COUNT} -W 5 {ip} 2>&1")
            probe = _parse_ping(r.stdout, name, now)
            results.append(probe)
        except Exception:
            log.exception("Latency probe failed for %s", name)
            results.append(LatencyProbe(timestamp=now, target=name))

    return results


def _parse_ping(output: str, target: str, ts: datetime) -> LatencyProbe:
    """Parse BusyBox ping summary into LatencyProbe."""
    probe = LatencyProbe(timestamp=ts, target=target)
    # Loss + samples from transmission summary:
    # "20 packets transmitted, 18 packets received, 10% packet loss"
    tx_rx_m = re.search(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+packets received",
        output,
    )
    if tx_rx_m:
        transmitted = int(tx_rx_m.group(1))
        received = int(tx_rx_m.group(2))
        probe.samples = transmitted
        if transmitted > 0:
            loss_pct = ((transmitted - received) / transmitted) * 100.0
            probe.loss_pct = max(0.0, loss_pct)

    # Fallback to explicit loss percent (supports integer and decimal forms).
    # Examples: "0% packet loss", "5.0% packet loss"
    if probe.samples == 0 or probe.loss_pct == 0.0:
        loss_m = re.search(r"([\d.]+)%\s*packet loss", output)
        if loss_m:
            try:
                probe.loss_pct = float(loss_m.group(1))
            except ValueError:
                pass

    # Samples fallback: "X packets transmitted"
    if probe.samples == 0:
        tx_m = re.search(r"(\d+)\s+packets transmitted", output)
        if tx_m:
            probe.samples = int(tx_m.group(1))

    # Stats: "round-trip min/avg/max = 6.633/9.591/17.813 ms"
    stats_m = re.search(r"min/avg/max\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
    if stats_m:
        probe.min_ms = float(stats_m.group(1))
        probe.avg_ms = float(stats_m.group(2))
        probe.max_ms = float(stats_m.group(3))
        probe.jitter_ms = probe.max_ms - probe.min_ms

    return probe


async def probe_system(ssh: RouterSSH) -> SystemSnapshot:
    """Capture CPU, RAM, temp, uptime, conntrack from the router."""
    now = utcnow()
    snap = SystemSnapshot(timestamp=now)

    try:
        # CPU usage from /proc/stat (instantaneous sample)
        r = await ssh.run(
            "head -1 /proc/stat | awk '{u=$2+$4; t=$2+$3+$4+$5+$6+$7+$8;"
            " printf \"%.1f %d %d\", u/t*100, u, t}'"
        )
        if r.ok and r.stdout:
            parts = r.stdout.split()
            if parts:
                snap.cpu_pct = float(parts[0])

        # RAM
        r = await ssh.run(
            "free | awk '/Mem:/ {printf \"%.1f %d %d\", $3/$2*100, $3/1024, $2/1024}'"
        )
        if r.ok and r.stdout:
            parts = r.stdout.split()
            if parts:
                snap.ram_pct = float(parts[0])

        # Temperature — /proc/dmu/temperature can emit binary data (0xf8 etc.)
        # that crashes asyncssh's UTF-8 decode, so pipe through tr to strip it.
        try:
            r = await ssh.run(
                "cat /proc/dmu/temperature 2>/dev/null | tr -cd '[:print:]\\n'"
                " || wl -i eth2 phy_tempsense 2>/dev/null | awk '{print $1/2+20}'"
            )
            if r.ok and r.stdout:
                temp_m = re.search(r"([\d.]+)", r.stdout)
                if temp_m:
                    snap.temp_c = float(temp_m.group(1))
        except Exception:
            log.warning("Temperature probe failed (binary data?), skipping")

        # Uptime in seconds
        r = await ssh.run("cat /proc/uptime | awk '{printf \"%d\", $1}'")
        if r.ok and r.stdout:
            snap.uptime_s = int(float(r.stdout))

        # Conntrack
        r = await ssh.run("cat /proc/sys/net/netfilter/nf_conntrack_count")
        if r.ok and r.stdout:
            snap.conntrack_count = int(r.stdout)
        r = await ssh.run("cat /proc/sys/net/netfilter/nf_conntrack_max")
        if r.ok and r.stdout:
            snap.conntrack_max = int(r.stdout)

    except Exception:
        log.exception("System probe failed")

    return snap


# NVRAM keys tracked for config snapshots
TRACKED_NVRAM_KEYS = [
    "sshd_enable", "sshd_port", "upnp_enable", "jffs2_on",
    "wrs_enable", "wrs_protect_enable", "apps_analysis", "TM_EULA",
    "wan_dns1_x", "wan_dns2_x", "wan0_dnsenable_x",
    "dhcp_static_x", "dhcp_staticlist", "dhcp_hostnames",
    "qos_enable", "qos_type", "qos_ibw", "qos_obw",
    "ctf_disable", "ctf_fa_mode",
    "wl0_chanspec", "wl1_chanspec", "wl0_bw", "wl1_bw",
    "wl0_txpower", "wl1_txpower",
    "wl0_turbo_qam", "wl1_turbo_qam",
    "wl0_txbf", "wl1_txbf", "wl0_itxbf", "wl1_itxbf",
    "wl0_frameburst", "wl1_frameburst",
    "wl0_obss_coex", "wl1_obss_coex",
    "wl0_mumimo", "wl1_mumimo",
    "amas_enable", "smart_connect_x",
    "misc_http_x", "enable_webdav", "ipv6_service",
    "VPNServer_enable", "enable_samba", "enable_ftp",
]


async def probe_config(ssh: RouterSSH, source: str = "scheduled") -> ConfigSnapshot:
    """Snapshot tracked NVRAM keys from the router."""
    import json

    now = utcnow()
    nvram: dict[str, str] = {k: "" for k in TRACKED_NVRAM_KEYS}

    key_pattern = "|".join(TRACKED_NVRAM_KEYS)
    batch_cmd = f"nvram show 2>/dev/null | grep -E '^({key_pattern})='"
    batch = await ssh.run(batch_cmd)
    if batch.ok and batch.stdout:
        for line in batch.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in nvram:
                nvram[key] = value
    else:
        # Fallback to per-key calls if batch command is unavailable/fails.
        for key in TRACKED_NVRAM_KEYS:
            r = await ssh.run(f"nvram get {key} 2>/dev/null")
            if r.ok:
                nvram[key] = r.stdout

    return ConfigSnapshot(
        timestamp=now,
        source=source,
        nvram_json=json.dumps(nvram, sort_keys=True),
    )


def diff_config_snapshots(current_json: str, previous_json: str) -> str:
    """Return human-readable diff between two NVRAM JSON blobs."""
    import json

    cur = json.loads(current_json)
    prev = json.loads(previous_json)
    changes: list[str] = []
    all_keys = sorted(set(cur) | set(prev))
    for k in all_keys:
        old = prev.get(k)
        new = cur.get(k)
        if old != new:
            changes.append(f"{k}: {old!r}→{new!r}")
    return "; ".join(changes) if changes else ""


# ---------------------------------------------------------------------------
# Known bloat services — unnecessary daemons on standalone routers
# ---------------------------------------------------------------------------

KNOWN_BLOAT: dict[str, str] = {
    "aaews": "AiCloud daemon — disabled via NVRAM but may survive without reboot",
    "mastiff": "AiMesh controller — unnecessary on standalone routers",
    "cfg_server": "AiMesh config sync — unnecessary on standalone routers",
    "amas_lib": "AiMesh library — unnecessary on standalone routers",
    "awsiot": "AWS IoT cloud push — ASUS cloud notifications",
    "conn_diag": "Connection diagnostics — low value on stable networks",
    "wred": "bwdpi deep packet inspection engine",
    "bwdpi": "bwdpi bandwidth analysis daemon",
    "bwdpi_check": "bwdpi health checker",
    "bwdpi_wred_alive": "bwdpi watchdog",
    "dcd": "bwdpi data collection daemon",
}


async def probe_services(ssh: RouterSSH) -> ServiceAudit:
    """Audit running services, flag known bloat daemons."""
    now = utcnow()
    audit = ServiceAudit(timestamp=now)

    try:
        # BusyBox ps — fields vary; 'ps w' gives PID, USER, VSZ, STAT, COMMAND
        r = await ssh.run("ps w 2>/dev/null || ps")
        if not r.ok or not r.stdout:
            return audit

        seen_names: dict[str, ServiceEntry] = {}
        for line in r.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            vsz = 0
            try:
                vsz = int(parts[2])
            except (ValueError, IndexError):
                pass
            cmd = parts[4] if len(parts) > 4 else ""
            # Extract base name from path
            name = cmd.rsplit("/", 1)[-1] if "/" in cmd else cmd
            if not name or name.startswith("["):
                continue  # skip kernel threads

            bloat_reason = ""
            is_bloat = False
            for bloat_name, reason in KNOWN_BLOAT.items():
                if bloat_name in name:
                    is_bloat = True
                    bloat_reason = reason
                    break

            if name in seen_names:
                # Aggregate as additional thread
                seen_names[name].threads += 1
                seen_names[name].rss_kb += vsz
            else:
                entry = ServiceEntry(
                    pid=pid,
                    name=name,
                    rss_kb=vsz,
                    threads=1,
                    is_bloat=is_bloat,
                    bloat_reason=bloat_reason,
                )
                seen_names[name] = entry

        audit.services = list(seen_names.values())
        audit.total_rss_kb = sum(s.rss_kb for s in audit.services)
        bloat_services = [s for s in audit.services if s.is_bloat]
        audit.bloat_rss_kb = sum(s.rss_kb for s in bloat_services)
        audit.bloat_count = len(bloat_services)

    except Exception:
        log.exception("Service audit failed")

    return audit


# ---------------------------------------------------------------------------
# Sysctl tuning probe
# ---------------------------------------------------------------------------

# Optimal values for a 300 Mbps connection on 256MB ARM router
SYSCTL_RECOMMENDATIONS: dict[str, tuple[str, str]] = {
    "net.core.rmem_max": ("4194304", "4MB — adequate for 300 Mbps"),
    "net.core.wmem_max": ("4194304", "4MB — adequate for 300 Mbps"),
    "net.ipv4.tcp_rmem": ("4096 87380 4194304", "default 87380 matches tcp_wmem"),
    "net.ipv4.tcp_wmem": ("4096 87380 4194304", "default 87380 for write buffers"),
    "net.core.netdev_max_backlog": ("2000", "increased from default 1000"),
    "net.core.somaxconn": ("128", "default is fine for router workload"),
    "net.ipv4.tcp_fastopen": ("3", "enable TFO for client+server"),
}


async def probe_sysctl(ssh: RouterSSH) -> SysctlSnapshot:
    """Read key TCP/network sysctl values and compare against optimal."""
    now = utcnow()
    snap = SysctlSnapshot(timestamp=now)

    try:
        entries: list[SysctlEntry] = []
        for key, (recommended, note) in SYSCTL_RECOMMENDATIONS.items():
            proc_path = "/proc/sys/" + key.replace(".", "/")
            r = await ssh.run(f"cat {proc_path} 2>/dev/null")
            current = r.stdout.strip().replace("\t", " ") if r.ok else "(unreadable)"
            is_optimal = current == recommended
            entries.append(SysctlEntry(
                key=key,
                current=current,
                recommended=recommended,
                is_optimal=is_optimal,
                note=note,
            ))
        snap.entries = entries
        snap.total_count = len(entries)
        snap.optimal_count = sum(1 for e in entries if e.is_optimal)

    except Exception:
        log.exception("Sysctl probe failed")

    return snap


# ---------------------------------------------------------------------------
# WiFi channel survey
# ---------------------------------------------------------------------------


async def probe_wifi_channels(ssh: RouterSSH) -> list[ChannelSurvey]:
    """Run WiFi channel survey via wl chanim_stats."""
    now = utcnow()
    results: list[ChannelSurvey] = []

    bands = [
        ("2.4", "eth1"),
        ("5", "eth2"),
    ]

    for band_name, iface in bands:
        survey = ChannelSurvey(timestamp=now, band=band_name, interface=iface)

        try:
            # Get current channel
            r = await ssh.run(f"wl -i {iface} channel 2>/dev/null")
            if r.ok and r.stdout:
                ch_m = re.search(r"current mac channel\s+(\d+)", r.stdout)
                if ch_m:
                    survey.current_channel = int(ch_m.group(1))

            # Channel survey via chanim_stats
            r = await ssh.run(f"wl -i {iface} chanim_stats 2>/dev/null")
            if r.ok and r.stdout:
                entries: list[ChannelSurveyEntry] = []
                for line in r.stdout.splitlines():
                    # chanspec tx inbss obss nocat nopkt doze txop
                    # goodtx badtx glitch badplcp knoise timestamp
                    # or simpler: channel util interference noise
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    try:
                        # Try to parse chanspec as channel number
                        chanspec = parts[0]
                        # Extract channel number from chanspec (e.g. "6" or "6l" or "36/80")
                        ch_num_m = re.match(r"(\d+)", chanspec)
                        if not ch_num_m:
                            continue
                        ch_num = int(ch_num_m.group(1))
                        # txop field is typically index 7 (0-indexed) — represents free airtime
                        # For simpler BusyBox output, try to find numeric fields
                        numeric_vals = []
                        for p in parts[1:]:
                            try:
                                numeric_vals.append(float(p))
                            except ValueError:
                                pass
                        if len(numeric_vals) < 2:
                            continue

                        # inbss = in-BSS utilization, obss = other-BSS (interference)
                        inbss = numeric_vals[0] if len(numeric_vals) > 0 else 0
                        obss = numeric_vals[1] if len(numeric_vals) > 1 else 0
                        noise = numeric_vals[-1] if len(numeric_vals) > 2 else 0

                        entries.append(ChannelSurveyEntry(
                            channel=ch_num,
                            utilization_pct=min(100, inbss + obss),
                            interference_pct=min(100, obss),
                            noise_dbm=noise if noise < 0 else 0,
                            is_current=(ch_num == survey.current_channel),
                        ))
                    except (ValueError, IndexError):
                        continue

                survey.entries = entries

                # Find best channel (lowest total utilization)
                if entries:
                    non_current = [e for e in entries if not e.is_current]
                    candidates = non_current if non_current else entries
                    best = min(candidates, key=lambda e: e.utilization_pct)
                    current_entry = next(
                        (e for e in entries if e.is_current), None
                    )
                    if current_entry and best.utilization_pct < current_entry.utilization_pct * 0.6:
                        survey.best_channel = best.channel
                        survey.best_reason = (
                            f"Channel {best.channel} has {best.utilization_pct:.0f}% util "
                            f"vs current {current_entry.utilization_pct:.0f}%"
                        )

        except Exception:
            log.exception("WiFi channel survey failed for %s band", band_name)

        results.append(survey)

    return results


async def probe_wifi(ssh: RouterSSH) -> list[WiFiSnapshot]:
    """Capture per-band WiFi client count, signal strength, noise floor, and byte counters."""
    now = utcnow()
    results: list[WiFiSnapshot] = []

    bands = [
        ("2.4", "eth1"),
        ("5", "eth2"),
    ]

    # Fetch /proc/net/dev once for all interfaces
    iface_bytes = await _read_iface_bytes(ssh)

    for band_name, iface in bands:
        snap = WiFiSnapshot(timestamp=now, band=band_name)

        try:
            # Client list
            r = await ssh.run(f"wl -i {iface} assoclist 2>/dev/null")
            macs = re.findall(r"assoclist\s+([0-9A-Fa-f:]+)", r.stdout)
            snap.client_count = len(macs)

            # RSSI per client
            rssi_vals: list[float] = []
            for mac in macs[:20]:  # Cap to avoid slow probe on many clients
                r2 = await ssh.run(
                    f"wl -i {iface} sta_info {mac} 2>/dev/null | grep 'per antenna rssi'"
                )
                # "per antenna rssi of last rx data frame: -38 -40 -42"
                rssi_m = re.findall(r"-\d+", r2.stdout)
                if rssi_m:
                    # Use the best antenna reading
                    rssi_vals.append(max(float(v) for v in rssi_m))

            if rssi_vals:
                snap.avg_rssi = sum(rssi_vals) / len(rssi_vals)
                snap.min_rssi = min(rssi_vals)

            # Noise floor
            r = await ssh.run(f"wl -i {iface} noise 2>/dev/null")
            if r.ok and r.stdout:
                noise_m = re.search(r"-?\d+", r.stdout)
                if noise_m:
                    snap.noise_floor = float(noise_m.group())

            # Current channel
            r = await ssh.run(f"wl -i {iface} channel 2>/dev/null")
            if r.ok and r.stdout:
                ch_m = re.search(r"current mac channel\s+(\S+)", r.stdout)
                if ch_m:
                    snap.channel = ch_m.group(1)

            # Per-interface byte counters (for bandwidth metering)
            if iface in iface_bytes:
                snap.rx_bytes, snap.tx_bytes = iface_bytes[iface]

        except Exception:
            log.exception("WiFi probe failed for %s band", band_name)

        results.append(snap)

    # --- Wired LAN (vlan1) byte counters ---
    wired = WiFiSnapshot(timestamp=now, band="wired")
    if "vlan1" in iface_bytes:
        wired.rx_bytes, wired.tx_bytes = iface_bytes["vlan1"]
    results.append(wired)

    return results


async def _read_iface_bytes(ssh: RouterSSH) -> dict[str, tuple[int, int]]:
    """Parse /proc/net/dev to get (rx_bytes, tx_bytes) per interface."""
    result: dict[str, tuple[int, int]] = {}
    try:
        r = await ssh.run("cat /proc/net/dev")
        if not r.ok:
            return result
        for line in r.stdout.splitlines():
            # Format: "  iface: rx_bytes rx_packets ... tx_bytes tx_packets ..."
            if ":" not in line:
                continue
            iface_part, stats_part = line.split(":", 1)
            iface_name = iface_part.strip()
            fields = stats_part.split()
            if len(fields) >= 9:
                rx_bytes = int(fields[0])
                tx_bytes = int(fields[8])
                result[iface_name] = (rx_bytes, tx_bytes)
    except Exception:
        log.exception("Failed to read /proc/net/dev")
    return result


async def probe_client_traffic(
    ssh: RouterSSH,
) -> list[dict]:
    """Collect per-client byte counters via ``wl sta_info`` for each WiFi band.

    Returns a list of dicts:
        {mac, band, rssi, rx_bytes, tx_bytes}
    """
    results: list[dict] = []
    bands = [("2.4GHz", "eth1"), ("5GHz", "eth2")]

    for band_name, iface in bands:
        try:
            r = await ssh.run(f"wl -i {iface} assoclist 2>/dev/null")
            macs = re.findall(r"assoclist\s+([0-9A-Fa-f:]+)", r.stdout)

            for mac in macs[:20]:
                r2 = await ssh.run(f"wl -i {iface} sta_info {mac} 2>/dev/null")
                if not r2.ok:
                    continue
                out = r2.stdout

                tx_bytes = rx_bytes = rssi = None
                m = re.search(r"tx data bytes:\s+(\d+)", out)
                if m:
                    tx_bytes = int(m.group(1))
                m = re.search(r"rx data bytes:\s+(\d+)", out)
                if m:
                    rx_bytes = int(m.group(1))
                m = re.findall(r"per antenna rssi of last rx data frame:\s*(.+)", out)
                if m:
                    vals = [int(v) for v in re.findall(r"-?\d+", m[0]) if int(v) != 0]
                    if vals:
                        rssi = max(vals)

                if tx_bytes is not None or rx_bytes is not None:
                    results.append({
                        "mac": mac,
                        "band": band_name,
                        "rssi": rssi,
                        "rx_bytes": rx_bytes or 0,
                        "tx_bytes": tx_bytes or 0,
                    })
        except Exception:
            log.exception("Client traffic probe failed for %s", band_name)

    return results


def compute_wifi_rates(
    current: WiFiSnapshot,
    prev_bytes: tuple[int, int] | None,
    prev_ts: datetime | None,
) -> None:
    """Compute rx/tx_rate_bps on *current* from delta against previous counters.

    Modifies *current* in place.  Handles counter wraps (32-bit overflow)
    by discarding negative deltas.
    """
    if (
        prev_bytes is None
        or prev_ts is None
        or current.rx_bytes is None
        or current.tx_bytes is None
    ):
        return

    dt = (current.timestamp - prev_ts).total_seconds()
    if dt <= 0:
        return

    prev_rx, prev_tx = prev_bytes
    drx = current.rx_bytes - prev_rx
    dtx = current.tx_bytes - prev_tx

    # Discard if counters wrapped or reset
    if drx < 0 or dtx < 0:
        return

    current.rx_rate_bps = (drx * 8) / dt
    current.tx_rate_bps = (dtx * 8) / dt
