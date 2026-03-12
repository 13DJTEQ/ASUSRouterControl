# RT-AC68U Deep Dive Performance Analysis
## Current Baseline
* **RAM**: 250MB total, ~54% used (135MB), 113MB free + 23MB reclaimable buffers/cache
* **CPU**: Dual-core ARM @ 1GHz, load avg 0.25 — barely loaded
* **Latency**: Gateway 7.5ms, Cloudflare 13ms, Google 13ms — 0% packet loss
* **Speed tests**: Broken (speedtest-cli not installed). Prior manual tests: 316.7/37.6 Mbps
* **Uptime**: 16 hours, stable
* **Temperature**: CPU ~59°C, 5G radio 70°C — normal
* **WiFi clients**: 4 on 2.4GHz, 3 on 5GHz. 11 total devices (7 wired+WiFi, rest wired-only)
## Finding 1 — CRITICAL: aaews Still Running (8 threads)
Despite setting NVRAM flags to disable AiCloud/aaews, **all 8 threads survived**. They consume ~10MB RSS each (shared pages, so actual unique footprint ~10-15MB total). A reboot is required to fully kill them — the NVRAM disable prevents restart but doesn't kill existing processes.
**Fix**: Reboot the router. The NVRAM settings (`enable_webdav=0`, etc.) will prevent aaews from starting. Alternatively, force-kill: `killall -9 aaews`.
## Finding 2 — HIGH: DNS Misconfiguration
NVRAM has `wan_dns1_x=1.1.1.1` / `wan_dns2_x=1.0.0.1` (Cloudflare), but **`wan0_dnsenable_x=1`** ("get DNS from ISP") overrides them. Actual upstream DNS is **Spectrum ISP servers** (209.18.47.62/61), which are typically slower and less private.
**Fix**: Set `wan0_dnsenable_x=0` via NVRAM + restart dnsmasq. This activates the Cloudflare DNS already configured.
## Finding 3 — HIGH: QoS Download Bandwidth Cap Too Low
Adaptive QoS `qos_ibw=269000` (269 Mbps) but actual line speed is ~317 Mbps. The scheduler thinks the pipe is 269 Mbps and may prematurely queue/drop packets under load.
**Fix**: Increase `qos_ibw` to 305000 (305 Mbps, ~96% of measured). Similarly, `qos_obw=32000` is fine (~86% of 37.6 Mbps measured upload).
## Finding 4 — HIGH: WAN Web UI Access Enabled (Security)
`misc_http_x=1` exposes the router's HTTPS admin panel (port 8443) to the WAN/internet. This is a significant attack surface.
**Fix**: Set `misc_http_x=0` unless remote admin access is specifically needed.
## Finding 5 — MEDIUM: Trend Micro bwdpi Stack Still Running (wred 11 threads + dcd 4 threads)
`wrs_enable=0` and `wrs_protect_enable=0` but `apps_analysis=1` and `bwdpi_db_enable=1` keep the bwdpi engine alive. This is the **largest RAM consumer** — wred alone has 11 threads at ~8.8MB RSS each. Combined with dcd (4 threads), bwdpi_check, and bwdpi_wred_alive, this stack uses ~20-30MB unique memory.
**Trade-off**: Adaptive QoS (`qos_type=1`) **depends on bwdpi** for application classification. Disabling bwdpi means switching to Traditional QoS or no QoS. With only 11 devices and a 300/35 Mbps pipe, manual QoS rules or no QoS at all would likely perform identically.
**Option A (recommended)**: Disable bwdpi entirely, switch to Traditional QoS with manual rules for SoundShield audio and gaming traffic. Saves ~20-30MB RAM.
**Option B**: Keep Adaptive QoS, accept the RAM cost.
## Finding 6 — MEDIUM: Unnecessary AiMesh / Cloud Services
Running on a standalone router (no mesh nodes):
* **mastiff** (5 threads) — AiMesh controller daemon. Unnecessary.
* **cfg_server** (4 threads) — AiMesh config sync. Unnecessary.
* **amas_lib** (2 threads) — AiMesh library. Unnecessary.
* **awsiot** (3 threads) — AWS IoT for ASUS cloud push notifications.
* **asd** (3 threads) — ASUS security daemon (AiProtection).
* **roamast** (4 threads) — Roaming assistant. Useful for WiFi band steering even without mesh.
* **conn_diag** (4 threads) — Connection diagnostics. Low value for a stable network.
Disabling AiMesh (`amas_enable=0` if not already) and killing mastiff/cfg_server/amas_lib/awsiot could free ~15-20MB.
**Caveat**: Some of these services restart automatically via watchdog. A reboot with correct NVRAM settings is the clean approach.
## Finding 7 — MEDIUM: 5GHz TurboQAM Disabled
`wl1_turbo_qam=0` on 5GHz means no 256-QAM modulation. Enabling it allows higher throughput for close-range clients (e.g., MacPro12Core, Denon-Home-150, Bedroom-2).
**Fix**: Set `wl1_turbo_qam=1` + restart wireless.
## Finding 8 — MEDIUM: TCP Buffer Tuning Suboptimal
* `net.core.rmem_max` / `wmem_max` = 122,880 (120KB) — low for 300 Mbps
* `tcp_wmem` default = 16,384 (16KB) — low write default
* `somaxconn` = 128 — default, fine
* `netdev_max_backlog` = 1000 — could increase to 2000
For a 300 Mbps connection, rmem_max/wmem_max should be ~2-4MB. tcp_wmem default should be ~87380 to match tcp_rmem.
**Fix**: Add sysctl tuning to `/jffs/scripts/init-start`.
## Finding 9 — LOW: No Swap Configured
256MB RAM with no swap. Under memory pressure, the kernel's only option is the OOM killer. A small swap file on JFFS (55.9MB free) could provide emergency overflow.
**Caveat**: JFFS is flash storage — swap writes would accelerate wear. A 32MB swap as emergency-only (swappiness=1) is reasonable.
## Finding 10 — LOW: Conntrack Max Oversized
`nf_conntrack_max=300,000` but only 130 entries in use. Each unused slot doesn't consume memory (Linux allocates per-entry), so this is harmless. No action needed.
## Finding 11 — LOW: speedtest-cli Missing
Scheduled speed tests are failing because `speedtest-cli` is not installed. If Entware is available, install it; otherwise use a curl-based alternative.
## Finding 12 — INFO: init-start Script Empty
`/jffs/scripts/init-start` is just `exit 0`. This should be the persistent home for TCP tuning, service kills, and other boot-time optimizations.

