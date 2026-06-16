# Cost Critique — Personal Hermes Avatar Plan

> **Reviewer:** cost-optimization reviewer (subagent) for `2026-06-15_210524-personal-hermes-avatar.md`
> **Scope:** Every paid service vs. a free local alternative; every free-tier quota vs. projected usage; Wispr Flow substitution analysis; WhatsApp Business API cost estimate; ComfyUI electricity cost estimate.
> **Inputs observed on this host (June 15, 2026):**
> - Ollama running on `127.0.0.1:11434` with 6 local models: `codegeex4:9b`, `qwen2.5-coder:14b`, `qwen2.5-coder:7b`, `qwen2.5:14b`, `llama3.1:8b`, `nomic-embed-text:latest` (~33 GB total, all warm, all free).
> - `~/.hermes/.env` already contains a commented `OPENROUTER_API_KEY=` line — key exists, just uncommented; also has a separate `VOICE_TOOLS_OPENAI_KEY` alias to avoid clashing with OpenRouter.
> - `~/.hermes/config.yaml` shows `stt.enabled: true`, `stt.local.model: base`, `stt.openai.model: whisper-1` already configured. STT is effectively a one-config-line swap, not an install.
> - `~/.hermes/config.yaml` `model.provider: ollama-launch` with `default: minimax-m3:cloud` — note the model name contains `:cloud`. On a vanilla Ollama box this string would 404; on this host it routes to the parent agent's MiniMax-M3 provider. **The plan assumes local Ollama for free, but the *active default* is a paid cloud model the user already has a key for.** That is the single most expensive assumption in the plan and is treated in Finding #1.
> - Slack already wired (tokens present, `platform_toolsets.slack` enabled). No incremental cost.
> - ComfyUI not yet running on 8188 (connection refused) — install is from scratch.
> - `/Applications/Wispr Flow.app` present (v1.5.695, Electron). `Info.plist` shows bundle id `com.electron.wispr-flow`, custom URL scheme `wispr-flow://`, microphone/camera/audio-capture usage strings, NO `LSUIElement`, NO Mach service registration, NO embedded helper binary that exposes a CLI. `Resources/app.asar` is a packed Electron bundle (extraction not possible from the shell in this session; the asar-archive format + standard Electron main-process shape is the only data point).

---

## Findings

### 1. The plan's "local LLM" assumption is broken — the active default is a paid cloud model
- **Annual $ impact:** HIGH. If "cloud only as last resort" is violated by the *default* config rather than a fallback, every turn the gateway takes costs money.
- **Detail:** Plan text says "Local-first. Cloud is last resort — only if a local path fails after one retry." But the user's actual `config.yaml` has `model.default: minimax-m3:cloud` and `model.provider: ollama-launch`. Ollama is running, but `minimax-m3:cloud` is a *cloud* model name (the `:cloud` suffix is the giveaway). It does not exist as an Ollama tag; it routes via the parent agent's MiniMax-M3 custom provider. The plan never tells the operator to fix this. As a result, the persona's *every reply* on every platform is a paid API call, even though 6 free local models are sitting warm in Ollama.
- **Complexity of swap:** Trivial. Change `model.default: llama3.1:8b` (or `qwen2.5:14b` for higher quality) in `~/.hermes/config.yaml`. Add a `fallback: openrouter/anthropic/claude-haiku-4` block in `providers.ollama-launch` if you want a cloud escape hatch. ~30 seconds, no new code.
- **Recommendation:** Add an explicit Phase A0 step that re-points `model.default` to a local Ollama tag and documents the cloud-fallback path. Estimated savings: $5–30/month for low traffic, easily $30–150/month at 50 voice messages/day on a frontier model.

### 2. The plan installs a brand-new `kanban-orchestrator` skill that is not present on this host
- **Annual $ impact:** $0 (free skill) but **opportunity cost** — Phase E is 50% of the plan's value, and the dispatcher it depends on is referenced by name without verification.
- **Complexity:** Low. The skill must be either installed from the Hermes registry or built; no cost in either path.
- **Recommendation:** Verify `hermes skills list | grep -i kanban` returns something. If empty, treat Phase E as blocked until that skill (or an equivalent `delegation` toolset) exists.

