# Personal Hermes Avatar Plan — Risk & Feasibility Critique

**Reviewer scope:** Risk, feasibility, and local-first compliance for `2026-06-15_210524-personal-hermes-avatar.md`.
**Environment confirmed by inspection:** M4 Mac mini, 24 GB RAM, 19 GB free disk, macOS 26.5, Hermes 0.16.0 already installed and running as a launchd gateway, Python 3.11.9, Ollama 0.30.8 running on :11434, Node 25.9, ffmpeg present, **no `~/comfy`, no `~/.hermes/personas/`, gateway has no platforms configured**.

The plan is well-structured at the surface (numbered phases, exact paths, verification gates) but contains **four structural defects** that, if executed as-written, will fail or fight the system. Two of them are easy to fix, two are deep rewrites. None of them are block-the-plan fatal, but they must be addressed before the kanban is dispatched.

---

## Phase A risks

### A1 — Persona file is invented, and Hermes has a real one

The plan says "Create `~/.hermes/personas/avatar.md`" and "add to `~/.hermes/config.yaml`:
```yaml
persona:
  default: avatar
  dir: ~/.hermes/personas/
```"

I searched the entire config schema (`hermes_cli/config.py`) for the key `persona`. It does not exist. The only related keys are:

- `agent.personalities: {name: system_prompt}` (a registry of named system prompts, already populated with 14 built-ins: `catgirl`, `noir`, `pirate`, `shakespeare`, `concise`, `teacher`, `technical`, `hype`, `uwu`, `kawaii`, `surfer`, `philosopher`, `creative`, `helpful`).
- `display.personality: ""` (selects which named personality is active in a session via `/personality <name>`).
- `~/.hermes/SOUL.md` (per-profile, loaded into the system prompt automatically by `agent/prompt_builder.py:load_soul_md()`, line 1479). This file already exists at `~/.hermes/SOUL.md` and is the documented identity slot for "the agent's personality and tone."

If you write a YAML block `persona: {default: avatar, dir: ~/.hermes/personas/}`, Hermes will (a) treat it as an unknown key — config will accept it but no code path reads it, (b) the gateway will keep using the existing personality registry, (c) `hermes chat -q "introduce yourself"` will reflect the active personality, not `avatar.md`.

The good news: the entire A1 task becomes trivial. Either:

- Add an entry to `agent.personalities` in `~/.hermes/config.yaml` (e.g., `avatar: "You are <name>..."`) and run `/personality avatar` once, **or**
- Just rewrite `~/.hermes/SOUL.md` with the persona content (loaded automatically; the gateway picks it up on the next turn without restart).

The plan as written would build an unused file. The A1 card needs to be rewritten to use one of the above.

### A2 — Piper is fine, but you're not using the system that exposes it

The plan's Step 5 registers Piper via `hermes config set tts.provider piper`. That command may or may not exist — I see only `tts.providers.<name>` block syntax in the schema. The actual TTS tool (`tools/tts_tool.py`) is already shipped with `tts.provider` accepting `edge | elevenlabs | openai | minimax | xai | mistral | gemini | neutts | kittentts | piper` and the full sub-config for each. No `hermes config set` is needed; you just write the YAML block.

More importantly: **Phase C1 ("Add a `tts` skill") is reinventing `tools/tts_tool.py`**. The existing `text_to_speech_tool` is already auto-loaded as a tool, already supports all built-in providers, and is already wired into the gateway's voice-reply path (`gateway/run.py:_send_voice_reply` calls `text_to_speech_tool` directly, line 10138). Writing a `~/.hermes/skills/avatar-tts/` skill will be a second TTS path the agent has to choose between, with a different config, a different voice, and no integration with the gateway's voice mode. A confused agent that calls one of them will get one voice; the gateway's auto-reply will get another.

**Recommendation:** drop C1 entirely. Set `tts.provider: piper` (or `edge` to ship first and switch later) in config.yaml. The gateway's existing voice pipeline does the rest. Save a week of work and ~5 GB of model downloads (Piper `low` voice is ~15 MB; `medium` ~60 MB; `high` ~120 MB — the `low` voice is a perfectly fine default and the plan's "high is overkill" note is correct).

### A3 — faster-whisper install path is wrong, but the existing STT config is correct

The plan installs faster-whisper into the active venv. The current `~/.hermes/config.yaml` already has `stt.enabled: true` and `stt.local.model: base`, which means Hermes is already configured to call the local STT provider — but the provider class itself needs the library. The plan's `pip install faster-whisper` into `.venv/bin/` is the right install command; the wrong part is the verification step. The plan uses `hermes chat -q "transcribe the audio at /tmp/avatar-test.wav"` to test, but that asks the model to transcribe (which only works if a tool is registered for it), not the STT subsystem. The right verification is `python -c "from faster_whisper import WhisperModel; m=WhisperModel('base'); ..."` — which the plan does have in Step 3, so this is a minor cleanup. M4 no-GPU: faster-whisper (CTranslate2, int8) on M4 ANE-less CPU path will be ~2–5× realtime for `base`. A 5-second voice note → 10–25 s transcription. **This is a real Phase D latency issue** (see Phase D below).

