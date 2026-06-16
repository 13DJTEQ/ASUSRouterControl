# Local-First & Integration Critique — Personal Hermes Avatar Plan

**Plan under review:** `2026-06-15_210524-personal-hermes-avatar.md`
**Reviewer role:** local-first-compliance and integration
**Machine profile (confirmed):** M4 / 24 GB unified memory / macOS 26.5 (Sequoia, arm64) / Ollama 0.30.8 already installed
**Verdict at a glance:** The plan is **not** local-first. It routes the avatar's 50+ daily text replies through OpenRouter (cloud) by default and offers no model-routing strategy. TTS, STT, and face are local; the **brain** is not. Several integration points (ports, file handoff, ComfyUI arm64 status) are also unstated. Fixable in a single edit pass before Phase A kicks off.

---

## 1. Local LLM candidates (table)

Recommendation basis: M4 / 24 GB unified, Metal GPU, macOS 26.5, Ollama 0.30.8 (present). Unified memory is shared with the OS and ComfyUI; budget **~16 GB practical for LLM weights** when ComfyUI is idle, **~10 GB** when LivePortrait is warm. Numbers below assume Ollama's Metal backend with KV cache quantized to Q8_0 (the default in 0.30.x).

| Model | Quant that fits on M4 24 GB | RAM headroom after load (ComfyUI idle / busy) | Quality for chat reply (subjective, MMLU/MT-Bench ballpark) | Suitability for 50 msgs/day avatar | Verdict |
|---|---|---|---|---|---|
| Llama 3.3 70B | Q2_K ~28 GB, Q3_K_S ~32 GB — **no fit** | n/a | Highest of the list (~88 MMLU) | Would be ideal but cannot load | **Reject** |
| Mistral Small 24B (v3, 2503) | Q4_K_M ~15.5 GB, IQ4_XS ~13.8 GB | 8.5 GB / 2.5 GB | Strong, good at short replies (~78 MMLU, ~8.4 MT-Bench) | Excellent for non-trivial replies; will leave enough RAM for ComfyUI | **Primary recommendation** |
| Qwen 2.5 14B (Instruct) | Q4_K_M ~9 GB, Q6_K ~12 GB | 15 GB / 9 GB | Strong at structured output, weaker on creative warmth (~76 MMLU, ~8.3 MT-Bench) | Good fallback; smaller footprint leaves room for LivePortrait | **Secondary (cold-cache wins)** |
| Gemma 3 12B (PT) | Q4_K_M ~8.5 GB | 15.5 GB / 9.5 GB | Solid chat, very fast tokens/sec on M4 (~74 MMLU) | Good for trivial replies; quality gap shows on nuance | **Tertiary (trivial-reply tier)** |
| Phi-4 14B | Q4_K_M ~9.2 GB | 15 GB / 9 GB | Strong reasoning, terse tone (~78 MMLU, ~8.5 MT-Bench) | Good but tone is dry — needs persona prompt shaping | **Use only with persona template** |
| Llama 3.1 8B | Q6_K ~6.5 GB | 18 GB / 12 GB | Adequate, generic | Trivial-reply tier; faster cold-load than 12–14B | **Trivial-reply tier** |

**Recommended split (one Ollama process, two loaded models):**