Key decisions I need from you:

1. Finding 4 (WAN Web UI) — Do you intentionally have remote admin access enabled on port 8443, or should we close it? - 
    1. Will this disable or reduce any of our monitoring or control functions?  If so, leave open. 
2. Finding 5 (bwdpi/Adaptive QoS) — Are you willing to switch from Adaptive QoS to Traditional QoS to reclaim ~20-30MB RAM? With 11 devices on a 300/35 pipe, you won't notice a difference.
    1. continue with Adaptive

1. Finding 6 (AiMesh services) — Confirm this is a standalone router with no mesh nodes, so we can disable mastiff/cfg_server/amas_lib.
This is a standalone router. 
## Recommended Execution Order
1. **Reboot** — clears aaews, applies all pending NVRAM changes cleanly
2. **DNS fix** — `nvram set wan0_dnsenable_x=0; nvram commit; service restart_dnsmasq`
3. **QoS ibw** — `nvram set qos_ibw=305000; nvram commit; service restart_qos`
4. **WAN access** — `nvram set misc_http_x=0; nvram commit; service restart_httpd`
5. **5G TurboQAM** — `nvram set wl1_turbo_qam=1; nvram commit; service restart_wireless`
6. **TCP tuning script** in `/jffs/scripts/init-start`
7. **bwdpi decision** — user decides on Adaptive vs Traditional QoS
8. **AiMesh services** — disable if confirmed standalone
9. **speedtest-cli** — install via Entware or alternate
## Estimated Impact
* **RAM savings**: ~30-50MB (aaews gone via reboot + bwdpi disable + AiMesh disable)
* **DNS latency**: ~5-15ms improvement on first-lookup queries
* **Download throughput**: Possible 5-15% improvement under load (QoS ibw fix)
* **WiFi throughput**: Up to 20% improvement on 5GHz close-range (TurboQAM)
* **Security**: WAN admin panel closed