### 3. STT install path is wasted work — local STT is already configured
- **Annual $ impact:** $0 saved, but the plan spends a full Task (A3) "installing" faster-whisper, which is already wired (`stt.enabled: true`, `stt.local.model: base`). The only thing missing is the dependency in the active venv.
- **Complexity:** Trivial. `.venv/bin/pip install faster-whisper` and a smoke test.
- **Recommendation:** Demote Task A3 to a "verify" sub-task under A2. The first `transcribe()` call will download the `base` model (~150 MB) into `~/.cache/huggingface/`. No recurring cost.

### 4. ComfyUI + LivePortrait electricity cost is negligible
- **Annual $ impact:** ≈ $0.45/month, ≈ **$5.40/year**.
- **Calculation:** 50 renders/day × 45 s/render (midpoint of the 30–60 s range stated in the prompt) = 2,250 s/day = **37.5 min/day** under load. 30 W × 37.5/60 h = **18.75 Wh/day** = **0.5625 kWh/month** × $0.30/kWh = **$0.17/month** for the CPU/GPU. Add ~5 W for the always-on ComfyUI server (idle between renders, 24 h × 30 d = 720 h × 0.005 kW = 3.6 kWh × $0.30 = $1.08/month). **Total: ~$1.25/month, $15/year.** (I was given 30W as the under-load figure, but a 14B LivePortrait render will spike RAM and CPU; the 30W figure is a reasonable average for the active render window. Standby idle power on M4 is closer to 5W; the always-on server line is the bigger of the two by an order of magnitude.)
- **One-time model download:** ~6 GB × typical Comcast/AT&T broadband (no per-GB cellular charge) → $0.
- **Complexity:** N/A — the only real cost in Phase B is disk (6 GB) and developer time.
- **Recommendation:** Keep as planned. The "cost" framing is misapplied; this is a **time** cost, not a money cost. Note that if the gateway is configured to *always* render a face reply when a voice reply is sent (e.g., `face_reply: true` on every platform), the server will be busy much more than 37.5 min/day. Cap it to on-demand only.

### 5. WhatsApp Business API is NOT what the plan describes — and the plan's warning is correct
- **Annual $ impact:** $0 for the personal-account bridge (and ToS violation risk), OR **$0–$5/month** for a properly Meta-verified Cloud API setup. See per-message math in section "Quota risk."
- **Detail:** Task C3 Step 3 tells the operator the gateway uses a *personal-account* bridge (a WhatsApp Web reverse-engineered client such as `whatsapp-web.js` or Baileys). That is NOT the Meta Cloud API. The user's stated requirement is "WhatsApp baked in BY DEFAULT" and "will get a Meta Business account if needed." Those are two different transports with two different cost/risk profiles.
- **Recommendation:** Add a fork in Task C3:
  - **Default path (cheap, risky):** personal-account bridge. $0/mo, immediate, but ToS violation, can be banned at any time, requires the phone to stay online.
  - **Hardened path (small $):** Meta Cloud API via a BSP (Twilio, 360dialog, MessageBird). The 1,000 service-conversations/month free tier covers ~33 conversations/day — *insufficient* for 50 msg/day (see Quota Risk §A). For 50 msg/day utility conversations, estimate $7.20/month in Meta fees; for marketing conversations, $30–$60/month. Plus Meta business verification overhead (one-time, days of waiting, requires a real business — the user said they will get one if needed).
- **Complexity of swap:** The gateway adapter is already abstracted (per C3 Step 3 wording). Swapping to Cloud API is a config change + a webhook URL + Meta app review.

### 6. Piper TTS download/install is fine; "ElevenLabs free tier fallback" in the Risks section is a red herring
- **Annual $ impact:** $0 if Piper is kept (recommended); up to **$22/month** if the user actually enables ElevenLabs.
- **Detail:** The Risks section says "if naturalness is unsatisfactory, swap to ElevenLabs free tier (10k chars/mo)." ElevenLabs' *current* free tier is 10,000 characters/month — that's roughly **one 3,000-character voice reply per day** before you hit the cap. At 50 voice messages/day (the target throughput in the brief), that is a **150,000-character** bill, which would land on the $22/month "Starter" plan. The fallback is a *cost trap*, not a free safety net.
- **Complexity:** The `speak.py` abstraction means swap is one file. But the "free tier" claim is wrong.
- **Recommendation:** Strike the ElevenLabs free-tier mention. If cloud TTS is ever needed, name the *paid* ElevenLabs plan explicitly, or recommend a local alternative (Coqui XTTS, Kokoro-82M, or `parler-tts`) that the user can run on the M4 with no quota.

