# Personal Hermes Avatar (v2) — UX & Operability Review

> **Reviewer scope:** What v2 makes too hard, or what a non-expert operator would get stuck on. This is **additive** to the existing cost (R1), risk/feasibility (R2), and local-first (R3) critiques — it does not re-litigate the persona-config block, Kokoro swap, WhatsApp command, or invented gateway keys. Those have already been resolved in v2.
>
> **Reviewer premise:** the reader is a Hermes operator on macOS 26.5 / M4 / 24 GB who has never shipped a personal avatar. They are technical but not an ML/Ops engineer. They want the v2 plan to read like a recipe, not a research paper. They will skim it once, jump to "do these 5 things first," and then expect every later step to either (a) work, or (b) tell them exactly what to paste into chat to get unstuck.

**Methodology:** every scenario below is what a real user does *after* all 11 verification gates pass. The gates are about correctness; this review is about **the gap between "all gates green" and "I trust this thing on my phone."**

**Severity legend:** P0 = blocks deployment, P1 = bad UX, must fix before launch, P2 = nice-to-have, P3 = follow-up.

---

## Scenario 1 — Cold-start on first run

### What v2 says happens
v2 does not mention cold-start time at all. Verification Gate #4 requires `hermes chat -q "what time is it?"` to respond within 2 s, and Gate #5 requires "a message on any platform receives a reply within 5 seconds." Both of these implicitly assume a **warm** gateway (Ollama model loaded, faster-whisper loaded, Kokoro loaded, ComfyUI responsive). None of them measure the first run after `hermes gateway install` (cold) versus the 50th reply of the day (warm).

### What the user actually experiences
The user's first run looks like this:

1. They run `hermes gateway install` (C3 Step 1, line 612).
2. The launchd plist is written. The gateway is launched cold. `launchd` `KeepAlive=true` means it stays up — but **the model is not in Ollama's `loaded` set yet.**
3. The first message arrives (Telegram DM, voice note or text). Inside the gateway:
   - faster-whisper loads `base` from `~/.cache/huggingface/` (~150 MB, 1–3 s, first time only).
   - Ollama cold-loads `mistral-small:24b Q4_K_M` from RAM-resident weights **only if the prior chat already kept it warm**; if the gateway was freshly started, this is a full 8–15 s model load.
   - Kokoro first-call loads the ONNX voice + phonemizer + espeak-ng (~1–3 s).
   - The 24B model emits a reply (~3–5 s on M4).
   - Kokoro synthesizes (~0.6 s for a 5-second reply).
4. **Total first-reply latency: 13–26 seconds for text-only; 18–31 seconds for voice-in/voice-out; 60–90 seconds if face is enabled (ComfyUI cold-launches its model loaders and the LivePortrait assets).**

A new operator will see "the avatar is broken — it's frozen for 25 seconds" on the first try. They will conclude the plan doesn't work, even though it's working as designed.

The "first impression" framing in the task brief is correct. As designed, v2 ships a 30-second blank stare for the very first message.

### Severity: **P1** — bad UX, must fix before launch.

### Required v3 plan edits
- Add a new **Task A5: Warm-start preflight** between A0 and A1:
  - **Step 1:** After `ollama pull`, run a warmup: `ollama run mistral-small "ping" --verbose` and `ollama run llama3.1:8b "ping" --verbose`. Capture the load time and append to `~/.hermes/logs/cold-start.log`.
  - **Step 2:** Preload the Kokoro ONNX voice: `python -c "from kokoro_onnx import Kokoro; k=Kokoro('af_sarah'); k.create('warmup', voice='af_sarah', speed=1.0)"`. Discard the file. After this, the first real synthesis is ~0.6 s, not 3 s.
  - **Step 3:** If Phase B is being run, warm ComfyUI: `comfy launch --background && sleep 5 && curl -s http://127.0.0.1:8188/system_stats | jq '.system.comfyui_version' >/dev/null`. ComfyUI's cold-start is the single biggest contributor to face-mode latency.
  - **Step 4:** Set `OLLAMA_KEEP_ALIVE=30m` is already in A0 Step 2 — **good** — but add a `launchd` `KeepAlive` post-start script that does a single "ping" message to each model so the first real message is not a 24B cold load. Document this as the `gateway_prewarm.sh` script and reference it from the launchd plist `ProgramArguments` as a `--prewarm` flag.
- Add a new **Verification Gate #12 (cold-start budget):**
  - **Gate:** After `hermes gateway install` + prewarm, send one Telegram text message and one Telegram voice note. Record wall-clock time from "sent" to "received" on the operator's second device. **Text: ≤ 4 s p50, ≤ 8 s p95. Voice-in → voice-out: ≤ 6 s p50, ≤ 10 s p95.** If cold-start > warm, document the delta and ensure prewarm is in the launchd plist.
- Update the plan's claim at line 244 ("smoke test in < 4 s") to make explicit that the 4 s applies to **warm** model. Add a footnote: "First message of the day will be 8–15 s slower while Ollama re-loads; prewarm script mitigates this."

---

## Scenario 2 — Live-failure UX (something breaks during a chat)

### What v2 says happens
D3 (line 787) defines 6 failure-mode tests, all run in advance. D3 #1 is "Voice reply when Kokoro is down" — the operator kills the process, sends a voice note, and expects "graceful fallback to text + log entry." D3 #2 is "Face render when ComfyUI is down" — operator expects "voice-only reply, no crash." These are **synthetic** tests run by E6, with the operator holding the kill switch. They do **not** cover the failure shapes that happen in real life.

