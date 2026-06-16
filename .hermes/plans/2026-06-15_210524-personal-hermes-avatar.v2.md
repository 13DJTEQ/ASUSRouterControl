# Personal Hermes Avatar — Implementation Plan (v2)

> **For Hermes:** Use **kanban-orchestrator** + **multi-agent-refinement-workflow** skills. v2 is the result of 3 parallel review passes (cost, risk, local-first). It corrects 4 structural defects in v1: the persona config block, the duplicate TTS skill, the wrong WhatsApp command, and 4 invented gateway config keys. v2 also adds: a 3-tier local LLM routing strategy, a Kokoro TTS upgrade path, a ComfyUI-on-arm64 verification test, and 17 testable "READY" criteria.

**Goal:** Turn Hermes into a reachable, named, voice-capable personal avatar — persona defined in `~/.hermes/SOUL.md`, talking on WhatsApp + Slack + Telegram, with a ComfyUI-driven local face that animates a still photo from the TTS audio. **Local-first, cloud as opt-in last resort.**

**Architecture (4 layers):**

1. **Persona layer** — `~/.hermes/SOUL.md` defines name, voice, backstory, address style, boundaries. Auto-loaded into the system prompt by `agent/prompt_builder.py:load_soul_md()`. No config edits required.
2. **Brain layer** — Local Ollama with 3-tier routing: trivial (llama3.1:8b Q6_K) → standard (mistral-small:24b Q4_K_M) → escalated (OpenRouter, opt-in only, $5/day cap).
3. **Voice + face layer** — Piper TTS (smoke test) → Kokoro TTS (production default) → ComfyUI + LivePortrait for face. All local.
4. **Transport layer** — Hermes gateway + platform adapters. WhatsApp via Baileys (secondary SIM, 30-day clock to Cloud API), Slack via OAuth, Telegram via BotFather.

**Tech stack (pinned):**
- Hermes 0.16.0 (already installed)
- Ollama 0.30.x (already installed, running on 127.0.0.1:11434)
- `mistral-small:24b-instruct-2503-q4_K_M`, `llama3.1:8b-instruct-q6_K` (pull once)
- Piper TTS 2023.11.x (smoke test only) — `kokoro-onnx 0.9.x` (production default)
- `faster-whisper 1.0.x` with `base` model (STT)
- ComfyUI 0.4.x + `kijai/ComfyUI-LivePortraitKJ@<pinned-sha>` (face)
- Hermes skills: `comfyui`, `kanban-orchestrator` (install if missing), `multi-agent-refinement-workflow`

**Constraint envelope (user):**
- Local AI tools as the main path. Cloud only as last resort.
- WhatsApp baked in by default; will get a Meta Business account if needed.
- Wispr Flow acknowledged as out-of-scope (no programmatic API).
- Multi-orchestrated loop to analyze, optimize, review, iterate 3x before final execution.
- This plan has been through 1 review iteration (R1 cost + R2 risk + R3 integration) and will go through 2 more before being marked READY.

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
                              └──────────────┬──────────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
              ▼                              ▼                              ▼
   ┌────────────────────┐       ┌────────────────────┐       ┌────────────────────┐
   │  STT tool          │       │  Classifier        │       │  Image downloader  │
   │  faster-whisper    │       │  llama3.1:8b       │       │  (httpx / aiohttp) │
   │  in:   /tmp/x.ogg  │       │  classify intent   │       └─────────┬──────────┘
   │  out:  /tmp/x.txt  │       │  ~50 ms            │                 │
   └─────────┬──────────┘       └─────────┬──────────┘                 │
             │                            │                            ▼
             │  text                      │  intent              ┌────────────────────┐
             └────────────────────────────┤                      │  ComfyUI server    │
                                          │                      │  127.0.0.1:8188    │
                                          ▼                      │  (LivePortrait +   │
                                ┌────────────────────┐           │   VHS_VideoCombine)│
                                │  Model router      │           └─────────┬──────────┘
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
- Ollama HTTP: `127.0.0.1:11434` (loopback only, `OLLAMA_HOST` pinned in `.env`)
- Hermes gateway: launchd user agent (no port; stdio + IPC)

**Filesystem handoff (preferred over streaming):**
- Inbound voice: `/tmp/hermes-in-<uuid>.{ogg,mp3,m4a}` (ffmpeg → 16 kHz mono WAV before STT)
- STT text: `/tmp/hermes-in-<uuid>.txt`
- TTS audio out: `~/.hermes/avatar/cache/<unix_ts>.wav`
- Face MP4 out: `~/.hermes/avatar/cache/<unix_ts>.mp4`
- ComfyUI input: `~/comfy/input/avatar-<uuid>.{png,wav}`
- ComfyUI output: `~/comfy/output/ComfyUI_<jobid>.mp4`

**Contract surface:** `~/.hermes/avatar/cache/` is the producer/consumer boundary. Both TTS and face write there; gateway reads from there. Makes the "send these two files together" logic trivial.

---

## Same-Machine Integration Table

| From | To | Transport | Address / Path | Auth | Notes |
|---|---|---|---|---|---|
| Hermes gateway (platform adapter) | ComfyUI server | HTTP POST `/prompt`, GET `/history/{id}`, GET `/system_stats` | `http://127.0.0.1:8188` | None (loopback) | Polling 1 s, max 120 s; timeout → voice-only reply |
| Hermes agent | Ollama server | HTTP POST `/api/chat`, GET `/api/tags` | `http://127.0.0.1:11434` | None (loopback) | `keep_alive: "30m"` primary, `5m` trivial; `OLLAMA_NUM_PARALLEL=1` |
| Hermes STT tool | faster-whisper | Python in-process (CTranslate2) | n/a — same venv | n/a | Load model once at gateway start, keep in process memory |
| Hermes TTS tool | Kokoro / Piper | In-process (Kokoro ONNX) or subprocess (Piper) | n/a | n/a | **Kokoro in-process recommended** — saves ~80 ms vs subprocess |
| TTS output | Platform adapter (send) | Filesystem read | `~/.hermes/avatar/cache/<ts>.wav` | n/a | `aiofiles` async read; ≤25 MB cap (WhatsApp) |
| ComfyUI render script | ComfyUI server | HTTP multipart workflow JSON | `http://127.0.0.1:8188/prompt` | None | Use `/prompt`, not legacy `/queue`; pass API-format JSON |
| Platform adapter (inbound voice) | STT tool | Filesystem drop | `/tmp/hermes-in-<uuid>.{ogg,mp3}` | n/a | ffmpeg → 16 kHz mono WAV before STT |
| ComfyUI | Hermes cache dir | Filesystem move | `~/comfy/output/ComfyUI_*.mp4` → `~/.hermes/avatar/cache/<ts>.mp4` | n/a | `shutil.move` after `/history/<id>` reports output |
| Gateway | launchd | launchd user agent | `~/Library/LaunchAgents/com.hermes.gateway.plist` | n/a | `KeepAlive=true`, `ThrottleInterval=10`; stdout to `~/.hermes/logs/gateway.log` |
| Hermes (persona loader) | `~/.hermes/SOUL.md` | Filesystem read at gateway start | `~/.hermes/SOUL.md` | n/a | Re-read on SIGHUP so persona edits don't require a restart |