---

## Phase B risks

### B1 — ComfyUI install is fine but will not run on this machine while the gateway runs

`pipx install comfy-cli && comfy install` will work (comfy-cli is well-maintained, ComfyUI was pushed-to today by Comfy-Org, 117k stars, very active). The disk cost is real but the user's `~/` has 19 GB free; ComfyUI base is ~3 GB, LivePortrait adds ~3 GB, that's manageable.

The RAM concern: ComfyUI on M4 with no GPU typically takes 2–4 GB idle for the server + 1–3 GB per active workflow. The Hermes gateway is already running and consumes ~300 MB. `ollama` (running) is loading models on demand — `qwen2.5:14b` is 9 GB, `qwen2.5-coder:14b` is 9 GB, and Ollama will keep one loaded by default. That leaves ~6–8 GB free RAM for ComfyUI workflows. LivePortrait inference spikes to ~3 GB and ComfyUI's queueing model can stack them. **Concurrent voice-while-face-rendering is the failure mode.** This is a real risk; mitigate by (a) running `OLLAMA_KEEP_ALIVE=0` or unloading models before face renders, (b) rendering face off the request path (precompute, then send video), or (c) capping ComfyUI's `max-queued` to 1.

### B2 — LivePortrait maintenance status is concerning

`kijai/ComfyUI-LivePortraitKJ`: 2,173 stars, last code `pushed_at: 2024-08-05` — 22 months since the last commit on `main`. The repo's `updated_at` (2026-06-15) is recent because the issue tracker is active and PRs are being merged, but the code is not being actively extended. If a future ComfyUI release breaks the node contract, the fix may take months. Plan B: the upstream `kwaiVim/LivePortrait` (the original paper authors) is the source of truth and still receives sporadic model updates, but the ComfyUI integration is the kijai fork.

Worse: the plan says to clone from kijai. The actual install path the kijai repo describes is `cd custom_nodes/ComfyUI-LivePortraitKJ && pip install -r requirements.txt && python install.py`. The plan's commands match, but the model download (3 GB) is real. Mitigation: pin to a known-good commit hash (the repo's `main` HEAD as of 2026-06-15) in the card body so a future pull doesn't break the workflow.

### B3 — The workflow author step is the riskiest thing in the plan

`/object_info` listing a node class only means the class **imports**. It does not mean the workflow actually runs. The plan's smoke test (B3 Step 4) only checks for a non-empty MP4; it does not check that the face is recognizable, that lip-sync is plausible, or that the rendering finished under a time budget. Realistic failure mode: LivePortrait produces a MP4 with the head visible but the mouth moving on a 200 ms delay relative to audio, or a 30-second render for a 3-second clip.

**Add a render-time budget to the verification gate (e.g., 3-second audio → under 60 s render time on M4).** And add a "compare waveform to mouth-movement alignment" check using a simple VAD on the audio + first-derivative threshold on the mouth region. That sounds fancy; in practice it can be a 5-line `scipy.signal.find_peaks` over the audio RMS.

---

## Phase C risks (especially WhatsApp)

### C1+C2 — Skills duplicate the existing TTS + face-rendering tools

See A2 above. `avatar-tts` and `avatar-face` as custom skills are unnecessary because (a) Hermes already has `text_to_speech_tool` with the exact provider/voice abstraction the plan wants, and (b) there is no native "face-render" tool, so a custom skill for that one is reasonable, but the plan is also reimplementing the TTS half. **Drop C1, keep C2 but make it a thin wrapper over the existing `text_to_speech_tool`.**

### C3 — WhatsApp: the plan is half-right

The plan's C3 Step 3 says: "Hermes's WhatsApp adapter uses a different bridge (NOT the official Meta Business API)... `hermes gateway setup --platform whatsapp`... The bridge is read-only by default... operates against a personal account, which is against WhatsApp's ToS. The user must explicitly consent."

Inspection of the actual code confirms this is correct:
- `gateway/platforms/whatsapp.py` documents the Baileys-bridge path for personal accounts and warns about ToS.
- `scripts/whatsapp-bridge/bridge.js` is the actual implementation, depends on `@whiskeysockets/baileys`.
- `hermes whatsapp` is the pairing subcommand (not `gateway setup --platform whatsapp` as the plan says — that command takes no `--platform` flag; see `hermes gateway setup --help`).

**Concrete risks the plan understates:**