### What the user actually experiences
The user lives a different world than E6:

- **Scenario 2a — Ollama is being upgraded in the background.** User types a message on WhatsApp. The gateway's classifier calls `llama3.1:8b` and gets back HTTP 404 ("model not found"). The standard-tier router calls `mistral-small:24b` and gets the same. The user sees **a 30-second blank stare, then "Model 'mistral-small:24b' not found. Run `ollama pull mistral-small:24b` to download it."** That message comes from inside the agent, not from the gateway — the user has no idea what to do with it. They will ping the operator ("Hermes is broken") and the operator will spend 20 minutes figuring out the right fix. v2's `routing.cloud_enabled: false` rule means the avatar cannot fall back to OpenRouter either, so the user gets a hard failure, not a degraded one.
- **Scenario 2b — ComfyUI crashes mid-render.** User asks for a face video. The driver script `~/.hermes/avatar/render_talking_head.py` polls for 120 s and times out (line 489). v2 says "timeout → return error and let gateway fall back to voice-only." But the gateway sends a multipart message: text + voice + (missing) video. The user sees a text reply and a voice reply arrive at 8 s, then **no face video at all** — and the user has no way to know whether (a) the face mode is off, (b) ComfyUI is down, (c) the video is still rendering and will arrive in 90 s, or (d) the render failed. The gateway silently drops the failed video. From the user's perspective, the avatar "forgot to send the face video."
- **Scenario 2c — Kokoro runs out of disk space for the model cache.** Kokoro's voice model is ~300 MB in `~/.cache/huggingface/` or `~/Library/Caches/kokoro/`. If the user's boot SSD is full, Kokoro raises `OSError: [Errno 28] No space left on device`. The gateway catches the exception, logs to `~/.hermes/logs/gateway.log`, and replies with text only. The user sees: text reply, no voice. They will say "I asked for voice and didn't get any." Same UX as 2b, different cause.
- **Scenario 2d — WhatsApp bridge disconnects mid-conversation.** The Baileys bridge drops the socket (network blip, phone sleeps, etc.). The user types a message on WhatsApp; it sits in the queue. The user is on Slack and gets a reply to the same question (because Slack still works). When WhatsApp reconnects, the queued message is delivered late and the avatar replies. The user now has **two out-of-order replies from the same question, on two platforms.** v2 says nothing about per-platform queueing or cross-platform message ID correlation.

### Severity: **P1** — bad UX, must fix before launch. Scenarios 2a, 2b, 2c are the most common real-world failures and v2 has no plan for any of them.

### Required v3 plan edits
- Add a new **Task C5: Live-failure UX** in Phase C, after C4:
  - **Step 1 — Define a `status: degraded` envelope for the gateway.** When TTS or face fails, the gateway sends a single reply that includes (a) the user's text answer, (b) a one-line inline status note: "_Voice reply failed: Kokoro model not loaded. Try `ollama pull` and `/voice voice_only` again._" or "_Face video not available: ComfyUI returned 503. Reply `/face retry` to try once more._" The user knows what happened, and they have a recovery command. The status line is **omitted on success.**
  - **Step 2 — Add a `/status` slash command** to the gateway. Returns: `gateway: up (PID 1234, uptime 6h) | ollama: loaded (mistral-small:24b, llama3.1:8b) | kokoro: ok | comfyui: ok | whatsapp: connected (last ack 14s ago) | slack: connected | telegram: connected | voice_mode: voice_only (3 chats) | face_mode: on (1 chat)`. This is the **single most useful command** a non-expert operator will use. Make it the headline of the runbook.
  - **Step 3 — Add a `/repair` slash command** that runs a known sequence: `ollama list | grep mistral-small && kokoro_smoke_test && curl -s http://127.0.0.1:8188/system_stats | jq -e '.system.comfyui_version' && echo "all services healthy"`. Reports the first thing that failed. This is the **second most useful command.** It saves 20 minutes of operator time every time something is broken.
  - **Step 4 — Define a `hermes gateway doctor --live` command** that hits all loopback services and reports status in one shot. Distinct from `hermes doctor` (which is a static config check). Update Verification Gate #1 (`hermes doctor`) to also include `hermes gateway doctor --live`.
  - **Step 5 — Add the 4 live-failure scenarios above as D3 test cases 7, 8, 9, 10:**
    - **7. Ollama 404** — rename the model in Ollama (`ollama cp mistral-small:24b Q4_K_M broken-temp`) and send a message. Expected: avatar returns text reply with `_Status: Ollama 404. Try /repair or `ollama pull mistral-small:24b-instruct-2503-q4_K_M`._`
    - **8. ComfyUI crash mid-render** — `kill -9` ComfyUI mid-job. Expected: text + voice reply at ~6 s, status line about face video, no zombie process, no half-written MP4 in `~/.hermes/avatar/cache/`.
    - **9. Kokoro disk full** — fill the disk to 0 bytes free, send a voice-out request. Expected: text reply with status line, no partial `.wav` file in the cache.
    - **10. WhatsApp bridge disconnect** — `kill -STOP` the bridge process, send 3 messages, then `kill -CONT`. Expected: 3 messages delivered in order, no cross-platform duplication, `/status` shows the disconnect window in the report.
  - **Step 6 — Define a `gateway.recovery` config block** in `~/.hermes/config.yaml`:
    ```yaml
    gateway:
      recovery:
        voice_failure: status_line   # or: "silent" (legacy)
        face_failure: status_line
        status_command: /status
        repair_command: /repair
    ```
    This is opt-out-able per the user's preference, but defaults to "always show the status line on degradation."

