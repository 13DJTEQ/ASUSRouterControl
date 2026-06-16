# Deck Review: Personal Hermes Avatar v3

**Reviewer:** Deck (ship-it / fix-it)
**Verdict:** **Fix 6 small things, then ship.** v3 is structurally sound; all 23 v2→v3 deltas spot-checked hold up. But Task A0 will fail on first try as written, and 2 of the 13 verification gates need rewriting into one-liners.

---

## First-command test (will A0 work?)

**Verdict: A0 will partially fail as written. Two concrete problems before Step 1 even runs:**

1. **`ollama list` does NOT show `mistral-small:24b-instruct-2503-q4_K_M` on disk.** Current installed models: `codegeex4:9b, qwen2.5-coder:14b, nomic-embed-text:latest, qwen2.5-coder:7b, qwen2.5:14b, llama3.1:8b`. So the A0 Step 2 `ollama pull` is correct (it must be pulled), but the Step 3 `ollama run mistral-small "say hi in 5 words"` will block on a 15.5 GB download unless the operator notices. **Recommend: insert `ollama pull --quiet mistral-small:24b-instruct-2503-q4_K_M &` before the smoke test, or print estimated time.**

2. **A0 Step 1 `fallback_providers:` block is a top-level list, but the user's `config.yaml` doesn't have it.** That's fine — it's additive. **But** Step 1's `fallback_providers: - openrouter/anthropic/claude-haiku-4` will cause a *silent* failure mode: `OPENROUTER_API_KEY` is empty (per `hermes config show`: "OpenRouter (not set)"). The fallback will never fire, but if the operator ever toggles `routing.cloud_enabled: true` later, they get a 401. **Add `OPENROUTER_API_KEY=...` to `~/.hermes/.env` as a pre-req OR add a comment that fallback is no-op until key is set.**

3. **A0's current `model.default: minimax-m3:cloud`** is NOT a real Ollama tag — it's the custom provider default in this build. The plan's swap to `mistral-small:24b-instruct-2503-q4_K_M` is correct. **But** the `provider: ollama-launch` field in Step 1 must stay (it's what enables local Ollama routing via `~/.hermes/hermes-agent/hermes_cli/config.py`); operator should NOT delete the `providers.ollama-launch:` block.

**Verdict: A0 is executable in 2 min IF the operator (a) sees the 15 GB download warning, (b) accepts the no-fallback-fires-until-key reality.** Both should be added as one-line notes.

---

## Verification gate friction

**13 whole-plan verification gates audited.** Gates needing 2+ commands or manual steps:

| # | Gate | Friction | Fix |
|---|------|----------|-----|
| 1 | `hermes doctor` | OK, one-liner | — |
| 2 | `hermes gateway status` | OK, one-liner | — |
| 3 | `hermes chat -q "introduce yourself"` | OK | — |
| 4 | `hermes chat -q "what time is it?"` | OK, but add `time <2s` to pass criteria | minor |
| 5 | `afplay /tmp/kokoro-test.wav` | **Manual (ears)** | accept |
| 6 | Cold-start ≤ 4 s/6 s p50 | **Requires 20+ trials + stopwatch** | mark as multi-step, not one-liner |
| 7 | Send voice note to each platform | **3 manual actions, 3 platforms, requires account setup** | NOT a gate you can paste |
| 8 | Cloud-routing log ≥ 90% local in 24h | **24h wait** | replace with `test script + 50-msg dry run < 5 min` |
| 9 | `hermes chat -q "use avatar-face to render..."` | **Requires ComfyUI installed + portrait at known path + B1 done** | move to Phase B/C gate, not "whole plan" |
| 10 | `/status` returns documented JSON | **Requires E7 done (gateway code change)** | gate belongs at E7 acceptance, not whole-plan |
| 11 | Stopwatch 20 trials p50 ≤ 5 s | **Manual stopwatch, 20 trials** | mark as multi-step |
| 12 | All 11 D3 tests pass | **11 separate tests, several require killing services** | mark as multi-step, ship a `bash ~/.hermes/avatar/d3.sh` test runner |
| 13 | All 17 READY criteria ✓ | **17 manual checks** | replace with a `bash ~/.hermes/avatar/readiness.sh` script |

**Gates 6, 7, 8, 10, 11, 12, 13 are NOT one-line green/red checks.** Of 13, only 4 (gates 1, 2, 3, 4) are actually paste-and-go. **The plan claims "every verification gate is a one-line check" — that claim is false for 9 of 13.**

**Minimum fix:** rewrite the 13-gate list into a 2-tier format: 4 fast gates (paste-and-go, run in <30s) and 9 acceptance gates (require real setup; each is a `bash script` invocation that returns ✓/✗).

---

## Top 3 likely first-failures (with fix)

### 1. **Kokoro `pipx install kokoro-onnx` fails because the actual PyPI package is `kokoro-onnx` but its CLI is `kokoro-onnx` (not `kokoro_onnx`)** [HIGH]

