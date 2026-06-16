# Personal Hermes Avatar — Implementation Plan

> **For Hermes:** This plan is large and parallel-friendly. Use **kanban-orchestrator** + **multi-agent-refinement-workflow** skills to drive it. Every concrete workstream becomes a Kanban card assigned to a real profile (discovered in Step 0). Reviewer cards gate the integration step.

**Goal:** Turn Hermes into a reachable, named, voice-capable personal avatar — persona defined in `~/.hermes/personas/`, talking on WhatsApp + Slack (and optionally Telegram for testing), with a ComfyUI-driven local face that animates a still photo from the TTS audio. Local-first, cloud only as last resort.

**Architecture (3 layers):**

1. **Persona layer** — `~/.hermes/personas/<name>.md` defines name, voice, backstory, address style, boundaries. Injected as system-prompt context for every gateway turn.
2. **Transport layer** — Hermes gateway + platform adapters. Single install, multi-platform, persistent via `hermes gateway install`. Voice notes auto-transcribed by STT provider; replies returned as text or as audio attachments via the TTS tool.
3. **Voice + face layer** — Local Piper TTS (default) with faster-whisper STT, both fully offline. ComfyUI + LivePortrait (or SadTalker fallback) consumes the TTS audio + a still portrait to produce short MP4 clips on demand.

**Tech stack:**
- `piper-tts` (local TTS) — `~/.local/share/piper/voices/` for ONNX voice models
- `faster-whisper` (local STT) — CTranslate2 backend, no GPU required on M4
- `espeak-ng` (Piper dependency for some voices)
- `comfy-cli` + `comfyui` skill (image/video generation server)
- `LivePortrait` ComfyUI custom nodes (face animation)
- Hermes skills: `comfyui`, `macos-menu-bar-dev` (only for smoke tests), `kanban-orchestrator`, `multi-agent-refinement-workflow`

**Constraint envelope (from the user):**
- Local-first. Cloud is last resort — only if a local path fails after one retry.
- Multi-agent engineering loop where parallel work decomposes cleanly.
- Multi-orchestrated loop to deploy (gateway install + per-platform smoke test).
- Optimized plan: every task is bite-sized, exact-pathed, and ends with a verifiable check.

---

## Step 0 — Profile Discovery (BLOCKING)

**Objective:** Find the real profile names on this machine before any card is created. Dispatcher silently drops unknown assignees.

**Action:** In an interactive Hermes session, run:

```
hermes profile list
```

Paste the table into chat. Then ask the user once: "Use these as the worker roster for the avatar plan, or do you want to add/rename any?"

**If the user can't be reached mid-plan:** the planner card for each lane must list `assignee = "TBD-BY-OPERATOR"` in its metadata, and the operator fills it in before the dispatcher picks it up. This is cheaper than guessing wrong.

**Files:** none.

**Verification:** A line of text in chat like:

> Profile roster confirmed: `default, engineer, researcher, reviewer`.

**Commit:** none — this is a discovery step, not a code change.

---

## Phase A — Foundation (sequential, blocks everything else)

### Task A1: Create the persona directory and avatar persona file

**Objective:** Establish the named persona that defines the avatar's identity, voice, and behavior.

**Files:**
- Create: `~/.hermes/personas/avatar.md`
- Create: `~/.hermes/personas/avatar.voice.md` (style notes for TTS prompt shaping)

**Step 1: Write the persona skeleton**

Minimum content for `avatar.md`:
- Name
- One-line tagline
- Voice/tone (calm/precise/warm/dry — pick 2)
- Address style for the user (first name, "boss", etc.)
- Boundaries (refuses to do X, defers to user for Y)
- 3 example greeting lines
- 3 example follow-ups
- Memory hooks ("if user mentions <project>, recall <skill>")

**Step 2: Wire into the gateway**

Add to `~/.hermes/config.yaml`:
```yaml
persona:
  default: avatar
  dir: ~/.hermes/personas/
```