---

## Scenario 3 — The "first time" experience

### What v2 says happens
The v2 plan is 988 lines, 53 KB, 5 phases (A–E), 7 cards (E0–E6), 17 READY criteria, 6 open questions, 11 verification gates, 10 pinned-version rows, and ~120 numbered steps. There is no executive summary, no "do these 5 things first" block, and no quick-start that bypasses the structure.

### What the user actually experiences
A new operator opens the plan. They see the architecture diagram (encouraging!), the Goal statement (helpful!), and then 900+ lines of structure. They have to read line 165 ("Step 0: Profile Discovery") to find out that they need to create 3 profiles before they can even start. They have to read line 956 ("Open Questions") to find the defaults. They will not read it. They will skim, see the phrase "ComfyUI on arm64 macOS" and panic. They will close the file and ask "where do I start?"

The cost critique found this implicitly (it added "blocking" labels to certain tasks). The risk critique noted "Phase E is not ready to dispatch." But neither critique addressed **the operator's first 10 minutes with the plan.**

### Severity: **P1** — bad UX, must fix before launch. The plan is unreadable to its target user as it stands.

### Required v3 plan edits
- **Add a new top section called "Quickstart — do these 5 things first"** between the existing line 29 (the `---` after the constraint envelope) and line 32 ("Component Data Flow"). It should be 6 lines, max. Exact text:
  ```
  ## Quickstart — do these 5 things first

  Skip the rest of this plan on first read. Do these in order; each takes < 5 min:

  1. **Confirm your profile.** Run `hermes profile list`. If you have only `default`,
     run `hermes profile create engineer && hermes profile create researcher && hermes profile create reviewer`.
     This unblocks parallel card dispatch in Phase E. (5 min, optional but recommended.)
  2. **Repoint the LLM to local.** Edit `~/.hermes/config.yaml` per Task A0 Step 1.
     This makes every reply free. (2 min, BLOCKING.)
  3. **Install Kokoro TTS.** Per Task A2 Steps 1–5. This is the voice the avatar actually uses. (10 min, BLOCKING.)
  4. **Write your persona.** Per Task A1 Step 1. Save `~/.hermes/SOUL.md`. (15 min, BLOCKING.)
  5. **Connect one platform.** Pick the easiest — Telegram. Per Task C3 Step 3. (10 min, BLOCKING.)

  After these 5, the avatar can text-chat in persona. Voice + face come in Phase B + C2 + C4.
  See "Phases" below for the rest.

  **Plan file size: 53 KB / 988 lines / 5 phases / 7 cards / 17 criteria. The plan is
  structured for one operator, one machine, one avatar. Every "verification gate" is a
  one-line check; the full Verification Gate section at the end is the final test.**
  ```
- **Reformat the "Open Questions" section (line 956) to be visually scannable.** Each question should be on three lines: question, default, and "change if you want:" with a one-line override. Use a definition list, not a numbered list. Exact text:
  ```
  ## Defaults (confirm each with a keystroke, or override)

  1. **Avatar name and voice gender**
     Default: `Sage` (gender-neutral, calm/precise/warm)
     Override: edit `~/.hermes/SOUL.md` "Name" + "Voice/tone" lines.
  2. **Portrait source**
     Default: take a self-portrait, save to `~/portraits/avatar.png` before Phase E.
     Override: skip; B1 Step 6 smoke test uses a stock portrait.
  ...
  ```
- **Add a 1-paragraph "What this plan is NOT" callout** at the top, so the operator doesn't spend time looking for things outside scope. Exact text:
  ```
  **Out of scope (explicit non-goals):**
  - Voice cloning of the operator's own voice (F5-TTS path documented as a follow-up, not built).
  - Web UI / dashboard for the avatar (text + voice + video are the only I/O).
  - Group-chat behavior on any platform (per-chat voice/face mode is for 1:1 DMs).
  - Multi-language / multilingual replies (English-only Kokoro voice `af_sarah`; swap in Phase 3 if needed).
  - Whisper / STT upgrade beyond `base` (D2 Step 3 documents `small`/`medium` as a knob).
  - Cloud LLM as the default (local-first is the plan; cloud is opt-in, $5/day cap).
  ```

---

## Scenario 4 — The Kokoro / Piper swap

### What v2 says happens
A2 installs both Kokoro (production default) and Piper (smoke test). A2 Step 5 writes `tts.provider: kokoro` in `~/.hermes/config.yaml`. C4 says voice mode is per-chat via `/voice voice_only` and stored in `~/.hermes/gateway_voice_mode.json`. D3 #1 covers "Kokoro is down → graceful fallback to text."