---

## Model Routing Strategy (3-tier, local-first, cloud opt-in)

```
inbound message arrives at gateway
        │
        ▼
classify(message)            ← local Ollama llama3.1:8b, ~50 ms
        │
        ├── trivial ──► Ollama "avatar-trivia" = llama3.1:8b
        │   • "ok", "thanks", "👍", "yes", "no"
        │   • One-line factual Q
        │   • Greeting / acknowledgment
        │   • ≤ 6 words AND no memory retrieval needed
        │   → local reply, ~0.8 s p50 on M4
        │
        ├── standard ──► Ollama "avatar-chat" = mistral-small:24b
        │   • Anything else
        │   • Replies requiring persona + memory + reasoning
        │   • Voice-out replies (always)
        │   → local reply, ~3 s p50 on M4
        │
        └── escalated ──► OpenRouter cloud (opt-in only)
            • Trigger: user explicitly says "use the big model"
            • Trigger: task requires web search or code across > 5 files
            • Trigger: KV cache would exceed 16K context
            → cloud reply, ~1.5 s p50 over WAN
            → log every cloud call with reason in ~/.hermes/logs/cloud-routing.log
            → cost cap: $5/day; alert at 80%
```

**Hard rule:** the avatar is local-first **by default**. Cloud is opt-in per session, never opt-out. If `routing.cloud_enabled: false`, every reply must come from a local model or the avatar returns "I can't answer that locally."

**Acceptance criterion:** ≥ 90% of replies in a 24 h period served by a local model. `hermes stats --routing` prints this percentage.

---

## Step 0 — Profile Discovery (BLOCKING)

**Objective:** Find the real profile names on this machine before any card is created. Dispatcher silently drops unknown assignees.

**Action:** Run `hermes profile list` in an interactive session. Paste the table into chat. Ask the user once: "Use these as the worker roster for the avatar plan, or do you want to add/rename any?"

**Current state on this machine (verified):** only the `default` profile exists. The plan's E1–E4 decomposition assumes 3+ worker profiles. With one profile, the kanban lanes will run **sequentially, not in parallel** (the dispatcher fans out per-profile, not per-card). To get the full multi-orchestrated benefit:

```bash
hermes profile create engineer
hermes profile create researcher
hermes profile create reviewer
```

(Each profile inherits the default model unless overridden via `hermes -p <profile> model`.)

If the user declines, set all E1–E4 cards' `assignee: default` and accept serial execution.

**Verification:** A line of text in chat like:
> Profile roster confirmed: `default, engineer, researcher, reviewer` (or `default` only).

**Commit:** none — discovery step.

**Kanban pre-flight check:** `hermes skills list | grep -i kanban` must show `kanban-orchestrator` and `multi-agent-refinement-workflow`. If not, install before dispatching.

---

## Phase A — Foundation (sequential, blocks everything else)

### Task A0: Repoint the LLM default to local Ollama (BLOCKING, 2 min)

**Objective:** Make the persona's gateway replies free. The current `~/.hermes/config.yaml` has `model.default: minimax-m3:cloud` — a paid cloud model — even though Ollama is installed with 6 local models warm.

**Files:** `~/.hermes/config.yaml`, `~/.hermes/.env`

**Step 1: Edit config.yaml**

```yaml
model:
  default: mistral-small:24b-instruct-2503-q4_K_M
  provider: ollama-launch
  fallback: openrouter/anthropic/claude-haiku-4   # only if user opts in

models:
  avatar_chat:    "mistral-small:24b-instruct-2503-q4_K_M"
  avatar_trivia:  "llama3.1:8b-instruct-q6_K"
  avatar_cloud:   "anthropic/claude-sonnet-4"   # opt-in only

routing:
  cloud_enabled: false           # default OFF
  cloud_daily_usd_cap: 5.0
  trivial_max_words: 6
  log_cloud_calls: true
```

**Step 2: Set Ollama env vars in `~/.hermes/.env`**

```
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_KV_CACHE_TYPE=q4_0
OLLAMA_NUM_PARALLEL=1
OLLAMA_KEEP_ALIVE=30m
```

**Step 3: Pull the two required models**

```bash
ollama pull mistral-small:24b-instruct-2503-q4_K_M
ollama pull llama3.1:8b-instruct-q6_K
```

**Step 4: Smoke test**

```bash
ollama run mistral-small "say hi in 5 words"   # must respond in <4 s
hermes chat -q "what time is it?"              # must respond in <2 s
```

**Step 5: Verify no cloud egress**

Activity Monitor → Network tab. While running the smoke test, confirm zero requests to `api.minimax.io` or `openrouter.ai`.

**Verification gate:** all four smoke tests pass. Local model loaded. Network tab clean.

**Commit:** none (config + dotfile, not in repo).

---

### Task A1: Author the persona in `~/.hermes/SOUL.md`

**Objective:** Establish the named persona via Hermes's actual identity slot — not the invented `persona:` config block from v1.

**Why the rewrite:** v1 wrote `~/.hermes/personas/avatar.md` and a `persona:` config block. **Neither is read by Hermes.** The real identity slots are:
- `agent.personalities.<name>` (a registry of named system prompts, settable via `/personality <name>`)
- `~/.hermes/SOUL.md` (per-profile, auto-loaded by `agent/prompt_builder.py:load_soul_md()`)

SOUL.md is the right choice for gateway-default identity across all platforms.

**Files:** `~/.hermes/SOUL.md` (overwrite, not create — it already exists)

**Step 1: Write the persona**