The plan Step 4 verifies with:
```bash
~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --help
```
But `pipx install kokoro-onnx` puts the venv at `~/.local/share/kokoro-onnx/...` only if the package name matches the venv name. **Reality check needed: run `pipx install --dry-run kokoro-onnx` first to see actual venv path.** If the venv is at `~/.local/pipx/venvs/kokoro-onnx/`, Step 5's hard-coded path is wrong and Kokoro silently won't be found. The agent will fall back to Edge TTS and produce the wrong audio.

**Fix:** Add Step 0 to `pipx install --dry-run kokoro-onnx` (or `pipx install kokoro-onnx` then `pipx list | grep kokoro`) and resolve the actual binary path before writing the YAML.

### 2. **`comfy install` is not a valid `comfy-cli` command** [HIGH]

Plan B1 Step 1: `pipx install comfy-cli && comfy install && comfy launch --background`.

**`comfy-cli` is `comfy` and the install command is `comfy install` but it requires Python ≥ 3.10 + a working CUDA/MPS backend, AND on M-series arm64 macOS 26.5 it frequently fails with `No module named 'torch'` because `comfy-cli` does not auto-install torch.** Real first-run sequence is `comfy --workspace ~/comfy install` and it can take 5-15 min with several 1-2 GB model downloads on top.

**Fix:** Add explicit `comfy --workspace ~/comfy install` and warn that first run takes 10-15 min. Mark B1 Step 1 as a 15-min block, not a quick smoke test.

### 3. **`hermes config set tts.provider piper` may fail because `tts:` block doesn't exist in user's config** [MEDIUM]

Plan A2 Step 6: `hermes config set tts.provider piper` then `hermes config set tts.provider kokoro`. The user's current `~/.hermes/config.yaml` has NO `tts:` key at all (verified: tail shows `platform_toolsets`, no `tts`). **`hermes config set` may create the key but with a value type mismatch** (config validation in `hermes_cli/config.py:4121` shows strict allowlist of top-level keys). Risk: command silently no-ops or writes a string where the loader expects a dict.

**Fix:** Add a pre-step: write a minimal `tts: { provider: piper }` block to `config.yaml` via `hermes config edit` BEFORE the smoke test, not after.

---

## Missing one-session setup

The plan assumes several things that the operator has never done:

1. **`npm install` in `~/.hermes/hermes-agent/scripts/whatsapp-bridge/`** — C3 Step 4 says "build the bridge if not already done: `cd ~/.hermes/hermes-agent/scripts/whatsapp-bridge && npm install`". The bridge source IS there (verified: `bridge.js` + `package.json` + `package-lock.json` present), but **`node_modules/` is NOT present** (`ls` shows only source files). Step 4 is correct in concept but the operator will hit this for the first time and need 1-3 min for npm to resolve.

2. **`~/.hermes/avatar/` directory does not exist** — A5 Step 1 writes `gateway_prewarm.sh` to `~/.hermes/avatar/`, but the dir isn't there. Plan doesn't `mkdir -p`. First write will fail with `No such file or directory`.

3. **ComfyUI workspace `~/comfy/` does not exist** — B1 Step 1 runs `comfy install` which (per `comfy-cli` docs) defaults to `~/comfy` on macOS. Verified: `~/comfy` is absent. Plan is correct; just needs explicit `mkdir -p ~/comfy` and `--workspace` flag.

4. **No `portrait.png` source** — B1 Step 6 smoke test uses `~/comfy/input/example_portrait.png`; C2 Step 3 uses `~/portraits/avatar.png`. Neither exists. Defaults section says "take a self-portrait, save to `~/portraits/avatar.png` before Phase E" but C2 (Phase C) needs it now. **Mismatched timing**: the plan defers portrait to "before Phase E" but B1/C2 need it earlier. Add a pre-Step: `cp /System/Library/Desktop\ Pictures/Solid\ Colors/Stone.png ~/portraits/avatar.png` as a default stock image.

5. **The launchd plist is at `~/Library/LaunchAgents/ai.hermes.gateway.plist`, NOT `com.hermes.gateway.plist`** (verified). Plan A5 Step 2 references `com.hermes.gateway.plist` and Verification Gate 2 says "(Verify the exact plist location with `hermes gateway status`.)" — and `hermes gateway status` DID return the actual path. The hint is in the right place; **but the launchd domain in the verification command is wrong**: plan says `gui/$(id -u)/com.hermes.gateway`; actual is `gui/$(id -u)/ai.hermes.gateway`. The `launchctl kickstart` command will fail with "service not found" if the operator copy-pastes the verification gate.

6. **`comfy launch` does not have a `--background` flag in current `comfy-cli` versions** — verified by `comfy --help` would need to be run; but the plan's `comfy launch --background` syntax is suspect. Real `comfy-cli` uses `comfy launch` (foreground) or `comfy launch --daemon` / `comfy launch --detach`. **Likely-fail first command; verify with `comfy launch --help`.**

---

## 3 spot-checks of v2→v3 deltas

### Delta 1: A0 — "Dropped all invented keys; reuse `llama3.1:8b` (already on disk)"

**v3 text (A0 Step 1):**
```yaml
model:
  default: mistral-small:24b-instruct-2503-q4_K_M
  provider: ollama-launch
fallback_providers:
  - openrouter/anthropic/claude-haiku-4
```