### 7. ComfyUI's "1,500 free GPU-seconds/month" framing isn't in the plan, but ComfyUI is fundamentally a local app — watch for hidden cloud dependencies
- **Annual $ impact:** $0 for the ComfyUI server itself (it runs locally). But: the LivePortrait node pack from `kijai/ComfyUI-LivePortraitKJ` may have a model host that does not have a CDN in your region; the only cost is developer time. Verify the model URL is reachable and that `python install.py` doesn't try to phone home to a paid service.
- **Complexity:** Low (clone + pip install + run install.py).
- **Recommendation:** Run the install once and watch outbound DNS in Activity Monitor. If the install tries to hit anything other than `huggingface.co` or GitHub release artifacts, block it and report.

### 8. OpenRouter "free-tier" models mentioned in latency tuning (D2) are the only place a free cloud LLM appears
- **Annual $ impact:** $0–$5/month if the operator actually adds OpenRouter as a fallback, and only because OpenRouter has free `:free` variants of several models.
- **Detail:** D2 says "pick a faster tier (OpenRouter's `anthropic/claude-haiku-4` or local Ollama for trivial replies)." `anthropic/claude-haiku-4` is *not free* on OpenRouter. The free-tier models on OpenRouter are community-hosted and rate-limited (~20 req/min, ~50 req/day on most `:free` models). For 50 voice messages/day, that quota is exceeded in a single morning.
- **Recommendation:** Replace with a local Ollama tag (`llama3.1:8b` for trivial replies, `qwen2.5:14b` for longer ones). Both are already on disk.

### 9. Memory wiring (D1) is already on — no install cost
- **Annual $ impact:** $0. `memory.memory_enabled: true` and `memory.user_profile_enabled: true` are already set in `config.yaml`.
- **Recommendation:** D1 reduces to a verification step (seed a fact, ask for it back, observe recall).

### 10. Persona config in `config.yaml` is not a Hermes-recognized key
- **Annual $ impact:** $0. But Task A1 Step 2 adds `persona.default: avatar` / `persona.dir:` to `config.yaml`. The Hermes config schema on this host does not contain a `persona` block (I read the full file). The plan even admits this: "If `persona` block doesn't exist yet, add it. The Hermes config schema accepts it." There is no evidence the schema accepts it. A misnamed key in YAML is silently ignored, so the avatar persona will *not* be applied by the gateway; only the in-session `/personality avatar` command will work.
- **Recommendation:** Verify `hermes config schema | grep persona` (or whatever the introspection command is on this Hermes build) before writing the block. If unsupported, fall back to the documented "personality" mechanism that the config already shows (`agent.personalities.<name>`).

---

## Free alternatives to paid services

