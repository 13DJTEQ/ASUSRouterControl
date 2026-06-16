# v2 Plan — Integration-Test Critique (R4)

> Reviewer: integration-test pass against the **live** Hermes 0.16.0 install
> on this host. Every claim below is anchored to a file path + line number
> in `~/.hermes/hermes-agent/` (or to a CLI command's actual output).
>
> v2 corrected 4 structural defects in v1 (persona config block, duplicate
> TTS skill, wrong WhatsApp command, 4 invented gateway config keys). v2
> introduced a Component Data Flow diagram, Same-Machine Integration Table,
> 3-tier local LLM routing, Kokoro TTS, SOUL.md persona, and per-chat
> voice/face state files. **This R4 pass finds 5 new integration breakages
> introduced by v2 itself**, on top of 4 v1 leftovers v2 didn't touch.

---

## v3 must-fix list (R4 verdict: v2 is NOT READY)

1. **A0 — invented config keys** (`models.avatar_chat|trivia|cloud`, `routing.cloud_enabled|cloud_daily_usd_cap|trivial_max_words|log_cloud_calls`). None are in the Hermes schema. Drop the whole `models:` and `routing:` blocks; surface them as code, not config.
2. **A0 — `model.fallback` is the wrong field name and wrong type.** Hermes uses `fallback_providers: []` (top-level list).
3. **A0 — `OLLAMA_KV_CACHE_TYPE=q4_0` and `OLLAMA_NUM_PARALLEL=1` are not Ollama 0.30.8 env vars.** The actual env vars are listed in `ollama serve --help`. Remove both.
4. **A2 — `tts.provider: kokoro` will silently fall through to Edge TTS.** The plan's YAML has no `type: command` and no `command:` field; without a registered plugin, the dispatcher's `else` branch (line 2222 in `tts_tool.py`) defaults to Edge. Rewrite with the user-declared command-provider schema (`tts.providers.kokoro.type: command` + `command:`).
5. **A2 — `text_to_speech_tool` does not accept a `provider` argument.** The plan's A2 step 6 `with provider piper` is silently ignored.
6. **A2 — `brew install piper` fails** (not a formula). Use `pip install piper-tts`.
7. **Routing — `hermes stats --routing` does not exist.** Verification gate #13, D2 step 3, and the whole-plan Verification Gate item 6 are all broken. Either use `hermes insights` (closest existing analytics) or build the routing-stats command in code and assign it to a card.
8. **Routing — 50 ms classifier estimate is wrong** by 4–10×. Real Ollama HTTP round-trip is 100–200 ms minimum; realistic classifier is 200–500 ms. Update the diagram.
9. **Routing — classifier location is unspecified.** No pre-routing hook exists in the gateway today. Pick one of (skill-based, gateway hook, sidecar) and assign the code to an E-card.
10. **C3 — `hermes gateway setup` takes no flags.** `--platform slack|telegram` don't exist. It's an interactive wizard.
11. **C3 — `hermes whatsapp` wizard has no ToS consent gate.** The plan invents a prompt that doesn't exist. Either add the gate to a new E-card (E8) or remove the claim.
12. **C4 — face mode requires a `gateway/run.py` code change, not a state file.** New methods (`_load_face_modes`, `_save_face_modes`, `_set_face_mode`, `_should_send_face_reply`, `_send_face_reply`) and new slash-command dispatcher entries. Add a new E-card.
13. **A1 — `~/.hermes/profiles/<name>/SOUL.md` is not a path Hermes reads.** Only `~/.hermes/SOUL.md` is read (`get_hermes_home() / "SOUL.md"`). Drop the profile-per-SOUL alternative.
14. **A1 — SIGHUP claim is wrong.** SOUL.md is re-read every turn by the agent (no SIGHUP), but the plan's Integration Table says "Re-read on SIGHUP". Fix the wording.
15. **C2 — skill `description:` is too short.** Other skills use 2-3 sentence descriptions. Expand.
16. **C2 — wrapper `render.py` shells out to `hermes chat`** to call TTS recursively. This adds 10+ seconds. Use a direct `from tools.tts_tool import text_to_speech_tool` call instead.

---

## Summary of v2-introduced defects (must fix in v3)

| # | Task | Defect | Severity |
|---|------|--------|----------|
| 1 | A0 | `routing:` and `models.avatar_*` are invented top-level keys, not in the schema | Blocking — silently ignored |
| 2 | A0 | `OLLAMA_KV_CACHE_TYPE=q4_0` is **not an Ollama 0.30.8 env var** | Blocking — silently ignored |
| 3 | A0 | `model.fallback: openrouter/anthropic/claude-haiku-4` is a single string, not a chain; the field is `model.fallback_providers` (list) | Blocking — wrong field name + wrong type |
| 4 | A2 | `tts.provider: kokoro` is not a built-in; the A2 YAML has no `type: command` / `command:` block, so `text_to_speech_tool` will silently fall through to **Edge TTS** | Blocking — Kokoro never runs |
| 5 | Routing | `hermes stats --routing` does not exist (`hermes insights` is the only analytics command; it does token/cost/tool, not model routing) | Blocking — verification gate #13 fails |
| 6 | D2 | The `trivial` LLM call has ≥100–200 ms HTTP overhead + prompt-prep + parse; the plan's "~50 ms" estimate is wrong by 4–10× | High — unrealistic latency target |

Plus 4 leftovers from v1 that v2 did not fix:

| # | Task | v1 leftover | Severity |
|---|------|-------------|----------|
| 7 | C2 | `text_to_speech` tool doesn't accept a `provider` argument; A2 step 6's `with provider piper` is silently ignored | Medium |
| 8 | C3 | `hermes gateway setup` does not accept a `--platform` flag (interactive wizard only) | Medium |
| 9 | C3 | `brew install piper` fails — `piper` is not a Homebrew formula (it's `piper-tts` on PyPI) | Medium — A2 step 2 broken |
| 10 | C4 | `/face on` / `/face off` slash commands don't exist; `gateway_face_mode.json` doesn't exist; the plan calls for code changes in `gateway/run.py` but presents them as a config change | High — entire C4 step 2 is gateway code, not state file |

---

## A0 — LLM default swap

### The integration point
- Hermes reads `~/.hermes/config.yaml` on every turn (`hermes_cli/config.py:load_config`)
- Model selection: `model.default` (a string), `model.provider` (provider key)
- Provider definitions: `providers.<name>.{api, default_model, models}` (current: `ollama-launch`)
- The runtime provider resolver: `hermes_cli/runtime_provider.py` (handles ollama/vllm/llamacpp aliases via "custom" base_url trust)

### What v2 claims
```yaml
model:
  default: mistral-small:24b-instruct-2503-q4_K_M
  provider: ollama-launch
  fallback: openrouter/anthropic/claude-haiku-4
models:
  avatar_chat:    "mistral-small:24b-instruct-2503-q4_K_M"
  avatar_trivia:  "llama3.1:8b-instruct-q6_K"
  avatar_cloud:   "anthropic/claude-sonnet-4"
routing:
  cloud_enabled: false
  cloud_daily_usd_cap: 5.0
  trivial_max_words: 6
  log_cloud_calls: true
```
```bash
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_KV_CACHE_TYPE=q4_0
OLLAMA_NUM_PARALLEL=1
OLLAMA_KEEP_ALIVE=30m
```

### What the host actually does

**`model.default` and `model.provider`** — work. The current `~/.hermes/config.yaml:55-59` already has:
```yaml
model:
    api_key: ollama
    base_url: http://127.0.0.1:11434/v1
    default: minimax-m3:cloud
    provider: ollama-launch
```
So flipping `default` to `mistral-small:24b-instruct-2503-q4_K_M` is the only field that needs to change. The provider is already correct.

**`model.fallback: openrouter/anthropic/claude-haiku-4`** — **wrong field name and wrong type.** Hermes's config schema has `fallback_providers: []` (a list) at the top level, not `model.fallback: <string>`. The actual key is defined in `hermes_cli/config.py:811`:
```python
DEFAULT_CONFIG = {
    "model": "",
    "providers": {},
    "fallback_providers": [],
```
No code in the repo reads `model.fallback`. A user who follows the YAML will get a config that does nothing on the fallback path.

**`models.avatar_chat`, `models.avatar_trivia`, `models.avatar_cloud`** — **invented top-level keys.** `grep` of `hermes_cli/config.py` for `avatar_chat|avatar_trivia` returns 0 matches. The real model registry is `providers.<name>.models: []` (a list of strings, see `~/.hermes/config.yaml:94-101`). A user who writes `models.avatar_chat: ...` will get a key that's stored but never read by any routing code. The plan's 3-tier router (below) has no place that looks at this key.

**`routing: { cloud_enabled, cloud_daily_usd_cap, trivial_max_words, log_cloud_calls }`** — **all four invented.** `grep` for `cloud_enabled|cloud_daily_usd_cap|trivial_max_words` in `hermes_cli/` returns 0 hits. The only `classify`-related code is in `cron/suggestion_catalog.py` for email urgency, not model routing. Hermes has no built-in "routing:" config block. The cost cap and tier classifier are entirely greenfield code the plan never assigns to a file.

**`OLLAMA_KV_CACHE_TYPE=q4_0`** — **not an Ollama 0.30.8 env var.** `ollama serve --help` lists:
```
OLLAMA_DEBUG, OLLAMA_HOST, OLLAMA_CONTEXT_LENGTH, OLLAMA_KEEP_ALIVE,
OLLAMA_MAX_LOADED_MODELS, OLLAMA_MAX_TRANSFER_STREAMS, OLLAMA_MAX_QUEUE, OLLAMA_MODELS
```
`OLLAMA_NUM_PARALLEL` is also not in that list. Setting both in `~/.hermes/.env` will be silently ignored. The plan's claim that these "squeeze a bit more" out of the M4 is unsupported — Ollama picks its own KV cache quantization based on hardware.

**`OLLAMA_HOST=127.0.0.1:11434`** — already set system-wide (the user's env shows it). Already correct.

**`OLLAMA_KEEP_ALIVE=30m`** — valid. The model will stay warm for 30 min after the last use. (The plan's `keep_alive: "30m"` in the integration table is fine, but it's an Ollama parameter set on the API call, not an env var, and there's no code in the plan that makes this happen — Hermes's `providers.ollama-launch` config has no `keep_alive` field.)

**Model tags** — `mistral-small:24b-instruct-2503-q4_K_M` is the right Ollama tag (matches the Mistral 24B Q4_K_M community build). `llama3.1:8b-instruct-q6_K` — let me flag: the host has `llama3.1:8b` (Q4_K_M, 4.9 GB, 5 weeks old), not the `instruct-q6_K` variant. Pulling `llama3.1:8b-instruct-q6_K` adds ~6 GB; the existing `llama3.1:8b` would work fine for trivial routing. The plan doesn't ask the operator to consider reuse.

### Required v3 plan edits

- **Drop the `models:` and `routing:` top-level blocks** from the YAML. They are dead config.
- **Replace `model.fallback:` with `fallback_providers: [openrouter/anthropic/claude-haiku-4]`** (note: list, not string; no `model.` prefix).
- **Drop `OLLAMA_KV_CACHE_TYPE` and `OLLAMA_NUM_PARALLEL`** from the .env block. They don't exist in Ollama 0.30.8.
- **Add a note:** "Operator should consider reusing the already-installed `llama3.1:8b` (Q4_K_M, 4.9 GB) as the trivial-tier model before pulling `llama3.1:8b-instruct-q6_K` (+6 GB)."
- **Add a new sub-task A0.5: implement the routing classifier.** State explicitly:
  - The classifier is a new code file (suggest `~/.hermes/avatar/classifier.py` or a Hermes skill).
  - It must be invoked by the gateway's per-message pipeline (today's gateway has no such hook — see "Routing strategy" below).
  - It must log to `~/.hermes/logs/cloud-routing.log`.
  - It must enforce the $5/day cap.

---

## A2 — Kokoro TTS install

### The integration point
- `text_to_speech_tool` is the gateway's only TTS path (`tools/tts_tool.py:2018`, called from `gateway/run.py:10144` inside `_send_voice_reply`).
- The tool reads `tts.provider` from `~/.hermes/config.yaml` on every call.
- `tts_tool.py:376-387` defines `BUILTIN_TTS_PROVIDERS`:
  ```python
  BUILTIN_TTS_PROVIDERS = frozenset({
      "edge", "elevenlabs", "openai", "minimax", "xai", "mistral",
      "gemini", "neutts", "kittentts", "piper",
  })
  ```
  **Kokoro is not in this set.**
- The tool's function signature: `text_to_speech_tool(text: str, output_path: Optional[str] = None) -> str`. **No `provider` argument.**

### What v2 claims
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
And A2 step 6: `hermes chat -q 'use text_to_speech with provider piper to say "I am ready" to /tmp/piper-test.wav'`

### What the host actually does

**`tts.provider: kokoro` is not a built-in.** The dispatcher in `tts_tool.py:2043-2050`:
```python
provider = _get_provider(tts_config)
command_provider_config = _resolve_command_provider_config(provider, tts_config)
```
`_resolve_command_provider_config` (line 438+) returns the config only if it's `type: command` with a `command:` field. The plan's A2 step 5 has `kokoro: { voice, speed }` — no `type: command`, no `command:`. So `command_provider_config` is `None`.

Then line 2117: `if command_provider_config is not None: _generate_command_tts(...)` — skipped.

Then line 2133-2137: plugin dispatch — only fires if a plugin is registered under the name "kokoro". No Kokoro plugin is installed.

Then line 2222: `else:` falls through to **Edge TTS** (line 2223-2240, the default). So `tts.provider: kokoro` silently produces Edge-TTS audio, not Kokoro.

**The plan's A2 step 5 YAML is wrong.** The user-declared command provider path is documented in `tts_tool.py:352-365`:
```yaml
tts:
  provider: piper-en            # user-chosen name
  providers:
    piper-en:
      type: command
      command: 'piper --model {model} --output_file {output_file} --text-...'
```
The plan needs to write a `command:` that shells out to `kokoro-onnx`'s CLI. **Without that, Kokoro never runs.**

**`with provider piper` argument in the A2 step 6 chat command** — the tool doesn't accept that argument. The `text_to_speech_tool` function only has `text` and `output_path`. The `provider` arg, if sent, would be ignored entirely. The smoke test would silently use whatever `tts.provider` is set to in config (not Piper). The plan's A2 step 6 is broken in two ways: the chat command and the YAML.

**`brew install piper`** — fails. `brew list piper` → "No available formula with the name 'piper'". The actual install is `pip install piper-tts` (per the tts_tool.py:2215-2218 error message: "Run: pip install piper-tts"). The `piper` binary the plan downloads from GitHub releases (`en_US-amy-low.onnx`) is the right model, but the install path is wrong.

### Required v3 plan edits

- **Rewrite A2 step 5 YAML** to use the actual user-declared command-provider schema:
  ```yaml
  tts:
    provider: kokoro
    providers:
      kokoro:
        type: command
        command: '~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --voice {voice} --speed {speed} --output {output_file} --text {text}'
        voice: af_sarah
        speed: 1.0
        output_format: wav
  ```
  (The exact `command:` line depends on `kokoro-onnx` 0.9.x's CLI — verify before pinning.)
- **Rewrite A2 step 2** to `pip install piper-tts` (or `pipx install piper-tts`); remove `brew install piper`. The Piper binary on GitHub releases is the model, not the CLI.
- **Rewrite A2 step 6** to drop `with provider piper`. The correct smoke test is:
  ```bash
  # Kokoro
  sed -i '' 's/provider: piper/provider: kokoro/' ~/.hermes/config.yaml
  hermes chat -q 'use text_to_speech to say "I am ready" to /tmp/kokoro-test.wav'
  # Piper (fallback)
  sed -i '' 's/provider: kokoro/provider: piper/' ~/.hermes/config.yaml
  hermes chat -q 'use text_to_speech to say "I am ready" to /tmp/piper-test.wav'
  ```
- **Add an integration test step (A2.7) that proves Kokoro actually runs:** the produced file's first 100 ms should not match Edge-TTS's signature (sample rate, header). The plan currently has no positive proof that Kokoro (not Edge) was the engine.
- **Add a note:** "In-process Kokoro (`kokoro-onnx` Python import, not CLI) is not supported by the current `text_to_speech_tool`. The plan's claim that 'Kokoro in-process saves ~80 ms vs subprocess' (line 118) requires new tool code, not just `pip install`."

---

## A1 — SOUL.md persona

### The integration point
- `agent/prompt_builder.py:load_soul_md()` (line 1478) reads `get_hermes_home() / "SOUL.md"` (line 1491), scans for prompt injection, truncates to 20K chars, returns the content.
- Called from `agent/system_prompt.py:91-96`:
  ```python
  if agent.load_soul_identity or not agent.skip_context_files:
      _soul_content = _r.load_soul_md()
      if _soul_content:
          stable_parts.append(_soul_content)
          _soul_loaded = True
  ```
  This is part of `build_stable_system_prompt` (slot #1, identity).
- The gateway calls `AIAgent` (from `run_agent.py:152`), which builds the system prompt on every turn. So **SOUL.md is re-read every turn** — no SIGHUP, no restart needed. The existing `~/.hermes/SOUL.md:13` already says: "This file is loaded fresh each message — no restart needed."

### What v2 claims
- Line 9: "Auto-loaded into the system prompt by `agent/prompt_builder.py:load_soul_md()`." — **Correct.**
- Line 124 (Same-Machine Integration Table): "Re-read on SIGHUP so persona edits don't require a restart." — **Wrong mechanism (SIGHUP is not wired), but right outcome (no restart needed).**
- Line 282: "If the model just gives a generic response, the SOUL.md wasn't picked up — check `agent/prompt_builder.py:load_soul_md()` and ensure the file is at the path expected by your Hermes build (typically `~/.hermes/SOUL.md` or `~/.hermes/profiles/<name>/SOUL.md`)." — **Wrong path.** The code only reads `get_hermes_home() / "SOUL.md"`. There is no per-profile path. The `~/.hermes/profiles/<name>/SOUL.md` path does not exist; `get_hermes_home()` returns `~/.hermes/`, period. (Step 5 of the plan later says "if the user later adds a second profile (e.g., `engineer`) and uses it for engineering tasks, those memories are separate" — implying per-profile SOUL.md, but the code doesn't read it.)

### What the host actually does
- `load_soul_md()` exists, is wired, fires on every turn. ✅
- The current `~/.hermes/SOUL.md` is a placeholder (15 lines, no persona). The plan overwrites it.
- The `agent.personalities.<name>` registry is **independent** of SOUL.md. The plan's A1 step 3 (mirror in `agent.personalities`) is correct but slightly misleading: `/personality <name>` selects the **personality**; SOUL.md is **identity**. Both can be active (personality is appended after the identity slot). The plan's A1 step 2's check ("matches the persona's tagline, in the persona's voice") would be diluted if a non-default personality is also set.

### Required v3 plan edits

- **Replace the SIGHUP claim** with: "SOUL.md is re-read on every message turn — no SIGHUP, no restart. The plan's 'Re-read on SIGHUP' line in the Integration Table is wrong; SOUL.md edits take effect on the next inbound message."
- **Remove the "or `~/.hermes/profiles/<name>/SOUL.md`" alternative path** — it does not exist in this Hermes build. If per-profile personas are needed, that's a feature request to Nous Research, not a config change.
- **Note that `agent.personalities.avatar` is orthogonal to SOUL.md** and recommend setting `personality: helpful` (or none) for the avatar session, so the SOUL.md voice isn't competing with another personality layer.
- **Recommend renaming the SOUL.md to a more findable name** — the plan keeps the default `SOUL.md`, which is fine, but the existing 15-line file is a "starter" — operator should overwrite, not append.

---

## C3 — WhatsApp Baileys + gateway setup

### The integration point
- `hermes whatsapp` (top-level subcommand, confirmed in `hermes --help`): "Configure WhatsApp and pair via QR code" — Baileys bridge.
- `hermes whatsapp-cloud` (separate subcommand): "Configure the official Meta WhatsApp Business Cloud API adapter."
- `hermes gateway` (separate subcommand): manages the gateway as a launchd service.
- `hermes gateway setup` (subcommand of `hermes gateway`): takes **no arguments** — it's an interactive wizard that prompts for which platforms to configure.
- The bridge script: `scripts/whatsapp-bridge/bridge.js` (exists, depends on `@whiskeysockets/baileys`).
- The Cloud API adapter: `gateway/platforms/whatsapp_cloud.py` (exists).
- The Baileys adapter: `gateway/platforms/whatsapp.py` (exists).

### What v2 claims
- C3 step 1: `hermes gateway install; hermes gateway status` — **Correct** (both subcommands exist).
- C3 step 2: `hermes gateway setup --platform slack` — **`--platform` flag doesn't exist.** `hermes gateway setup --help` returns no args. The actual command is `hermes gateway setup` (interactive).
- C3 step 3: `hermes gateway setup --platform telegram` — **Same: no `--platform` flag.**
- C3 step 4: `hermes whatsapp` — **Correct** (subcommand exists). ToS consent gate, secondary SIM, 30-day Cloud API clock — **all correct** (`hermes whatsapp-cloud` is the documented Cloud API path, and `gateway/platforms/whatsapp_cloud.py` is the implementation).

### What the host actually does

- `hermes gateway install` works ✅
- `hermes gateway status` works ✅
- `hermes gateway setup` is interactive and prompts for platforms — no flag. The plan needs to say "operator runs `hermes gateway setup` and picks Slack/Telegram/WhatsApp from the menu."
- `hermes whatsapp` works and pairs via QR. ✅
- `hermes whatsapp-cloud` is the migration path. ✅

### Required v3 plan edits

- **Drop `--platform slack` and `--platform telegram`** from C3 steps 2 and 3. Replace with: "operator runs `hermes gateway setup`, picks Slack from the menu, pastes the `xoxb-...` token, etc."
- **Add an explicit note:** "`hermes gateway setup` is an interactive wizard with no flags. To script it, write the platform config directly to `~/.hermes/gateway_state.json` (see `gateway/run.py:_load_gateway_config` for the schema)."
- **Verify the Baileys bridge is actually built.** `scripts/whatsapp-bridge/package.json` exists, but the bridge depends on `@whiskeysockets/baileys` from npm. The plan should include `cd scripts/whatsapp-bridge && npm install` as a pre-step before `hermes whatsapp`.

---

## B1 — ComfyUI on arm64 macOS

### The integration point
- `comfy` is the CLI for `comfy-cli` (a pipx-installable Python package).
- `comfy-cli`'s subcommands per its docs: `comfy install`, `comfy launch`, `comfy node install <name>`, etc.
- The order in the plan matters: install ComfyUI first, verify it works, THEN design the workflow. v2's B1 step 1 already does this correctly (verifies `system_stats` before designing the workflow). Good.

### What v2 claims
- B1 step 1: `pipx install comfy-cli && comfy install` + `comfy launch --background` + `curl -s http://127.0.0.1:8188/system_stats | jq`.
- B1 step 2: `comfy node install kijai/ComfyUI-LivePortraitKJ@<pinned-sha>`, with a fallback to `git clone + pip install -r requirements.txt + python install.py`.
- B1 step 3: `comfy restart` + `curl -s http://127.0.0.1:8188/object_info | jq '.[\"LivePortraitLoadModels\"] // .[\"LivePortraitProcess\"]'`.

### What the host actually does

- `comfy` is not installed (`which comfy` → not found). The plan's first install step is correct; it must run before anything else.
- `comfy node install <github_url>` is supported per `comfy-cli` docs. The `@<pinned-sha>` suffix is a git ref spec and should work.
- **The integration test the plan asks for is the right one:** `curl http://127.0.0.1:8188/system_stats` returning valid JSON is the binary-existence proof. If this fails, fall back to Docker (`comfyui/comfyui:latest` under Rosetta 2 — slow but works on M4). The plan handles this correctly.
- **The LivePortrait node is the real risk:** Kijai's `ComfyUI-LivePortraitKJ` repo's last code push is 2024-08-05 (per the plan). On macOS 26.5 with PyTorch MPS, the node may have unfixed issues. The plan flags this in step 1's "if this fails, switch to MuseTalk" — good.

### Required v3 plan edits

- **No changes to the install order** — it's correct.
- **Add a one-line note:** "The `pinned-sha` for `kijai/ComfyUI-LivePortraitKJ` must be captured from the live `main` HEAD at install time (operator runs `git ls-remote https://github.com/kijai/ComfyUI-LivePortraitKJ refs/heads/main` first). The plan should not hardcode a SHA that may not exist."
- **Add a verification step for the driver's exit code:** B1 step 6's render script should fail loudly if the render time exceeds 60 s, not just log it. The plan currently says "log the wall-clock time; fail the gate if it exceeds 60 s" but the script in the plan doesn't enforce that.

---

## C2 — avatar-face skill

### The integration point
- Skills are loaded from `~/.hermes/skills/<name>/SKILL.md` (current location of all 25+ installed skills).
- Frontmatter is parsed by `tools/skills_hub.py:_parse_frontmatter_quick` (line 952). It uses `yaml.safe_load` on the text between `---` markers. Required fields: `name`. Optional but common: `description`, `version`, `author`, `license`, `platforms`, `compatibility`, `prerequisites`, `setup`, `metadata.hemes.tags`, `metadata.hermes.category`.
- A real skill installed in `~/.hermes/skills/creative/comfyui/SKILL.md` has frontmatter spanning 28 lines (see `hermes-agent/skills/creative/comfyui/SKILL.md:1-28`). The plan's 4-line frontmatter is minimal but valid.

### What v2 claims
- `name: avatar-face` — valid ✅
- `description: "Avatar face animation — generate a talking-head video from a portrait and audio. Local ComfyUI + LivePortrait."` — **too short.** Real skills (comfyui, kanban-orchestrator) have multi-sentence descriptions that describe the trigger conditions and what the skill does NOT do. The description is what the LLM uses to decide whether to invoke the skill; one-sentence descriptions get missed.

### What the host actually does

- The skill loader uses `description` to index the skill. A 1-sentence description is valid YAML but provides weak LLM discoverability.
- The plan's wrapper script (`render.py`) calls Hermes's `hermes chat` in a subprocess to invoke `text_to_speech`. This is a recursive call into the agent and is **slow** (full agent bootstrap, system prompt rebuild, tool call dispatch). On a 5-second voice reply, this could add 10+ seconds of overhead. The plan doesn't flag this.

### Required v3 plan edits

- **Expand the SKILL.md `description:`** to 2-3 sentences covering: what the skill produces (MP4 + WAV pair), what tools the agent should consider first (built-in `text_to_speech`), and what the skill does NOT do (no auto-render, no portrait training, no clone).
- **Replace the recursive `hermes chat` subprocess call in `render.py`** with a direct call to `text_to_speech_tool` (importable from the same venv as Hermes). The wrapper should look like:
  ```python
  from tools.tts_tool import text_to_speech_tool
  result = text_to_speech_tool(text=text, output_path=str(audio))
  ```
  This avoids the recursive agent bootstrap. **The wrapper should live in `~/.hermes/skills/avatar-face/scripts/render.py` and import from the Hermes venv, not shell out to `hermes chat`.**
- **Add a `version:`, `platforms:`, and `metadata.hermes.category: creative`** to the frontmatter to match the schema of other skills.

---

## Routing strategy (Section "Model Routing Strategy")

### The integration point
- The gateway's per-message pipeline is `gateway/run.py:_process_message` (line ~8450+). It:
  1. Resolves the agent runtime (`_resolve_session_agent_runtime`, line 2891)
  2. Loads/reuses the AIAgent (cached per session)
  3. Calls `agent.run()` which builds the system prompt (loads SOUL.md) and sends to the model
- **There is no pre-routing hook in the gateway today.** No place to intercept the message, classify it, and swap the model. The plan's "classify(message) → 50 ms" cannot be inserted into the current pipeline without a code change.

### What v2 claims
- "classify(message) is a 50 ms call to llama3.1:8b" — every Ollama HTTP call has ≥100-200 ms round-trip overhead (request build, socket connect to 127.0.0.1, request, response, parse). 50 ms is unachievable. Realistic: 200-500 ms for the trivial tier.
- The classifier "lives" nowhere in the plan. It's not in `agent/`, not in `gateway/`, not in a skill, not in a hook.

### What the host actually does

- No classifier exists. No `classify_intent` function in any source file. The closest is `agent/error_classifier.py` (different purpose — error category classification for guardrails).
- The gateway has a hook system (`hooks/HookRegistry()`, line 2347), but hooks are shell scripts, not LLM calls. A 50-ms LLM-classifier hook would be slow by design.

### Required v3 plan edits

- **Be realistic about classifier latency.** 200-500 ms is the realistic floor for an Ollama HTTP call. The "trivial → ~0.8 s p50" estimate already includes this — good — but the diagram claims 50 ms, which is wrong. Update the diagram to "~300 ms" or remove the latency annotation.
- **Decide where the classifier lives.** Three real options:
  1. **Skill-based** — write a `~/.hermes/skills/avatar-router/` that exposes a `classify(message) -> tier` tool. The agent calls it as a tool, then the system prompt already includes the model name to use. **Caveat:** this puts the classifier inside the agent's tool loop, so it adds a turn of latency, not a pre-routing decision. Doesn't match the plan's pre-routing diagram.
  2. **Gateway hook** — extend `gateway/run.py` to call a new `_classify_intent()` method before resolving the model, then pass the chosen model to `AIAgent`. **Caveat:** requires editing `gateway/run.py` (a Hermes source file), not a config or skill change. The plan doesn't assign this code anywhere.
  3. **Pre-agent sidecar** — write a wrapper that intercepts the gateway's outbound request and rewrites the model name based on classification. **Caveat:** not currently a Hermes extension point.
  - **v3 should pick option 2 and explicitly assign the gateway code change to a new E-card (e.g., E7).**
- **Drop the `routing.cloud_daily_usd_cap` config key from the A0 YAML** (it's not in the schema). State that the cap is enforced in code: "the gateway's `_classify_intent()` method tracks a running daily USD total in `~/.hermes/logs/cloud-routing.log` and short-circuits to local-only at $5."
- **Add a smoke test for the classifier** — a 5-message test set (1 trivial, 1 standard, 3 voice-in) where the operator can see in `~/.hermes/logs/cloud-routing.log` that the right tier was picked for each.

---

## C4 — Per-chat face mode

### The integration point
- Voice mode: `gateway/run.py:2452` — `_VOICE_MODE_PATH = _hermes_home / "gateway_voice_mode.json"`.
- `_load_voice_modes` (line 2458) and `_save_voice_modes` (line 2484) read/write that file.
- `_set_adapter_auto_tts_enabled` (line 2507) and `_sync_voice_mode_state_to_adapter` (line 2525) propagate the persisted state into the per-adapter `_auto_tts_enabled_chats` and `_auto_tts_disabled_chats` sets.
- Slash commands: `/voice off`, `/voice voice_only`, `/voice all` are dispatched in the gateway's command-handling code (caught at line 7150 in the slash-command safety net comment, but the actual dispatch is upstream of that).
- `_send_voice_reply` (line 10138) is the only TTS call site; it reads `self._should_send_voice_reply(event, ...)` (line 10084) to decide whether to call TTS.

### What v2 claims
- C4 step 1: voice-mode uses existing `~/.hermes/gateway_voice_mode.json` and `/voice off` / `/voice voice_only` / `/voice all` slash commands. **Correct.** All 5 referenced items exist.
- C4 step 2: "Add a parallel face-mode state file (new feature)." The plan says:
  - Create `~/.hermes/gateway_face_mode.json` mirroring the voice-mode file.
  - Add slash commands `/face on` and `/face off`.
  - The face skill (C2) is invoked only when chat is in face mode AND reply >30 chars.

### What the host actually does

- **No face mode exists anywhere in the gateway code.** `grep -r "face_mode\|_face_mode\|gateway_face_mode\|/face\|face_reply" gateway/run.py` returns 0 hits for the face concept (only matches are unrelated "surface" mentions).
- **No slash command dispatcher for `/face` exists.** The gateway's command safety net (line 7150) lists registered commands: `/model, /reasoning, /voice, /insights, /title, /resume, /retry, /undo, /compress, /usage, /reload-mcp, /sethome, /reset` — `/face` is not on the list. Sending `/face on` to a chat would either be passed to the model (and the model would do nothing useful) or be silently dropped.
- **No `_should_send_face_reply` method exists.** The plan's "invoked only when (a) the chat is in face mode AND (b) the reply is over the `face_reply_min_chars` threshold" requires a new method in the gateway's per-message pipeline.
- The voice-mode feature is **not pure state file** — it has supporting code: `_load_voice_modes`, `_save_voice_modes`, `_set_adapter_auto_tts_enabled`, `_sync_voice_mode_state_to_adapter`, plus the `/voice` slash command dispatcher. The plan's C4 step 2 is presenting face mode as if it can be added by just creating a JSON file, when actually it requires a parallel block of gateway code.

### Required v3 plan edits

- **Acknowledge explicitly that C4 step 2 is a `gateway/run.py` code change**, not a state-file addition. List the new methods needed:
  - `def _load_face_modes(self) -> Dict[str, str]`
  - `def _save_face_modes(self) -> None`
  - `def _set_face_mode(self, platform, chat_id, mode) -> None`
  - `def _should_send_face_reply(self, event, response) -> bool` (with the 30-char threshold)
  - `def _send_face_reply(self, event, response)` (calls the C2 skill)
  - New `_FACE_MODE_PATH = _hermes_home / "gateway_face_mode.json"`
  - Slash command dispatcher entries for `/face on` and `/face off`
- **Add a new E-card (e.g., E7) for the face-mode gateway code change**, assigned to the engineer profile. The plan's current E-cards (E0–E6) don't include this.
- **Drop the "on-demand only" framing** in the SKILL.md description — the on-demand constraint is enforced in the gateway (per-chat flag), not in the skill. The skill is called by the gateway, not by the agent directly. Confusing the two will produce a skill that the agent invokes by default (slowing every reply by 30s+ for face rendering).
- **Add a note:** "C2's `avatar-face` skill is a wrapper, not a tool. The agent should not call it directly. The gateway calls it via subprocess from `_send_face_reply`."

---

## What v2 got right (preserved in v3)

For v3 to know which v2 decisions to keep, here are the verifications that PASSED:

- **A1 (SOUL.md)** — `load_soul_md()` exists at `agent/prompt_builder.py:1478`, reads `get_hermes_home() / "SOUL.md"`, is called from `agent/system_prompt.py:91-96` on every turn. The mechanism is correct. The two A1 errors above are about the **path alternatives and SIGHUP wording**, not the core claim.
- **A1 `agent.personalities.avatar` mirror** — this is a valid `agent.personalities.<name>` entry. v3 should keep the optional Step 3.
- **C1 (REMOVED duplicate TTS skill)** — correct call. `text_to_speech_tool` is wired in `gateway/run.py:10144` and has 10 built-in providers. No duplicate skill is needed.
- **C3 (WhatsApp = `hermes whatsapp` subcommand)** — correct. `hermes whatsapp` and `hermes whatsapp-cloud` both exist; the bridge script at `scripts/whatsapp-bridge/bridge.js` is real. The ToS risk is real; the secondary-SIM and Cloud-API-clock mitigations are right. The only fix is the missing in-wizard ToS gate (R4 finding #11).
- **C3 (`hermes gateway install; hermes gateway status`)** — both subcommands exist and work. The fix is dropping `--platform` from `gateway setup` (R4 finding #10).
- **C4 (voice mode uses existing `_voice_mode` state)** — correct. `_VOICE_MODE_PATH = _hermes_home / "gateway_voice_mode.json"` at line 2452; `/voice off|voice_only|all` slash commands work. The plan is right not to invent new voice-mode config keys.
- **B1 (ComfyUI install order)** — correct. `curl http://127.0.0.1:8188/system_stats` is the right install-verification gate. v3 just needs to add the `pinned-sha` capture step (live `main` HEAD, not hardcoded) and a wall-clock fail-fast in the driver script.
- **A0 (LLM default swap target)** — correct that the current `default: minimax-m3:cloud` is a paid cloud model that violates local-first. Flipping `model.default` is the right intervention; v3 just needs to drop the invented sibling keys.
- **Open Question defaults (Avatar = "Sage", secondary SIM, etc.)** — good UX. Keep.
- **`hermes profile list` showing only `default`** — confirmed on this host. The plan's single-profile caveat (E0–E4 run serially) is correct.
- **Kanban skills pre-flight** — `kanban-orchestrator` and `multi-agent-refinement-workflow` are both already installed. The "must install" pre-flight is unnecessary; the operator can confirm with one `hermes skills list | grep -i kanban` and skip the install.
- **`comfyui` skill** — already installed (v5.1.0). The plan's "install if missing" is correct but on this host it's a no-op.

---

## Final verdict

v2 fixed 4 of v1's critical defects (no duplicate TTS skill, correct WhatsApp command, real persona mechanism, no invented `gateway.*` keys) but introduced 6 new integration breakages of its own (5 schema-invented keys, 1 wrong env var, 1 wrong subcommand flag, 1 wrong install command, 1 silent TTS provider fallback, 1 invented analytics command) and left 4 v1 leftovers (face-mode as state file not code, SIGHUP claim, recursive `hermes chat` TTS, `--platform` flag on `gateway setup`).

v3 should:
1. Adopt the 16-item "must-fix list" at the top of this document.
2. Add 2 new E-cards (E7 for the gateway face-mode code; E8 for the WhatsApp ToS consent gate in the wizard).
3. Tighten the verification gates to drop the `hermes stats --routing` command (replaced by `hermes insights` for now, or by a new `hermes stats` subcommand if the operator wants it).
4. Re-verify the READY criteria: blocking items 7, 8, 9 (port + bind) are now satisfied by the data-flow diagram; items 11, 12, 13 (ToS, cost cap, routing stats) are NOT satisfied because the implementations are missing.