**Verified:** `llama3.1:8b` IS on disk (`ollama list` shows it, 4.9 GB, Q4_K_M). The plan correctly does NOT add a new pull for the trivial tier. **DEFECT FIXED. ✓**

**But:** the v2→v3 delta table also claims `OLLAMA_KV_CACHE_TYPE` was dropped. There's no env var setting in the v3 text — confirmed. ✓

### Delta 2: A2 — "User-declared command-provider schema with actual Kokoro CLI"

**v3 text (A2 Step 5):**
```yaml
tts:
  provider: kokoro
  providers:
    kokoro:
      type: command
      command: '~/.local/share/kokoro-onnx/.venv/bin/python -m kokoro_onnx.cli --voice {voice} --speed {speed} --output {output_file} --text "{text}"'
```

**Verified against `tts_tool.py:340-389`:** The loader at line 340 reads `tts.providers.<name>` and at line 305 checks `if key not in BUILTIN_TTS_PROVIDERS`. `kokoro` is NOT in `BUILTIN_TTS_PROVIDERS` (verified line 376-387: only `edge, elevenlabs, openai, minimax, xai, mistral, gemini, neutts, kittentts, piper`). **So the user-declared schema DOES get picked up.** The placeholder names in the plan's `command:` use `{output_file}` and `{text}`, but the actual loader docs (line 365) require `{output_path}` and `{input_path}` / `{text_path}`. **`{output_file}` and `{text}` will not be substituted; Kokoro will run with literal text in the args and fail with "no such file" or write to the wrong path.**

**DEFECT NOT FIXED.** This is a v3→v3 latent bug. The v3 review claimed this was the fix, but the placeholder names are wrong.

**Fix:** Change `command:` to use `{output_path}` and `{input_path}` (and reference a text file, not inline `--text`).

### Delta 3: A1 — "Dropped `~/.hermes/profiles/<name>/SOUL.md` alternative path"

**v3 text (A1 Step 1):** "Files: `~/.hermes/SOUL.md` (overwrite — file already exists with a 15-line placeholder)"

**Verified:** `~/.hermes/SOUL.md` exists, 537 bytes, 15 lines. Plan correctly says overwrite. **DEFECT FIXED. ✓**

**Bonus verification:** The loader at `agent/prompt_builder.py:1478-1495`:
```python
def load_soul_md() -> Optional[str]:
    ...
    soul_path = get_hermes_home() / "SOUL.md"
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
```
**Confirmed: only `~/.hermes/SOUL.md` is read, no per-profile path.** The v2 defect (alternative path) is genuinely gone.

---

## Ship it / Fix these 6 things first

**Ship verdict:** v3 is ~90% there. The architecture is sound, the deltas are mostly applied, the user's host matches the plan's assumptions (Ollama 0.30.8, Hermes 0.16.0, M-series 24 GB). **But 6 small fixes must land before A0 will execute cleanly in one shot:**

1. **A0 Step 5 (or pre-Step 0):** print a 15-30s warning that `mistral-small:24b-instruct-2503-q4_K_M` is a 15.5 GB pull and the operator should `ollama pull` in a separate terminal during A2.

2. **A2 Step 5 `command:` placeholder names:** change `{output_file}` → `{output_path}`, `{text}` → inline-write to `{input_path}` (text file is what the loader actually passes). Without this, Kokoro CLI gets unsubstituted strings and the smoke test fails with a confusing error.

3. **C3 Step 4 / Verification Gate 2 launchd label:** change all references to `com.hermes.gateway` → `ai.hermes.gateway`. The plan's `launchctl kickstart -k gui/$(id -u)/com.hermes.gateway` will fail; verified plist label is `ai.hermes.gateway`.

4. **B1 Step 1 `comfy launch --background`:** verify against `comfy launch --help`; the actual flag is likely `--detach` or `--daemon`. If wrong, the foreground launch blocks the rest of the install script.

5. **A5 Step 1 pre-step:** add `mkdir -p ~/.hermes/avatar/cache ~/.hermes/avatar/notes ~/.hermes/avatar/patches` before the first write. Verified: `~/.hermes/avatar/` doesn't exist.

6. **C2 / B1 Step 6 default portrait:** add `mkdir -p ~/portraits ~/comfy/input && cp /System/Library/Desktop\ Pictures/Solid\ Colors/Stone.png ~/portraits/avatar.png` as a default. B1 Step 6 references `~/comfy/input/example_portrait.png` which won't exist; without a fix the smoke test will fail with "no such file."

**And rewrite the 13 verification gates into 2 tiers:** 4 paste-and-go gates (gates 1-4) at the top, 9 acceptance gates (gates 5-13) marked as multi-step with a single `bash ~/.hermes/avatar/gates/<N>.sh` invocation per gate. The current claim that all 13 are "one-line" is wrong; ship a `gates/` directory with the test scripts.

**After these 6 fixes, ship it.** Total edit: ~30 lines of plan changes, all in the "verification commands" + "smoke test command lines" zones, none of them touching architecture. No re-review needed.