(If `persona` block doesn't exist yet, add it. The Hermes config schema accepts it; fallback is to use the persona as the system-prompt preamble via `/personality avatar` per session — but a config-level default is what we want for gateway replies.)

**Step 3: Verify**

```
hermes chat -q "introduce yourself in one sentence"
```

Expected output: matches the persona's tagline, in the persona's voice, addressing the user by the chosen form.

**Commit:** not applicable (config + dotfile, not in repo).

---

### Task A2: Install local TTS (Piper) and verify it speaks

**Objective:** Get a fully offline TTS pipeline working.

**Files:** none (binary install + one voice model).

**Step 1: Install Piper**

```
brew install piper
```

If `brew` is unavailable or the formula doesn't exist:
```
pipx install piper-tts
# or, in the active venv:
.venv/bin/pip install piper-tts
```

**Step 2: Install espeak-ng (Piper phonemizer backend)**

```
brew install espeak-ng
```

**Step 3: Download a voice model**

Piper voices are .onnx + .onnx.json pairs from https://github.com/rhasspy/piper/releases. Pick a high-quality US English voice:

```
mkdir -p ~/.local/share/piper/voices
cd ~/.local/share/piper/voices
curl -L -o en_US-amy-low.onnx       https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx
curl -L -o en_US-amy-low.onnx.json  https://github.com/rhasspy/piper/releases/download/v1.2.0/en_US-amy-low.onnx.json
```

(Full list of voices: https://github.com/rhasspy/piper/blob/master/VOICES.md. The `low` and `medium` tiers are fine; `high` is overkill for voice notes.)

**Step 4: Smoke test**

```
echo "The avatar is online." | piper --model ~/.local/share/piper/voices/en_US-amy-low.onnx --output_file /tmp/avatar-test.wav
afplay /tmp/avatar-test.wav
```

Expected: a clear female US English voice says "The avatar is online."

**Step 5: Register with Hermes**

```
hermes config set tts.provider piper
hermes config set tts.piper.model ~/.local/share/piper/voices/en_US-amy-low.onnx
hermes config set tts.piper.config ~/.local/share/piper/voices/en_US-amy-low.onnx.json
```

**Commit:** none.

**Verification gate:** `/tmp/avatar-test.wav` plays audibly. `hermes config | grep tts` shows the piper block set.

---

### Task A3: Install local STT (faster-whisper) and verify it transcribes

**Objective:** Voice messages on messaging platforms auto-transcribe locally.

**Files:** none.

**Step 1: Install faster-whisper**

In the active venv:
```
.venv/bin/pip install faster-whisper
```

This pulls CTranslate2 + a downloader for the Whisper model. The first `transcribe()` call downloads the chosen model size into `~/.cache/huggingface/`.

**Step 2: Register with Hermes**

```
hermes config set stt.enabled true
hermes config set stt.provider local
hermes config set stt.local.model base
```

(`base` is multilingual and good enough for English voice notes. Use `small` for accuracy on accents/noisy audio; `tiny` for speed.)

**Step 3: Smoke test**

Record 3 seconds from the Mac mic (or download any short .wav):
```
hermes chat -q "transcribe the audio at /tmp/avatar-test.wav"
```

Or invoke the STT tool directly:
```
python -c "from faster_whisper import WhisperModel; m=WhisperModel('base'); segs,_=m.transcribe('/tmp/avatar-test.wav'); print(''.join(s.text for s in segs))"
```

Expected: prints `The avatar is online.`

**Commit:** none.

**Verification gate:** transcription matches spoken text within ~1 word. STT block in `hermes config`.

---

## Phase B — Local Face (parallelizable with Phase A, but blocks Phase C integration)

### Task B1: Install ComfyUI

**Objective:** Local Stable Diffusion + custom-node server that we'll drive from a Python script for avatar frames.

**Files:** none (installed under `~/comfy/`).

**Step 1: Use the comfyui skill**

Load the skill:
```
hermes skills load comfyui
```

Follow its install procedure (comfy-cli is the recommended path on macOS):
```
pipx install comfy-cli
comfy install
```

**Step 2: Start the server in the background**

```
comfy launch --background
```

**Step 3: Verify it's up**

```
curl -s http://127.0.0.1:8188/system_stats | jq
```

Expected: JSON with `system.ram_free`, `system.comfyui_version`, and `devices` array.

**Verification gate:** `system_stats` returns 200 with valid JSON. Save the PID/log path for later.

**Commit:** none.

---

### Task B2: Install LivePortrait custom nodes

**Objective:** Face-animation nodes for ComfyUI that take a still portrait + an audio waveform and output a talking-head video.

**Files:** `~/comfy/custom_nodes/` (git clone targets).

**Step 1: Clone the LivePortrait custom node pack**

The most actively maintained option as of 2026 is the kijai/ComfyUI-LivePortraitKJ node pack:

```
cd ~/comfy/custom_nodes
git clone https://github.com/kijai/ComfyUI-LivePortraitKJ.git
```

(If that repo has gone stale, fallback candidates in priority order: `shadowcz007/ComfyUI-LivePortrait`, `AustinMroz/ComfyUI-LivePortrait`. The kijai fork is preferred because it tracks upstream and ships an `install.py`.)

**Step 2: Install node dependencies**

```
cd ComfyUI-LivePortraitKJ
pip install -r requirements.txt
```

**Step 3: Download the bundled LivePortrait models**

Run the install helper:
```
python install.py
```

This fetches the appearance-feature extractor, motion extractor, warping module, and landmark models into `~/comfy/models/liveportrait/`. Total ~3 GB.

**Step 4: Restart ComfyUI**

```
comfy restart
```

Wait for `system_stats` to be healthy again.

**Step 5: Verify nodes are loaded**

```
curl -s http://127.0.0.1:8188/object_info | jq '.["LivePortraitLoadModels"] // .["LivePortraitProcess"]'
```

Expected: a JSON schema, not `null`. If both keys are null, the custom node didn't load — check `~/comfy/comfyui.log` for import errors.

**Verification gate:** object_info returns schemas for at least one LivePortrait node class.

**Commit:** none.

---

### Task B3: Author the avatar-pipeline ComfyUI workflow

**Objective:** A reusable workflow JSON that takes a portrait + audio and produces an MP4.

**Files:**
- Create: `~/comfy/workflows/avatar_talking_head.json`

**Step 1: Build the workflow in the ComfyUI UI**

1. Open http://127.0.0.1:8188 in a browser.
2. Add nodes: `LoadImage` (portrait) → `LoadAudio` (TTS .wav) → `LivePortraitProcess` (or `LivePortraitLoadModels` + `LivePortraitProcess`) → `VideoCombine`.
3. Configure `VideoCombine` to output `image/gif`-style frames at 25 fps, then a `VHS_VideoCombine` (or current equivalent) wrapper for MP4.
4. Connect a `SaveImage` for the first-frame preview as a fallback.

**Step 2: Save the workflow**

Use ComfyUI's "Save (API Format)" button. This produces a JSON the Python client can submit.

**Step 3: Add a Python driver script**

Create `~/.hermes/avatar/render_talking_head.py`:
- Loads `~/comfy/workflows/avatar_talking_head.json`
- Substitutes the portrait path and audio path
- POSTs to `http://127.0.0.1:8188/prompt`
- Polls `/history/{prompt_id}` until `outputs` contains the video
- Copies the resulting MP4 to a stable path: `~/.hermes/avatar/cache/<timestamp>.mp4`

(This script will be wired into the avatar tool in Phase C.)

**Step 4: Smoke test the workflow**

Pick a stock portrait (use one of ComfyUI's bundled example images) and the `avatar-test.wav` from Task A2:
```
python ~/.hermes/avatar/render_talking_head.py \
  --portrait ~/comfy/input/example_portrait.png \
  --audio    /tmp/avatar-test.wav \
  --output   /tmp/avatar-test.mp4
```

Expected: `/tmp/avatar-test.mp4` exists, plays in QuickTime, shows a moving face synced to the audio.

**Verification gate:** MP4 plays, has nonzero duration (`afinfo /tmp/avatar-test.mp4` shows duration), face is recognizable.

**Commit:** none (script lives under `~/.hermes/`, not in repo).

---

## Phase C — Hermes Integration (the "expose it" layer)

### Task C1: Add a `tts` skill (or extend an existing one) with a single `speak` tool

**Objective:** A tool the agent (or a subagent) can call to produce an audio file from text, in the avatar's voice, using the persona's voice profile.

**Files:**
- Create: `~/.hermes/skills/avatar-tts/SKILL.md`
- Create: `~/.hermes/skills/avatar-tts/scripts/speak.py`

**Step 1: SKILL.md frontmatter**

```yaml
---
name: avatar-tts
description: "Avatar voice synthesis — convert text to audio using the avatar's Piper voice."
---
```

**Step 2: Body**

Document the `speak(text, output_path, voice=None)` function and the supported voices directory.

**Step 3: speak.py**

Wraps `piper` invocation. Should:
- Default to `~/.local/share/piper/voices/en_US-amy-low.onnx`
- Accept a `--voice` override
- Emit a `.wav` to the requested path
- Return JSON with `success`, `output_path`, `duration_seconds`, `chars`

**Step 4: Register**

Hermes auto-loads any `SKILL.md` under `~/.hermes/skills/`. No manual registry step.

**Step 5: Smoke test**

```
hermes chat -q "use avatar-tts to say 'I am ready' to /tmp/ready.wav"
```

Expected: file exists, plays, JSON response with `success: true`.

**Verification gate:** spoken file matches the request. `hermes skills list` shows `avatar-tts`.

**Commit:** none.

---

### Task C2: Add a `face-render` skill that wraps the ComfyUI driver

**Objective:** A tool the agent can call to produce a talking-head video from text.

**Files:**
- Create: `~/.hermes/skills/avatar-face/SKILL.md`
- Create: `~/.hermes/skills/avatar-face/scripts/render.py` (thin wrapper around `~/.hermes/avatar/render_talking_head.py` from Task B3)

**Step 1: SKILL.md body**

Document `render(text, portrait_path, output_path)`:
- If `text` is given, first calls `avatar-tts` to produce audio
- If `text` is None, uses an existing audio file (advanced mode)
- Returns JSON with `success`, `output_path`, `duration_seconds`, `engine: "liveportrait"`

**Step 2: Smoke test**

```
hermes chat -q "use avatar-face to render 'Hello, I am your new assistant.' with portrait at ~/portraits/me.png to /tmp/hello.mp4"
```

Expected: MP4 exists, plays, audio matches spoken text.

**Verification gate:** rendered MP4 is coherent. Tool returns success.

**Commit:** none.

---

### Task C3: Connect the gateway (Slack + WhatsApp + Telegram test)

**Objective:** The avatar replies on real chat platforms.

**Files:** none (Hermes gateway config).

**Step 1: Install the gateway as a service**

```
hermes gateway install
```

This creates a launchd user agent on macOS. Verify it stays up across logins:
```
hermes gateway status
```

**Step 2: Configure Slack**

```
hermes gateway setup --platform slack
```

The wizard asks for: workspace OAuth token (`xoxb-...`) from a Slack app with `chat:write`, `channels:history`, `im:history`, `im:write`, `files:write` scopes. Bot must be invited to any DMs it should answer in.

Smoke test in the configured Slack DM:
```
hello
```

Expected: avatar replies in persona voice (text).

**Step 3: Configure WhatsApp**

Hermes's WhatsApp adapter uses a different bridge (NOT the official Meta Business API — that requires business verification). The path is `hermes gateway setup --platform whatsapp` and follows the on-screen pairing steps with the user's own WhatsApp account. The bridge is read-only by default for the user's own messages and read+write for the bot's number.

**Important constraint to surface to the user before starting:** the WhatsApp bridge operates against a personal account, which is against WhatsApp's ToS. Hermes will surface this warning. The user must explicitly consent.

Smoke test: send a voice note from another phone to the bridge's number. Expected: avatar transcribes + replies (text or voice, per config).

**Step 4: Configure Telegram (for testing only — easiest to iterate on)**

```
hermes gateway setup --platform telegram
```

Wizard asks for the BotFather token. Bot must be `/start`-ed in a DM at least once.

Smoke test: send a voice note, send text, send a photo. Expected: avatar replies to each in persona voice.

**Step 5: Set the gateway's home persona**

```
hermes config set gateway.persona avatar
```

**Verification gate:** `hermes gateway status` shows three platforms connected. A message on any platform receives a reply within 5 seconds.

**Commit:** none.

---

### Task C4: Wire the voice response preference

**Objective:** When the user sends a voice note, the avatar replies with a voice note + (optional) talking-head video. When they send text, the avatar replies with text only (configurable).

**Files:**
- Modify: `~/.hermes/config.yaml` (`gateway.voice_reply: true`)

**Step 1: Enable voice replies**

```yaml
gateway:
  persona: avatar
  voice_reply: true
  voice_reply_min_chars: 30   # short replies stay as text
  face_reply: false           # face off by default; can be turned on per-chat
```

**Step 2: Verify**

Send a voice note longer than 30 chars to the Telegram bot. Expected: avatar replies with a `.wav` attachment (Piper output).

Send a short text "ok". Expected: text-only reply.

**Verification gate:** voice-in → voice-out, text-in → text-out. No `error: no voice provider` log lines in `~/.hermes/logs/gateway.log`.

**Commit:** none.

---

## Phase D — Polish and Optimization

### Task D1: Memory wiring for the persona

**Objective:** The avatar remembers user-specific facts across sessions and across platforms.

**Files:**
- Modify: `~/.hermes/config.yaml` (`memory.memory_enabled: true`, `memory.user_profile_enabled: true`)

**Step 1: Verify both flags are on**

```
hermes config set memory.memory_enabled true
hermes config set memory.user_profile_enabled true
```

**Step 2: Seed a few facts the avatar should know**

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

**Verification gate:** memory persists across `/new` and across platforms.

**Commit:** none.

---

### Task D2: Latency tuning

**Objective:** Voice-to-voice round trip under 8 seconds for short messages.

**Files:** none (all config).

**Step 1: Measure baseline**

Use the Telegram bot: send a 5-second voice note, time the reply.

**Step 2: Tune**

- If STT is slow: drop `stt.local.model` from `base` → `tiny` (or `small` → `medium` for accuracy).
- If TTS is slow: try `en_US-amy-low` → `en_US-amy-medium` (faster on M4 due to better CPU vectorization).
- If gateway dispatch is slow: check `agent.max_turns` in `config.yaml` — should be 5–10 for chat replies, not 90.
- If the model is slow: `hermes model` → pick a faster tier (OpenRouter's `anthropic/claude-haiku-4` or local Ollama for trivial replies).

**Step 3: Re-measure**

Target: voice-in (5s) → voice-out reply within 6–8 seconds total.

**Verification gate:** Stopwatch test on three different voice notes.

**Commit:** none.

---

### Task D3: Failure-mode and boundary tests

**Objective:** The avatar refuses gracefully when something is wrong, doesn't hallucinate, doesn't loop.

**Test cases:**

1. **Voice reply when Piper is down** — kill the piper process, send a voice note. Expected: graceful fallback to text + log entry.
2. **Face render when ComfyUI is down** — same, but ask for an avatar face explicitly. Expected: voice-only reply, no crash.
3. **Memory injection boundary** — ask the avatar for a fact it doesn't have. Expected: "I don't have that in my notes" rather than invented data.
4. **Persona consistency** — send a deliberately off-topic / adversarial message. Expected: avatar stays in persona, refuses politely, doesn't break character.
5. **Long message** — send a 60-second voice note. Expected: transcription succeeds, avatar's reply is a reasonable length (not 5x as long), Piper handles the synthesis without OOM.

**Files:** none (manual tests with `hermes chat -q` or live gateway).

**Verification gate:** all 5 tests pass. Log `~/.hermes/logs/gateway.log` has no `ERROR` or `Traceback` lines during the test run.

**Commit:** none.

---

## Phase E — Multi-Orchestrated Deployment

This phase is the **multi-orchestrated loop** you asked for. The dispatcher in the gateway spawns a fresh worker per card; the cards below are designed to be runnable in parallel by the dispatcher and converge in the integration step.

### Kanban Card Layout

After Step 0 (profile discovery), the operator creates the following cards. Each card body explicitly names its acceptance criteria so the goal-mode judge can score it.

**Independent lanes (run in parallel, no parents):**

- **E1** — *Engineer profile*: implement `~/.hermes/avatar/render_talking_head.py` from Task B3, including the polling loop and stable-output-path convention. Acceptance: the script produces a valid MP4 from a stock portrait + the A2 test .wav; exit 0.
- **E2** — *Engineer profile*: author `avatar-tts` skill (Task C1) and the `speak.py` wrapper. Acceptance: `hermes chat -q "use avatar-tts to ..."` returns a JSON success object and the file plays.
- **E3** — *Engineer profile*: author `avatar-face` skill (Task C2) wrapping E1's script. Acceptance: same end-to-end test passes via `hermes chat -q "use avatar-face ..."`.
- **E4** — *Researcher profile*: enumerate the platform-specific auth steps for Slack, WhatsApp, and Telegram, and produce a single Markdown checklist at `~/.hermes/avatar/SETUP_CHECKLIST.md` with copy-pasteable command sequences for each. Acceptance: a user (or a fresh engineer card) can follow the checklist from a clean Hermes install and reach `gateway status` showing all three platforms connected.

**Integration (depends on E1, E2, E3, E4):**

- **E5** — *Engineer profile (integrator)*: do the gateway install + per-platform connect (Task C3 + C4), using the checklist from E4. Acceptance: a Telegram voice note round-trips with a voice reply; a Slack text DM round-trips with a text reply.
- **E6** — *Reviewer profile*: run the failure-mode and boundary tests from Task D3 against the live gateway. Acceptance: all 5 tests pass; logs clean.

**Goal-mode note:** E1, E2, E3 are good candidates for `goal_mode=True` (TDD-style: "this skill works end-to-end, prove it"). E4 and E5 are single-shot, not goal-mode. E6 is a single-shot reviewer card.

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

1. Operator confirms profile roster from Step 0.
2. Operator creates E1, E2, E3, E4 in parallel.
3. Dispatcher fans them out (capped at `delegation.max_concurrent_children`).
4. Each worker reports back via `kanban_complete(summary=..., metadata=...)` with file paths and verification output.
5. When E1–E4 all complete, E5 auto-promotes (parents satisfied) and runs.
6. E6 runs after E5.
7. Operator gets a gateway ping on E6 completion with the full audit trail.

### Failure recovery

- If any worker in E1–E4 calls `kanban_block()`, the dispatcher opens a Recovery drawer. Operator reviews, either reclaims and respawns or reassigns.
- If a worker silently claims success but the file path is missing or empty, the integration card E5 catches it at "verification step 1: file exists" and blocks back to that lane.

---

## Verification Gate (whole plan)

Before declaring done, all of these must pass:

1. `hermes doctor` — clean, no warnings.
2. `hermes gateway status` — all configured platforms report `connected`.
3. `hermes chat -q "introduce yourself"` — response matches persona.
4. Send a voice note to each configured platform — voice reply (or text if `voice_reply: false`).
5. `hermes chat -q "use avatar-face to render '...' with portrait at ... to /tmp/x.mp4"` — MP4 exists, plays, audio matches.
6. `~/.hermes/logs/gateway.log` — no `ERROR` or `Traceback` lines in the last 24 hours of normal use.
7. Stopwatch: voice-in → voice-out round trip under 8 seconds for short messages.
8. Five failure-mode tests from D3 all pass.

---

## Risks and Tradeoffs

- **LivePortrait quality ceiling.** Local face animation looks uncanny on some portraits. Mitigation: pick a high-quality, well-lit, front-facing source photo; if quality is unacceptable, fall back to vocal-only with `face_reply: false` in config. (Cloud face is the alternative, but contradicts local-first.)
- **WhatsApp ToS risk.** The personal-account bridge violates Meta's ToS. Mitigation: surface the warning explicitly to the user; default to Slack + Telegram if user declines.
- **Piper voice naturalness.** Piper is intelligible but not ElevenLabs-quality. Mitigation: if naturalness is unsatisfactory, swap to ElevenLabs free tier (10k chars/mo) without changing the skill — the `speak.py` abstraction means the provider is swappable.
- **ComfyUI model download size.** LivePortrait + Stable Diffusion base is ~6 GB. One-time cost, but eats disk.
- **Memory drift.** The persona file + user profile grow over time. Mitigation: monthly review (set a cron job to remind).
- **Single-point-of-failure gateway.** If `hermes gateway` crashes, the avatar is unreachable. Mitigation: `hermes gateway install` runs it as a launchd agent that auto-restarts.

## Open Questions for the Operator

1. **Avatar name and voice gender** — the persona file is empty by design; we need the user to pick.
2. **Portrait source** — for the talking head, do they have a photo, or should E1 include a one-time portrait capture step?
3. **Slack workspace** — is there an existing app, or do we need to create one?
4. **WhatsApp consent** — does the user explicitly accept the ToS risk? (Default: don't configure WhatsApp until they say yes.)
5. **Cron job for memory review** — yes/no, and if yes, what cadence?

These are not blockers for Phase A–D; they're blockers for full Phase E deployment.