Minimum content for SOUL.md:
- Name
- One-line tagline
- Voice/tone (pick 2: calm/precise/warm/dry)
- Address style for the user (first name, "boss", etc.)
- Boundaries (refuses X, defers to user for Y)
- 3 example greeting lines
- 3 example follow-ups
- Memory hooks ("if user mentions <project>, recall <skill>")

**Step 2: Verify auto-load**

```bash
hermes chat -q "introduce yourself in one sentence"
```

Expected: matches the persona's tagline, in the persona's voice, addressing the user by the chosen form. **If the model just gives a generic response, the SOUL.md wasn't picked up — check `agent/prompt_builder.py:load_soul_md()` and ensure the file is at the path expected by your Hermes build (typically `~/.hermes/SOUL.md` or `~/.hermes/profiles/<name>/SOUL.md`).**

**Step 3 (optional): Mirror in `agent.personalities`**

If you want quick A/B between personas:

```yaml
agent:
  personalities:
    avatar: |
      <SOUL.md contents, abbreviated>
```

Then `/personality avatar` switches per session.

**Commit:** none.

---

### Task A2: Install Kokoro TTS (production default) and Piper (smoke test fallback)

**Objective:** Two TTS engines installed; Kokoro is the production voice, Piper is the smoke test.

**Why both:** v1 picked Piper only. v2 review found Piper is robotic ("intelligible but below the bar" for an avatar that talks 50×/day). Kokoro is the recommended 2026 local TTS — much more natural, smaller model, ONNX runtime.

**Files:** none (binary install + model download).

**Step 1: Install Kokoro**

```bash
pipx install kokoro-onnx
# or, in the active venv:
.venv/bin/pip install kokoro-onnx
brew install espeak-ng   # if not already present
```

**Step 2: Install Piper (smoke test only)**

```bash
brew install piper
```

**Step 3: Download a Kokoro voice**

Kokoro ships ~50 voices. The default is `af_sarah` (US English, female, natural). Download per kokoro-onnx docs (typically fetched on first run, ~300 MB).

**Step 4: Download a Piper voice (smoke test)**

```bash
mkdir -p ~/.local/share/piper/voices
cd ~/.local/share/piper/voices
curl -L -o en_US-amy-low.onnx       https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx
curl -L -o en_US-amy-low.onnx.json  https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx.json
```

**Step 5: Register with Hermes**

Edit `~/.hermes/config.yaml`:

```yaml
tts:
  provider: kokoro          # production default
  kokoro:
    voice: af_sarah
    speed: 1.0
  piper:                    # smoke test fallback
    model: ~/.local/share/piper/voices/en_US-amy-low.onnx
    config: ~/.local/share/piper/voices/en_US-amy-low.onnx.json
```

Restart the gateway: `hermes gateway restart`.

**Step 6: Smoke test both**

```bash
hermes chat -q 'use text_to_speech to say "I am ready" to /tmp/kokoro-test.wav'  # kokoro
hermes chat -q 'use text_to_speech with provider piper to say "I am ready" to /tmp/piper-test.wav'
afplay /tmp/kokoro-test.wav
afplay /tmp/piper-test.wav
```

Expected: Kokoro sounds natural; Piper sounds robotic. Both play.

**Verification gate:** both engines produce audio, both play, Kokoro clearly more natural.

**Commit:** none.

---

### Task A3: Verify faster-whisper is wired (no install needed)

**Objective:** Voice messages auto-transcribe locally. STT is already configured (`stt.enabled: true`, `stt.local.model: base`). Only the Python package may be missing from the active venv.

**Why the rewrite:** v1 spent a full Task "installing" faster-whisper. It's already wired in config. The v1 verification step (`hermes chat -q "transcribe the audio at /tmp/avatar-test.wav"`) was wrong — that asks the model, not the STT subsystem.

**Step 1: Install the library**

```bash
.venv/bin/pip install faster-whisper
```

Idempotent; first `transcribe()` call also downloads the `base` model into `~/.cache/huggingface/`, ~150 MB.

**Step 2: Smoke test**

```bash
python -c "from faster_whisper import WhisperModel; m=WhisperModel('base'); segs,_=m.transcribe('/tmp/kokoro-test.wav'); print(''.join(s.text for s in segs))"
```

Expected: prints `I am ready.` (matches the synthesized audio from A2 Step 6).

**Step 3 (optional accuracy upgrade):** If accents or noisy audio are a problem, switch to `small`:

```yaml
stt:
  local:
    model: small    # 2x slower, ~500 MB download
```

**Verification gate:** transcription matches synthesized text within ~1 word.

**Commit:** none.

---

### Task A4: Wispr Flow — out of scope (one-liner)

Add a note to the persona SOUL.md or to a `~/.hermes/avatar/notes.md` file:

```
# Wispr Flow
Out of scope for the avatar's STT pipeline. It is a system-level dictation utility
that types into the focused app; no programmatic API, no CLI, no IPC surface.
Used instead for the operator's own interactive dictation when chatting with Hermes.
Avatar's STT is faster-whisper (local, no quota).
```

This prevents a future review iteration from re-proposing it.

**Commit:** none.

---

## Phase B — Local Face (atomic, single card, parallelizable with A but blocks C2)

### Task B1: ComfyUI + LivePortrait + workflow (ATOMIC)

**Objective:** ComfyUI server running with LivePortrait nodes loaded, and a reusable talking-head workflow.

**Why atomic:** v1 split B1 (ComfyUI install) and B2 (LivePortrait install). Splitting them invites a partial state where ComfyUI is up but LivePortrait isn't loaded. `comfy node install` handles clone + pip install + restart in one command.

**Files:**
- `~/comfy/` (ComfyUI install dir)
- `~/comfy/custom_nodes/ComfyUI-LivePortraitKJ/`
- `~/comfy/workflows/avatar_talking_head.json`
- `~/.hermes/avatar/render_talking_head.py` (driver script, used by C2)

**Step 1: Verify ComfyUI works on this machine (arm64 macOS, macOS 26.5)**

ComfyUI's `pipx install comfy-cli && comfy install` path is the documented install. **Verification gate for this step:**

```bash
comfy launch --background
sleep 10
curl -s http://127.0.0.1:8188/system_stats | jq '.system.comfyui_version, .devices[].name'
```