Nowhere in v2 does the plan say **how to actually swap Kokoro ↔ Piper at runtime**. A non-expert reading v2 will not know:
- Is there a per-platform voice config? (Slack voice A, Telegram voice B?)
- Is there a per-chat voice config? (Only the on/off mode, per `/voice`. Not the engine.)
- Is it a global "Kokoro always" once A2 Step 5 is run? (Yes, per the YAML.)
- If Kokoro fails at runtime, does the gateway fall back to Piper automatically, or does the user have to edit config and restart? (D3 #1 says "graceful fallback to text" — **not** fallback to Piper. v2 silently demotes to text-only, which is a worse outcome than degraded-voice.)

### What the user actually experiences
A user with two different audiences — say, Slack for work (wants a calm Sarah voice) and Telegram for friends (wants a more playful voice) — has no mechanism in v2 to express this. The plan says "edit `tts.kokoro.voice` in config and restart the gateway" — but **the gateway has to be restarted**, which means a 30-second window where the avatar is unreachable. v2 does not document a "swap voice without restart" mechanism. (Hermes's `prompt_builder.py:load_soul_md()` reloads on SIGHUP — TTS voice config does not.)

When Kokoro fails at runtime, the user gets text-only. This is a worse outcome than a robotic Piper voice, because the user expected voice and got silence. They have no way to enable Piper as a fallback without editing YAML and restarting.

### Severity: **P1** — bad UX, must fix before launch. The plan explicitly compares Kokoro to Piper in 3 places (A2, Risks, D3) but never resolves how the operator chooses between them at runtime.

### Required v3 plan edits
- **In Task A2 Step 5 (line 339), replace the YAML block with one that supports runtime voice-per-platform and runtime engine-fallback:**
  ```yaml
  tts:
    provider: kokoro          # default engine
    engine_fallback_order: [kokoro, piper]   # tried in order on failure
    per_platform:
      slack:    { provider: kokoro, voice: af_sarah,   speed: 0.95 }
      telegram: { provider: kokoro, voice: af_bella,   speed: 1.0  }
      whatsapp: { provider: kokoro, voice: af_sarah,   speed: 1.0  }
      default:  { provider: kokoro, voice: af_sarah,   speed: 1.0  }
    per_chat_override: true    # users can do /voice voice:af_bella speed:1.1 in any chat
    kokoro:
      voice: af_sarah
      speed: 1.0
    piper:
      model: ~/.local/share/piper/voices/en_US-amy-low.onnx
      config: ~/.local/share/piper/voices/en_US-amy-low.onnx.json
  ```
- **Add a new slash command `/voice set <engine>:<voice>:<speed>`** to the gateway. Examples: `/voice set kokoro:af_bella:1.0`, `/voice set piper:en_US-amy-low`. Persists to `~/.hermes/gateway_voice_mode.json` (extend the existing schema) or a new `gateway_voice_config.json`. This is per-chat, with the per-platform defaults from YAML as the fallback. Document the command in the Quickstart runbook.
- **In D3 #1, change the expected behavior from "graceful fallback to text" to "graceful fallback to next engine in `engine_fallback_order`; if all engines fail, fall back to text."** Add a new test case 11:
  - **11. Engine-fallback chain** — kill Kokoro, send a voice-out request. Expected: avatar replies with Piper voice (`en_US-amy-low`), logs a `WARN: tts engine kokoro failed, falling back to piper` line in `~/.hermes/logs/gateway.log`, and sends a status line in the reply if `gateway.recovery.voice_failure: status_line` (per the Scenario 2 edit above). If Piper is also killed, expected: text-only with status line.
- **In the Risks section (line 947), add:** "Kokoro cold-start: 1–3 s on first call of the day after gateway restart. Mitigated by the A5 prewarm script (Scenario 1 edit) and the `engine_fallback_order` chain."
- **In the Pinned Versions table (line 924), add a row for "kokoro-onnx voice models" and pin the SHA256 of the downloaded voice file.** Voice model drift is a real risk — Kokoro has ~50 voices and the default (`af_sarah`) is updated periodically. Pin the model file with `shasum -a 256 ~/.cache/kokoro/voices/af_sarah.bin` recorded in `~/.hermes/avatar/pins.txt`.

---

## Scenario 5 — The face-render experience

### What v2 says happens
B1 Step 6 (line 492) requires a stock portrait for the smoke test. C2 is the agent-callable `avatar-face` skill. Open Question #2 (line 959) defaults to "operator takes a self-portrait and saves to `~/portraits/avatar.png` before Phase E starts." Risks section (line 948) says "LivePortrait quality on M4 is uncanny on some portraits. Mitigation: pick a high-quality, well-lit, front-facing source photo."

v2 does not provide:
- A way for the user to **take** the passport-style photo (the user might not know what "well-lit, front-facing" means in practice, or might not have ImageMagick / a webcam-capture script).
- A way to **validate** the photo before plugging it into ComfyUI (LivePortrait will silently produce a bad result; the user has no way to know if the photo is "good enough" until they run a 60-second render).
- A way to **test the audio without the face** (if LivePortrait produces an uncanny video, the user has to debug the ComfyUI workflow before they can tell whether the Kokoro audio was even correct).

### What the user actually experiences
The user takes a selfie with their iPhone, AirDrops it to the Mac, renames it `avatar.png`, and drops it in `~/portraits/`. They run the C2 smoke test:
```bash
hermes chat -q "use avatar-face to render 'Hello, I am your new assistant.' with portrait at ~/portraits/me.png to /tmp/hello.mp4"
```
The render takes 90 seconds (ComfyUI cold + LivePortrait + 25 fps encode). The output is an MP4 of their face mouthing "Hello, I am your new assistant." The mouth moves, but the eyes are dead, the head doesn't move, and the whole thing looks like a deepfake from 2019. The user has no idea whether:
- The face quality is limited by the input photo (it is — iPhone selfies with strong front-camera distortion look bad in LivePortrait).
- The face quality is limited by the LivePortrait model on M4 (it is — LivePortrait on MPS has reduced fidelity).
- The audio is actually correct (they can't tell, because the face is so distracting).
- The workflow has a bug (it might — a misconfigured VideoCombine node will produce a black frame, and the user has no way to distinguish "the workflow is wrong" from "LivePortrait on M4 just looks like this").

The user is now in a 30-minute debugging session with no log file, no diagnostic output, and no fallback path.

### Severity: **P1** — bad UX, must fix before launch. Face is the headline feature of the plan; the user will judge the avatar by the face.

### Required v3 plan edits
- **Add a new Task B2: Photo capture and validation script**, between B1 and Phase C:
  - **Step 1: Author `~/.hermes/avatar/capture_portrait.sh`** that uses macOS's built-in `imagesnap` (or `ffmpeg -f avfoundation -i "0" -frames:v 1`) to grab a single frame from the Mac's FaceTime camera, save to `~/portraits/avatar_<timestamp>.png`. Show a live preview using `ffplay` for 3 seconds before capture. The user can re-run until they get a shot they like. The script then runs a Python validation pass:
  - **Step 2: `~/.hermes/avatar/validate_portrait.py`** — checks:
    - Resolution ≥ 512×512 (LivePortrait's working size).
    - Single detected face (use `face_recognition` or a bundled OpenCV Haar cascade).
    - Face is roughly centered (face bounding box within 20% of the image center).
    - Face yaw/pitch/roll within ±15° (use `dlib` landmark detector or a heuristic).
    - Lighting: pixel histogram in the face region is not clipped (no fully black or fully white regions).
    - File size ≤ 5 MB.
  - **Step 3:** If validation fails, the script prints a specific, actionable error: e.g. "Face not centered. Move the camera or your head. Detected face at (x=120, y=340) in a 1920×1080 image. Expected within ±20% of (960, 540)." If validation passes, the script copies the file to `~/portraits/avatar.png` and prints "Portrait validated. Run `hermes chat -q 'use avatar-face to render ...' to test."
  - **Step 4: Add a one-liner install command** to the B2 task: `pipx install face-recognition dlib` (with the dlib install caveat — it's a heavy native dep). Or, simpler, bundle a smaller heuristic detector (the bundled OpenCV Haar cascade is ~5 MB and works without face_recognition).
- **Add a "test mode" to the `avatar-face` skill** in C2. New flag: `--no-face` (or env var `AVATAR_FACE_MODE=audio_only`). The wrapper script skips the ComfyUI render and just produces a `.wav` + a **static image + waveform overlay** MP4 (or just the `.wav` with a note). This is the **first thing the user should run** when the avatar is misbehaving — it isolates "is the audio correct?" from "is the face correct?". The C2 SKILL.md body should describe `render(text, portrait, output, mode='face'|'audio'|'both')`:
  - `mode='face'` (default): full pipeline, B1 driver script.
  - `mode='audio'`: just Kokoro, no ComfyUI, fastest debug.
  - `mode='both'`: face + a "test card" image overlay (e.g., the portrait with the audio waveform drawn on top via `ffmpeg -i input.mp4 -i waveform.png -filter_complex overlay`). The user can see both at once.
- **Update B1 Step 6 verification (line 503)** to include a **second** sub-test: `mode='audio'` succeeds in < 5 s. This proves the audio is right before the user invests 90 s in a face render.
- **Add a "Face mode is on by default" gate to the Quickstart:** "Phase E is not complete until you can `/face on` in any chat and get a 5-second face video in < 90 s. If the face is uncanny, `/face off` and the avatar falls back to text + voice. Don't debug the face in production — debug it in the smoke test."

---

## Scenario 6 — Multi-platform consistency

### What v2 says happens
C4 (line 669) describes per-chat voice mode via `/voice voice_only` and the `~/.hermes/gateway_voice_mode.json` state file. C4 Step 2 adds per-chat face mode via `/face on`. v2 does not document a per-platform default for voice or face. There is no `per_platform.voice_mode: voice_only` or similar in the config.

### What the user actually experiences
The user wants different behavior per platform:
- **Slack** is for work. Voice notes are awkward in a work context. The user wants **always-text on Slack**.
- **Telegram** is for personal. Voice feels natural. The user wants **voice-in → voice-out, no face**.
- **WhatsApp** is for family. The user wants **voice-in → voice + face** (face feels personal).

In v2, the user has to:
1. Open Slack DM. Type `/voice off`. (Persists for this chat, fine.)
2. Open Telegram DM. Type `/voice voice_only`.
3. Open WhatsApp DM. Type `/voice voice_only` and `/face on`.

But:
- v2 doesn't say whether `/voice` and `/face` are per-chat or per-platform. The state file `gateway_voice_mode.json` is keyed by `<platform>:<chat_id>` (line 690), so it's per-chat. **That means every new chat on Telegram starts in `/voice off`** — the user has to re-enable voice mode in every new group DM or new 1:1 chat. This is the "configuration drift" problem.
- A user who adds a new platform after launch (say, Discord) has to remember to run `/voice voice_only` in every Discord channel. There's no "set the default for this platform" mechanism.
- v2 has no documented command for "make voice the default on Telegram." A user discovering this gap will assume v2 is incomplete.

### Severity: **P1** — bad UX, must fix before launch. The per-chat primitive is the right design, but the user needs a per-platform default to avoid re-configuring every new chat.

### Required v3 plan edits
- **In Task C4 Step 1 (line 677), add a new slash command `/voice default <mode> <platform>`** with the example invocation:
  - `/voice default voice_only telegram` — sets the default for all future Telegram chats (existing chats keep their per-chat override).
  - `/voice default off slack` — Slack defaults to text-only.
  - `/voice default all whatsapp` — WhatsApp always sends text + voice.
  - `/voice default show` — prints the current per-platform defaults.
  - The defaults persist to `~/.hermes/gateway_voice_defaults.json` (new file). Schema:
    ```json
    {
      "slack":    "off",
      "telegram": "voice_only",
      "whatsapp": "all",
      "default":  "off"
    }
    ```
- **Mirror the same mechanism for face mode:** `/face default <mode> <platform>`, `~/.hermes/gateway_face_defaults.json`. The `face` slash command should distinguish "I am turning on face for this chat" (`/face on` in chat) from "I am setting the platform default" (`/face default on telegram`).
- **Add a verification gate to C4 Step 3 (line 702):** "Send a brand-new 1:1 message on Telegram from a new chat (i.e., a contact the avatar has not spoken to before). Verify the platform default is applied automatically, not the global `off` default." This proves the per-platform default works for new chats, not just for chats the user has manually configured.
- **Add a "first run" gate to the gateway install:** on `hermes gateway install`, the gateway should prompt the operator once: "Set per-platform voice defaults? [s]lack, [t]elegram, [w]hatsapp, [d]one." If the operator skips, the defaults are all `off`. If they configure, the defaults are persisted and applied to every new chat from that platform.

---

## Scenario 7 — Memory hygiene

### What v2 says happens
D1 (line 714) verifies memory is wired and seeds 3 example facts. D1 Step 5 (line 750) explicitly says "Memory fragmentation note (out of scope for v1) — Document as a follow-up; do not engineer a consolidation cron in v1." Open Question #5 (line 962) says "default: no cron in v1. Document as a follow-up."

v2 has:
- A `remember:` syntax for seeding facts (D1 Step 2, line 730).
- A `/new` command to start a fresh session and prove cross-session recall works (D1 Step 3, line 738).
- Cross-platform recall test (D1 Step 4, line 744).
- **No** `/forget <id>` or `/forget <pattern>` command.
- **No** way to inspect `~/.hermes/memories/MEMORY.md` from inside a chat.
- **No** way to mark a memory as stale or low-confidence.
- **No** automated pruning.
- **No** documented best-practice guide for what kinds of facts to remember.

### What the user actually experiences
The user starts chatting. They `/new` a few times. They use `remember: I am working on project X` and `remember: I prefer short replies`. Six months later, they have 200 memories in `~/.hermes/MEMORY.md`, half of which are stale (the project is over, the user's preference changed, the fact was wrong). The avatar keeps recalling these stale memories and producing weird answers. The user has no way to prune.

When the user types `remember: my wife's name is Sarah`, the system stores it — but how does the user know it's stored? How do they know if it conflicts with a previous `remember: my wife's name is Sarah-Ann`? v2 has no visibility.

A user trying to debug "why did the avatar say that?" has to:
- Know that memories are at `~/.hermes/MEMORY.md`.
- Open the file in a text editor.
- Read through hundreds of lines.
- Manually delete the stale ones (no syntax for "delete entry #N" or "delete all entries matching 'project X'").
- Re-trigger the avatar to see the new behavior.

This is operator-hostile. A non-expert will not do it.

### Severity: **P2** — nice-to-have at launch, but **P1** within 30 days of usage. The plan explicitly defers memory hygiene to a follow-up; that's fine, but the launch should at least have the inspect-and-forget primitive.

### Required v3 plan edits
- **Add a new Task D4: Memory hygiene primitives**, after D3:
  - **Step 1: `/memories` slash command** in the gateway. Returns a numbered list of the 20 most recent memories, with date and source (e.g., "1. [2026-06-15, telegram] I am working on project X"). Pagination: `/memories 21-40`.
  - **Step 2: `/forget <id>` slash command.** Removes the memory at that id. The user runs `/memories` first to find the id, then `/forget 7` to remove it. (Don't use semantic search; ids are predictable and the user is the source of truth.)
  - **Step 3: `/forget all <pattern>` slash command.** Removes all memories whose text matches the pattern (case-insensitive substring). Confirmation prompt: "Delete 12 memories matching 'project X'? [y/N]". This is the bulk-cleanup tool.
  - **Step 4: `/memory show <id>` slash command.** Shows the full text of memory #N, plus the chat it came from, plus the date.
  - **Step 5: `hermes memories` CLI command** (companion to the in-chat slash command). Same functionality for terminal users. Useful when the user wants to script a cleanup.
- **Add a `~/.hermes/MEMORY.md` "best practices" comment block** at the top of the file, generated automatically on first `remember:`. One-liner each: "DO remember stable facts (names, projects, preferences). DO remember things you'd be annoyed to repeat. DON'T remember ephemeral state (current task, today's weather). DON'T remember things you'd be embarrassed to leak — the file is plaintext on disk."
- **Update Open Question #5 (line 962)** to read: "Default: no monthly cron for memory review in v1; ship the `/memories` and `/forget` primitives instead, document the cron as a Phase 3 follow-up. The cron is 5 lines; the user-facing primitive is 50 lines and is the more important deliverable."
- **Add a D1 verification gate:** after seeding 3 facts, the user runs `/memories` and confirms the 3 facts appear with ids 1, 2, 3. Then `/forget 2` and confirms only 1, 3 remain. This proves the prune primitive works.

---

## Scenario 8 — The "what now" gap

### What v2 says happens
The 11 verification gates (line 908) cover "is the system working?" but not "what does the user do with it on a Tuesday morning?" The Verification Gate is the end of the plan — there is no runbook, no day-in-the-life, no "here's how to use what you built" section.

### What the user actually experiences
After all 11 gates pass, the user has:
- A gateway that runs as a launchd agent.
- A persona in `~/.hermes/SOUL.md`.
- Three platforms connected.
- TTS, STT, face all working.

And the user thinks: "OK, now what?" They have a vague idea they can send a WhatsApp message and the avatar will reply, but they don't know:
- The 6 slash commands (`/voice off`, `/voice voice_only`, `/voice all`, `/face on`, `/face off`, and the new ones from Scenarios 2, 4, 6, 7 above).
- The 3 CLI commands (`hermes doctor`, `hermes gateway status`, `hermes stats --routing`).
- The expected latency budget (5 s text, 8 s voice, 60–90 s face).
- The recovery commands when something is broken (`/repair`, `hermes gateway doctor --live`, `hermes whatsapp` re-pair).
- The fact that they can run `hermes chat -q "..."` from the terminal to test without going through a platform.

The user will discover these by accident over the course of a month. In the meantime, they will form wrong mental models ("the avatar only does voice on Telegram — why isn't it replying on WhatsApp with voice?" — answer: they haven't run `/voice voice_only` in that chat yet).

### Severity: **P1** — bad UX, must fix before launch. A working system without a runbook is half a system.

### Required v3 plan edits
- **Add a new section "Day in the life"** at the end of the plan, after the Verification Gate, before the Pinned Versions table. It should be a 1-page Markdown document covering:
  - **A normal Tuesday morning:** the user sends 3 WhatsApp voice notes, 5 Telegram texts, 1 Slack DM. The avatar replies within the latency budget on all 3 platforms. Memory of yesterday's "remember: I finished the report" is recalled when the user asks "what did I finish yesterday?"
  - **The 6 most useful slash commands, with copy-pasteable examples:** `/voice voice_only` (turn on voice in this chat), `/face on` (turn on face in this chat), `/status` (show all-service health), `/repair` (run the recovery sequence), `/memories` (list stored facts), `/forget 7` (delete fact #7).
  - **The 3 most useful CLI commands:** `hermes doctor` (config check), `hermes gateway doctor --live` (live service check), `hermes stats --routing` (local-vs-cloud ratio).
  - **The 5 most common failure modes and their fix:**
    1. "Avatar doesn't reply" → run `/status`. First red line is the cause.
    2. "Voice reply is robotic" → Kokoro failed; Piper fallback engaged. Check `~/.hermes/logs/gateway.log` for `WARN: tts engine kokoro failed`.
    3. "Face video is missing" → ComfyUI is down or `/face off` is set. Run `/face on` and re-send.
    4. "Avatar doesn't remember me" → `~/.hermes/MEMORY.md` was deleted. Re-seed with `remember: I am your operator`.
    5. "Avatar replies in the wrong voice" → `/voice set kokoro:af_sarah:1.0` to reset.
  - **The 1 monthly maintenance task:** run `hermes memories` and prune stale facts with `/forget`.
  - **The "ask for help" command:** `/help` returns the same content as the runbook, in-chat. This is the "man page" entry point.
- **Add a runbook file `~/.hermes/avatar/RUNBOOK.md`** with the same content as the in-plan "Day in the life" section. Generated by `hermes gateway install` on first run. Symlinked or copied from the plan file so it stays in sync if the plan is updated.
- **Add a `/help` slash command to the gateway** that returns the runbook in-chat. Implementation: load `~/.hermes/avatar/RUNBOOK.md` and send its contents as a single text reply. If the file doesn't exist, return a one-liner pointing to the plan.
- **Add a Verification Gate #13 (runbook present):** "`~/.hermes/avatar/RUNBOOK.md` exists, is non-empty, and `/help` in any chat returns its content within 1 s."

---

## Cross-cutting observations

These are smaller items, but they compound into "v2 is hard to operate." Each is a one-liner fix.

### X1 — The verification gates are not in the right order

The Verification Gate section (line 906) has 11 items, but they are ordered by topic, not by execution time. A new operator reading them top-to-bottom will hit Gate #11 ("All 17 READY criteria ✓") before they've even started. Reorder to execution order: install → model → persona → STT → TTS → face → gateway → platform → memory → stats → all-criteria.

**Edit:** Reorder the 11 gates to match Phase A → E. Add a one-line "(in execution order)" header.

### X2 — Pinned versions has 10 rows, but no SHA for the Ollama models

Line 930–931 says "pinned at first pull" — that's not a pin. A real pin is the SHA256 of the GGUF file in Ollama's blob store (`ollama show mistral-small:24b --modelfile` shows the digest; `ollama list` shows the size). If Ollama's model registry is updated upstream and the user runs `ollama pull` again, they get a new model silently.

**Edit:** Add to Pinned Versions: "Ollama model digests — capture with `ollama show mistral-small:24b-instruct-2503-q4_K_M | grep digest` and store in `~/.hermes/avatar/pins.txt`. Re-check on every `ollama pull`."

### X3 — No log-rotation policy for `~/.hermes/logs/`

v2 references 3 log files: `gateway.log` (line 123), `cloud-routing.log` (line 154), and implicitly `cold-start.log` (from the Scenario 1 edit). None of them have a rotation policy. On a long-running gateway, `gateway.log` will grow to GB over months, eventually filling the user's SSD (which is the failure mode of Scenario 2c — Kokoro cache eviction triggered by full disk).

**Edit:** Add a Task A6 (or a paragraph in the launchd plist description): "Logrotate via `newsyslog` or `logrotate` with a 100 MB cap, keep 5 rotated files. The plist runs the gateway as a launchd agent — add a `StandardOutPath` and `StandardErrorPath` config and a `~/Library/LaunchAgents/com.hermes.gateway.logrotate.plist` that runs `logrotate` daily. Or simpler: pipe stdout/stderr through `rotatelogs` in the plist's `ProgramArguments`."

### X4 — No "test chat" command in the Quickstart

A new operator wants to verify the avatar is working *without* setting up a platform. The Quickstart jumps from "connect Telegram" to "expect a reply on Telegram." There's no in-between "send a test message to the gateway directly" step.

**Edit:** Add to the Quickstart: "0. **Sanity-check the local model** — run `hermes chat -q "introduce yourself"`. This bypasses all platforms and proves the gateway + model + persona are wired. (30 s, BLOCKING.)" This is also the recovery command when the user thinks "the avatar is broken on WhatsApp" but doesn't know if the local stack is healthy at all.

### X5 — The plan uses `~/comfy/` but doesn't say what to do if it doesn't exist

B1 Step 1 (line 443) uses `comfy launch --background`, which assumes `~/comfy/` exists. v2 does not say what to do on a clean install. The user will run `comfy launch`, get `comfy: command not found`, and have to read the ComfyUI docs to find that `pipx install comfy-cli && comfy install` is the install path. (This is in B1 Step 1 but buried in the verification gate block.)

**Edit:** Move the `pipx install comfy-cli && comfy install` to a Step 0.5 in Phase B, **before** Step 1. The verification gate should be "ComfyUI installs and `comfy --version` works," not "ComfyUI runs and we can curl 8188."

---

## Summary — severity-ranked edit list

For the parent agent's convenience, here is the full edit list, ranked by severity and grouped by the v2 section each one touches.

**P0 (blocks deployment):** None. v2 is structurally shippable; the gaps are UX.

**P1 (must fix before launch):**
- Scenario 1: Add Task A5 (warm-start preflight) and Verification Gate #12 (cold-start budget).
- Scenario 2: Add Task C5 (live-failure UX, `/status` + `/repair` + status-line envelope) and D3 test cases 7–10.
- Scenario 3: Add "Quickstart — do these 5 things first" at the top; reformat "Open Questions" as "Defaults"; add "What this plan is NOT" callout.
- Scenario 4: Add runtime voice-per-platform and engine-fallback chain (`engine_fallback_order`, `/voice set ...`); change D3 #1 to expect Piper fallback, not text-only; pin Kokoro voice SHA256.
- Scenario 5: Add Task B2 (photo capture + validation script); add `mode='audio'|'face'|'both'` to `avatar-face` skill; update B1 Step 6 verification.
- Scenario 6: Add `/voice default <mode> <platform>` and `/face default ...`; persist to `gateway_voice_defaults.json` / `gateway_face_defaults.json`; add first-run prompt to `hermes gateway install`.
- Scenario 7: Add Task D4 (memory hygiene: `/memories`, `/forget <id>`, `/forget all <pattern>`, `/memory show`).
- Scenario 8: Add "Day in the life" runbook section + `RUNBOOK.md` + `/help` command + Verification Gate #13.

**P2 (nice-to-have):**
- Scenario 7 (memory): monthly cron is the explicit follow-up; the primitives above are the must-fix.
- X1: Reorder the 11 verification gates to execution order.
- X2: Pin Ollama model digests (SHA256), not just "pinned at first pull."
- X3: Add log-rotation policy to the launchd plist.
- X4: Add `hermes chat -q "introduce yourself"` as Quickstart step 0.
- X5: Move `pipx install comfy-cli && comfy install` to a B1 Step 0.5 (before the verify step).

**P3 (follow-up):**
- The monthly memory-review cron (already deferred in v2; keep deferred, but the primitives above must exist).
- A "Discord adapter" or other future platforms (out of scope, but the per-platform default mechanism makes this trivial when it lands).
- A web UI for inspecting memories and platform status (out of scope, but the file-based state in `~/.hermes/avatar/` is ready for a future TUI/GUI to consume).

---

## What I did not find

For the parent agent's calibration:

- I did not find any **factual errors** in v2's technical claims. The Kokoro/Piper swap rationale, the WhatsApp ToS analysis, the model routing strategy, the integration table, the data flow diagram, the persona-config correction, and the 17 READY criteria all hold up.
- I did not find any **security issues** beyond the WhatsApp ToS risk already flagged in v2. The plan keeps all loopback services on `127.0.0.1` and never exposes them externally.
- I did not find any **cost surprises** beyond what's already in the cost critique. The new "scenario 4 engine fallback" doesn't add cost — Piper is free and already installed.
- I did not find any **gating issues** with the Phase E dispatch. The 7 cards are well-decomposed; the per-profile parallelism caveat is clearly documented; the verification gates are testable.

The findings above are **entirely about the operator's experience using v2**, not about whether v2 is correct. v2 is correct. v2 is just hard to use.