1. **`avatar-chat` model = `mistral-small:24b-instruct-2503-q4_K_M`** — primary text brain. Pulled once, kept warm (Ollama's keep_alive).
2. **`avatar-trivia` model = `llama3.1:8b-instruct-q6_K`** — used for ack / one-line answers / classification. Smaller, faster cold load, leaves RAM for ComfyUI during face renders.

**Why not Qwen 2.5 14B as primary:** quality is comparable to Mistral Small 24B Q4 on most chat benchmarks, but Mistral Small is materially better at the "warm but precise" persona tone the plan implies. Qwen 14B is the right choice only if the persona is heavily instruction-tuned / structured.

**Why Phi-4 is not the primary:** the plan's persona is "warm, dry, calm" — Phi-4 tends to terse and pedagogical, which fights the persona.

**RAM budget verification (load with everything idle):**
- Mistral Small 24B Q4_K_M: ~15.5 GB
- macOS + windowserver + Hermes: ~3.5 GB
- Ollama overhead (Metal driver, KV cache Q8_0 for 8k ctx): ~2 GB
- Free: ~3 GB headroom
- Decision: **8K context max for chat replies.** Bump KV cache quant to Q4_0 (`OLLAMA_KV_CACHE_TYPE=q4_0`) to add another ~0.8 GB if the user wants 16K context. Anything above 16K is a cloud call.

---

## 2. Local TTS upgrade options

Piper is fine for v1 but is **robotic** and the plan admits it. For an avatar that speaks 50+ times/day, "intelligible" is below the bar. Strictly local, 2026-vintage alternatives:

| Engine | Quality vs Piper | Install cost on M4 arm64 | Latency on M4 (per ~10 s output) | Voice cloning | License | Verdict |
|---|---|---|---|---|---|---|
| **Piper** (baseline) | Robotic but intelligible | 1 brew, 1 model | ~0.4 s | No | MIT | Plan default. Fine for smoke tests. |
| **Kokoro TTS** (82M, ONNX) | Noticeably warmer, near-natural for short replies | `brew install kokoro-tts` or `pipx install kokoro-onnx`; ~250 MB | ~0.6 s | No (fixed voice set) | Apache 2.0 | **Recommended upgrade** — best quality/footprint ratio in 2026 |
| **Piper + ONNX Community voices** | Marginal improvement | Same as Piper | Same | No | MIT | Skip; not worth the swap |
| **OpenVoice v2** | Strong emotion/voice cloning | Heavy: PyTorch + VITS 2 + MeloTTS, ~3 GB; install is non-trivial on M4 | ~2.5 s | **Yes** (reference audio) | MIT | Use **only if user wants voice cloning** |
| **Coqui XTTS v2** | Excellent, near-ElevenLabs | Heavy: PyTorch + ~2 GB model; known MPS hiccups on M4 | ~3 s | **Yes** (6 s ref) | CPML (Coqui) — **LICENSE DEPRECATED Jan 2024** | **Do not use** — license is non-redistributable |
| **F5-TTS** (Python pkg) | Excellent, very natural | PyTorch + ~1.2 GB; works on MPS | ~2 s | **Yes** (short ref) | MIT | Strong alternative to XTTS v2 |
| **SparkTTS** | Strong, controllable | Newer (2025); smaller model ~500 MB; MPS path works | ~1.5 s | Limited | Apache 2.0 | Viable; less battle-tested |
| **NeuTTS** (2025) | Best quality in 2026 OSS | Newer; smaller model ~1.5 GB | ~2 s | **Yes** | Apache 2.0 | Use if user is comfortable being on the bleeding edge |

**Recommended default for the avatar:** **Kokoro** for chat replies (fast, natural, no model download beyond a few hundred MB), with **F5-TTS** as the optional "named voice" path if the user wants their voice cloned from a 10 s reference sample. The plan's `speak.py` abstraction is the right shape — keep it, swap the engine.

**Add to the plan as Task A2.1:** "Kokoro TTS install + smoke test" with the same verification gate shape as A2. Piper becomes the fallback path. Do not couple the skill to Piper in `SKILL.md` — document both engines and let config choose.

---

## 3. Wispr Flow programmatic integration: yes/no + why

**No.** Wispr Flow is not designed to be invoked programmatically. As of mid-2026 it is a system-level dictation utility (a menu-bar app with a global hotkey that types into the focused text field). It does **not** expose:

- A CLI (`wispr` is not on PATH after install)
- An HTTP server
- A published API or SDK
- A documented AppleScript / Accessibility hook beyond "type into the focused field"

**Workaround that fails the local-first bar:** drive Wispr by sending keystrokes via `osascript` and switching focus to a text field. This is brittle (requires an active text field, is hijacked by the system shortcut, and breaks on focus changes), and it would mean the avatar's STT path runs through the Wispr daemon anyway, which is no more local than `faster-whisper` and strictly less reliable.

**Correct call:** keep **faster-whisper** as the STT engine. Drop Wispr from the brain-stack discussion entirely. The only place Wispr belongs in the user's stack is as a *user-side* dictation tool for them to type messages into the gateway's chat surface — it is a UX feature for the user, not a backend for the avatar.

**Action:** remove the Wispr-mention hint in the plan's STT section (the plan does not currently mention Wispr — it picked faster-whisper, which is correct). Add a one-line note in Open Questions / Risks explicitly ruling it out so it doesn't get re-proposed in the next review iteration.

---

## 4. Component data flow diagram (ASCII)

This is the shape of data flow that the plan should be explicit about and currently is not. File paths and transports are the ones the plan needs to commit to.

```
                              ┌─────────────────────────────┐
                              │   WhatsApp / Slack /        │
                              │   Telegram (cloud transport)│
                              └──────────────┬──────────────┘
                                             │ inbound message
                                             │ (text | voice.ogg | photo)
                                             ▼
                              ┌─────────────────────────────┐
                              │  Hermes gateway (launchd)   │
                              │  localhost:0  (no port —    │
                              │  stdio + IPC to platform    │
                              │  adapters)                  │
                              └──────────────┬──────────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
              ▼                              ▼                              ▼
   ┌────────────────────┐       ┌────────────────────┐       ┌────────────────────┐
   │  STT tool          │       │  Agent loop        │       │  Image downloader  │
   │  faster-whisper    │       │  (Ollama local +   │       │  (httpx / aiohttp) │
   │  base | small      │       │   persona preamble)│       └─────────┬──────────┘
   │  in:   /tmp/x.ogg  │       │  in:   text        │                 │
   │  out:  /tmp/x.txt  │       │  out:  text reply  │                 ▼
   └─────────┬──────────┘       └─────────┬──────────┘       ┌────────────────────┐
             │                            │                  │  ComfyUI server    │
             │  text                      │  reply text      │  127.0.0.1:8188    │
             └────────────────────────────┤                  │  (LivePortrait +   │
                                          │                  │   VHS_VideoCombine)│
                                          ▼                  └─────────┬──────────┘
                                ┌────────────────────┐                 │
                                │  TTS tool          │                 │ POST /prompt
                                │  Piper | Kokoro    │                 │  payload: paths
                                │  in:  reply text   │                 ▼
                                │  out: ~/.hermes/   │       ┌────────────────────────┐
                                │       avatar/      │       │ ComfyUI poll loop      │
                                │       cache/       │       │ GET /history/<id>      │
                                │       <ts>.wav     │       │ until outputs ready    │
                                └─────────┬──────────┘       │ copy to cache/<ts>.mp4 │
                                          │                  └─────────┬──────────────┘
                                          │                            │
                                          ▼                            │
                                ┌────────────────────┐                 │
                                │  Gateway send      │ ◀───────────────┘
                                │  multipart: text + │
                                │   .wav + .mp4      │
                                └─────────┬──────────┘
                                          │
                                          ▼
                              ┌─────────────────────────────┐
                              │  WhatsApp / Slack /         │
                              │  Telegram  (cloud transport)│
                              └─────────────────────────────┘

Localhost services (all on M4, same machine):
  - ComfyUI HTTP:       127.0.0.1:8188        (loopback only)
  - Ollama HTTP:        127.0.0.1:11434       (loopback only)
  - Hermes gateway:     launchd user agent    (no port; stdio + IPC)

Filesystem handoffs (preferred over streaming — no in-memory pipes to manage):
  - Inbound voice:    /tmp/hermes-in-<uuid>.{ogg,mp3,m4a}
  - STT text:         /tmp/hermes-in-<uuid>.txt
  - TTS audio out:    ~/.hermes/avatar/cache/<unix_ts>.wav
  - Face MP4 out:     ~/.hermes/avatar/cache/<unix_ts>.mp4
  - ComfyUI input:    ~/comfy/input/avatar-<uuid>.{png,wav}
  - ComfyUI output:   ~/comfy/output/ComfyUI_<jobid>.mp4
  - Persona file:     ~/.hermes/personas/avatar.md
```

**Key shape decisions baked into the diagram:**

1. **File-path handoff, not streaming.** Whisper, Piper/Kokoro, and ComfyUI all read files. Streaming is a complexity tax with no latency win on localhost. Compress only the wire format to the cloud transport (e.g., ogg → opus for WhatsApp).
2. **Loopback only.** ComfyUI and Ollama bind to 127.0.0.1. The plan should call this out explicitly so a future contributor doesn't expose them to LAN.
3. **One cache directory per producer.** `~/.hermes/avatar/cache/` is the contract surface; both TTS and face write there, gateway reads from there. Makes the "send these two files together" logic trivial.
4. **ComfyUI is a polled async server.** The render script is a state machine, not a request-response call.

---

## 5. Same-machine integration points (table)

| From | To | Transport | Address / Path | Auth | Notes |
|---|---|---|---|---|---|
| Hermes gateway (platform adapter) | ComfyUI server | HTTP POST `/prompt`, GET `/history/{id}`, GET `/system_stats` | `http://127.0.0.1:8188` | None (loopback) | Polling interval 1 s, max wait 120 s; timeout fails face render to voice-only |
| Hermes agent | Ollama server | HTTP POST `/api/chat`, GET `/api/tags` | `http://127.0.0.1:11434` | None (loopback) | `keep_alive: "30m"` for primary model, `5m` for trivial-reply model; OLLAMA_NUM_PARALLEL=1 (M4 has one GPU) |
| Hermes STT tool | faster-whisper | Python in-process (CTranslate2) | n/a — same venv | n/a | Load model once at gateway start, keep in process memory; do not spawn a subprocess per call |
| Hermes TTS tool | Piper / Kokoro | Subprocess (Piper CLI) or in-process (Kokoro ONNX) | n/a | n/a | Recommend **Kokoro in-process** — no subprocess fork per call, ~80 ms saved per reply |
| TTS output | Platform adapter (send) | Filesystem read | `~/.hermes/avatar/cache/<ts>.wav` | n/a | `aiofiles` async read; ≤25 MB cap (WhatsApp limit) |
| ComfyUI render script | ComfyUI server | HTTP multipart workflow JSON | `http://127.0.0.1:8188/prompt` | None | Use the `prompt` endpoint, not the legacy `/queue`; pass API-format workflow JSON (not the UI format) |
| Platform adapter (inbound voice) | STT tool | Filesystem drop | `/tmp/hermes-in-<uuid>.{ogg,mp3}` | n/a | ffmpeg converts to 16 kHz mono WAV before STT |
| ComfyUI | Hermes cache dir | Filesystem move | `~/comfy/output/ComfyUI_*.mp4` → `~/.hermes/avatar/cache/<ts>.mp4` | n/a | `shutil.move` after `/history/<id>` reports the output |
| Gateway | launchd | launchd user agent | `~/Library/LaunchAgents/com.hermes.gateway.plist` | n/a | `KeepAlive=true`, `ThrottleInterval=10`; `StandardOutPath` to `~/.hermes/logs/gateway.log` |
| Hermes (persona loader) | `~/.hermes/personas/avatar.md` | Filesystem read at gateway start | `~/.hermes/personas/avatar.md` | n/a | Re-read on SIGHUP so persona edits don't require a restart |

**Critical port decisions to add to the plan:**
- ComfyUI: `127.0.0.1:8188` (default; lock it down in `extra_model_paths.yaml` if exposed)
- Ollama: `127.0.0.1:11434` (default; set `OLLAMA_HOST=127.0.0.1:11434` explicitly)
- Hermes gateway: **no port** — it's a launchd user agent, not a server. The plan should not specify a port for it.

---

## 6. Model routing strategy (decision tree: when local vs cloud)

The plan is silent on this. Here's the explicit strategy to embed in Task A1 (config) and the `avatar-tts` / `avatar-face` skill frontmatter.

```
inbound message arrives at gateway
        │
        ▼
classify(message)            ← local, fast, ~50 ms
        │
        ├── trivial ──► Ollama "avatar-trivia" = llama3.1:8b
        │   • "ok", "thanks", "👍", "yes", "no"
        │   • One-line factual Q: "what time is it?"
        │   • Greeting / acknowledgment
        │   • ≤ 6 words AND no memory retrieval needed
        │   → local reply, ~0.8 s p50 on M4
        │
        ├── standard ──► Ollama "avatar-chat" = mistral-small:24b
        │   • Anything else
        │   • Replies requiring persona + memory + reasoning
        │   • Voice-out replies (always, because latency matters)
        │   → local reply, ~3 s p50 on M4
        │
        └── escalated ──► OpenRouter cloud
            • Trigger: local reply contains "I don't know" + user has retried
            • Trigger: user explicitly says "use the big model"
            • Trigger: task requires web search or code execution across > 5 files
            • Trigger: KV cache size would exceed 16K context
            → cloud reply, ~1.5 s p50 over WAN
            → log every cloud call with reason in ~/.hermes/logs/cloud-routing.log
            → cost cap: $5/day; alert at 80%
```

**Implementation:** the classifier is itself local. Use the trivial-tier model (llama3.1:8b) to classify, then dispatch to the chosen tier. This is one extra LLM call (~0.5 s) but saves a cloud call on the majority of traffic.

**Config knob:** `~/.hermes/config.yaml`:
```yaml
models:
  avatar_chat:    "mistral-small:24b-instruct-2503-q4_K_M"
  avatar_trivia:  "llama3.1:8b-instruct-q6_K"
  avatar_cloud:   "anthropic/claude-sonnet-4"   # only if user opts in
routing:
  cloud_enabled: false           # default OFF
  cloud_daily_usd_cap: 5.0
  trivial_max_words: 6
  log_cloud_calls: true
```

**Hard rule for local-first compliance:** the avatar is local-first **by default**. Cloud is opt-in per session, never opt-out. If `cloud_enabled: false`, every reply must come from a local model or the avatar returns a structured "I can't answer that locally" message.

**Acceptance criterion for "local-first":** ≥ 90% of replies in a 24 h period are served by a local model. The verification gate should include a `hermes stats --routing` command that prints this percentage.

---

## 7. Definition of "plan ready after 3 iterations" (testable criteria)

Each iteration is a full review pass with **3 parallel reviewers** + **1 synthesizer**. After 3 iterations, the plan must pass **all** of the following testable criteria. None of these are subjective; each is a yes/no check.

### Reviewer composition per iteration

| Reviewer | Lens | Output |
|---|---|---|
| **R1: Cost** | Token spend, disk, RAM, cloud-egress | Per-task cost table; cumulative $/day |
| **R2: Risk** | Failure modes, ToS, security, single points of failure | Risk register with mitigations and test names |
| **R3: Integration** | Ports, file paths, lifecycle, version pins, arm64 compatibility | Integration contract (table 5 above) plus diff against current plan |
| **S: Synthesizer** | Reconciles R1+R2+R3 into a unified delta to the plan | New plan version; "READY" / "NOT READY" verdict with checklist |

### Iteration gates (3 iterations, not 1)

**Iteration 1 (after draft):**
- R1+R2+R3 produce their first deltas.
- S reconciles, produces v2 of the plan.
- Gate: all 4 "blocking" categories below have at least one item addressed.

**Iteration 2:**
- R1+R2+R3 re-review v2 with the v1 deltas applied.
- S produces v3.
- Gate: zero new "blocking" items introduced; cost per task stable or down; no new ports/services added.

**Iteration 3 (final):**
- R1+R2+R3 verify the v3 plan against the full testable checklist below.
- S signs off.
- Gate: all checklist items ✓.

### Testable criteria for "READY"

| # | Criterion | How to check | Type |
|---|---|---|---|
| 1 | **Local LLM is the default brain**, not OpenRouter | `grep -c "Ollama" plan.md` > 0 AND `models.avatar_cloud` is gated behind a `cloud_enabled: false` default in the YAML | Blocking |
| 2 | **Model routing decision tree is in the plan** | Section "Model routing strategy" exists with at least trivial / standard / escalated tiers | Blocking |
| 3 | **All 3 TTS engines are documented** (Piper = default, Kokoro = upgrade, F5-TTS = voice-clone) | `grep -c "Kokoro\\|F5-TTS" plan.md` ≥ 4 | Blocking |
| 4 | **Wispr Flow is explicitly excluded** with a one-paragraph "why not" | Plan contains a "Wispr" section or note that says "do not use, because [reason]" | Blocking |
| 5 | **Component data flow diagram exists** with all 4 local services and their loopback addresses | Section 4 of this critique, or equivalent, is present in the plan | Blocking |
| 6 | **Same-machine integration table exists** with from/to/transport/port for at least 8 pairs | Table 5 of this critique, or equivalent, is present in the plan | Blocking |
| 7 | **ComfyUI on arm64 macOS** has a tested install path, not a "should work" assumption | Plan contains an explicit test of `comfy install` on M4 in Phase B, with a fallback to a native arm64 build or to running ComfyUI via Docker (Docker Desktop on M4 is fine; the linux/amd64 image runs under Rosetta 2 emulation but is slow) | Blocking |
| 8 | **ComfyUI port + bind address** are explicit (`127.0.0.1:8188`) | Plan mentions `127.0.0.1:8188` | Blocking |
| 9 | **Ollama port + bind address** are explicit (`127.0.0.1:11434`) | Plan mentions `127.0.0.1:11434` | Blocking |
| 10 | **File-path handoff convention** is standardized on `~/.hermes/avatar/cache/` | Plan defines the cache dir + naming convention | Blocking |
| 11 | **WhatsApp ToS risk** is surfaced and consent is gated before any WhatsApp step | Plan contains a "WhatsApp consent" gate before C3 Step 3 | Blocking |
| 12 | **Daily cost cap** is in the config with an alert at 80% | `cloud_daily_usd_cap` and `log_cloud_calls` in the routing YAML | Blocking |
| 13 | **Verification gate includes routing stats** (`hermes stats --routing` showing ≥ 90% local) | Verification Gate #6 expanded to include routing stats | Blocking |
| 14 | **No Kanban card can start without its parent's verification command printed** | Card metadata convention includes `verification: <exact commands>` | Process |
| 15 | **E6 (Reviewer) runs the D3 failure-mode tests AND the new "cloud call cap" test** | E6 acceptance criterion expanded | Process |
| 16 | **Plan file size** is ≤ 30 KB after the 3 iterations (signals over-engineering if larger) | `wc -c plan.md` | Sanity |
| 17 | **Open Questions section is empty** OR each open question has a default answer that the operator can confirm with a single keystroke | Section has ≤ 5 items, each with a "default:" line | Sanity |

**If any "Blocking" row is ✗ after iteration 3, the plan is NOT READY. Loop back to iteration 1 with the failing rows as the new gate.**

---

## 8. Required plan edits

Concrete edits, in priority order. Each is a single pass, not a discussion.

### Must-fix before Phase A (blocking)

1. **Replace the implicit cloud default with a local-first routing strategy.** Add a new section "Model routing strategy" to Phase A with the YAML in §6 of this critique. The current plan never names Ollama; this is the largest gap. **Insert before Task A1.**

2. **Add `ollama pull` to the foundation phase.** New task A0.5: install Ollama (already on the machine per `which ollama`), pull `mistral-small:24b-instruct-2503-q4_K_M` and `llama3.1:8b-instruct-q6_K`, set `OLLAMA_HOST=127.0.0.1:11434` and `OLLAMA_KV_CACHE_TYPE=q4_0` in `~/.hermes/.env`, smoke-test with `ollama run mistral-small "say hi in 5 words"`. Verification gate: response within 4 s.

3. **Add a Kokoro install path to Task A2 (or as a new Task A2.1).** Piper is the smoke-test default; Kokoro becomes the production default. Document both in the skill frontmatter.

4. **Add the Component data flow diagram (§4 of this critique) as a new section between Tech Stack and Step 0.** Without it, Phase C integration will improvise ports and paths.

5. **Add the same-machine integration table (§5 of this critique) as a new section in Phase C.** Without it, E5 will guess port numbers and the integration will fail.

6. **Add a ComfyUI arm64 macOS test as the first step of Task B1.** The current `comfy install` path is unverified for M4 / macOS 26.5. The test must produce `system_stats` JSON or block B1.

7. **Add a "Local-first verification gate" to the plan's verification section.** Item: "≥ 90% of replies in 24 h served by a local model; `hermes stats --routing` shows the percentage." Item: "No `cloud-routing.log` entries with reason other than `user_requested` unless `cloud_enabled: true`."

### Should-fix before Phase E

8. **Reframe the WhatsApp task as consent-gated.** Move Task C3 Step 3 behind an explicit `hermes config set whatsapp.consent_granted true` flag that the user must flip in response to a single on-screen prompt. Default = false.

9. **Tighten the latency target in D2.** Current target is "voice-in → voice-out under 8 s" but with cloud model + Piper TTS this is plausible; with local Mistral 24B + Kokoro, p50 is more like 4–5 s, p95 8 s. State the target as **p50 ≤ 5 s, p95 ≤ 8 s** measured over 20 trials.

10. **Pin versions in the plan.** Add a "Pinned versions" appendix: `ollama 0.30.x`, `comfy-cli 0.4.x`, `piper-tts 2023.11.x` (or `kokoro-onnx 0.9.x`), `faster-whisper 1.0.x`, `kijai/ComfyUI-LivePortraitKJ` @ a specific commit hash. Drift is the #1 cause of multi-day debugging on local-first stacks.

11. **Add a one-line "Wispr Flow is out of scope" note to the Risks section.** Saves a future iteration.

### Nice-to-have

12. **Add a "cold start" task** to Phase A: measure the time from `hermes gateway start` to first successful reply. Target ≤ 12 s including Ollama model load. This is the user's first impression.

13. **Add a "kill switch" to the config** (`gateway.kill_switch: false`). A single line that disables all replies and sends a "avatar offline" message; for when the user is on vacation or testing.

14. **Move the verification gate to a runnable script** (`~/.hermes/avatar/verify.sh`) that the integrator runs at the end of Phase E. Currently it's a checklist a human reads.

---

## Summary for the parent agent

- The plan is **mostly local-first** for the **periphery** (STT, TTS, face) but **fully cloud-default** for the **brain**. This is the single biggest gap and is fixable in one edit pass.
- Recommended local brain: **Mistral Small 24B Q4_K_M** as primary, **Llama 3.1 8B Q6_K** as trivial-reply tier, both via the Ollama already on the machine.
- Recommended TTS upgrade: **Kokoro** for chat, **F5-TTS** if voice cloning is wanted; keep Piper as the smoke-test default.
- **Wispr Flow: out of scope.** Use faster-whisper.
- The plan has **no data flow diagram** and **no integration table** — both are required before Phase C kicks off. Both are provided in this critique.
- The plan's **3-iteration review loop** is the right shape but the "ready" criteria are not defined. Section 7 supplies 17 testable criteria.
- **14 specific edits** are listed in priority order. The 7 must-fix items block Phase A; the 4 should-fix items block Phase E.

**File created:** `/Volumes/2TB NvMe/MediaWave Development Projects/ASUSRouterControl/.hermes/plans/2026-06-15_210524-personal-hermes-avatar.LOCAL_FIRST_CRITIQUE.md` (this critique).