| Plan line item | Plan choice | Free local alternative | Swap complexity | Est. annual $ saved |
|---|---|---|---|---|
| Default LLM | `minimax-m3:cloud` (parent-agent's MiniMax-M3 cloud) | Ollama `llama3.1:8b` or `qwen2.5:14b` (already on disk) | 1-line `config.yaml` edit | $60–$1,800 (depends on volume) |
| Cloud fallback in D2 | OpenRouter `anthropic/claude-haiku-4` | Ollama `llama3.1:8b` (already on disk) | 1-line `config.yaml` edit | $0–$120 (free tier is rate-limited) |
| TTS | Piper (already free) ✓ | — | — | $0 |
| STT | faster-whisper `base` (already wired) ✓ | — | — | $0 |
| Face animation | ComfyUI + LivePortrait local ✓ | — | — | $0.45/month electricity |
| WhatsApp | personal-account bridge (free, ToS-violating) | **No** — Meta Cloud API or nothing for a *legal* solution; the personal bridge is the only "free" path | If user wants legal: BSP integration | $0 (current path) or $86/year (Meta utility at 50 msg/day) |
| ElevenLabs "fallback" (mentioned in Risks) | ElevenLabs free tier → actually $22/mo if used | Kokoro-82M (local, runs on M4) or keep Piper | 1-file swap in `speak.py` | $0–$264/year |
| Kanban orchestration | "kanban-orchestrator" skill (not present on host) | The `delegation` toolset that already exists in Hermes | Install skill or use existing | $0 |

---

## Quota risk

### A. WhatsApp Meta Cloud API utility conversations
- **Rate limit / pricing model:** Meta Cloud API has shifted to per-`conversation` pricing, not per-message. Categories:
  - **Service** (user-initiated): **1,000 free conversations/month**, then $0.0025/conv in the US.
  - **Utility** (business-initiated, transactional): **1,000 free/month**, then **$0.004/conv** in the US.
  - **Marketing**: **1,000 free/month**, then **$0.0125/conv** in the US.
- **Usage projection (50 msg/day × 30 days = 1,500 msgs/month):** This is *conversations*, not messages — a 24-hour window counts as one conversation. Realistic distribution: 30 days × 1 conv/day = 30 conversations/month, well within the 1,000 free tier. **BUT** if the bot is restarted, has a service window, or fails to close a conversation within 24 h, a single user can rack up multiple conversations per day, blowing through the free tier. With a poor config, 50 msg/day could turn into ~200 conversations/month at $0.004 = **$0.80/month, $9.60/year** — manageable. Worst case if marketing gets mixed in: **$30–$60/month, $360–$720/year.**
- **Verdict:** Free tier *probably* sufficient for the projected 50 msg/day if the bot maintains a single 24h conversation window per user, but the plan does not configure that and the gateway is unlikely to do it for free. **Recommend: keep the personal-account bridge (free, ToS-violating) until the user actually needs 5+ recipients, then move to Meta Cloud with a verified business.**

### B. OpenRouter `:free` models (D2 fallback)
- **Rate limit:** ~20 requests/minute, ~50 requests/day on most `:free` tags (community-hosted inference).
- **Usage projection:** 50 voice messages/day alone exceeds the daily cap. **Verdict:** DO NOT use `:free` as the fallback. Either use a *paid* OpenRouter model explicitly, or — preferred — keep Ollama local as the fallback and OpenRouter as a tertiary last-resort cloud.

### C. ElevenLabs free tier
- **Rate limit:** 10,000 characters/month, ~3,000 characters per 1,000-char voice note = ~3 voice notes/month.
- **Usage projection:** 50 voice messages/day = 1,500/month. **Verdict:** Free tier is **50x too small** for the target use case. The Risks section's "ElevenLabs free tier" suggestion is misleading.

### D. Meta Business verification overhead (one-time)
- **Rate limit / time:** 1–14 days for Meta to verify a business, plus a real business (LLC, sole proprietorship, etc.). Not a quota, but a real cost: filing fees, registered agent, or just a sole-proprietor EIN if the user has one.
- **Verdict:** User said "will get a Meta Business account if needed" — flagged as a Phase E blocker per Open Question #4.

### E. faster-whisper `base` model on M4
- **Rate limit:** None (local). Throughput on M4: roughly 5–10x realtime for `base` (a 5-second voice note transcribes in ~0.5–1.0 s). 50 messages/day ≈ 25 minutes of audio total → ~3–5 minutes of compute/day. **Verdict:** free, no quota risk.

---

## Final cost summary

| Line item | Local | Cloud | Monthly $ (typical use) | Notes |
|---|---|---|---|---|
| LLM (default) | Ollama `llama3.1:8b` / `qwen2.5:14b` | MiniMax-M3 (current default) | $0 local / **$5–$30 cloud** | **This is the single biggest cost lever.** Cloud default burns $5–$30/mo at low traffic, $100+/mo at 50 turns/day. |
| LLM (fallback) | Ollama | OpenRouter | $0–$5 | Use only on retry; rate-limit-aware. |
| TTS | Piper (local) | — | $0 | Piper download is one-time, no quota. |
| STT | faster-whisper `base` (local) | — | $0 | One-time ~150 MB model download, then free. |
| Face animation | ComfyUI + LivePortrait (local) | — | $0.45 electricity + $1.08 always-on = **~$1.25** | 6 GB one-time model download. |
| WhatsApp | personal-account bridge (free, ToS-violating) | Meta Cloud API via BSP (if hardened) | $0 current / $0–$5 hardened | Cloud only needed if >5 recipients or business verification done. |
| Slack | already wired | — | $0 | Tokens already present. |
| Telegram | already wired (gateway has the toolset) | — | $0 | `platform_toolsets.telegram` enabled. |
| ElevenLabs fallback | Kokoro-82M / Piper (local) | ElevenLabs Starter (if used) | $0 local / **$22 cloud** | Free tier is 50x too small; would silently become paid. |
| **Total (local-first, as recommended)** | | | **~$1.25–$5/month** | |
| **Total (plan as written)** | | | **~$5–$35/month** | The plan inherits the cloud default and doesn't fix it. |

**Annual range, plan as written:** $60–$420.
**Annual range, plan with the recommended edits:** $15–$60.

---

## Required plan edits

The plan needs these exact additions/changes (no "consider reducing" hand-waving):

### Edit 1 — Insert Phase A0, *before* Task A1

```markdown
### Task A0: Repoint the LLM default to a local Ollama model (BLOCKING, 2 minutes)

**Objective:** Make the persona's gateway replies free.

**Files:** `~/.hermes/config.yaml`

**Step 1:** Change the active model to a local Ollama tag. Pick one based on the M4's 24 GB RAM headroom:

```yaml
model:
  default: qwen2.5:14b      # or llama3.1:8b for speed
  provider: ollama-launch
  fallback: openrouter/anthropic/claude-haiku-4   # only if user accepts last-resort cloud
```

`qwen2.5:14b` and `llama3.1:8b` are both already on disk (verify with `ollama list`).

**Step 2:** Smoke test: `hermes chat -q "say hi in 5 words"` — must respond in <2 s without an outbound API call (Activity Monitor → Network tab, watch for traffic to `api.minimax.io` or `openrouter.ai`).

**Verification gate:** Tail the gateway log while sending a Telegram message; confirm no requests to non-local hosts.
```

### Edit 2 — Strike the misleading ElevenLabs free-tier claim in Risks

Replace:

> Mitigation: if naturalness is unsatisfactory, swap to ElevenLabs free tier (10k chars/mo) without changing the skill

With:

> Mitigation: if naturalness is unsatisfactory, swap to **Kokoro-82M** (local, ~300 MB, runs on M4 with no quota) or **Coqui XTTS v2** (local, voice-cloning capable). Do **not** use the ElevenLabs free tier — its 10k characters/month cap is exceeded by 3 voice notes/month, which would silently fall to the $22/month Starter plan. The `speak.py` abstraction supports either swap.

### Edit 3 — Replace OpenRouter fallback in D2

Replace:

> `hermes model` → pick a faster tier (OpenRouter's `anthropic/claude-haiku-4` or local Ollama for trivial replies).

With:

> `hermes model` → pick a faster **local** Ollama tag. `llama3.1:8b` is the right choice for short chat replies; `qwen2.5:14b` is the right choice for longer synthesis. Do **not** point at OpenRouter `:free` models — their ~50 req/day cap is exceeded by a single morning of voice traffic. Cloud fallback is reserved for true last-resort cases after one local retry has already failed.

### Edit 4 — Add a fork in Task C3 Step 3 (WhatsApp)

Replace the single "Configure WhatsApp" step with two sub-paths:

```markdown
**Step 3a (default, free, ToS-violating):** Personal-account bridge via the gateway's built-in `whatsapp` toolset. The user must explicitly accept the ToS risk; the gateway will surface a consent prompt. Cost: $0/month, one phone must stay online. Sufficient for personal use with ≤5 trusted contacts.

**Step 3b (hardened, optional, costs $):** Meta WhatsApp Cloud API via a Business Solutions Provider (Twilio, 360dialog, MessageBird). Requires:
- Meta Business verification (1–14 days, requires a real business entity).
- A registered WhatsApp Business phone number.
- Webhook URL reachable from the BSP.

Estimated cost at 50 messages/day, all utility category, single 24h conversation window per user: $0.80/month. Worst case with multiple conversations per user per day: $5–$10/month. Free tier (1,000 service conversations/month) covers the projected use case if conversation windows are kept clean.

**Default behavior:** ship Step 3a; treat 3b as a Phase E upgrade card triggered by user request.
```

### Edit 5 — Add face-render rate-limit guard to Task C4

Append to Task C4:

```markdown
**Rate-limit guard:** the gateway must only invoke `avatar-face` when the user *explicitly* requests a video (e.g., message contains "show me", "video", or a `/face` command). Default `face_reply: false`. A misconfigured `face_reply: true` would consume ~37 minutes of M4 CPU per day at 50 voice replies/day — small in $, big in heat/noise/battery.
```

### Edit 6 — Fix the "install" framing of Task A3

Replace "Install local STT (faster-whisper) and verify it transcribes" with:

```markdown
### Task A3: Verify faster-whisper is wired (no install needed)

**Objective:** Voice messages auto-transcribe locally. STT is already configured (`stt.enabled: true`, `stt.local.model: base`). Only the Python package may be missing from the active venv.

**Step 1:** `.venv/bin/pip install faster-whisper` (idempotent; first call also downloads the `base` model into `~/.cache/huggingface/`, ~150 MB).

**Step 2:** Smoke test as written in the original A3.

(If the operator wants better accuracy on accents, switch `stt.local.model` from `base` to `small` — same API, 2x slower, ~500 MB download.)
```

### Edit 7 — Verify the kanban-orchestrator skill exists, or fall back to the `delegation` toolset

Insert at the top of Phase E:

```markdown
**Pre-flight check (BLOCKING):** run `hermes skills list | grep -i kanban`. If `kanban-orchestrator` and `multi-agent-refinement-workflow` are not both present, the dispatcher will silently fail to fan out E1–E4. Fallback: use Hermes's built-in `delegation` toolset directly (see `hermes delegation --help`). This is not a downgrade — it is the same primitive, just lower-level.
```

### Edit 8 — Fix the persona config block in A1

Replace Step 2 of A1:

```yaml
persona:
  default: avatar
  dir: ~/.hermes/personas/
```

With a **verified** approach. The `persona` key is not in the active Hermes config schema (read from `~/.hermes/config.yaml` on this host). Two options:

```yaml
# Option A (if Hermes supports the persona block — verify with `hermes config schema | grep persona`):
persona:
  default: avatar
  dir: ~/.hermes/personas/

# Option B (safe fallback — always works):
# Do nothing in config.yaml. Use `/personality avatar` at the start of each
# session, or add the persona text as a system-prompt preamble in
# agent.personalities.avatar (alongside the existing 'helpful', 'concise', etc.).
```

Add a smoke test that asserts the persona is actually applied (the current plan only checks the tagline text, which is satisfied by the model regardless of the persona block).

---

## Things I could not verify precisely (and how to verify them)

- **Wispr Flow programmatic API surface:** `Info.plist` shows an Electron app with a `wispr-flow://` URL scheme (for `wispr-flow://` deep links, not for third-party use) and microphone/audio-capture permissions, but **no Mach service name, no `LSUIElement`, no CLI binary in `Contents/MacOS/`, and no embedded helper app that exposes an HTTP server or stdin transcript stream**. The `Resources/app.asar` file is a packed Electron bundle; extracting it requires `npx asar extract` (network access) or the `asar` Python package (not installed in the active venv). The asar is signed (`ElectronAsarIntegrity` hash present), so the binary structure is not modifiable. **Conclusion based on observable evidence + the standard architecture of a dictation Electron app:** Wispr Flow has **no public programmatic STT API**. It types into the active focused application. It is not a substitute for faster-whisper in the avatar pipeline.
  - **How to verify with certainty:** install the `asar` Python package (`pip install asar`), then `python -c "import asar; r = asar.open_asar('/Applications/Wispr Flow.app/Contents/Resources/app.asar'); print([k for k in r.keys() if 'ipc' in k.lower() or 'cli' in k.lower() or 'main' in k.lower()])"`. If no IPC channel or CLI surface is in the bundle, my conclusion is confirmed. If there *is* one, the plan should adopt it.
- **Ollama inference throughput on M4 for `qwen2.5:14b`:** I observed the model is loaded (warm in the model list), but did not time inference. The plan's "8-second voice-in → voice-out" target is plausible for `qwen2.5:14b` (M4 should do ~30 tok/s on a 14B Q4) but not guaranteed. Run the D2 stopwatch test before shipping.
- **Meta Cloud API current per-conversation pricing for 2026:** The numbers I cite (service $0.0025, utility $0.004, marketing $0.0125 per US conversation) are the published rates as of early 2026 and may have shifted. **Verify at https://developers.facebook.com/docs/whatsapp/pricing** before committing to a BSP.
- **Personal WhatsApp bridge ToS enforcement risk:** Meta has periodically banned accounts using unofficial bridges. No way to quantify, but the risk is real.

---

## Bottom line

The plan is **structurally sound** (local-first, parallel lanes, clear verification gates) but is **silently paying for the default LLM** because nobody fixed `model.default`. That one edit takes the plan from ~$5–$35/month to ~$1.25–$5/month — an **80–95% cost reduction with a single config change**. The Wispr Flow substitution is not viable (no programmatic API), so keep faster-whisper for the avatar pipeline and use Wispr Flow for the operator's *own* interactive dictation when talking to Hermes. WhatsApp should stay on the personal bridge until the user actually needs 5+ recipients.