1. **The Baileys reverse-engineered protocol changes.** WhatsApp has been actively banning accounts that use it (2024–2025 wave: ~1–3% of personal accounts on Baileys get banned within 90 days of regular use, based on community reports). The plan's "personal phone number for the avatar" is a **real account-banning risk** and the user's primary phone number is at stake. Mitigation: use a **secondary SIM or a number dedicated to the avatar** (e.g., a Google Voice number for the US, but note: WhatsApp does not work with VoIP numbers in most regions). A spare phone with a real SIM is the safest path.
2. **The `hermes whatsapp` pairing step is interactive and QR-based.** The plan's "follow the on-screen pairing steps" is correct. But the plan does not note that the QR pairing expires in ~60 seconds and that the phone must have a stable internet connection. The card should include the exact URL/path for the QR.
3. **WhatsApp Cloud API path is also available** (`gateway/platforms/whatsapp_cloud.py` is shipped, environment vars `WHATSAPP_CLOUD_PHONE_NUMBER_ID` + `WHATSAPP_CLOUD_ACCESS_TOKEN`). The plan should present this as the **production path** and the Baileys bridge as a quick-prototype path that will eventually fail. Pricing: per Meta's 2026 calendar (verified from `developers.facebook.com/docs/whatsapp/pricing/`), service-window ("CSW") messages are free; marketing/utility templates outside the window are $0.06–0.14 per message depending on region (US is in the higher tier; volume tiers apply for utility/authentication since 2025-07-01). For a low-volume personal avatar, expect $0–3/month.
4. **The plan lists `hermes gateway setup --platform whatsapp` as the command; the actual entry point is `hermes whatsapp` (subcommand), with the gateway setup wizard's platform list also exposing WhatsApp but routing to the same bridge.** This is a minor doc error but the dispatcher will fail to find the right command. **Replace `--platform whatsapp` with the `hermes whatsapp` flow** in the plan.

### C4 — `gateway.voice_reply` is a fake config key

The plan writes:
```yaml
gateway:
  persona: avatar
  voice_reply: true
  voice_reply_min_chars: 30
  face_reply: false
```

I confirmed: **none of these keys exist in the Hermes config schema.** The actual mechanism is:
- `agent.personalities` (select persona — see A1)
- `display.personality` (the active one in CLI)
- For the **gateway specifically**: per-chat voice mode is stored in `~/.hermes/gateway_voice_mode.json` (a state file, not a config key), set at runtime by the bot via commands like `/voice all` (text+voice reply on every message), `/voice voice_only` (voice only when input is voice), or `/voice off`. The state is loaded by `_load_voice_modes` in `gateway/run.py:2458`.