Expected: JSON with `comfyui_version` and a devices array. If this fails on M4 arm64 (possible — ComfyUI's PyTorch MPS support has been spotty), fallback: run ComfyUI via Docker Desktop (`docker run -p 8188:8188 comfyui/comfyui:latest`). Docker Desktop on M4 runs the linux/amd64 image under Rosetta 2 emulation — slow but works.

**If the install fails:** log the exact error, mark B1 blocked, and switch to MuseTalk (the only face animator with a near-realtime M4 path) per the alternatives table in the v2 risk review.

**Step 2: Install LivePortrait custom node (pinned commit)**

```bash
cd ~/comfy/custom_nodes
comfy node install kijai/ComfyUI-LivePortraitKJ@<pinned-sha>
# If `comfy node install` doesn't support the github URL, fall back to:
# git clone https://github.com/kijai/ComfyUI-LivePortraitKJ.git
# cd ComfyUI-LivePortraitKJ && pip install -r requirements.txt && python install.py
```

`kijai/ComfyUI-LivePortraitKJ` last code push was 2024-08-05. **Pin a known-good commit SHA** so a future `comfy node install` re-fetch doesn't break the workflow. (Capture the SHA from the repo's `main` HEAD as of 2026-06-15.)

**Step 3: Restart ComfyUI and verify nodes are loaded**

```bash
comfy restart
curl -s http://127.0.0.1:8188/object_info | jq '.["LivePortraitLoadModels"] // .["LivePortraitProcess"]'
```

Expected: a JSON schema, not `null`. If both keys are null, check `~/comfy/comfyui.log` for import errors.

**Step 4: Author the talking-head workflow in the ComfyUI UI**

1. Open http://127.0.0.1:8188 in a browser.
2. Add nodes: `LoadImage` (portrait) → `LoadAudio` (TTS .wav) → `LivePortraitProcess` (or `LivePortraitLoadModels` + `LivePortraitProcess`) → `VideoCombine`.
3. Configure `VideoCombine` at 25 fps, MP4 output.
4. Connect `SaveImage` for first-frame preview as fallback.
5. Save with "Save (API Format)" → `~/comfy/workflows/avatar_talking_head.json`.

**Step 5: Author the driver script `~/.hermes/avatar/render_talking_head.py`**

- Loads `~/comfy/workflows/avatar_talking_head.json`
- Substitutes portrait path and audio path
- POSTs to `http://127.0.0.1:8188/prompt`
- Polls `/history/{prompt_id}` until `outputs` contains the video
- Copies the resulting MP4 to `~/.hermes/avatar/cache/<unix_ts>.mp4`
- Polling interval 1 s, max 120 s; timeout → return error and let gateway fall back to voice-only
- Exits 0 on success with the output path on stdout (JSON)

**Step 6: Smoke test the whole pipeline**

Use a stock portrait + the A2 kokoro-test.wav:

```bash
python ~/.hermes/avatar/render_talking_head.py \
  --portrait ~/comfy/input/example_portrait.png \
  --audio    /tmp/kokoro-test.wav \
  --output   /tmp/avatar-test.mp4
```

**Verification gate:**
- MP4 file exists with non-zero size
- `afinfo /tmp/avatar-test.mp4` shows duration
- `ffprobe -v error -select_streams v -show_entries stream=width,height /tmp/avatar-test.mp4` shows the resolution matches the portrait
- **Render-time budget:** 3-second audio → under 60 s render time on M4. Log the wall-clock time; fail the gate if it exceeds 60 s.
- Play in QuickTime; face should be recognizable and mouth should move

**Commit:** none.

---

## Phase C — Hermes Integration

### Task C1: REMOVED — use the built-in `text_to_speech` tool

v1 created a new `avatar-tts` skill. v2 review found this duplicates `tools/tts_tool.py::text_to_speech_tool`, which already:
- supports all 10 built-in providers (edge, piper, neutts, kittentts, openai, elevenlabs, minimax, mistral, gemini, xai)
- is auto-loaded as a tool
- is wired into the gateway's voice-reply path (`gateway/run.py:_send_voice_reply`)

Adding a second TTS path confuses the agent and creates two voices. **Do not build this skill.** A2's Kokoro config is sufficient.

---

### Task C2: Add a thin `avatar-face` skill (wraps the B1 driver script)

**Objective:** A tool the agent can call to produce a talking-head video from text. Thin wrapper; the heavy lifting is the B1 driver script.

**Files:**
- Create: `~/.hermes/skills/avatar-face/SKILL.md`
- Create: `~/.hermes/skills/avatar-face/scripts/render.py` (wrapper around `~/.hermes/avatar/render_talking_head.py`)

**Step 1: SKILL.md frontmatter**

```yaml
---
name: avatar-face
description: "Avatar face animation — generate a talking-head video from a portrait and audio. Local ComfyUI + LivePortrait."
---
```

**Step 2: SKILL.md body**

Document `render(text, portrait_path, output_path=None)`:
- If `text` given, first calls `text_to_speech` to produce audio
- If `text` is None, uses an existing audio file
- Returns JSON with `success`, `output_path`, `duration_seconds`, `engine: "liveportrait"`
- Face is on-demand only — never auto-render (avoids ~37 min/day of M4 CPU on 50 voice replies)

**Step 3: Wrapper script**

```python
# ~/.hermes/skills/avatar-face/scripts/render.py
import argparse, json, subprocess, os
from pathlib import Path

CACHE = Path.home() / ".hermes/avatar/cache"
CACHE.mkdir(parents=True, exist_ok=True)

def render(text, portrait, output=None):
    ts = int(time.time())
    audio = CACHE / f"{ts}.wav"
    if text is not None:
        # call Hermes's built-in TTS tool via hermes chat
        subprocess.run([
            "hermes", "chat", "-q",
            f'use text_to_speech to say "{text}" to {audio}'
        ], check=True)
    if output is None:
        output = CACHE / f"{ts}.mp4"
    result = subprocess.run([
        "python", str(Path.home() / ".hermes/avatar/render_talking_head.py"),
        "--portrait", portrait,
        "--audio", str(audio),
        "--output", str(output),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return json.dumps({"success": False, "stderr": result.stderr})
    return json.dumps({
        "success": True,
        "output_path": str(output),
        "duration_seconds": probe_duration(output),
        "engine": "liveportrait",
    })
```

**Step 4: Smoke test**

```bash
hermes chat -q "use avatar-face to render 'Hello, I am your new assistant.' with portrait at ~/portraits/me.png to /tmp/hello.mp4"
```

Expected: MP4 exists, plays, audio matches spoken text.

**Verification gate:** rendered MP4 is coherent. Tool returns success JSON.

