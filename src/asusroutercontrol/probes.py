"""Network and system health probes — all run via SSH to the router."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from asusroutercontrol.models import (
    ConfigSnapshot,
    LatencyProbe,
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
    now = datetime.utcnow()
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

    # Loss: "20 packets transmitted, 18 packets received, 10% packet loss"
    loss_m = re.search(r"(\d+)% packet loss", output)
    if loss_m:
        probe.loss_pct = float(loss_m.group(1))

    # Samples: "X packets transmitted"
    tx_m = re.search(r"(\d+) packets transmitted", output)
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
    now = datetime.utcnow()
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

    now = datetime.utcnow()
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


async def probe_wifi(ssh: RouterSSH) -> list[WiFiSnapshot]:
    """Capture per-band WiFi client count, signal strength, and noise floor."""
    now = datetime.utcnow()
    results: list[WiFiSnapshot] = []

    bands = [
        ("2.4", "eth1"),
        ("5", "eth2"),
    ]

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

        except Exception:
            log.exception("WiFi probe failed for %s band", band_name)

        results.append(snap)

    return results
