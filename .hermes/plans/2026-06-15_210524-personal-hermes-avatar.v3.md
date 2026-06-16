# Personal Hermes Avatar — Implementation Plan (v3)

> **For Hermes:** v3 corrects **all** defects surfaced in 2 review iterations (6 reviewers total: cost×2, risk×2, integration×2, UX, operability). v3 is **NOT READY** until 1 more review pass completes. v3 is structurally shippable; what it lacks is verification that the corrections hold up against a third pair of eyes.

**Goal:** Turn Hermes into a reachable, named, voice-capable personal avatar — persona defined in `~/.hermes/SOUL.md`, talking on WhatsApp + Slack + Telegram, with a ComfyUI-driven local face that animates a still photo from the TTS audio. **Local-first, cloud as opt-in last resort.**

**Architecture (4 layers):**

1. **Persona layer** — `~/.hermes/SOUL.md` defines name, voice, backstory, address style, boundaries. Auto-loaded by `agent/prompt_builder.py:load_soul_md()` on every turn. No config edits required.
2. **Brain layer** — Local Ollama with 3-tier routing implemented in a new gateway hook (E7): trivial (llama3.1:8b Q4_K_M, already installed) → standard (mistral-small:24b Q4_K_M) → escalated (OpenRouter, opt-in, $5/day cap, 1-hour TTL).
3. **Voice + face layer** — Kokoro TTS (production default) via the user-declared command-provider schema (Kokoro isn't a built-in). Piper TTS (smoke test fallback) via the built-in `piper` provider. ComfyUI + LivePortrait for face. All local.
4. **Transport layer** — Hermes gateway + platform adapters. WhatsApp via Baileys (secondary SIM, 30-day clock to Cloud API), Slack via OAuth, Telegram via BotFather.

**Tech stack (pinned, verified on this host):**
- Hermes 0.16.0 (already installed)
- Ollama 0.30.8 running on 127.0.0.1:11434 (already installed; `ollama-launch` provider already configured)
- `mistral-small:24b-instruct-2503-q4_K_M` (new pull), `llama3.1:8b` (already on disk)
- `kokoro-onnx 0.9.x` (new install) — must use the user-declared command-provider schema
- `piper-tts` (PyPI, not `brew install piper` which doesn't exist as a formula)
- `faster-whisper 1.0.x` with `base` model (STT; config already wired)
- ComfyUI + `kijai/ComfyUI-LivePortraitKJ@<pinned-sha>` (face)

**Constraint envelope (user):**
- Local AI tools as the main path. Cloud only as last resort.
- WhatsApp baked in by default; will get a Meta Business account if needed.
- Wispr Flow acknowledged as out-of-scope (no programmatic API).
- Multi-orchestrated loop to analyze, optimize, review, iterate 3x before final execution. **v3 is the output of iteration 2; iteration 3 still required.**

---

## Quickstart — do these 5 things first

Skip the rest of this plan on first read. Each step takes < 15 min:

1. **Confirm your profile.** Run `hermes profile list`. If you have only `default`, run `hermes profile create engineer && hermes profile create researcher && hermes profile create reviewer`. This unblocks parallel card dispatch in Phase E. *(5 min, optional but recommended.)*
2. **Repoint the LLM to local.** Edit `~/.hermes/config.yaml` per Task A0 Step 1. This makes every reply free. *(2 min, BLOCKING.)*
3. **Install Kokoro TTS.** Per Task A2 Steps 1–5. This is the voice the avatar actually uses. *(10 min, BLOCKING.)*
4. **Write your persona.** Per Task A1 Step 1. Save `~/.hermes/SOUL.md`. *(15 min, BLOCKING.)*
5. **Connect one platform.** Pick the easiest — Telegram. Per Task C3 Step 3. *(10 min, BLOCKING.)*

After these 5, the avatar can text-chat in persona. Voice + face come in Phase B + C2 + C4.

**Plan file:** 988 lines / 5 phases / 9 cards (E0–E8) / 17 READY criteria. The plan is structured for one operator, one machine, one avatar. Every "verification gate" is a one-line check; the full Verification Gate section at the end is the final test.

**Out of scope (explicit non-goals):**
- Voice cloning of the operator's own voice (F5-TTS path documented as a follow-up, not built).
- Web UI / dashboard for the avatar (text + voice + video are the only I/O).
- Group-chat behavior on any platform (per-chat voice/face mode is for 1:1 DMs).
- Multi-language / multilingual replies (English-only Kokoro voice `af_sarah`; swap in Phase 3 if needed).
- Whisper / STT upgrade beyond `base` (D2 Step 3 documents `small`/`medium` as a knob).
- Cloud LLM as the default (local-first is the plan; cloud is opt-in, $5/day cap, 1-hour TTL).

---

## Component Data Flow

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
                              │  no port; stdio + IPC       │
                              │  + E7 classifier hook       │
                              └──────────────┬──────────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
              ▼                              ▼                              ▼
   ┌────────────────────┐       ┌────────────────────┐       ┌────────────────────┐
   │  STT tool          │       │  Classifier (E7)   │       │  Image downloader  │
   │  faster-whisper    │       │  llama3.1:8b       │       │  (httpx / aiohttp) │
   │  in:   /tmp/x.ogg  │       │  ~200-500 ms       │       └─────────┬──────────┘
   │  out:  /tmp/x.txt  │       │  (real, not 50ms)  │                 │
   └─────────┬──────────┘       └─────────┬──────────┘                 │
             │                            │                            ▼
             │  text                      │  intent              ┌────────────────────┐
             └────────────────────────────┤                      │  ComfyUI server    │
                                          │                      │  127.0.0.1:8188    │
                                          ▼                      │  (LivePortrait +   │
                                ┌────────────────────┐           │   VHS_VideoCombine)│
                                │  Model router (E7) │           └─────────┬──────────┘
                                │  trivial|standard| │                     │
                                │  escalated         │                     │ POST /prompt
                                │  in:  text         │                     ▼
                                │  out: reply text   │           ┌────────────────────────┐
                                └─────────┬──────────┘           │ ComfyUI poll loop      │
                                          │                      │ GET /history/<id>      │
                                          ▼                      │ copy to cache/<ts>.mp4 │
                                ┌────────────────────┐           └─────────┬──────────────┘
                                │  TTS tool          │ ◀─────────────────────┘
                                │  Kokoro | Piper    │
                                │  in:  reply text   │
                                │  out: ~/.hermes/   │
                                │       avatar/      │
                                │       cache/       │
                                │       <ts>.wav     │
                                └─────────┬──────────┘
                                          │
                                          ▼
                                ┌────────────────────┐
                                │  Gateway send      │
                                │  multipart: text + │
                                │   .wav + .mp4      │
                                └─────────┬──────────┘
                                          │
                                          ▼
                              ┌─────────────────────────────┐
                              │  WhatsApp / Slack /         │
                              │  Telegram  (cloud transport)│
                              └─────────────────────────────┘
```

**Loopback services (all on M4):**
- ComfyUI HTTP: `127.0.0.1:8188` (loopback only)
- Ollama HTTP: `127.0.0.1:11434` (loopback only)
- Hermes gateway: launchd user agent (no port; stdio + IPC)

**Filesystem handoff (preferred over streaming):**
- Inbound voice: `/tmp/hermes-in-<uuid>.{ogg,mp3,m4a}` (ffmpeg → 16 kHz mono WAV before STT)
- STT text: `/tmp/hermes-in-<uuid>.txt`
- TTS audio out: `~/.hermes/avatar/cache/<unix_ts>.wav`
- Face MP4 out: `~/.hermes/avatar/cache/<unix_ts>.mp4`
- ComfyUI input: `~/comfy/input/avatar-<uuid>.{png,wav}`
- ComfyUI output: `~/comfy/output/ComfyUI_<jobid>.mp4`

---

## Same-Machine Integration Table

| From | To | Transport | Address / Path | Auth | Notes |
|---|---|---|---|---|---|
| Hermes gateway (E7 classifier) | Ollama server | HTTP POST `/api/chat`, GET `/api/tags` | `http://127.0.0.1:11434` | None (loopback) | `keep_alive: "5m"` primary, `"0"` trivial; `OLLAMA_MAX_LOADED_MODELS=1` |
| Hermes STT tool | faster-whisper | Python in-process (CTranslate2) | n/a — same venv | n/a | Load model once at gateway start, keep in process memory |
| Hermes TTS tool | Kokoro / Piper | Subprocess (Kokoro CLI via user-declared command-provider) or built-in (Piper) | n/a | n/a | **Kokoro is NOT in-process** — Hermes's TTS tool doesn't import it; uses the user-declared `tts.providers.kokoro.command:` schema |
| TTS output | Platform adapter (send) | Filesystem read | `~/.hermes/avatar/cache/<ts>.wav` | n/a | `aiofiles` async read; ≤25 MB cap (WhatsApp) |
| ComfyUI render script | ComfyUI server | HTTP multipart workflow JSON | `http://127.0.0.1:8188/prompt` | None | Use `/prompt`, not legacy `/queue`; pass API-format JSON |
| Platform adapter (inbound voice) | STT tool | Filesystem drop | `/tmp/hermes-in-<uuid>.{ogg,mp3}` | n/a | ffmpeg → 16 kHz mono WAV before STT |
| ComfyUI | Hermes cache dir | Filesystem move | `~/comfy/output/ComfyUI_*.mp4` → `~/.hermes/avatar/cache/<ts>.mp4` | n/a | `shutil.move` after `/history/<id>` reports output |
| Gateway | launchd | launchd user agent | `~/Library/LaunchAgents/com.hermes.gateway.plist` | n/a | `KeepAlive=true`, `ThrottleInterval=10`; stdout to `~/.hermes/logs/gateway.log` |
| Hermes (persona loader) | `~/.hermes/SOUL.md` | Filesystem read on every turn | `~/.hermes/SOUL.md` | n/a | No SIGHUP needed; re-read on every turn |
| Gateway (E7) | Cloud fallback (OpenRouter) | HTTPS | `https://openrouter.ai/api/v1` | API key from `~/.hermes/.env` | Only fires when `routing.cloud_enabled: true` AND per-chat `/cloud on` AND TTL not expired |

---

## RAM Budget (verified for M4 24 GB)

**At chat reply (no face render), ComfyUI idle but warm:**

| Component | RAM (GB) |
|---|---|
| macOS 26.5 + WindowServer + kernel | 3.5 |
| Gateway + STT + Kokoro subprocess | 1.0 |
| `mistral-small:24b Q4_K_M` | 15.5 |
| Ollama runtime + KV cache Q8 @ 8K ctx | 1.5 |
| ComfyUI idle (server + models warm) | 2.5 |
| **Chat-only sum** | **24.0** |

**At face render + chat reply in flight:** +3.0 GB for LivePortrait active render = **27.0 GB → swap/OOM.**

**The model-unload sequence (mandatory, defined by E7):**

```
on inbound message:
  if face mode is ON:
    1. ask_ollama_unload(mistral-small:24b)  # POST /api/generate with keep_alive=0
    2. submit face render to ComfyUI
    3. on face render done: re-warm mistral-small:24b (8-12 s cold load)
  else:
    mistral-small:24b stays warm (keep_alive=5m)
  always:
    llama3.1:8b stays at keep_alive=0 (cold load is ~2 s, faster than swap)
    kokoro-onnx + faster-whisper stay loaded (~1.4 GB combined)
```

**Practical ceiling: 8K context for chat replies (NOT 16K).** Larger contexts require unloading ComfyUI first.

---

## Model Routing Strategy (3-tier, local-first, cloud opt-in with TTL)

The classifier is **new gateway code** (E7) — it doesn't exist today. The plan assigns it to a dedicated E-card.

```
inbound message arrives at gateway
        │
        ▼
_classify_intent(message)    ← new E7 method, calls Ollama llama3.1:8b
        │                     ← realistic latency: 200-500 ms
        │                     ← output: { tier: "trivial"|"standard"|"escalated", reason: str }
        │
        ├── trivial ──► Ollama "avatar-trivia" = llama3.1:8b
        │   • ≤ 6 words AND no memory retrieval needed
        │   • "ok", "thanks", "👍", "yes", "no", "what time is it?"
        │   → local reply, ~0.8 s p50 on M4
        │
        ├── standard ──► Ollama "avatar-chat" = mistral-small:24b
        │   • Anything else
        │   → local reply, ~3 s p50 on M4
        │
        └── escalated ──► OpenRouter cloud (opt-in)
            • Trigger: routing.cloud_enabled=true AND per-chat /cloud on AND TTL<1h
            • Always logged to ~/.hermes/logs/cloud-routing.log
            • Daily cap $5 (OpenRouter returns 402 at limit)
            • Auto-disable: cloud_enabled expires 1 hour after last use
```

**Hard rule:** `routing.cloud_enabled` defaults to `false` AND no per-chat opt-in → no cloud call. The classifier returns `tier: "degraded"` and the avatar says "I can't answer that locally right now."

**Acceptance criterion:** ≥ 90% of replies in 24 h served by a local model. Use `hermes insights` (existing analytics) plus a small script that parses `cloud-routing.log` to compute the percentage.

---

## Step 0 — Profile Discovery + Pre-flight (BLOCKING)

**Objective:** Find the real profile names and verify the kanban skills exist before any card is created.

**Action:**
```bash
hermes profile list
hermes skills list | grep -iE "kanban|comfyui"
```

**Current state on this host (verified):** only `default` profile exists. `kanban-orchestrator`, `multi-agent-refinement-workflow`, and `comfyui` skills ARE installed (no install needed).

**Decision matrix:**
- If user has only `default`: optionally create `engineer`, `researcher`, `reviewer` profiles (parallel dispatch) OR accept serial execution.
- If kanban skills are missing: install from the skills hub.

**Verification:** A line of text in chat like:
> Profile roster: `default, engineer, researcher, reviewer`. Kanban skills: present.

---

## Phase A — Foundation (sequential, blocks everything else)

### Task A0: Repoint the LLM default to local Ollama (BLOCKING, 2 min)

**Objective:** Make the persona's gateway replies free. The current `model.default: minimax-m3:cloud` is a paid cloud model that violates local-first.

**v3 fix from v2 review:** v2's `models:`, `routing:`, and `OLLAMA_KV_CACHE_TYPE` blocks were all invented keys (not in Hermes/Ollama schema) and have been removed.

**Files:** `~/.hermes/config.yaml`

**Step 1: Edit config.yaml — only the verified fields**

```yaml
model:
  default: mistral-small:24b-instruct-2503-q4_K_M
  provider: ollama-launch

fallback_providers:    # top-level list, NOT model.fallback
  - openrouter/anthropic/claude-haiku-4
```

**Step 2: Pull the standard model + reuse the already-installed trivial model**

```bash
ollama pull mistral-small:24b-instruct-2503-q4_K_M
# Reuse the already-installed llama3.1:8b (Q4_K_M, 4.9 GB) for the trivial tier.
# Do NOT pull llama3.1:8b-instruct-q6_K — it adds 6 GB for marginal quality gain.
```

**Step 3: Smoke test**

```bash
ollama run mistral-small "say hi in 5 words"   # must respond in <4 s
hermes chat -q "what time is it?"              # must respond in <2 s
```

**Step 4: Verify no cloud egress**

Activity Monitor → Network tab. While running the smoke test, confirm zero requests to `api.minimax.io` or `openrouter.ai`.

**Step 5 (model-unload test):** verify Ollama can unload the 24B model on demand:

```bash
curl -X POST http://127.0.0.1:11434/api/generate -d '{"model":"mistral-small:24b-instruct-2503-q4_K_M","keep_alive":0}' | head -1
ollama list   # should show mistral-small as not loaded (or 0 seconds since loaded)
```

**Verification gate:** all four smoke tests pass. Local model loaded on demand. Network tab clean.

**Commit:** none.

---

### Task A1: Author the persona in `~/.hermes/SOUL.md`

**v3 fix from v2 review:** v2 listed `~/.hermes/profiles/<name>/SOUL.md` as an alternative path. **That path doesn't exist in this Hermes build** — only `~/.hermes/SOUL.md` is read.

**Files:** `~/.hermes/SOUL.md` (overwrite — file already exists with a 15-line placeholder)

**Step 1: Write the persona**

Minimum content:
- Name (default: `Sage`)
- One-line tagline
- Voice/tone (pick 2: calm/precise/warm/dry)
- Address style for the user (first name, "boss", etc.)
- Boundaries (refuses X, defers to user for Y)
- 3 example greeting lines
- 3 example follow-ups
- Memory hooks ("if user mentions <project>, recall <skill>")

**Step 2: Verify auto-load (re-read every turn, no SIGHUP, no restart)**

```bash
hermes chat -q "introduce yourself in one sentence"
```

Expected: matches the persona's tagline, in the persona's voice, addressing the user by the chosen form. If the model returns a generic response, check `agent/prompt_builder.py:1478` (`load_soul_md()`) and confirm the file is at `~/.hermes/SOUL.md` (no per-profile path).

**Step 3 (optional):** add an `agent.personalities.avatar` mirror for `/personality avatar` A/B between personas. Keep `display.personality` empty so the SOUL.md voice isn't competing with another personality layer.

**Commit:** none.

---

### Task A2: Install Kokoro TTS (production default) + Piper (smoke test)

**v3 fixes from v2 review:**
- `tts.provider: kokoro` silently falls through to Edge TTS because Kokoro isn't in `BUILTIN_TTS_PROVIDERS` (`tts_tool.py:376-387`). **Must use the user-declared command-provider schema.**
- `brew install piper` fails (not a Homebrew formula). Use `pip install piper-tts`.
- `text_to_speech_tool` does NOT accept a `provider` argument. The smoke test commands have been rewritten.

**Files:** none (binary install + voice model).

**Step 1: Install Kokoro ONNX**

```bash
pipx install kokoro-onnx
# or in the active venv:
.venv/bin/pip install kokoro-onnx
brew install espeak-ng   # phonemizer dependency
```

**Step 2: Install Piper (smoke test fallback)**

```bash
.venv/bin/pip install piper-tts
```

(Piper is the *built-in* TTS provider; Kokoro needs the user-declared command provider below.)

**Step 3: Download Piper voice model**

```bash
mkdir -p ~/.local/share/piper/voices
cd ~/.local/share/piper/voices
curl -L -o en_US-amy-low.onnx       https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx
curl -L -o en_US-amy-low.onnx.json  https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx.json
```

**Step 4: Verify Kokoro CLI is invokable**

```bash
~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --help
```

Capture the exact CLI signature for the next step.

**Step 5: Register Kokoro as a user-declared command provider**

Edit `~/.hermes/config.yaml`:

```yaml
tts:
  provider: kokoro
  providers:
    kokoro:
      type: command
      command: '~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --voice {voice} --speed {speed} --output {output_file} --text "{text}"'
      voice: af_sarah
      speed: 1.0
      output_format: wav
  piper:
    model: ~/.local/share/piper/voices/en_US-amy-low.onnx
    config: ~/.local/share/piper/voices/en_US-amy-low.onnx.json
```

**Engine fallback order** (a `gateway.recovery.voice_failure` decision):
- Try Kokoro first (production quality)
- On failure, fall back to Piper (smoke test quality)
- On both failing, fall back to text-only with a status line: "Voice reply unavailable; text only."

```yaml
gateway:
  recovery:
    voice_failure: status_line   # or "silent" (legacy)
    face_failure: status_line
    engine_fallback_order: [kokoro, piper]
    per_platform:
      slack:    { provider: kokoro, voice: af_sarah,   speed: 0.95 }
      telegram: { provider: kokoro, voice: af_bella,   speed: 1.0  }
      whatsapp: { provider: kokoro, voice: af_sarah,   speed: 1.0  }
      default:  { provider: kokoro, voice: af_sarah,   speed: 1.0  }
```

Restart the gateway: `hermes gateway restart`.

**Step 6: Smoke test both engines (corrected)**

```bash
# Kokoro (production)
hermes chat -q 'use text_to_speech to say "I am ready" to /tmp/kokoro-test.wav'
afplay /tmp/kokoro-test.wav

# Switch to Piper (fallback)
hermes config set tts.provider piper
hermes gateway restart
hermes chat -q 'use text_to_speech to say "I am ready" to /tmp/piper-test.wav'
afplay /tmp/piper-test.wav

# Restore Kokoro
hermes config set tts.provider kokoro
hermes gateway restart
```

**Step 7: Proof that Kokoro actually ran (not Edge fallback)**

The Edge TTS default produces a specific audio signature. Confirm Kokoro ran:

```bash
afinfo /tmp/kokoro-test.wav | head -5
# Should NOT show "manufacturer: Microsoft" or similar Edge-TTS artifacts
# Kokoro produces a 22050 Hz, 16-bit, mono WAV
```

**Verification gate:** both engines produce audio, both play, Kokoro clearly more natural than Piper, audio file metadata confirms Kokoro (not Edge) ran.

**Commit:** none.

---

### Task A3: Verify faster-whisper is wired (no install needed)

**Step 1:** `.venv/bin/pip install faster-whisper` (idempotent; first call also downloads `base` model, ~150 MB).

**Step 2:** Smoke test:

```bash
python -c "from faster_whisper import WhisperModel; m=WhisperModel('base'); segs,_=m.transcribe('/tmp/kokoro-test.wav'); print(''.join(s.text for s in segs))"
```

Expected: prints `I am ready.`

**Step 3 (optional accuracy upgrade):** `stt.local.model: small` in config.

**Verification gate:** transcription matches synthesized text within ~1 word.

**Commit:** none.

---

### Task A4: Wispr Flow — out of scope (one-liner)

Add to `~/.hermes/avatar/notes.md`:
> Wispr Flow is out of scope for the avatar's STT pipeline. It is a system-level dictation utility; no programmatic API. Used only for the operator's own interactive dictation. Avatar's STT is faster-whisper (local, no quota).

**Commit:** none.

---

### Task A5: Warm-start preflight (NEW, P1 from UX review)

**Objective:** First-message latency ≤ 4 s p50 for text, ≤ 6 s p50 for voice-in/voice-out. The plan currently has no warm-start script; cold-start is 13-31 s.

**Step 1: Write a prewarm script `~/.hermes/avatar/gateway_prewarm.sh`**

```bash
#!/bin/bash
# gateway_prewarm.sh — called from launchd plist on gateway start
set -e
LOG=~/.hermes/logs/cold-start.log
echo "=== prewarm $(date -u +%FT%TZ) ===" >> "$LOG"

# Warm Ollama primary
START=$(date +%s)
ollama run mistral-small:24b-instruct-2503-q4_K_M "ping" >> "$LOG" 2>&1
echo "ollama_primary_load: $(($(date +%s) - START))s" >> "$LOG"

# Warm Ollama trivial
START=$(date +%s)
ollama run llama3.1:8b "ping" >> "$LOG" 2>&1
echo "ollama_trivial_load: $(($(date +%s) - START))s" >> "$LOG"

# Warm Kokoro
START=$(date +%s)
~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --voice af_sarah --speed 1.0 --output /tmp/kokoro-warmup.wav --text "warmup" >> "$LOG" 2>&1
echo "kokoro_load: $(($(date +%s) - START))s" >> "$LOG"
rm -f /tmp/kokoro-warmup.wav

# Warm ComfyUI (if installed)
if curl -s --max-time 2 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
  echo "comfyui: already running" >> "$LOG"
else
  comfy launch --background >> "$LOG" 2>&1
  for i in {1..30}; do
    if curl -s --max-time 1 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
      echo "comfyui_startup: ${i}s" >> "$LOG"
      break
    fi
    sleep 1
  done
fi

echo "=== prewarm done ===" >> "$LOG"
```

**Step 2: Wire the script into the launchd plist**

Edit `~/Library/LaunchAgents/com.hermes.gateway.plist`:

```xml
<key>ProgramArguments</key>
<array>
  <string>/bin/bash</string>
  <string>-c</string>
  <string>~/.hermes/avatar/gateway_prewarm.sh && hermes gateway run</string>
</array>
```

(Verify the exact plist location with `hermes gateway status`.)

**Verification gate:** after `hermes gateway install && launchctl kickstart -k gui/$(id -u)/com.hermes.gateway`, the first message replies in ≤ 4 s p50 (text) and ≤ 6 s p50 (voice).

**Commit:** none.

---

## Phase B — Local Face (atomic, parallelizable with A but blocks C2)

### Task B1: ComfyUI + LivePortrait + workflow (ATOMIC)

**v3 fixes from v2 review:**
- `kijai/ComfyUI-LivePortraitKJ` pinned SHA must be captured from the live `main` HEAD at install time (operator runs `git ls-remote` first), not hardcoded.
- The driver script's render-time budget must fail loudly, not just log.

**Files:**
- `~/comfy/` (ComfyUI install)
- `~/comfy/custom_nodes/ComfyUI-LivePortraitKJ/`
- `~/comfy/workflows/avatar_talking_head.json`
- `~/.hermes/avatar/render_talking_head.py` (driver, used by C2)

**Step 1: Verify ComfyUI works on M4 (arm64 macOS, macOS 26.5)**

```bash
pipx install comfy-cli
comfy install
comfy launch --background
sleep 10
curl -s http://127.0.0.1:8188/system_stats | jq '.system.comfyui_version, .devices[].name'
```

**Gate:** JSON returned. If fails on M4 arm64, fallback: Docker (`docker run -p 8188:8188 comfyui/comfyui:latest` under Rosetta 2 — slow but works).

**If install fails:** log error, mark B1 blocked, switch to MuseTalk (TMElyralab, last push 2025-09-26, realtime path on M4).

**Step 2: Install LivePortrait custom node (pinned commit)**

```bash
# Capture the live main HEAD SHA FIRST
PINNED_SHA=$(git ls-remote https://github.com/kijai/ComfyUI-LivePortraitKJ.git refs/heads/main | awk '{print $1}')
echo "Pinned to: $PINNED_SHA" | tee ~/.hermes/avatar/pins.txt

cd ~/comfy/custom_nodes
git clone https://github.com/kijai/ComfyUI-LivePortraitKJ.git
cd ComfyUI-LivePortraitKJ
git checkout "$PINNED_SHA"
pip install -r requirements.txt
python install.py   # downloads ~3 GB of models
```

**Step 3: Restart ComfyUI and verify nodes are loaded**

```bash
comfy restart
curl -s http://127.0.0.1:8188/object_info | jq '.["LivePortraitLoadModels"] // .["LivePortraitProcess"]'
```

Expected: a JSON schema, not `null`. If null, check `~/comfy/comfyui.log` for import errors.

**Step 4: Author the talking-head workflow in the ComfyUI UI**

1. Open http://127.0.0.1:8188 in a browser.
2. Nodes: `LoadImage` (portrait) → `LoadAudio` (TTS .wav) → `LivePortraitProcess` (or `LivePortraitLoadModels` + `LivePortraitProcess`) → `VideoCombine` at 25 fps, MP4 output.
3. `SaveImage` for first-frame preview.
4. Save with "Save (API Format)" → `~/comfy/workflows/avatar_talking_head.json`.

**Step 5: Author the driver `~/.hermes/avatar/render_talking_head.py`**

- Loads `~/comfy/workflows/avatar_talking_head.json`
- Substitutes portrait path and audio path
- POSTs to `http://127.0.0.1:8188/prompt`
- Polls `/history/{prompt_id}` every 1 s, max 120 s
- Copies the resulting MP4 to `~/.hermes/avatar/cache/<unix_ts>.mp4`
- **Fails with exit code 2 if render time > 60 s** (was a soft log in v2; now a hard fail)
- Outputs JSON to stdout: `{"success": bool, "output_path": str, "render_seconds": float}`

**Step 6: Smoke test the whole pipeline**

```bash
python ~/.hermes/avatar/render_talking_head.py \
  --portrait ~/comfy/input/example_portrait.png \
  --audio    /tmp/kokoro-test.wav \
  --output   /tmp/avatar-test.mp4
```

**Verification gate:**
- MP4 exists with non-zero size
- `afinfo /tmp/avatar-test.mp4` shows duration
- `ffprobe -v error -select_streams v -show_entries stream=width,height /tmp/avatar-test.mp4` matches the portrait's resolution
- **Render-time budget enforced: 3 s audio → < 60 s render on M4, exit 0. > 60 s → exit 2.**
- Play in QuickTime; face recognizable and mouth moves

**Commit:** none.

---

## Phase C — Hermes Integration

### Task C1: REMOVED — use the built-in `text_to_speech` tool

Correct as in v2. No new skill.

---

### Task C2: Add a thin `avatar-face` skill (wraps the B1 driver script)

**v3 fixes from v2 review:**
- SKILL.md `description:` is too short for LLM discoverability. Expand to 2-3 sentences.
- `render.py` must NOT shell out to `hermes chat` (recursive agent bootstrap = 10+ s). Import `text_to_speech_tool` directly.
- The skill is a wrapper, not a tool. The gateway calls it via subprocess from `_send_face_reply`. The agent should not call it directly.

**Files:**
- `~/.hermes/skills/avatar-face/SKILL.md`
- `~/.hermes/skills/avatar-face/scripts/render.py`

**Step 1: SKILL.md frontmatter (expanded)**

```yaml
---
name: avatar-face
description: "Avatar face animation — generate a talking-head MP4 video from a still portrait and a Kokoro-synthesized audio file. Local ComfyUI + LivePortrait, on-demand only. Use this skill when the user explicitly requests a video or has face mode on for a chat. The skill does NOT auto-render on every reply; the gateway's per-chat face mode flag controls invocation. This skill is called by the gateway, not by the agent directly."
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    category: creative
    tags: [avatar, video, comfyui, liveportrait]
---

# avatar-face

Thin wrapper around the B1 driver script. Called by the gateway's `_send_face_reply` method (E7), not by the agent.

## When invoked

- The chat is in face mode (per-chat flag, set by `/face on`).
- The reply is over the `face_reply_min_chars` threshold (30 chars default).
- A Kokoro .wav file already exists in `~/.hermes/avatar/cache/`.

## Output

JSON to stdout: `{"success": bool, "output_path": str, "render_seconds": float, "engine": "liveportrait"}`.
```

**Step 2: Wrapper script (direct import, no recursive chat)**

```python
# ~/.hermes/skills/avatar-face/scripts/render.py
import argparse, json, subprocess, sys
from pathlib import Path

CACHE = Path.home() / ".hermes/avatar/cache"
CACHE.mkdir(parents=True, exist_ok=True)

def render(text, portrait, output=None):
    import time
    ts = int(time.time())
    audio = CACHE / f"{ts}.wav"
    if text is not None:
        # Direct import of the built-in tool, NOT a recursive hermes chat call
        from tools.tts_tool import text_to_speech_tool
        result_str = text_to_speech_tool(text=text, output_path=str(audio))
        result = json.loads(result_str)
        if not result.get("success", False):
            return json.dumps({"success": False, "stage": "tts", "error": result})
    if output is None:
        output = CACHE / f"{ts}.mp4"
    proc = subprocess.run([
        "python", str(Path.home() / ".hermes/avatar/render_talking_head.py"),
        "--portrait", portrait,
        "--audio", str(audio),
        "--output", str(output),
    ], capture_output=True, text=True)
    if proc.returncode == 2:
        return json.dumps({"success": False, "stage": "render_timeout", "stderr": proc.stderr})
    if proc.returncode != 0:
        return json.dumps({"success": False, "stage": "render", "stderr": proc.stderr})
    return proc.stdout  # already JSON from the driver

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--portrait", required=True)
    p.add_argument("--output", default=None)
    args = p.parse_args()
    print(render(args.text, args.portrait, args.output))
```

**Step 3: Smoke test**

```bash
hermes chat -q "use avatar-face to render 'Hello, I am your new assistant.' with portrait at ~/portraits/avatar.png to /tmp/hello.mp4"
```

Expected: MP4 exists, plays, audio matches.

**Verification gate:** rendered MP4 coherent, JSON return includes `success: true`.

**Commit:** none.

---

### Task C3: Connect the gateway (Slack + WhatsApp + Telegram)

**v3 fixes from v2 review:**
- `hermes gateway setup --platform slack|telegram` is wrong — the wizard takes no flags. Use the interactive wizard.
- `hermes whatsapp` wizard has no built-in ToS consent gate. **E8 adds one** (or operator acknowledges in writing before this step).

**Files:** none (Hermes gateway config + platform-specific auth files).

**Step 1: Install the gateway as a launchd service**

```bash
hermes gateway install
hermes gateway status
```

**Step 2: Configure Slack (zero cost)**

```bash
hermes gateway setup   # interactive wizard; pick Slack from the menu
```

Wizard asks for: workspace OAuth token (`xoxb-...`) from a Slack app with `chat:write`, `channels:history`, `im:history`, `im:write`, `files:write` scopes. Bot must be `/invite @<bot>`-d in any DM it should answer in.

**Smoke test:** text in the Slack DM. Expected: avatar replies (text).

**Step 3: Configure Telegram (zero cost, easiest test platform)**

Run `hermes gateway setup`, pick Telegram from the menu. Wizard asks for the BotFather token. Bot must be `/start`-ed in a DM at least once.

**Smoke test:** send a voice note, text, photo. Expected: avatar replies to each.

**Step 4: Configure WhatsApp — Baileys bridge (default, free, ToS-violating)**

```bash
# First, build the bridge if not already done
cd ~/.hermes/hermes-agent/scripts/whatsapp-bridge
npm install

# Then pair
hermes whatsapp
```

QR code pairs to the user's personal WhatsApp account using the Baileys bridge (`scripts/whatsapp-bridge/bridge.js`).

**ToS consent gate (E8 adds to wizard, OR operator acknowledges in writing before this step):**
> I understand that using a personal WhatsApp account via Baileys violates WhatsApp's ToS, may result in account banning within 90 days, and that I should use a secondary phone number. I am proceeding at my own risk.

**Mitigations (required):**
1. **Secondary phone number** (spare SIM, not the user's primary). WhatsApp does not accept most VoIP numbers. **$10-15/month US prepaid.**
2. Daily cron to re-pair: `~/Library/LaunchAgents/com.hermes.whatsapp-keepalive.plist`.
3. Plan Cloud API migration within 30-60 days (`hermes whatsapp-cloud`).

**Step 5: Configure voice defaults per platform (NEW from UX review)**

```bash
hermes chat -q "/voice default voice_only telegram"
hermes chat -q "/voice default voice_only whatsapp"
# Slack stays default (text-only) — work context
```

This creates `~/.hermes/gateway_voice_defaults.json` (new file, read by E7) so that new chats on a platform inherit the per-platform default rather than starting in `off`.

**Verification gate:** `hermes gateway status` shows all three connected. A message on any platform receives a reply within 5 s.

**Commit:** none.

---

### Task C4: Per-chat voice mode (existing) + per-chat face mode (new gateway code, E7)

**v3 fix from v2 review:** face mode is **NOT a state-file addition** — it requires `gateway/run.py` code changes. E7 implements the new methods.

**Step 1: Voice mode (existing, no changes)**

Voice mode is per-chat state in `~/.hermes/gateway_voice_mode.json` (`_load_voice_modes` in `gateway/run.py:2458`). Commands:

- `/voice off` — text-only (default)
- `/voice voice_only` — voice reply when input was a voice note
- `/voice all` — every reply sent as both text and voice

**Step 2: Face mode (NEW, implemented in E7)**

E7 adds to `gateway/run.py`:
- `_FACE_MODE_PATH = _hermes_home / "gateway_face_mode.json"`
- `def _load_face_modes(self) -> Dict[str, str]`
- `def _save_face_modes(self) -> None`
- `def _set_face_mode(self, platform, chat_id, mode) -> None`
- `def _should_send_face_reply(self, event, response) -> bool` (checks: face mode on AND reply > 30 chars)
- `def _send_face_reply(self, event, response)` (calls C2 wrapper as subprocess)
- Slash command dispatcher entries for `/face on` and `/face off`

**Step 3: Engine fallback (NEW from UX review)**

Per `gateway.recovery.voice_failure: status_line` and `engine_fallback_order: [kokoro, piper]`:
- Try Kokoro. If TTS fails, try Piper. If both fail, text-only with a status line.

**Step 4: Recovery `/status` and `/repair` slash commands (NEW from UX review, E7)**

- `/status` → `gateway: up (PID 1234, uptime 6h) | ollama: loaded (mistral-small:24b, llama3.1:8b) | kokoro: ok | comfyui: ok | whatsapp: connected (last ack 14s ago) | slack: connected | telegram: connected | voice_mode: voice_only (3 chats) | face_mode: on (1 chat) | cloud_enabled: false | daily_cloud_usd: 0.00`
- `/repair` → runs `ollama list | grep mistral-small && kokoro_smoke_test && curl -s http://127.0.0.1:8188/system_stats | jq -e '.system.comfyui_version' && echo "all services healthy"`, reports the first failure.

**Step 5: Verify**

Send a voice note >30 chars to the Telegram bot. With `/voice voice_only`, expected: avatar replies with `.wav` (Kokoro). With `/face on`, expected: avatar also replies with `.mp4`.

**Verification gate:** voice-in → voice-out, text-in → text-out, face mode adds `.mp4` only when enabled. `/status` reports healthy.

**Commit:** none (E7's gateway code change is a separate E-card commit).

---

## Phase D — Polish and Optimization

### Task D1: Memory wiring verification (no install needed)

**Step 1:** Confirm `memory.memory_enabled: true` and `memory.user_profile_enabled: true` in `~/.hermes/config.yaml`.

**Step 2:** Seed facts: `remember: I run the ASUSRouterControl project` etc.

**Step 3:** Cross-session recall: `/new` then `what projects am I running?`

**Step 4:** Cross-platform recall: remember on Telegram, recall on Slack.

**Step 5:** **Memory hygiene (NEW from UX review, P2):** add `/memories`, `/forget <id>`, `/forget all <pattern>` slash commands. The plan defers the monthly cron to a follow-up but the user needs the ability to inspect and prune.

**Verification gate:** memory persists across `/new` and across platforms. `/memories` lists current entries.

**Commit:** none.

---

### Task D2: Latency tuning

**Objective:** Voice-in → voice-out **p50 ≤ 5 s, p95 ≤ 8 s, over 20 trials.**

**Step 1: Measure baseline**

Use the Telegram bot: 5-second voice note, time the reply. 20 trials. Record p50, p95.

**Step 2: Tune**

- STT slow → `stt.local.model: tiny` (faster, accuracy tradeoff).
- TTS slow → Kokoro in-process not possible (Hermes TTS tool subprocess path); consider F5-TTS if quality matters more.
- Agent loop slow → check `agent.max_turns` (5-10 for chat).
- Model slow → llama3.1:8b for short replies, mistral-small:24b for longer.
- **Do NOT use OpenRouter `:free`** — ~50 req/day cap, exceeded by morning traffic.
- **Switch the model default** per A0 (v2 was using `minimax-m3:cloud`).

**Step 3: Cold-start budget (NEW from UX review)**

After A5 prewarm, send one Telegram text + one Telegram voice. Wall-clock:
- Text: ≤ 4 s p50, ≤ 8 s p95
- Voice: ≤ 6 s p50, ≤ 10 s p95

**Verification gate:** 20-trial measurement meets target. Cold-start within budget.

**Commit:** none.

---

### Task D3: Failure-mode and boundary tests (10 cases)

**v3 additions from UX review:**

1. Voice reply when Kokoro is down → graceful fallback to Piper → text-only with status line.
2. Face render when ComfyUI is down → text+voice only, no zombie MP4, status line.
3. Memory boundary → "I don't have that in my notes" not invented data.
4. Persona consistency → stays in character, refuses politely.
5. Long message (60s voice note) → reasonable-length reply, no OOM.
6. Voice-in race condition → two voice notes within 5 s, both replied in order.
7. **Ollama 404** → rename model temporarily, expected: text reply with status line.
8. **ComfyUI crash mid-render** → text+voice at 6s, no zombie process.
9. **Kokoro disk full** → text reply with status line, no partial .wav.
10. **WhatsApp bridge disconnect** → 3 messages queued, reconnected, delivered in order, no cross-platform duplication.
11. **Engine fallback chain** → kill Kokoro, expect Piper; kill Piper, expect text.

**Verification gate:** all 11 tests pass. `~/.hermes/logs/gateway.log` clean.

**Commit:** none.

---

## Phase E — Multi-Orchestrated Deployment

The dispatcher fans out per-profile, not per-card. With one profile (`default`), E0-E8 run **sequentially**. The plan recommends creating `engineer`, `researcher`, `reviewer` profiles for parallel dispatch.

### Pre-flight (BLOCKING)

```bash
hermes profile list             # confirm profile roster
hermes skills list | grep -iE "kanban|comfyui"   # confirm skills present
```

Both kanban-orchestrator and multi-agent-refinement-workflow are already installed on this host.

### Card layout (9 cards)

**Independent lanes (parallel, no parents):**

- **E0** — *engineer*: ComfyUI + LivePortrait + workflow (B1) atomically. Acceptance: `system_stats` returns 200, LivePortrait node loaded, driver smoke test produces a valid MP4 under 60s render budget, pinned SHA recorded in `~/.hermes/avatar/pins.txt`.
- **E1** — *engineer*: Kokoro TTS install + user-declared command provider (A2). Acceptance: `text_to_speech_tool` produces Kokoro audio (not Edge fallback), Piper works as fallback, `afinfo` confirms Kokoro signature.
- **E2** — *engineer*: LLM default swap (A0). Acceptance: `model.default: mistral-small:24b`, network tab clean of cloud egress, unload test passes.
- **E3** — *engineer*: Persona SOUL.md (A1). Acceptance: `hermes chat -q "introduce yourself"` returns persona text in persona voice, SIGHUP not needed.
- **E4** — *researcher*: Platform setup checklist (C3). Output: `~/.hermes/avatar/SETUP_CHECKLIST.md` with copy-pasteable command sequences for Slack, Telegram, WhatsApp.
- **E5** — *engineer*: Warm-start preflight script (A5). Acceptance: prewarm script wired into launchd plist, first message reply ≤ 4 s p50 (text) / ≤ 6 s p50 (voice).

**Integration (depends on E0-E5):**

- **E6** — *engineer (integrator)*: Gateway install + per-platform connect (C3) + voice/face mode (C4) + warm-start. Acceptance: a Telegram voice note round-trips with a voice reply; a Slack text DM round-trips with text; a WhatsApp voice note (secondary SIM) round-trips with voice.
- **E7** — *engineer*: **NEW — Routing classifier + face-mode gateway code** (Model Routing Strategy + C4 Step 2). Adds `_classify_intent` method, `_load_face_modes`/`_save_face_modes`/`_set_face_mode`/`_should_send_face_reply`/`_send_face_reply` methods, `/face` and `/status` and `/repair` slash commands, and the per-platform voice defaults file. Acceptance: 5-message test set classified correctly, `/status` returns the documented JSON shape, `/face on` toggles face mode for a chat.
- **E8** — *engineer*: **NEW — WhatsApp ToS consent gate** in `cmd_whatsapp` (`hermes_cli/main.py:2352`). Adds a yes/no prompt before QR pairing; refuses to proceed without explicit consent. Acceptance: `hermes whatsapp` with stdin redirected from `/dev/null` exits non-zero with the consent message printed.

**Review (depends on E6, E7, E8):**

- **E9** — *reviewer*: D3 failure-mode tests + cloud-call cap test. Send 10 trivial messages, verify `cloud-routing.log` empty. Acceptance: all 11 D3 tests pass, no cloud calls in log, no ERROR/Traceback in gateway.log.

### Card metadata

Every card body must include:
```
assignee: <profile>
parents: [<id> | empty]
goal_mode: true|false
acceptance: <bulleted, judge-readable criteria>
files: <exact paths>
verification: <exact commands + expected output>
```

### Dispatch flow

1. Confirm profile roster + kanban skills.
2. Create E0, E1, E2, E3, E4, E5 in parallel.
3. Dispatcher fans them out.
4. Workers report back via `kanban_complete(summary, metadata)`.
5. E6, E7, E8 auto-promote when E0-E5 done. E6, E7, E8 are independent of each other (E6 connects platforms, E7 implements routing, E8 adds ToS gate).
6. E9 runs after E6+E7+E8.
7. Gateway ping on E9 completion.

### Failure recovery

- Worker calls `kanban_block()` → Recovery drawer. Operator reviews, reclaims or reassigns.
- Worker silently claims success but file missing → E6 catches at verification step 1.
- E7's gateway code change is the highest-risk integration. If E7's `_send_face_reply` method has a bug, E9's D3 #2 (ComfyUI crash mid-render) and D3 #8 (ComfyUI crash mid-render — added) tests catch it.

---

## Testable "READY" Criteria

The plan is **NOT READY** until all 17 of these are ✓.

| # | Criterion | How to check | Type |
|---|---|---|---|
| 1 | **Local LLM is the default brain** | `model.default: mistral-small:24b`; no `routing:` or `models.avatar_*` invented blocks | Blocking |
| 2 | **Routing decision tree exists** | "Model Routing Strategy" section with trivial/standard/escalated tiers; E7 card created | Blocking |
| 3 | **Kokoro TTS configured** | `tts.providers.kokoro.type: command` with the actual CLI command; Pipe voice downloaded; `afinfo` confirms Kokoro (not Edge) signature | Blocking |
| 4 | **Wispr Flow excluded** | One-paragraph "do not use" note | Blocking |
| 5 | **Data flow diagram** | "Component Data Flow" section with all 4 services | Blocking |
| 6 | **Integration table** | "Same-Machine Integration Table" with ≥ 8 pairs | Blocking |
| 7 | **ComfyUI arm64 verified** | `system_stats` returns 200 on M4 (or Docker fallback documented) | Blocking |
| 8 | **ComfyUI port explicit** | `127.0.0.1:8188` in plan | Blocking |
| 9 | **Ollama port explicit** | `127.0.0.1:11434` in plan | Blocking |
| 10 | **File-path handoff** | `~/.hermes/avatar/cache/` defined | Blocking |
| 11 | **WhatsApp ToS consent gate** | E8 creates the gate in `cmd_whatsapp`; or operator acknowledges in writing before `hermes whatsapp` | Blocking |
| 12 | **Daily cost cap + 1-hour TTL** | E7 enforces both; cloud cap checked against OpenRouter usage | Blocking |
| 13 | **Routing stats verification** | A small script parses `cloud-routing.log` and computes local-vs-cloud percentage; ≥ 90% local | Blocking |
| 14 | **Verification commands printed on cards** | Card metadata convention includes `verification: <exact commands>` | Process |
| 15 | **E9 runs D3 + cloud cap test** | E9 acceptance includes all 11 D3 tests + 10-msg cloud cap | Process |
| 16 | **Plan file size ≤ 50 KB** | `wc -c plan.md` ≤ 51,200 bytes | Sanity |
| 17 | **Defaults section (not open questions)** | Section renamed "Defaults", each has a one-keystroke override | Sanity |

---

## Verification Gate (whole plan)

Before declaring done, all of these must pass:

1. `hermes doctor` — clean, no warnings.
2. `hermes gateway status` — Slack, Telegram, WhatsApp all `connected`.
3. `hermes chat -q "introduce yourself"` — persona text, persona voice, correct address.
4. `hermes chat -q "what time is it?"` — < 2 s, no cloud egress.
5. `afplay` the kokoro-test.wav — natural voice, not Edge default.
6. Cold-start: first Telegram text ≤ 4 s p50, first Telegram voice ≤ 6 s p50 (A5 prewarm).
7. Send a voice note to each platform — voice reply (or text if `/voice off`).
8. Cloud-routing log: ≥ 90% local in 24 h.
9. `hermes chat -q "use avatar-face to render '...' with portrait at ... to /tmp/x.mp4"` — MP4 plays, audio matches.
10. `/status` returns the documented JSON shape; all services healthy.
11. Stopwatch: voice-in → voice-out p50 ≤ 5 s, p95 ≤ 8 s, 20 trials.
12. All 11 D3 tests pass.
13. All 17 READY criteria ✓.

---

## Pinned Versions (verified on this host)

| Component | Version | Source |
|---|---|---|
| Hermes | 0.16.0 | (already installed) |
| Ollama | 0.30.8 | `ollama --version` (already running) |
| `mistral-small:24b-instruct-2503-q4_K_M` | pinned at first pull | `ollama list` |
| `llama3.1:8b` (Q4_K_M, 4.9 GB) | reused, no pull | `ollama list` |
| `kokoro-onnx` | 0.9.x | `pipx install kokoro-onnx` |
| `piper-tts` | 2023.11.x | `.venv/bin/pip install piper-tts` |
| `faster-whisper` | 1.0.x | `.venv/bin/pip install faster-whisper` |
| ComfyUI | 0.4.x | `pipx install comfy-cli && comfy install` |
| `kijai/ComfyUI-LivePortraitKJ` | pin to live `main` HEAD at install | `git ls-remote` then `git checkout $SHA` |
| `@whiskeysockets/baileys` | latest at install | `npm install` in `scripts/whatsapp-bridge/` |

Pin to `~/.hermes/avatar/pins.txt` after install.

---

## Cost Summary (v3 updated)

| Line item | Local | Cloud | Monthly $ (typical) | Annual $ |
|---|---|---|---|---|
| LLM (default) | Ollama mistral-small:24b | — | $0 | $0 |
| LLM (fallback) | Ollama llama3.1:8b | OpenRouter claude-haiku-4 (10% failure) | $0.16 | $1.89 |
| TTS (Kokoro + Piper) | Local | — | $0 | $0 |
| STT (faster-whisper) | Local | — | $0 | $0 |
| Face (ComfyUI + LivePortrait) | Local | — | $1.25 (electricity) | $15.00 |
| ComfyUI idle mitigation | launchd-on-demand | — | -$1.08 (savings) | -$13.00 |
| WhatsApp | Baileys (free, ToS-violating) | Meta Cloud API (future, hardens) | $0 current, $0-5 hardened | $0-$60 |
| **Secondary SIM (US prepaid)** | — | — | **$10-15** | **$120-$180** |
| Meta Business verification | one-time | — | — | $0-$500 + $0-$300/yr |
| Slack | already wired | — | $0 | $0 |
| Telegram | already wired | — | $0 | $0 |
| Memory review cron (if added) | local LLM | — | $0 hard | $0 hard, ~10 hr/yr soft |
| **Total (year 1, Baileys WhatsApp)** | | | **~$11-16** | **~$135-195** |
| **Total (year 2+, no Meta migration)** | | | **~$11-16** | **~$135-195** |
| **Total (with Meta Cloud API)** | | | **~$11-21** | **~$135-255 + registered agent** |

---

## Risks and Tradeoffs

- **RAM is at the edge of budget during chat with ComfyUI idle.** A face render in parallel will swap/OOM unless E7's model-unload sequence runs first. Mitigation: enforce unload in E7.
- **Wispr Flow not integrated.** System dictation app, no programmatic API. Used only for the operator's own dictation.
- **Kokoro voice naturalness.** Better than Piper, not ElevenLabs class. `F5-TTS` is the local-first voice-clone path (follow-up).
- **LivePortrait quality on M4.** Uncanny on some portraits. Mitigation: `/face off` per chat. Stale fork risk (~70% prob of issue in 12 months); MuseTalk as fallback.
- **WhatsApp account-banning risk.** ~1-3% within 90 days for personal Baileys accounts. Mitigation: secondary SIM + 30-day clock to Cloud API.
- **Meta Business verification overhead.** 1-14 days, real business entity required. Cost $0-500 + registered agent $50-300/yr.
- **macOS major-version risk.** ComfyUI on M-series has had MPS regressions on .0 releases. Mitigation: pin minor version, snapshot before upgrade.
- **Single-profile parallelism.** Default-only profile means E0-E5 run serially. Create `engineer`/`researcher`/`reviewer` to unlock parallel dispatch.
- **Gateway code changes (E7, E8).** These modify `gateway/run.py` and `hermes_cli/main.py`. If Hermes updates, the changes may conflict. Mitigation: minimal patches, well-commented, in a separate `~/.hermes/avatar/patches/` dir for easy rebase.
- **Memory fragmentation across profiles.** v1 uses one profile; if user adds `engineer` etc. later, memories are separate. Documented as a follow-up.
- **Single-point-of-failure gateway.** If `hermes gateway` crashes, the avatar is unreachable. Mitigation: `hermes gateway install` runs it as a launchd agent with `KeepAlive=true`.

---

## Defaults (confirm each with a keystroke, or override)

1. **Avatar name and voice gender**
   Default: `Sage` (gender-neutral, calm/precise/warm, Kokoro `af_sarah` voice)
   Override: edit `~/.hermes/SOUL.md` "Name" + "Voice/tone" + change `tts.providers.kokoro.voice` in config.
2. **Portrait source**
   Default: take a self-portrait, save to `~/portraits/avatar.png` before Phase E.
   Override: skip; B1 Step 6 smoke test uses a stock portrait.
3. **Slack workspace**
   Default: create a Slack app at api.slack.com with the listed scopes, paste the `xoxb-...` token.
   Override: use Telegram only.
4. **WhatsApp consent**
   Default: acknowledge ToS risk in writing before `hermes whatsapp`. Use a secondary SIM.
   Override: skip WhatsApp entirely; Slack + Telegram only.
5. **Cron job for memory review**
   Default: no cron in v1. Manual via `/memories` and `/forget` slash commands.
   Override: add a daily 02:00 cron that calls a summarizer.
6. **Profile creation**
   Default: create `engineer`, `researcher`, `reviewer` profiles for parallel dispatch.
   Override: stay on `default`, accept serial E0-E5.

---

## Summary of v2 → v3 changes (delta from iteration 2)

| Section | v2 | v3 | Source of fix |
|---|---|---|---|
| Step 0 | Profile discovery only | Added kanban-skill pre-flight verification (already installed on this host) | R2 (UX) |
| A0 | `models.avatar_*` + `routing.*` + `OLLAMA_KV_CACHE_TYPE=q4_0` | Dropped all invented keys; reuse `llama3.1:8b` (already on disk); add unload test | R4 (integration) |
| A1 | `~/.hermes/profiles/<name>/SOUL.md` alternative path | Dropped — only `~/.hermes/SOUL.md` is read | R4 |
| A1 | SIGHUP claim | Re-read every turn, no SIGHUP | R4 |
| A2 | `tts.provider: kokoro` (silently falls through to Edge) | User-declared command-provider schema with actual Kokoro CLI | R4 |
| A2 | `brew install piper` (fails) | `pip install piper-tts` | R4 |
| A2 | `with provider piper` arg (silently ignored) | Switch via `hermes config set tts.provider piper` | R4 |
| A2 | `engine_fallback_order: [kokoro, piper]` not in plan | Added in A2 Step 5 config + D3 #11 | R3 (UX) |
| A2 | Per-platform voice config not in plan | Added in A2 Step 5 | R3 (UX) |
| A5 | NEW | Warm-start preflight script + launchd wiring | R3 (UX) |
| B1 | Hardcoded pinned SHA | Capture live `main` HEAD via `git ls-remote` | R4 |
| B1 | Soft 60s render budget | Hard fail with exit code 2 | R4 |
| C2 | SKILL.md description 1 sentence | 3 sentences with explicit "not auto-render" | R4 |
| C2 | `render.py` calls `hermes chat` recursively | Direct import of `text_to_speech_tool` | R4 |
| C3 | `--platform` flag (doesn't exist) | Interactive wizard | R4 |
| C3 | ToS consent gate invented (doesn't exist) | E8 card creates it OR operator acknowledges in writing | R4 |
| C3 | NEW: per-platform voice defaults | `/voice default <mode> <platform>` + `gateway_voice_defaults.json` | R3 (UX) |
| C4 | Face mode as state-file addition | NEW E7 implements the gateway code change | R4 |
| C4 | NEW: `/status` and `/repair` slash commands | Added to E7 | R3 (UX) |
| C5 | NEW | "Live-failure UX" — status line on degradation | R3 (UX) |
| D1 | NEW: memory hygiene | `/memories`, `/forget <id>`, `/forget all <pattern>` | R3 (UX) |
| D2 | "under 8s" latency target | p50 ≤ 5 s, p95 ≤ 8 s, 20 trials + cold-start budget | R3 (UX) |
| D3 | 6 tests | 11 tests (added 7, 8, 9, 10, 11) | R3 (UX) |
| E | 7 cards (E0-E6) | 9 cards (E0-E8) + 1 review (E9) | R4 + R3 |
| Open Questions | 6 items, no defaults | "Defaults" section, each with one-keystroke override | R3 (UX) |
| Quickstart | NEW | 5-step block at the top of the plan | R3 (UX) |
| Out of scope | NEW | Explicit non-goals callout | R3 (UX) |
| RAM budget | "16K context max with q4_0 KV" | 8K context max, explicit model-unload sequence, 27.5 GB peak | R1 (cost) |
| Cost summary | Dropped secondary-SIM line | Added $120-180/yr | R1 |
| Cost summary | Dropped ComfyUI electricity | Re-added $15/yr | R1 |

**v3 still requires iteration 3 to verify these corrections hold up. R5 should be:** (1) cost-ram verifier confirms the new unload sequence + RAM budget, (2) integration tester confirms the new E7 gateway code change is feasible, (3) UX tester confirms the new Quickstart + Defaults section reads well.