**Commit:** none.

---

### Task C3: Connect the gateway (Slack + WhatsApp + Telegram)

**Objective:** The avatar replies on real chat platforms.

**Files:** none (Hermes gateway config + platform-specific auth files).

**Step 1: Install the gateway as a launchd service**

```bash
hermes gateway install
hermes gateway status
```

Expected: status shows the gateway running as a launchd user agent with auto-restart.

**Step 2: Configure Slack (zero cost, low complexity)**

```bash
hermes gateway setup --platform slack
```

The wizard asks for: workspace OAuth token (`xoxb-...`) from a Slack app with `chat:write`, `channels:history`, `im:history`, `im:write`, `files:write` scopes. Bot must be `/invite @<bot-name>`-d in any DM it should answer in.

**Smoke test:** send a text message in the configured Slack DM. Expected: avatar replies in persona voice (text).

**Step 3: Configure Telegram (zero cost, low complexity — best test platform)**

```bash
hermes gateway setup --platform telegram
```

Wizard asks for the BotFather token. Bot must be `/start`-ed in a DM at least once.

**Smoke test:** send a voice note, send text, send a photo. Expected: avatar replies to each in persona voice.

**Step 4: Configure WhatsApp — DEFAULT Baileys bridge (free, ToS-violating), 30-day clock to Cloud API**

**v1 error:** v1 used the command `hermes gateway setup --platform whatsapp` — **this command does not exist.** The actual entry point is `hermes whatsapp`.

```bash
hermes whatsapp
```

This pairs via QR code to the user's personal WhatsApp account using the Baileys bridge (`scripts/whatsapp-bridge/bridge.js`, depends on `@whiskeysockets/baileys`).

**ToS warning — MUST be acknowledged before this step:** The Baileys reverse-engineered protocol is against WhatsApp's ToS. Account-banning risk is real (~1–3% of personal accounts on Baileys get banned within 90 days, per community reports). The user's primary phone number is at stake.