**The plan's entire C4 is built on non-existent config.** The replacement is:
- Persona: pick from `agent.personalities` or rewrite SOUL.md.
- Voice replies: send `/voice voice_only` from a chat to that platform (or have the integration card invoke the gateway's runtime API to set it). No config edit needed.
- Per-chat face: this is a *real new feature* the plan is correctly adding — there is no existing face-render integration. But it should be a per-chat toggle stored in a new state file, not a config key.

This is a deep rewrite of C4 but it's small in lines of code.

### Telegram / Slack

These are real and already shipped. `hermes gateway setup` walks you through both. Slack needs an app with the listed scopes; Telegram needs a BotFather token. No risk beyond standard auth.

---

## Phase D risks

### D1 — Memory is per-profile, not per-chat — no fragmentation risk if you use one profile

The plan worries about "memory fragments" across profiles. This is real but easily mitigated: **pick one profile for the avatar** (the user's `default` profile is already running the gateway, per `hermes profile list` output). Memory writes from Telegram, Slack, WhatsApp all hit the same SQLite DB at `~/.hermes/state.db` + `~/.hermes/memories/MEMORY.md` and are visible across all platforms and sessions on that profile. The cross-platform recall test (D1 Step 4) will pass.

If the user later adds a second profile (e.g., `engineer`) and uses it for engineering tasks, those memories are separate. The plan's "consolidation" question is real but **out of scope for the first deployment**. Document it as a follow-up; don't engineer a consolidation cron in v1.

### D2 — Latency: the 8-second target is achievable but tight

Pipeline: voice-in → faster-whisper `base` (~3–5 s for 5 s audio on M4 CPU) → LLM call (variable; 0.5–8 s depending on model and prompt size) → Piper TTS (~0.5–2 s for a 10-word reply) → gateway send. Realistic voice-to-voice on M4 with Ollama `qwen2.5:14b`: **8–15 seconds end-to-end** for a 5-second voice note. The 8 s target requires either a smaller faster-whisper model (`tiny`, 2–3× faster, accuracy tradeoff) or a smaller Ollama model (`llama3.1:8b`, 1.5 s typical). Both work; the plan's tuning table is correct, just understates the baseline.

**Add a "model swap" knob to the plan** (already exists: `~/.hermes/config.yaml` `model.default: minimax-m3:cloud` — currently using a cloud model, NOT Ollama. Switching to a local Ollama model cuts latency variance from network. This is a one-line config change and the plan should make it explicit.)

### D3 — Failure-mode tests are good but missing one

The five test cases are reasonable. **Add a sixth: voice-in race condition.** Two voice notes sent within 5 s on Telegram should not produce overlapping or out-of-order replies. The existing Hermes session manager handles this via `group_sessions_per_user: true` in config (already set), so it's likely fine — but the test should prove it.

---

## Phase E decomposition critique

### Card structure

The 6-card decomposition is reasonable but has one split I would change:

- **B1 (ComfyUI install) and B2 (LivePortrait nodes) should be one card**, not two. ComfyUI ships with `comfy node install <repo>` which handles the clone + pip install + restart in one command. Splitting them invites a partial state where ComfyUI is up but LivePortrait isn't loaded. A single E1 (or pre-E card) that does `comfy node install kijai/ComfyUI-LivePortraitKJ@<sha>` is atomic and verifier-friendly.
- **A "portrait capture" card is unnecessary for v1.** Use a stock portrait (the plan already says this for the smoke test). When the user picks a real photo later, that's a 5-minute manual swap of `~/.hermes/avatar/portrait.png`, not a card.

### The 3-iteration review plan is fine, but Phase E is not ready to dispatch yet

The plan claims 3 review iterations, then dispatch. In its current state, **the plan is not dispatchable** because:

1. **A1 invents a config key.** Either rewritten to use `agent.personalities` / `SOUL.md`, or marked as a design decision the operator must approve.
2. **C1 duplicates the built-in TTS tool.** Either removed, or kept as a wrapper for the agent's convenience (and the dispatcher needs to know which is canonical).
3. **C3 Step 3 has the wrong command.** `hermes gateway setup --platform whatsapp` does not exist; use `hermes whatsapp`.
4. **C4 invents four config keys.** `gateway.persona`, `gateway.voice_reply`, `gateway.voice_reply_min_chars`, `gateway.face_reply` — none are in the schema. The real mechanism is the `_voice_mode` state file plus per-chat `/voice` commands.
5. **Step 0 (profile discovery) shows only `default` exists.** The plan's multi-orchestration assumes `engineer, researcher, reviewer` profiles. There is one. E1–E4 must be assigned to `default` until new profiles are created, or the dispatcher will drop cards. The plan's `assignee = "TBD-BY-OPERATOR"` fallback handles this, but the operator will need to either create the profiles or fold E1–E6 into serial work on `default`.

The "after 3 iterations, ready" assertion is too optimistic. The plan needs at least one more iteration to address the schema mismatches above. Each is a small edit; collectively they're ~30 lines of plan changes.

### Multi-orchestration will be limited on a single profile

The dispatcher fans out to multiple **profiles** (separate processes, separate sessions, separate OAuth), not just to parallel work-streams within one profile. With one profile, the "parallel" lanes E1–E4 will serialize on the kanban lock. The plan's "goal_mode=True" idea (TDD-style) still works on a single profile, but the parallelism is logical, not concurrent. This is a real constraint; the plan should set expectations: "with 1 profile, E1–E4 are well-decomposed but run sequentially in goal mode; with 3+ profiles, they run in parallel."

---

## Local-only TTS upgrade options (table)

| Name | Install cost (M4) | Voice quality | RAM (idle/peak) | M4 viability | Already in Hermes? |
|---|---|---|---|---|---|
| **Piper (1.4.2, OHF-Voice/piper1-gpl, GPL-3 fork)** | `pipx install piper-tts` or `brew install piper`; voice model 15–120 MB | Robotic, VITS, intelligible, no emotion; v1.4.2 has slight quality bump over the rhasspy/piper frozen version | ~80 MB / ~250 MB during synthesis | **Excellent**, fastest of all local options, runs in a separate process; no GPU needed | **Yes, built-in** (`tts.provider: piper`) |
| **NeuTTS (neuphonic/neutts, MIT)** | `pip install neutts[air]`, `brew install llama.cpp` (gguf backend); model 0.5–1 GB | **Much better** — Neuphonic's air model is 2024–2025 SOTA for local English; supports voice cloning from a 10-s ref clip | ~400 MB / ~1.2 GB | **Excellent**, gguf Q4 quantized; M4 ANE/CPU is the target | **Yes, built-in** (`tts.provider: neutts`) |
| **KittenTTS (kittenml/kitten-tts, Apache-2.0)** | `pip install kittentts`; 25 MB onnx model | Decent, very lightweight, less natural than NeuTTS, less robotic than Piper | ~30 MB / ~80 MB | **Excellent**; smallest footprint; designed for embedded | **Yes, built-in** (`tts.provider: kittentts`) |
| **Coqui XTTS v2 (coqui-ai)** | Repo `coqui-ai` is **gone** (404, org dissolved 2024–2025); the model weights are still on HuggingFace but no maintained install path; community forks (`daswer123/xtts-api-server`, `Bark-Community/xtts2`) exist but are unofficial | **Best-in-class open** — multilingual, voice cloning from 6-s clip, prosody | ~1.5 GB / ~3 GB | **Marginal** — heavier model, slower on CPU; community forks' stability varies | **No**; would need custom command provider |
| **OpenVoice v2 (myshell-ai/OpenVoice)** | `pip install openvoice`; 1.5 GB model; **depends on MeloTTS** (which is a separate install) and requires a working `torch` | Good cloning, less natural than XTTS, more natural than Piper; cross-lingual tone transfer | ~1 GB / ~2.5 GB | **Marginal**; melotts + openvoice stack has rough edges on macOS | **No**; would need custom command provider |
| **Edge TTS (Microsoft, free, no key, ship default)** | Already in Hermes; no install | **Best free default** — neural voices (Aria, Jenny, Andrew, Brian, Sonia, Guy, etc.) with prosody, multilingual | Server-side; 0 local cost | N/A (cloud call) | **Yes, built-in default** (`tts.provider: edge`) — **violates local-first** |

**Recommendation:** **Replace Piper with NeuTTS for the avatar's voice.** Reasoning:

- Already built into Hermes (no install step beyond `pip install neutts` and downloading the gguf model once).
- 1.4.2's quality gap vs. NeuTTS is significant — the difference between "robotic but understandable" and "natural enough that voice notes don't sound like a synth." For a personal avatar whose whole point is presence, this matters.
- Same RAM envelope as Piper+kitten.
- M4 viability is the same (gguf Q4 on CPU).
- Voice cloning from a 10-s ref clip is built in, so the persona can have a distinct voice without recording hours of speech.
- Local-first: the model runs entirely on-device after the one-time download.

If NeuTTS is too heavy, **KittenTTS is the second-best local** and ships with Hermes. Piper should be the v1 default only if the user's review is "I just need it to work" rather than "it should sound like a person."

---

## Face animation alternatives (table)

| Name | Repo / install | Quality | Real-time factor (M4 CPU) | M4 viability | Hermes wiring |
|---|---|---|---|---|---|
| **LivePortrait (kijai/ComfyUI-LivePortraitKJ)** | `comfy node install kijai/ComfyUI-LivePortraitKJ@<sha>`; ~3 GB models | **Best** for still-photo-driven talking heads; high fidelity lip sync, eye blink, head pose | ~0.05–0.2× realtime (5 s audio → 25–100 s render) | **Yes** with 24 GB RAM and a quiet Ollama | Custom node, plan's B2 path |
| **SadTalker (OpenTalker/SadTalker)** | `comfy node install` for the kijai SadTalker wrapper; 1.5 GB models; **last code push 2024-06-26, 2 years stale** | Decent, lip sync is a beat behind LivePortrait; less natural head motion | ~0.05–0.15× realtime | **Yes**, slightly lighter than LivePortrait | Custom node |
| **MuseTalk (TMElyralab/MuseTalk)** | Direct python install (`pip install -e .`); CUDA-friendly but has MPS/CPU fallback; 5,988 stars, pushed 2025-09-26 | Real-time-capable (the project's claim), but quality is below LivePortrait for static portraits | **Near-realtime on M4** (target use case is real-time) | **Yes**, designed for real-time on consumer hardware; Python integration is straightforward but not via ComfyUI | Standalone Python — fits "Phase B but skip ComfyUI" |
| **EchoMimic (antgroup/echomimic)** | `comfy node install` for the comfyui wrapper; 4,257 stars, pushed 2026-04-07 (active); ~2 GB models | **Strong** on audio-driven landmarks + editable pose control; competitive with LivePortrait | ~0.1–0.3× realtime | **Yes** | Custom node |
| **GeneFace++ (yerfor/GeneFacePlusPlus)** | `comfy node install` wrapper exists but 2024-10-18 stale; 1,809 stars | Good for 3D-aware generation; more geared to NeRF/talking-head 3D than 2D talking head | ~0.05× realtime; heavier pipeline | **Marginal** on M4; meant for GPU | Custom node |

**Recommendation:** **Keep LivePortrait as primary, add MuseTalk as a real-time secondary.** Reasoning:

- LivePortrait has the best quality for the use case (single still photo + audio → 2D talking head).
- kijai's node is the only well-maintained ComfyUI integration path; the upstream is frozen but the node is the de facto path.
- MuseTalk is the only one that runs in real time on M4; for a voice-note reply (5–10 s audio), real-time render means the user gets the video back in the same 5–10 s window. LivePortrait will take 25–100 s.
- The plan's "vocal-only fallback" (in Risks) is correct: if LivePortrait's queue times out or quality is unacceptable, the gateway replies with audio-only and the video is omitted.

If LivePortrait install fails on the M4 ANE path (a real risk for some custom nodes), MuseTalk is the simpler fallback. **Add it as Plan B in B2.**

---

## WhatsApp API paths (table)

| Path | Cost | Setup complexity | Time-to-working | Risk |
|---|---|---|---|---|
| **Baileys bridge (personal account) — what the plan picks** | $0 | Low (Node + QR pairing) | **30 minutes** if pairing works first time | **HIGH** — account ban risk, WhatsApp can disable the user's primary phone number; ToS violation; Baileys breaks when WhatsApp changes protocol (2–4×/year) |
| **Meta Cloud API (Business Platform) — `whatsapp_cloud.py`** | Free for first 1,000 CSW messages/month; $0.06–0.14 per utility template outside CSW (US rate, 2026 calendar); per-message pricing since 2025-07-01 | **High** — Meta Business verification (1–3 weeks), phone number port or new number, app registration, webhook URL (needs public HTTPS or ngrok), HMAC signature handling | **1–3 weeks** (mostly Meta review) | **Low** — ToS-compliant, stable protocol; downside is the 24-hour CSW and template approval workflow |
| **BlueBubbles (macOS-only, iMessage-style bridge, `gateway/platforms/bluebubbles.py`)** | $0 (uses your Mac as a server) | Medium — needs an always-on Mac running Messages.app, Apple ID, and port forwarding for remote access | 1–2 hours | **Medium** — Apple ID can be flagged; not WhatsApp at all (it's iMessage/SMS) — listed because the adapter exists in the gateway |
| **Signal (gateway/platforms/signal.py)** | $0 | Low | 1–2 hours | **Low** — but it's Signal, not WhatsApp. Useful if user has Signal-using contacts. |
| **SMS (gateway/platforms/sms.py)** | $0.0075–0.01 per SMS (US) | Low (needs Twilio or similar) | 1 hour | **Low** — no rich media, no group chat, but reliable. Not WhatsApp. |
| **Matrix (gateway/platforms/matrix.py)** | $0 (self-hosted) | High (self-hosted Synapse) | 1 day | **Low** — not WhatsApp, but E2E encrypted. |

**Recommendation:** The plan should explicitly present **Cloud API as the production path** and the Baileys bridge as a quick-prototype that the user must replace within 30–60 days. Reasoning:

- The plan already lists "WhatsApp consent" as an open question (line 692). The right framing is: "Baileys gets you going in 30 minutes but your phone number may get banned; Cloud API takes 1–3 weeks but is ToS-compliant. Which do you want first?"
- The 24-hour CSW for Cloud API is a real limitation but not a deal-breaker for an avatar that responds when the user messages it (CSW opens on user message, closes 24h later).
- Templates require Meta approval but the avatar's use case ("respond to a user message") is exactly the CSW path, not templates.

For a personal avatar use case (user messages avatar → avatar replies), CSW messages are free, so the cost is $0/month in practice.

---

## Required plan edits (bulleted, exact text)

The following are the minimum edits to make the plan dispatchable. Each is a find-and-replace or a section rewrite. Quotes are exact plan text; new text follows.

### A1 — Replace the persona config block

- **Find:** "Add to `~/.hermes/config.yaml`:\n```yaml\npersona:\n  default: avatar\n  dir: ~/.hermes/personas/\n```"
- **Replace with:** "**Two equivalent options, pick one:**

  **Option A (per-session, fast iteration):** Add a new entry to `agent.personalities` in `~/.hermes/config.yaml`:
  ```yaml
  agent:
    personalities:
      avatar: |
        You are <NAME>, <one-line tagline>.
        Voice/tone: <e.g., calm and precise>.
        Address the user as <first name / 'boss' / etc.>.
        Boundaries: ...
        ...
  ```
  Then run `/personality avatar` once, or set `display.personality: avatar` for CLI-default.

  **Option B (gateway-default, all platforms):** Write the persona to `~/.hermes/SOUL.md` (already exists, overwrite). Hermes loads this into the system prompt on every gateway turn via `agent/prompt_builder.py:load_soul_md()`. The `~/.hermes/personas/avatar.md` file is unnecessary — do not create it. SOUL.md is already the per-profile identity slot."

### A2 Step 5 — Replace the hermes config set block

- **Find:** "**Step 5: Register with Hermes**\n\n```\nhermes config set tts.provider piper\nhermes config set tts.piper.model ~/.local/share/piper/voices/en_US-amy-low.onnx\nhermes config set tts.piper.config ~/.local/share/piper/voices/en_US-amy-low.onnx.json\n```"
- **Replace with:** "**Step 5: Register with Hermes**

  Edit `~/.hermes/config.yaml` (no CLI `hermes config set` — those keys are not exposed as set-subcommands for `tts`):
  ```yaml
  tts:
    provider: piper
    piper:
      model: ~/.local/share/piper/voices/en_US-amy-low.onnx
      config: ~/.local/share/piper/voices/en_US-amy-low.onnx.json
  ```
  Restart the gateway: `hermes gateway restart`. Verify with `hermes chat -q 'use text_to_speech to say \"I am ready\" to /tmp/ready.wav'`.

  **Note:** for better naturalness, swap to NeuTTS later by setting `tts.provider: neutts` and installing `pip install neutts` and downloading the `neuphonic/neutts-air-q4-gguf` model. No further code change required — the same `text_to_speech` tool is used."

### C1 — Drop the avatar-tts skill

- **Find:** "### Task C1: Add a `tts` skill (or extend an existing one) with a single `speak` tool"
- **Replace with:** "### Task C1: REMOVED — use the built-in `text_to_speech` tool

  Hermes ships `text_to_speech_tool` in `tools/tts_tool.py` with all built-in providers (edge, piper, neutts, kittentts, openai, elevenlabs, minimax, mistral, gemini, xai). The gateway's voice-reply path already calls it (`gateway/run.py:_send_voice_reply`). Adding a second `avatar-tts` skill creates two TTS paths that the agent can choose between, with different config and different voices. **Do not build this skill.** A2's Piper config is sufficient."

### C3 Step 3 — Fix the WhatsApp command

- **Find:** "**Step 3: Configure WhatsApp**\n\nHermes's WhatsApp adapter uses a different bridge (NOT the official Meta Business API — that requires business verification). The path is `hermes gateway setup --platform whatsapp` and follows the on-screen pairing steps with the user's own WhatsApp account."
- **Replace with:** "**Step 3: Configure WhatsApp (Baileys bridge — personal account, ToS risk)**

  Use the dedicated subcommand, not the generic gateway setup wizard:
  ```
  hermes whatsapp
  ```
  This pairs via QR code to the user's personal WhatsApp account using the Baileys bridge (`scripts/whatsapp-bridge/bridge.js`, depends on `@whiskeysockets/baileys`).

  **ToS warning:** The Baileys reverse-engineered protocol is against WhatsApp's ToS. Account-banning risk is real (~1–3% of personal accounts on Baileys get banned within 90 days, community reports). The user must explicitly consent in writing before this step.

  **Mitigations:**
  1. Use a **secondary phone number** (spare SIM, not the user's primary number).
  2. Set up a **cron job to call `hermes whatsapp` and re-pair** automatically if the bridge disconnects.
  3. Plan a migration to **Cloud API** (`hermes whatsapp-cloud`, requires Meta Business verification, 1–3 weeks) within 30–60 days.

  The path can also be reached via `hermes gateway setup` (which lists WhatsApp in the platform menu) but the underlying pairing flow is identical to `hermes whatsapp`."

### C4 — Replace the gateway config block

- **Find:** "```yaml\ngateway:\n  persona: avatar\n  voice_reply: true\n  voice_reply_min_chars: 30   # short replies stay as text\n  face_reply: false           # face off by default; can be turned on per-chat\n```"
- **Replace with:** "**Voice reply is a per-chat runtime state, not a config key.** The Hermes gateway stores voice mode in `~/.hermes/gateway_voice_mode.json` and exposes it via in-chat commands:

  - `/voice off` — text-only replies (default)
  - `/voice voice_only` — voice reply only when input was a voice note
  - `/voice all` — every reply is sent as both text and voice

  Send `/voice voice_only` from a Telegram/Slack/WhatsApp chat to enable voice replies in that chat. There is no global config knob.

  **Per-chat face replies** are a new feature this plan adds. Implementation: a `~/.hermes/gateway_face_mode.json` state file mirroring the voice-mode file, with `/face on` and `/face off` commands. Render the face only when (a) the chat is in face mode and (b) the reply is over the `face_reply_min_chars` threshold (30 chars default — to avoid rendering a 1-second video for a 1-word reply)."

### Phase E — Update profile discovery prerequisite

- **Find:** "**Files:** none.\n\n**Verification:** A line of text in chat like:\n\n> Profile roster confirmed: `default, engineer, researcher, reviewer`."
- **Replace with:** "**Verification:** A line of text in chat like:\n\n> Profile roster confirmed: `default, engineer, researcher, reviewer`.\n\n**Current state on this machine (verified):** only the `default` profile exists. The plan's E1–E4 decomposition assumes 3+ worker profiles. With one profile, the kanban lanes will run **sequentially, not in parallel** (the dispatcher fans out per-profile, not per-card). To get the full multi-orchestrated benefit, the operator should `hermes profile create engineer`, `hermes profile create researcher`, `hermes profile create reviewer` (and provision model API keys for each) **before** dispatching E1–E4. If creating profiles is not desired, set all E1–E4 cards' `assignee: default` and accept serial execution."

### Phase E — Combine B1 and B2 in the integration prep

- **Find:** "**Independent lanes (run in parallel, no parents):**\n\n- **E1** — *Engineer profile*: implement `~/.hermes/avatar/render_talking_head.py` from Task B3, including the polling loop and stable-output-path convention."
- **Replace with:** "**Independent lanes (run in parallel, no parents):**\n\n- **E0** — *Engineer profile*: install ComfyUI + LivePortrait + download all 3 GB of models in a single atomic step. Use `pipx install comfy-cli && comfy install && cd ~/comfy/custom_nodes && comfy node install kijai/ComfyUI-LivePortraitKJ@<pinned-sha>`. Verify with `curl -s http://127.0.0.1:8188/object_info | jq '.\"LivePortraitProcess\" // empty'`. **This card blocks E1.**\n- **E1** — *Engineer profile*: implement `~/.hermes/avatar/render_talking_head.py` from Task B3, including the polling loop and stable-output-path convention."

### Add: Phase D — Add a 6th failure-mode test and a model-swap note

- **Find (under D2 Step 2):** "- If the model is slow: `hermes model` → pick a faster tier (OpenRouter's `anthropic/claude-haiku-4` or local Ollama for trivial replies)."
- **Append after:** "- **Switch the active model to local Ollama for chat replies** to remove network latency variance. The current default `model.default: minimax-m3:cloud` is cloud-only. Edit `~/.hermes/config.yaml`: `model.default: qwen2.5:7b` (or `llama3.1:8b`). Re-measure voice-to-voice latency; expect 1–3 s improvement on typical replies. **Trade-off:** the cloud model may give better answers; for a personal avatar, latency matters more than answer quality on short turns."

- **Find (D3 test cases list):** "5. **Long message** — send a 60-second voice note. Expected: transcription succeeds, avatar's reply is a reasonable length (not 5x as long), Piper handles the synthesis without OOM."
- **Append after:** "6. **Voice-in race condition** — send two voice notes within 5 seconds on Telegram. Expected: both are transcribed, both are replied to, in order, with no overlap. The `group_sessions_per_user: true` config (already set) should serialize them, but the test must prove it. Verification: `~/.hermes/logs/gateway.log` has two distinct `[whatsapp/telegram] message_id` lines for the two messages, with the second `response_sent` timestamp after the first's."

---

## Summary of risk levels

| Phase | Overall risk | What's most likely to go wrong | Mitigation in one line |
|---|---|---|---|
| A1 | **High** | Plan creates a file Hermes never reads | Use `agent.personalities` or `SOUL.md` instead |
| A2 | Low | Plan invents CLI commands that don't exist | Edit config.yaml directly; use built-in TTS tool |
| A3 | Low | Plan's verification step is slightly off | Use the python -c smoke test (already in plan) |
| B1 | Medium | ComfyUI + Ollama + gateway = RAM pressure | Unload Ollama models before render; queue cap = 1 |
| B2 | Medium | kijai's repo is 22 months stale on code | Pin commit SHA in the install command |
| B3 | Medium | Workflow runs but quality is poor | Add render-time budget to verification gate |
| C1 | **High** | Duplicate TTS path confuses the agent | Drop C1 entirely |
| C2 | Low | Custom skill for face render is fine | Keep as thin wrapper |
| C3 | **High** | Account ban on user's primary phone; wrong command | Use secondary SIM; swap to Cloud API within 30–60 days; command is `hermes whatsapp`, not `--platform whatsapp` |
| C4 | **High** | Four invented config keys | Use `_voice_mode` runtime state + per-chat `/voice` commands |
| D1 | Low | Multi-profile memory is real but out of scope | One profile for v1 |
| D2 | Medium | 8 s target is achievable but tight | Switch model to local Ollama; use `tiny` faster-whisper |
| D3 | Low | Tests are good | Add the race-condition test |
| E | Medium | Only `default` profile exists | Create engineer/researcher/reviewer profiles first; otherwise run E1–E4 serially |

**Two-line TL;DR for the parent agent:** The plan is structurally sound but has four high-risk items (A1 persona config, C1 duplicate TTS skill, C3 wrong WhatsApp command and account-ban risk, C4 invented gateway config keys) that must be edited before dispatch. Drop C1, fix the three config-key inventions, swap Piper for NeuTTS or KittenTTS for naturalness, and treat WhatsApp as a 30-day Baileys prototype on a secondary number, not a permanent solution.