**Mitigations (required):**
1. **Use a secondary phone number** (spare SIM, not the user's primary number). WhatsApp does not accept most VoIP numbers (Google Voice fails in many regions).
2. **Set up a daily cron job** to call `hermes whatsapp` and re-pair automatically if the bridge disconnects. QR pairing expires in ~60 seconds; the phone must have a stable internet connection.
3. **Plan a migration to Cloud API** within 30–60 days. The gateway already ships `gateway/platforms/whatsapp_cloud.py` — switch is a config change once Meta Business verification completes (1–3 weeks).

**ToS consent gate:** before running `hermes whatsapp`, the operator must respond to a single on-screen prompt: "I understand that using a personal WhatsApp account via Baileys violates WhatsApp's ToS, may result in account banning within 90 days, and that I should use a secondary phone number. I am proceeding at my own risk." Default = no, abort.

**Smoke test:** send a voice note from another phone to the bridge's number. Expected: avatar transcribes + replies (text or voice, per per-chat voice mode).

**Step 5: Set the gateway's home persona**

`~/.hermes/SOUL.md` is already auto-loaded. No additional config needed.

**Verification gate:** `hermes gateway status` shows three platforms connected. A message on any platform receives a reply within 5 seconds.

**Commit:** none.

---

### Task C4: Per-chat voice mode (using the existing _voice_mode state, not invented config keys)

**v1 error:** v1 wrote four config keys (`gateway.persona`, `gateway.voice_reply`, `gateway.voice_reply_min_chars`, `gateway.face_reply`) — **none exist in the Hermes config schema.** The real mechanism is the per-chat voice mode state file + in-chat commands.

**Files:** none (uses existing infrastructure + adds a parallel face-mode state file).

**Step 1: Use the existing voice-mode mechanism**

Voice reply is a per-chat runtime state stored in `~/.hermes/gateway_voice_mode.json` (`_load_voice_modes` in `gateway/run.py:2458`). Commands:

- `/voice off` — text-only replies (default)
- `/voice voice_only` — voice reply only when input was a voice note
- `/voice all` — every reply is sent as both text and voice

Send `/voice voice_only` from a Telegram/Slack/WhatsApp chat to enable voice replies in that chat. **No global config knob exists.**

**Step 2: Add a parallel face-mode state file (new feature)**

Create `~/.hermes/gateway_face_mode.json` mirroring the voice-mode file. Schema:

```json
{
  "<platform>:<chat_id>": "on" | "off"
}
```

Add slash commands in the gateway:
- `/face on` — render face videos for replies >30 chars
- `/face off` — text+voice only

The face skill (C2) is invoked only when (a) the chat is in face mode AND (b) the reply is over the `face_reply_min_chars` threshold (30 chars default — to avoid rendering a 1-second video for a 1-word reply).

**Step 3: Verify**

Send a voice note >30 chars to the Telegram bot. With `/voice voice_only`, expected: avatar replies with a `.wav` attachment (Kokoro output).
With `/face on`, expected: avatar replies with both `.wav` and `.mp4` attachments.

**Verification gate:** voice-in → voice-out, text-in → text-out, face mode adds `.mp4` only when enabled. No `error: no voice provider` log lines in `~/.hermes/logs/gateway.log`.

**Commit:** none.

---

## Phase D — Polish and Optimization

### Task D1: Memory wiring verification (no install needed)

**Objective:** Verify the persona remembers user-specific facts across sessions and platforms.

**Why the rewrite:** v1 wrote a config edit. Memory is already on (`memory.memory_enabled: true`, `memory.user_profile_enabled: true`).

**Step 1: Verify both flags**

```bash
grep -E "memory_enabled|user_profile_enabled" ~/.hermes/config.yaml
```

**Step 2: Seed a few facts**

In an interactive session:
```
remember: I run the ASUSRouterControl project
remember: my timezone is America/Los_Angeles
remember: I prefer concise replies unless I ask for detail
```

**Step 3: Verify cross-session recall**

```
/new
what projects am I running?
```

Expected: avatar mentions ASUSRouterControl.

**Step 4: Verify cross-platform recall**

Send a message in Telegram: `remember this: I am testing the avatar right now`
Send a message in Slack 5 minutes later: `what was I doing?`
Expected: avatar recalls the testing context.

**Step 5: Memory fragmentation note (out of scope for v1)**

If the user later adds a second profile (e.g., `engineer`) and uses it for engineering tasks, those memories are separate from the avatar's profile. Document as a follow-up; do not engineer a consolidation cron in v1.

**Verification gate:** memory persists across `/new` and across platforms.

**Commit:** none.

---

### Task D2: Latency tuning

**Objective:** Voice-to-voice round trip p50 ≤ 5 s, p95 ≤ 8 s, measured over 20 trials.

**Why the rewrite:** v1 said "under 8 seconds" with no percentile. p50 ≤ 5 s is realistic with local Mistral 24B + Kokoro; p95 ≤ 8 s catches the slow tail.

**Step 1: Measure baseline**

Use the Telegram bot: send a 5-second voice note, time the reply. Repeat 20 times. Record p50, p95.

**Step 2: Tune**

- If STT is slow: drop `stt.local.model` from `base` → `tiny` (or `small` → `medium` for accuracy).
- If TTS is slow: Kokoro is in-process (~0.6 s per 10 s output). If the bottleneck is something else, profile with `time` invocations.
- If the agent loop is slow: check `agent.max_turns` in `config.yaml` — should be 5–10 for chat replies, not 90.
- If the model is slow: switch the trivial-tier model to `llama3.1:8b` for short replies, `mistral-small:24b` for longer. Avoid OpenRouter `:free` — ~50 req/day cap is exceeded by a single morning of voice traffic.

**Step 3: Re-measure**

Target: voice-in (5s) → voice-out reply p50 ≤ 5 s, p95 ≤ 8 s, over 20 trials.

**Verification gate:** stopwatch test on 20 different voice notes meets the target.

**Commit:** none.

---

### Task D3: Failure-mode and boundary tests (6 cases)

**Test cases:**

1. **Voice reply when Kokoro is down** — kill the kokoro process, send a voice note. Expected: graceful fallback to text + log entry.
2. **Face render when ComfyUI is down** — ask for an avatar face explicitly with `/face on`. Expected: voice-only reply, no crash.
3. **Memory injection boundary** — ask the avatar for a fact it doesn't have. Expected: "I don't have that in my notes" rather than invented data.
4. **Persona consistency** — send a deliberately off-topic / adversarial message. Expected: avatar stays in persona, refuses politely, doesn't break character.
5. **Long message** — send a 60-second voice note. Expected: transcription succeeds, avatar's reply is a reasonable length (not 5x as long), Kokoro handles the synthesis without OOM.
6. **Voice-in race condition** — send two voice notes within 5 seconds on Telegram. Expected: both are transcribed, both are replied to, in order, with no overlap. The `group_sessions_per_user: true` config (already set) should serialize them, but the test must prove it. Verification: `~/.hermes/logs/gateway.log` has two distinct `[telegram] message_id` lines for the two messages, with the second `response_sent` timestamp after the first's.

**Files:** none (manual tests with `hermes chat -q` or live gateway).

**Verification gate:** all 6 tests pass. Log `~/.hermes/logs/gateway.log` has no `ERROR` or `Traceback` lines during the test run.

**Commit:** none.

---

## Phase E — Multi-Orchestrated Deployment

This is the **multi-orchestrated loop** the user requested. The dispatcher in the gateway spawns a fresh worker per card; the cards below run in parallel by the dispatcher and converge in the integration step.

### Pre-flight (BLOCKING)

```bash
hermes skills list | grep -i kanban
```

Must show `kanban-orchestrator` and `multi-agent-refinement-workflow`. If not, install from the skills hub before dispatching.

### Card layout

After Step 0 (profile discovery), the operator creates the following cards. Each card body explicitly names its acceptance criteria so the goal-mode judge can score it.

**Independent lanes (run in parallel, no parents):**

- **E0** — *Engineer profile*: install ComfyUI + LivePortrait + download all 3 GB of models in a single atomic step. Use `pipx install comfy-cli && comfy install && cd ~/comfy/custom_nodes && comfy node install kijai/ComfyUI-LivePortraitKJ@<pinned-sha>`. Verify with `curl -s http://127.0.0.1:8188/object_info | jq '.["LivePortraitProcess"] // empty'`. **This card blocks E1.** Acceptance: `system_stats` returns 200 with valid JSON, LivePortrait node loaded, driver script smoke test produces a valid MP4 under the 60s render budget.

- **E1** — *Engineer profile*: implement the TTS upgrade (A2 Kokoro install + config wiring). Acceptance: `hermes chat -q "use text_to_speech to say..."` returns a Kokoro-synthesized file; afplay confirms natural voice; Piper still works as fallback via explicit provider parameter.

- **E2** — *Engineer profile*: implement the local LLM routing (A0). Acceptance: `~/.hermes/config.yaml` updated, models pulled, smoke test returns in <4s, network tab clean of cloud egress, `hermes stats --routing` shows ≥ 90% local.

- **E3** — *Engineer profile*: implement the persona file (A1). Acceptance: SOUL.md populated, `hermes chat -q "introduce yourself"` returns persona text in persona voice, SIGHUP reloads without restart.

- **E4** — *Researcher profile*: enumerate the platform-specific auth steps for Slack, WhatsApp, and Telegram, and produce a single Markdown checklist at `~/.hermes/avatar/SETUP_CHECKLIST.md` with copy-pasteable command sequences for each. Acceptance: a user (or a fresh engineer card) can follow the checklist from a clean Hermes install and reach `gateway status` showing all three platforms connected.

**Integration (depends on E0, E1, E2, E3, E4):**

- **E5** — *Engineer profile (integrator)*: do the gateway install + per-platform connect (C3 + C4), using the checklist from E4. Acceptance: a Telegram voice note round-trips with a voice reply; a Slack text DM round-trips with a text reply; a WhatsApp voice note (secondary SIM) round-trips with a voice reply.

- **E6** — *Reviewer profile*: run the failure-mode and boundary tests from D3 against the live gateway, plus a cloud-call cap test (send 10 trivial messages, verify `~/.hermes/logs/cloud-routing.log` is empty because `routing.cloud_enabled: false`). Acceptance: all 7 tests pass; logs clean.

**Goal-mode note:** E0, E1, E2, E3 are good candidates for `goal_mode=True` (TDD-style: "this skill works end-to-end, prove it"). E4 and E5 are single-shot, not goal-mode. E6 is a single-shot reviewer card.

### Card metadata convention

Every card body must include:

```
assignee: <from step 0>
parents: [<id> | empty]
goal_mode: true|false
acceptance: <bulleted, judge-readable criteria>
files: <exact paths>
verification: <exact commands + expected output>
```

This is the contract — workers who don't follow it get blocked at review.

### Dispatch flow

1. Operator confirms profile roster from Step 0 and kanban skill install.
2. Operator creates E0, E1, E2, E3, E4 in parallel.
3. Dispatcher fans them out (capped at `delegation.max_concurrent_children`).
4. Each worker reports back via `kanban_complete(summary=..., metadata=...)` with file paths and verification output.
5. When E0–E4 all complete, E5 auto-promotes (parents satisfied) and runs.
6. E6 runs after E5.
7. Operator gets a gateway ping on E6 completion with the full audit trail.

### Failure recovery

- If any worker in E0–E4 calls `kanban_block()`, the dispatcher opens a Recovery drawer. Operator reviews, either reclaims and respawns or reassigns.
- If a worker silently claims success but the file path is missing or empty, the integration card E5 catches it at "verification step 1: file exists" and blocks back to that lane.

### Single-profile caveat

With only the `default` profile, the dispatcher fans out per-profile, not per-card. E0–E4 will run **sequentially, not in parallel**. To get the full multi-orchestrated benefit, create `engineer`, `researcher`, `reviewer` profiles before dispatch.

---

## Testable "READY" Criteria (after 3 review iterations)

The plan is **NOT READY** until all 17 of these are ✓. Each is a yes/no check; none are subjective.

| # | Criterion | How to check | Type |
|---|---|---|---|
| 1 | **Local LLM is the default brain**, not OpenRouter | `grep -c "Ollama" plan.md` > 0 AND `routing.cloud_enabled: false` default in YAML | Blocking |
| 2 | **Model routing decision tree is in the plan** | "Model routing strategy" section with trivial / standard / escalated tiers | Blocking |
| 3 | **All TTS engines are documented** (Piper = smoke test, Kokoro = production) | `grep -c "Kokoro" plan.md` ≥ 4 | Blocking |
| 4 | **Wispr Flow is explicitly excluded** with a "why not" | Plan contains a "Wispr" section saying "do not use, because [reason]" | Blocking |
| 5 | **Component data flow diagram exists** with all 4 local services and their loopback addresses | "Component Data Flow" section present | Blocking |
| 6 | **Same-machine integration table exists** with from/to/transport/port for at least 8 pairs | "Same-Machine Integration Table" section present | Blocking |
| 7 | **ComfyUI on arm64 macOS** has a tested install path | Task B1 Step 1 explicitly tests `comfy install` on M4 with Docker fallback | Blocking |
| 8 | **ComfyUI port + bind address** are explicit (`127.0.0.1:8188`) | Plan mentions `127.0.0.1:8188` | Blocking |
| 9 | **Ollama port + bind address** are explicit (`127.0.0.1:11434`) | Plan mentions `127.0.0.1:11434` | Blocking |
| 10 | **File-path handoff convention** is standardized on `~/.hermes/avatar/cache/` | Plan defines the cache dir + naming convention | Blocking |
| 11 | **WhatsApp ToS risk** is surfaced and consent is gated before any WhatsApp step | Task C3 Step 4 has a "ToS consent gate" before running `hermes whatsapp` | Blocking |
| 12 | **Daily cost cap** is in the config with an alert at 80% | `routing.cloud_daily_usd_cap` and `log_cloud_calls` in the YAML | Blocking |
| 13 | **Verification gate includes routing stats** (`hermes stats --routing` showing ≥ 90% local) | Verification gate includes routing percentage check | Blocking |
| 14 | **No Kanban card can start without its parent's verification command printed** | Card metadata convention includes `verification: <exact commands>` | Process |
| 15 | **E6 runs the D3 failure-mode tests AND the new "cloud call cap" test** | E6 acceptance criterion includes both | Process |
| 16 | **Plan file size** is ≤ 40 KB after 3 iterations (signals over-engineering if larger) | `wc -c plan.md` ≤ 40,960 bytes | Sanity |
| 17 | **Open Questions section is empty** OR each has a default answer | Section has ≤ 5 items, each with a "default:" line | Sanity |

If any "Blocking" row is ✗ after iteration 3, the plan is NOT READY. Loop back to iteration 1 with the failing rows as the new gate.

---

## Verification Gate (whole plan)

Before declaring done, all of these must pass:

1. `hermes doctor` — clean, no warnings.
2. `hermes gateway status` — Slack, Telegram, WhatsApp all `connected`.
3. `hermes chat -q "introduce yourself"` — response matches persona tagline, in persona voice, addressing user by chosen form.
4. `hermes chat -q "what time is it?"` — response within 2 s, no cloud egress in network tab.
5. Send a voice note to each configured platform — voice reply (or text if `/voice off`).
6. `hermes stats --routing` — ≥ 90% local replies in last 24 h.
7. `hermes chat -q "use avatar-face to render '...' with portrait at ... to /tmp/x.mp4"` — MP4 exists, plays, audio matches.
8. `~/.hermes/logs/gateway.log` — no `ERROR` or `Traceback` lines in the last 24 hours of normal use.
9. Stopwatch: voice-in → voice-out round trip p50 ≤ 5 s, p95 ≤ 8 s, over 20 trials.
10. All 6 failure-mode tests from D3 pass.
11. All 17 "READY" criteria above ✓.

---

## Pinned Versions

| Component | Version | Source |
|---|---|---|
| Hermes | 0.16.0 | (already installed) |
| Ollama | 0.30.x | `brew upgrade ollama` |
| `mistral-small:24b-instruct-2503-q4_K_M` | pinned at first pull | `ollama list` |
| `llama3.1:8b-instruct-q6_K` | pinned at first pull | `ollama list` |
| Piper TTS | 2023.11.x | `brew install piper` |
| `kokoro-onnx` | 0.9.x | `pipx install kokoro-onnx` |
| `faster-whisper` | 1.0.x | `.venv/bin/pip install faster-whisper` |
| ComfyUI | 0.4.x | `pipx install comfy-cli && comfy install` |
| `kijai/ComfyUI-LivePortraitKJ` | pin to commit SHA at install time | `git rev-parse HEAD` after clone |
| `@whiskeysockets/baileys` | latest at install time | `npm list -g` |

Drift is the #1 cause of multi-day debugging on local-first stacks. Pin and check before every upgrade.

---

## Risks and Tradeoffs

- **Local LLM quality ceiling.** `mistral-small:24b Q4_K_M` is not GPT-4 class. For non-trivial reasoning, the cloud tier is opt-in (gated by user request, $5/day cap, every call logged). Mitigation: the routing strategy makes the tier choice explicit per message.
- **Wispr Flow not integrated.** Acknowledged: it's a system dictation app with no programmatic API. Used only for the operator's own interactive dictation.
- **Kokoro voice naturalness.** Kokoro is significantly more natural than Piper but not ElevenLabs class. If the user wants cloning: `F5-TTS` is the local-first clone path (PyTorch + 1.2 GB, MPS-compatible). Mitigation: the `tts.provider` field in config is swappable; no code change.
- **LivePortrait quality on M4.** Local face animation looks uncanny on some portraits. Mitigation: pick a high-quality, well-lit, front-facing source photo; if quality is unacceptable, swap to MuseTalk (realtime, simpler, lower quality) or omit the face entirely (`/face off`).
- **WhatsApp account-banning risk.** Baileys reverse-engineered protocol can get personal accounts banned (~1–3% within 90 days per community reports). Mitigation: secondary SIM + 30-day clock to Cloud API + cron-based re-pair.
- **Meta Business verification overhead.** 1–14 days for Meta to verify; requires a real business entity. Not a quota, but a real cost (filing fees, registered agent, or sole-proprietor EIN).
- **ComfyUI arm64 status.** `comfy install` may fail on M4 / macOS 26.5. Mitigation: Docker fallback documented in B1 Step 1; MuseTalk fallback in B1.
- **RAM pressure with ComfyUI + Ollama + gateway.** LivePortrait spikes to ~3 GB; Ollama `mistral-small:24b` is 15.5 GB; gateway + macOS = ~4 GB. **Practical ceiling: 16K context max for chat replies.** `OLLAMA_KV_CACHE_TYPE=q4_0` to squeeze a bit more.
- **Memory fragmentation across profiles.** v1 uses one profile; if the user adds `engineer` etc. later, memories are separate. Documented as a follow-up.
- **Single-point-of-failure gateway.** If `hermes gateway` crashes, the avatar is unreachable. Mitigation: `hermes gateway install` runs it as a launchd agent that auto-restarts.

## Open Questions (each has a default the operator can confirm with a single keystroke)

1. **Avatar name and voice gender** — *default:* `Sage` (gender-neutral, calm/precise/warm voice). Edit SOUL.md to change.
2. **Portrait source** — *default:* operator takes a self-portrait and saves to `~/portraits/avatar.png` before Phase E starts. If absent, B1 Step 6 smoke test uses a stock portrait.
3. **Slack workspace** — *default:* operator creates a Slack app at api.slack.com with the listed scopes, pastes the `xoxb-...` token into the `hermes gateway setup` wizard.
4. **WhatsApp consent** — *default:* operator explicitly acknowledges the ToS risk in writing before running `hermes whatsapp`. If declined, the WhatsApp card is skipped; gateway connects Slack + Telegram only.
5. **Cron job for memory review** — *default:* no cron in v1. Document as a follow-up.
6. **Profile creation** — *default:* operator runs `hermes profile create engineer/researcher/reviewer` to enable parallel E0–E4 dispatch. If declined, all cards run serially on `default`.

---

## Summary of v1 → v2 changes (delta from the first review iteration)

| Section | v1 | v2 | Why |
|---|---|---|---|
| Step 0 | Asked for profile list | Added kanban-skill pre-flight + "single-profile caveat" | R2 found the kanban skill is not installed; parallelism requires 3+ profiles |
| Phase A | Started with persona file | **New Task A0** repoints LLM to local Ollama | R1 found the active default is `minimax-m3:cloud` (paid), which violates "local-first" |
| Task A1 | Created `~/.hermes/personas/avatar.md` and invented `persona:` config | Uses `~/.hermes/SOUL.md` (already loaded by `prompt_builder.py`) | R2 found the `persona:` key is not in the schema; file is never read |
| Task A2 | Piper only | **Adds Kokoro as production default; Piper as smoke test fallback** | R3 found Piper is robotic; Kokoro is 2026-best local quality/footprint |
| Task A3 | Install faster-whisper | **"Verify"** (already wired) + correct smoke test command | R1 found STT is already configured |
| Task A4 | n/a | **NEW: Wispr Flow out-of-scope note** | R3 found Wispr keeps getting re-proposed; one-liner exclusion |
| Phase B | B1 + B2 split | **Single atomic Task B1** (ComfyUI + LivePortrait + workflow + driver) | R2 found the split invites partial state |
| Task C1 | New `avatar-tts` skill | **REMOVED — use built-in `text_to_speech` tool** | R2 found this duplicates the built-in TTS tool with 10 providers |
| Task C2 | New `avatar-face` skill | **Kept** (thinner wrapper) + explicit on-demand-only constraint | R2 confirmed no native face tool; the on-demand guard prevents 37 min/day of CPU |
| Task C3 | `hermes gateway setup --platform whatsapp` | **`hermes whatsapp` (subcommand) + ToS consent gate + secondary SIM + Cloud API 30-day clock** | R2 found the flag doesn't exist; R1 quantified the ban risk |
| Task C4 | Invented 4 config keys (`gateway.persona`, `gateway.voice_reply`, etc.) | **Uses existing `_voice_mode` state + per-chat `/voice` commands; new `_face_mode` state mirrors it** | R2 confirmed none of the invented keys exist in the schema |
| Phase D2 | "under 8 s" | **p50 ≤ 5 s, p95 ≤ 8 s, over 20 trials** | R3 found "under 8s" is too loose; percentiles catch tail latency |
| Phase D3 | 5 test cases | **6 cases** (added voice-in race condition) | R2 found the existing session manager handles it but the test should prove it |
| Phase E | 6 cards (E1–E6) | **7 cards** (E0 added for atomic ComfyUI install) + explicit pre-flight check | R2 found B1+B2 split was the wrong decomposition |
| Open Questions | 5 items, no defaults | **6 items, each with a default answer** | R3 found the open questions should have one-keystroke defaults |
| New in v2 | — | **Component data flow diagram, same-machine integration table, model routing strategy, testable "READY" criteria, pinned versions table, v1→v2 delta** | R1+R2+R3 collectively identified these as missing |

**Next iteration (R2 of the 3-iteration review loop):** 3 parallel reviewers re-read this v2 with v1's deltas applied. Synthesizer produces v3. Gate: zero new blocking items introduced; cost per task stable or down; no new ports/services added.
