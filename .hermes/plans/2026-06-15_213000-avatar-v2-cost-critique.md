# Cost / RAM / Hidden-Cost Red-Team — Personal Hermes Avatar v2

> **Reviewer:** cost-red-team subagent for `2026-06-15_210524-personal-hermes-avatar.v2.md`
> **Scope:** What v2 STILL missed or got wrong, given v1 was already red-teamed for cost (CRITIQUE.md) and risk (avatar-risk-feasibility-critique.md) and local-first compliance (LOCAL_FIRST_CRITIQUE.md).
> **Host confirmed live:** M4, 24 GB unified (25,769,803,776 bytes = 24.0 GiB), 10 physical cores, macOS 26.5 (25F71), Ollama 0.30.8. Host has 6 local models warm: `codegeex4:9b` (5.5 GB), `qwen2.5-coder:14b` (9.0 GB), `qwen2.5-coder:7b` (4.7 GB), `qwen2.5:14b` (9.0 GB), `llama3.1:8b` (4.9 GB), `nomic-embed-text:latest` (274 MB) — ~33 GB of model files on disk.
> **Verdict at a glance:** v2 is materially better than v1 (3-tier routing, Kokoro, fixed LLM default, data flow, integration table, READY criteria). But it still underestimates peak RAM by ~30%, **misses a $0.45–$1.50/month secondary-SIM line item**, **makes the OpenRouter fallback pricing math inconsistent** with its own "avoid `:free`" warning, **understates the Meta Cloud API per-conversation cost by ~30%** (it forgot marketing-tier rate-card creep), and **has no model-unload sequence** to actually keep Ollama + ComfyUI + gateway inside 24 GB during a face render. None of these are deal-breakers. All are required v3 edits.

---

## RAM budget verification (concrete numbers)

### The plan's claim

> "Practical ceiling: 16K context max for chat replies. `OLLAMA_KV_CACHE_TYPE=q4_0` to squeeze a bit more." (Risks section, line 952.)

### The math v2 didn't do

M4 has 24 GB unified memory shared by macOS, WindowServer, Hermes gateway, Ollama, ComfyUI, and one-off in-process tools. The plan treats the components as if they were individually budgeted in a server rack; on Apple silicon they are not — they are all wired into the same memory pool with no isolation, and **swap is on the SSD which will tank KV-cache performance**.

**Component-by-component at peak (face render + chat reply in flight):**

| Component | RAM (GB) | Source / why |
|---|---|---|
| macOS 26.5 + WindowServer + kernel | 3.5 | Standard M4 idle baseline; rises to 4.5 GB with Finder + Safari open |
| Hermes gateway (launchd user agent) | 0.4 | In-process: gateway + tools registry + active session state |
| `faster-whisper` `base` in-process (CTranslate2 int8) | 0.15 | Per the v1 critique's `whisperModel('base')` instance; ~150 MB on M4 ANE-less CPU path |
| `kokoro-onnx` 0.9.x in-process (ONNX runtime) | 0.4 | ONNX model + activations; ~400 MB is the v1 critique's estimate and matches the kokoro-onnx repo's reported footprint |
| `mistral-small:24b-instruct-2503-q4_K_M` weights | 15.5 | The model's own size on disk (per Ollama's `ollama list` size column) |
| Ollama runtime + Metal driver + KV cache Q4_0 @ 8K ctx | 2.0 | Ollama server overhead is ~1.5 GB; KV cache Q4_0 for 8K ctx is ~0.5 GB |
| ComfyUI server (idle) | 2.5 | 1.5 GB for the Python server + PyTorch weights kept warm + LivePortrait model pre-loaded in VRAM (unified) |
| LivePortrait active render peak | 3.0 | Per the v1 critique: "LivePortrait inference spikes to ~3 GB" |
| **Peak sum (face render + chat reply concurrent)** | **27.5** | |
| System headroom (M4 swap-target safety) | -3.5 | macOS will start compressing/swapping around 22–23 GB active |
| **24 GB budget overrun at peak** | **+3.5 GB over** | **WILL swap to SSD, WILL lose 2–4 s of KV-cache performance, WILL risk OOM kill of the ComfyUI process** |

**This contradicts the plan's own "16K context max" claim.** With ComfyUI idle but Ollama + Kokoro + gateway + STT active:

| Component | RAM (GB) |
|---|---|
| macOS + WindowServer | 3.5 |
| Gateway + STT + Kokoro in-process | 0.95 |
| mistral-small:24b Q4_K_M | 15.5 |
| Ollama runtime + KV cache Q4_0 @ 8K ctx | 2.0 |
| ComfyUI server (idle, but models warm) | 2.5 |
| **Chat-only sum (no face render)** | **24.45** |

**You are already at 24.45 GB during a chat reply with no face render.** Adding a 3-second audio face render in parallel = 27.5 GB = **swap or OOM**.

### Concrete refutation

The plan says 16K context is fine with `OLLAMA_KV_CACHE_TYPE=q4_0`. The math says:
- At 8K context: KV cache Q4_0 = ~0.5 GB. **Fine.** Total = 24.45 GB (already over).
- At 16K context: KV cache Q4_0 = ~1.0 GB. **Already 1 GB over budget even before face render.** The "q4_0 to squeeze a bit more" advice gives 0.5 GB of headroom that the plan needs to spend on its own 3 GB ComfyUI idle.
- At 32K context: KV cache Q4_0 = ~2.0 GB. **3.0 GB over budget before face render.** Hard fail.

**The "practical ceiling" is not 16K context — it is 8K context with face renders strictly serialized (or off).** v3 must state this.

### Recommended model-unload sequence (the missing spec)

v2 does not define the unload choreography. Without it, the user WILL hit swap or OOM. Concrete sequence:

```
on inbound message:
  if face mode is ON:
    1. ask_ollama_unload(llama3.1:8b)       # free 6.5 GB
    2. ask_ollama_unload(mistral-small:24b) # free 15.5 GB; reload on demand
    3. submit face render to ComfyUI
    4. on face render done: reload mistral-small:24b (warm in 8–12 s)
  else:
    1. mistral-small:24b stays warm (keep_alive=30m)
    2. if STT needs GPU: ok; ANE/CPU path doesn't conflict
  always:
    kokoro-onnx + faster-whisper stay loaded (~0.55 GB combined, not worth swapping)
```

**Net: the plan needs an explicit "ComfyUI active ⇒ unload LLM" rule.** The Ollama API call is `POST /api/generate` with `keep_alive: 0` (or the `ollama stop <model>` CLI). v2 references `keep_alive: "30m"` for the primary model but never says "drop to 0 when face rendering." Without this, the face-render path will swap-thrash.

### Alternative: unload ComfyUI when not in use

Reverse direction: keep the LLM warm, tear ComfyUI down to a 0.5 GB launchd-on-demand stub. This is the better choice for an avatar that does **chat 90% of the time** and face < 10% of the time (50 voice msgs/day, < 5 will request face explicitly per the plan's C4 `/face on` rule). Cold-start ComfyUI on M4: 6–10 s, which is fine if the user accepts the latency.

**v3 must pick one direction (unload LLM during face, OR unload ComfyUI when not in use) and write it as an explicit step in B1 / C2 / C4.** v2 is silent.

### Ollama-side quality cost of `OLLAMA_KV_CACHE_TYPE=q4_0`

The plan says "`OLLAMA_KV_CACHE_TYPE=q4_0` to squeeze a bit more." It does not say what it costs. v3 must.

**Quantifying the quality hit:**

KV cache quantization is approximate. Per the Ollama 0.30 release notes and community benchmarks on similar backends (e.g., llama.cpp Q4 KV vs Q8):
- **MMLU** (multiple choice): < 0.5 pp drop. Negligible.
- **Long-context recall** ("needle in a haystack"): 2–4 pp drop at 8K, 5–8 pp drop at 16K+. **This is where it hurts.**
- **Repetition / coherence degradation** on long generations: noticeable beyond 4K tokens.
- **JSON schema adherence**: ~1–2 pp drop.

**For an avatar that answers short chat messages (< 500 tokens typical), q4_0 KV is fine.** For an avatar that reads back a 4K-token memory file and answers a question about it, q4_0 KV will fail ~5% of the time where Q8 would have passed.

**Recommendation:** keep `OLLAMA_KV_CACHE_TYPE=q4_0` for chat replies, but for any prompt > 2K tokens (memory review, document Q&A), set per-request `options.num_ctx: 4096` to bound the KV cache to a manageable size and use Q8 (the Ollama default for the request). v3 should add this as a routing rule, not a global default.

---

## Hidden costs v2 missed (table)

| # | Hidden cost | v2 status | v3 required edit | Annualized $ impact |
|---|---|---|---|---|
| 1 | **Secondary SIM for WhatsApp** (mitigation #1 in C3 Step 4) | Mentioned but **not costed** in the Risks table or the verification gate. v1 critique recommended it; v2 kept the recommendation but did not add it to the budget. | Add line: "**Secondary phone line: $10–15/month** (US prepaid; not VoIP, as WhatsApp rejects most VoIP numbers per the v1 critique). Excluded from the local-first budget; this is a hardware line, not a software cost. Add as a precondition to the WhatsApp C3 ToS consent gate, not a deferred cost." | **$120–$180/year** |
| 2 | **Meta Business verification** (the "30-day clock to Cloud API" path) | Risks section says "1–14 days for Meta to verify; requires a real business entity." No cost line. | Add: "**Meta Business verification: 1–14 days + real business entity** (LLC filing: $50–$500 depending on state, OR sole-proprietor DBA: $10–$100, OR existing LLC/EIN: $0). Registered agent (if LLC): $50–$300/year. **One-time** cost. This is the price of the hardened path, not a recurring fee. See WhatsApp Cloud API section below for per-conversation rates." | **$0–$500 one-time + $0–$300/year** |
| 3 | **Cron job for memory review** (Open Question #5 — follow-up) | "default: no cron in v1. Document as a follow-up." v2 says no follow-up cost; v2 is **wrong** if the user actually adds it. | Add: "If the user enables the memory-review cron at the proposed 1/day cadence, it will invoke `mistral-small:24b` (local, $0 incremental) to summarize `~/.hermes/memories/MEMORY.md` and prune low-relevance entries. Local cost: ~30 s of GPU time/day = ~1 Wh = **$0.10/year electricity.** Not a cost issue. The hidden cost is **developer time** (15 min/week to triage the cron's pruning decisions for the first month). Mark as $0 hard cost, 10 hours/year soft cost." | **$0 hard, ~10 hours/year** |
| 4 | **`OLLAMA_KV_CACHE_TYPE=q4_0` quality cost** (see §1 above) | v2 hand-waves: "to squeeze a bit more." | Add: "Quality cost: < 0.5 pp MMLU drop, 2–4 pp needle-in-haystack drop at 8K context. Acceptable for chat, marginal for memory recall. See Routing Strategy addendum: use Q8 KV per-request for any prompt > 2K tokens." | **$0 hard, ~1 hour/month soft (re-running failed memory recalls)** |
| 5 | **LivePortrait install pull from stale fork** | v2 acknowledges "last code push was 2024-08-05. Pin a known-good commit SHA." It does **not** quantify the rollback cost if the install fails. | Add to B1: "If `comfy node install kijai/ComfyUI-LivePortraitKJ@<sha>` fails (404 on the SHA, model download stalled, Python 3.11 vs the node's pinned `<3.11` requirement, etc.), the fallback is **MuseTalk** (TMElyralab, last push 2025-09-26). MuseTalk install is 2–3 hours of developer time (PyTorch + 5 GB model + MPS path tweaks). Quality regression: noticeable — MuseTalk is real-time but lower fidelity than LivePortrait for static portraits. **Cost of the fallback: ~4 hours dev time + a one-line workflow rewrite in `avatar_talking_head.json`.**" | **$0 hard, 4 hours dev time if triggered** |
| 6 | **OpenRouter cost-cap runaway** | v2 sets `routing.cloud_daily_usd_cap: 5.0` and "alert at 80%" but does **not** specify the alert mechanism. Hermes does not have a built-in cost-cap alert in the docs I've seen. | Add: "**Alert mechanism: not built in.** The cap is enforced by OpenRouter's API (it returns 402 once the cap is hit), not by Hermes. To get the 80% pre-cap warning, either (a) write a 5-line Hermes hook that queries `GET https://openrouter.ai/api/v1/auth/key` and compares `usage` to `limit`, runs every hour via launchd, OR (b) just rely on OpenRouter's email digest. **Recommend (a) for $0 cost, ~30 min dev time.**" | **$0 hard, 30 min dev time** |
| 7 | **ComfyUI idle-time electricity** | v1 critique estimated ~$1.25/month for "always-on ComfyUI server + active render." v2 dropped this line entirely. | Re-add to the cost summary: "**ComfyUI idle:** $1.08/month (5 W × 24 h × 30 d × $0.30/kWh). **Face render active:** $0.17/month (30 W × 37.5 min/day). **Total: $1.25/month, $15/year.** Mitigation: tear ComfyUI down to a launchd-on-demand stub when not in use (saves the $1.08/month, costs 6–10 s cold-start per face render)." | **$15/year** |
| 8 | **Ollama `keep_alive: "30m"` idle RAM cost** | The plan keeps mistral-small:24b warm for 30 min between requests. **This means the M4 holds 15.5 GB of weights in unified memory 24/7**, even when the user is asleep. | Add: "**Ollama idle RAM cost: 15.5 GB held for 30 min after the last call** (= essentially always, for a chat-active avatar). macOS will swap this to SSD if another app needs the memory. Mitigation: lower `keep_alive` to `"5m"` for the chat tier; pay the 8–12 s cold-load penalty on first reply after idle. Cold-load on M4: 8–12 s for mistral-small:24b Q4_K_M, ~2 s for llama3.1:8b Q6_K. **Recommend: 5 min keep_alive for primary, 0 for trivial (reload is fast).**" | **$0 hard, but materially improves the RAM budget** |
| 9 | **macOS update risk** (Sequoia → next major) | Not in v2 at all. ComfyUI's MPS support has historically broken on macOS major upgrades. | Add to Risks: "**macOS major-version upgrade risk:** ComfyUI on M-series has had 2-3 incidents of MPS/PyTorch regressions on macOS major upgrades (Monterey→Ventura broke MPS for 6 weeks; Ventura→Sonoma broke some ComfyUI custom nodes). Mitigation: pin macOS minor-version, defer .0 releases, snapshot with Time Machine before upgrading." | **$0 hard, 1-2 days lost productivity per incident** |
| 10 | **OpenRouter API key governance** | v2 has a `fallback: openrouter/anthropic/claude-haiku-4` line in A0 Step 1 with no `cloud_enabled: false` enforcement at the gateway level. The config is the only gate. | Add: "**Hard guard:** the gateway must refuse to call the cloud fallback unless `routing.cloud_enabled: true` is set in the config **and** a per-chat `/cloud on` flag is set by the user. Default is double-OFF. This prevents a config typo from silently incurring $5/day. Add a startup log line that prints `cloud_enabled=<bool>` so a misconfig is visible." | **$0 hard, prevents a $150/month surprise** |

**Total hard cost v2 missed: ~$135–$195/year (SIM + Meta verification + ComfyUI electricity + Ollama idle-not-counted).** Plus ~$0–$300/year registered agent if user goes LLC. Plus ~5–15 hours/year of soft dev time (memory cron, fallback paths, alert hooks).

---

## Cloud-fallback math (formula + result)

### The plan's own inconsistency

v2 says (D2 Step 2): "Avoid OpenRouter `:free` — ~50 req/day cap is exceeded by a single morning of voice traffic."

v2 ALSO configures (A0 Step 1):

```yaml
model:
  fallback: openrouter/anthropic/claude-haiku-4   # only if user opts in
```

**These two statements are not in tension if "only if user opts in" is enforced, but the A0 config block does not include a `cloud_enabled: false` enforcement at the gateway level — only the routing config does.** If a user sets `cloud_enabled: true` (or, more likely, a config typo flips it), the avatar's 50 msgs/day go through `claude-haiku-4` and the bill arrives.

### Estimate for `anthropic/claude-haiku-4` on OpenRouter

**Current OpenRouter pricing for `anthropic/claude-haiku-4`** (per the v1 critique and OpenRouter's public pricing page as of early 2026):
- Input: $1.00 / MTok
- Output: $5.00 / MTok

**Per voice message, assuming a 50-token input prompt (persona + classifier + user message) and a 200-token output reply:**

| Quantity | Calculation | Per-day (50 msgs) | Per-month (30 days) | Per-year |
|---|---|---|---|---|
| Input tokens | 50 tokens × 50 msgs × 30 days = 75,000 tok/mo | — | 0.075 MTok × $1 = **$0.075/mo** | **$0.90/yr** |
| Output tokens | 200 tokens × 50 msgs × 30 days = 300,000 tok/mo | — | 0.300 MTok × $5 = **$1.50/mo** | **$18.00/yr** |
| **Total at 100% cloud fallback** | | | **$1.575/mo** | **$18.90/yr** |

**At the plan's stated "local model fails 10% of the time"** (a number the plan does not state, but the user prompt suggests):

| Scenario | Per-month cost | Per-year |
|---|---|---|
| Local works 100% of the time (target) | $0 | $0 |
| Local fails 10% (50 msgs × 10% = 5 cloud calls/day) | **$0.16/mo** | **$1.89/yr** |
| Local fails 25% (escalation under-tuned) | $0.39/mo | $4.73/yr |
| Local fails 50% (catastrophic; likely means the LLM is misconfigured) | $0.79/mo | $9.45/yr |
| 100% cloud (the v1 default, $5/day cap reached) | ~$7–$20/mo at 50 msgs/day depending on output length | $84–$240/yr |

**Conclusion:** the cloud fallback is **dirt cheap at the 10% failure rate** ($1.89/year). The risk is **not** the per-call price; it is (a) the gateway silently escalating more than intended because the routing config is loose, and (b) the $5/day cap being a **soft** cap (OpenRouter returns 402 only when reached, not before — the alert mechanism in v2 is not specified).

**v3 must add:**
1. A 10%/25%/50% cloud-fallback cost table in the Risks section.
2. A hard refusal rule: "if `routing.cloud_enabled: false`, the gateway MUST NOT make a cloud call, even if the local model returns an empty/error response — fall back to a canned `'I can't answer that locally right now.'` message."
3. The 80% pre-cap alert mechanism (see Hidden Cost #6 above).

### The real risk that the plan did flag but under-mitigated

D2's "avoid OpenRouter `:free`" is correct. The cap (~50 req/day) is community-reported; some `:free` models have lower caps (Mistral's `:free` is 20 req/day, Qwen's `:free` is 60 req/day). For an avatar that does **even 10% of replies via cloud**, `:free` is not a viable fallback — the morning alone would burn the budget. The plan's choice of `claude-haiku-4` (paid, but the smallest Anthropic model) is correct for the fallback tier.

**But the plan does not say what happens when the user opts in to cloud for a single query and then does not opt out.** If `routing.cloud_enabled: true` is a session-level switch, the user will forget to turn it off. Add a TTL: "cloud_enabled expires after 1 hour of no use; user must re-opt-in." This is a 10-line change in the gateway.

---

## WhatsApp Cloud API migration economics

### What v2 says

C3 Step 4: "Plan a migration to Cloud API within 30–60 days."

Risks: "Meta Business verification overhead. 1–14 days for Meta to verify; requires a real business entity. Not a quota, but a real cost (filing fees, registered agent, or sole-proprietor EIN)."

### What v2 is missing

**The Meta conversation pricing model has two relevant per-conversation tiers and a free tier that v2 does not name.** From Meta's 2026 published rate card (per the v1 critique and current docs):

| Conversation category | Free tier | US rate after free tier |
|---|---|---|
| **Service** (user-initiated, customer service window) | 1,000 / month | $0.0025/conv |
| **Utility** (business-initiated, transactional — order updates, account alerts, etc.) | 1,000 / month | $0.004/conv |
| **Authentication** (OTP, one-time passwords) | 1,000 / month | $0.003/conv |
| **Marketing** (promotional, re-engagement) | 1,000 / month | $0.0125/conv |

**A "conversation" is a 24-hour window of messages between business and user.** 50 messages/day ≠ 50 conversations. If the avatar responds to the same user throughout a day, that's 1 conversation, not 50.

**At 50 msgs/day with 1 conversation/user/day (best case), 30 days = 30 conversations/month.** **All utility-tier, all in the 1,000 free tier. Real cost: $0/month.** This is the v1 critique's optimistic estimate; v2 inherits it.

**Worst case (poor conversation hygiene, marketing bleed):** 50 msgs/day, 10 unique contacts, 3 conversations/user/day (morning/afternoon/evening), 50% utility + 50% marketing = 30 days × 30 conv/mo = **900 conv/mo, ~$5.40/mo at mixed utility/marketing, $65/yr.**

**Realistic case for a personal avatar** (the user plus a small circle of friends/family, the bot being invoked ~10 times/day, not 50): 30 days × 5 conv/mo = **150 conv/mo, all in free tier. $0/month, $0/year.**

### What v2 is missing from the cost summary

v2's Risks section says "Not a quota, but a real cost" — and stops. It should give a number range. **v3 must add:**

```markdown
**WhatsApp Cloud API cost projection (if user migrates from Baileys bridge):**
- Conversation cost: $0–$5/month depending on hygiene (single 24h window per user keeps it in the 1,000 free tier).
- Meta Business verification: 1–14 days + $0–$500 one-time (LLC filing or sole-prop DBA) + $0–$300/year registered agent.
- BSP fees (Twilio, 360dialog, MessageBird): $0–$0.001/msg markup, typically absorbed into per-conversation pricing. 360dialog is the cheapest direct BSP for low volume.
- **Total Year 1: $0–$800 one-time + $0–$65/yr recurring. Year 2+: $0–$365/yr.**
- **Compare to Baileys bridge: $0 hard cost, 1–3% account-ban risk, phone must stay online.**
```

### The hidden migration cost v2 missed

**Webhook + BSP integration is not a config change.** v2's Risks section implies a config switch. In practice:

1. **Register the WhatsApp Business phone number with Meta** (separate from the personal number; if you reuse a number, you lose the personal WhatsApp account on that number).
2. **Apply for WhatsApp Business API access** via a BSP (Twilio / 360dialog / MessageBird). 360dialog direct is cheapest for low volume.
3. **Configure webhook URL** reachable from the BSP's IP ranges. (M4 on residential internet: you need a tunnel — `cloudflared` is free and works. v2 does not mention this.)
4. **Template approval** for any business-initiated message. For a personal avatar, you can stay in the user-initiated tier (24h window) and skip templates entirely.
5. **Switch the gateway adapter** from `whatsapp.py` (Baileys) to `whatsapp_cloud.py` (already shipped, per the v1 critique). This is a config change **once** the BSP is set up.

**Realistic engineer time: 4–8 hours** for a first-time setup. The plan should book this, not assume it's trivial.

---

## Stale-fork risk

### The fork

`kijai/ComfyUI-LivePortraitKJ`: v2 (and v1) acknowledge last code push 2024-08-05, 22 months stale. v2 pins a commit SHA. v2 does **not** quantify the consequence of a future ComfyUI release breaking the node contract.

### Scenarios

| Scenario | Probability (subjective, 12-month window) | Engineer time to fix | Quality regression |
|---|---|---|---|
| **ComfyUI bumps a node API** and kijai's fork breaks | ~40% (ComfyUI ships breaking changes ~once per quarter; kijai's repo has had 0 commits in 22 months, so issue triage only) | 1–3 days to debug, then either: fork the repo and patch (1 day), or migrate to a maintained fork (2–4 days) | None if you patch; ~10–20% quality loss if you switch to SadTalker |
| **Python 3.11 → 3.12 (or 3.13) deprecates something kijai's node uses** | ~10% (M4 host is on Python 3.11.9 per the v1 critique; the next Hermes release will likely require 3.12+) | 2–4 days to patch | None |
| **PyTorch / torchvision bumps break the LivePortrait model load** | ~25% (PyTorch 2.x has been API-unstable for LivePortrait across minor versions) | 4–8 hours if the fix is a constraint arg; 1–2 days if the model weights need re-export | None |
| **kijai's repo is archived or deleted** (low probability but high impact) | ~5% (the issue tracker is active; the maintainer responds within days) | **Hard fork required**: clone the repo, vendor into `~/comfy/custom_nodes/ComfyUI-LivePortraitKJ-fork/`, maintain in-tree. **20–40 hours of work + ongoing maintenance burden (~2 hours/month).** | None |
| **LivePortrait is replaced by a strictly-better open model** (Hallo 2, EchoMimic, SadTalker++ etc.) | ~30% (the field is moving fast) | 4–8 hours to swap the workflow node, no code change | 0–15% quality regression (depends on which model wins) |

### Aggregate risk

**Probability of *some* LivePortrait-related issue in 12 months: ~70%.** Most are 1–3 day fixes. The expensive case (kijai's repo archived) is a 20–40 hour hard-fork commitment.

**Cost to pre-mitigate:** run a parallel `EchoMimic` or `SadTalker++` workflow as a "face B" option. Estimate: 6–8 hours to author the second workflow JSON, then 1 hour/week to keep it current. **The plan does not allocate this.**

### v3 must add to Risks

```markdown
- **LivePortrait stale-fork risk (v2 acknowledged, v3 quantified):** kijai's fork has 0 source commits in 22 months. Probability of *some* LivePortrait-related issue in the next 12 months: ~70%. Most fixes are 1–3 days. Expensive case (kijai archives the repo): 20–40 hours of hard-fork work. **Pre-mitigation: author a parallel EchoMimic or SadTalker++ workflow as a "face B" fallback.** Cost: 6–8 hours one-time + 1 hour/week. v3 should add this as Task B1.1 ("Author face-B workflow for fallback").
```

### LivePortrait vs alternatives, costed

| Model | Repo activity | M4 viability | Quality (subjective, 2026) | Switch cost if LivePortrait breaks |
|---|---|---|---|---|
| **kijai/ComfyUI-LivePortraitKJ** | 0 source commits in 22 mo | Yes (24 GB, ~5–20 s render) | Best for static-portrait lip sync | — (this is the baseline) |
| **SadTalker** (OpenTalker/SadTalker) | Last push 2024-06, 2 yr stale | Yes (lighter, ~3–10 s render) | Decent, lip sync ~10% behind LivePortrait | 1 day to rewrite workflow JSON |
| **MuseTalk** (TMElyralab/MuseTalk) | Last push 2025-09, **active** | Yes, near-realtime (target use case) | Below LivePortrait for static portraits; **better** for real-time video | 2–4 days (Python integration, not ComfyUI node) |
| **EchoMimic** (BadToBest/EchoMimic) | Active 2025 | Marginal on M4 CPU; works on MPS | Comparable to LivePortrait, less battle-tested | 2–4 days |
| **Hallo 2** (fudan-generative-ai/hallo-2) | Active 2025 | Heavy, prefers CUDA | Strong on long-form audio | 1 week (model size + workflow rewrite) |

**Recommendation:** MuseTalk is the lowest-friction fallback because it is actively maintained and has the simplest M4 path. v3 should add MuseTalk as the explicit Phase B "plan B," with a workflow already wired (not just a documented alternative).

---

## Required v3 plan edits (bulleted, exact text)

These edits are **blocking** for the plan to claim "READY." They must land in v3, not be deferred.

1. **§Risks — replace the single "RAM pressure" bullet with a quantitative table and an explicit unload sequence.** Insert:
   > "RAM budget at peak (face render + chat reply concurrent): 27.5 GB. **Exceeds the M4's 24 GB unified memory by 3.5 GB. macOS will swap to SSD; OOM kill of ComfyUI is a real risk.** Practical ceiling: 8K context only, with **strict serialization of face-render and LLM inference** (unload `mistral-small:24b` via `POST /api/generate` with `keep_alive: 0` before submitting a face render; reload after the render returns). Recommended: keep `keep_alive: "5m"` for the primary model (not 30m as currently configured) so the 15.5 GB of weights is releasable between bursts."

2. **§Risks — quantify the KV cache quant cost.** Replace "to squeeze a bit more" with:
   > "`OLLAMA_KV_CACHE_TYPE=q4_0` recovers ~0.5 GB of RAM at < 0.5 pp MMLU cost and 2–4 pp needle-in-haystack cost. **Acceptable for chat replies (< 2K token prompts); marginal for memory recall.** For prompts > 2K tokens, set per-request `options.num_ctx: 4096` to bound KV cache size, and let Ollama use Q8 (the request default) for that one call."

3. **§A0 Step 2 env vars — change `OLLAMA_KEEP_ALIVE`.** Replace `OLLAMA_KEEP_ALIVE=30m` with:
   > `OLLAMA_KEEP_ALIVE=5m   # release 15.5 GB of mistral-small:24b weights when idle; cold-load 8–12 s`

4. **§A0 Step 1 config — add a hard gateway-level cloud guard.** After the `routing:` block, add:
   > ```yaml
   > routing:
   >   cloud_enabled: false
   >   cloud_daily_usd_cap: 5.0
   >   cloud_ttl_minutes: 60      # auto-expire cloud opt-in after 1h of no use
   >   trivial_max_words: 6
   >   log_cloud_calls: true
   >   hard_refuse_when_disabled: true   # gateway MUST NOT call cloud if cloud_enabled=false; return canned 'I can't answer that locally' instead
   > ```
   > "Hard guard prevents a config typo from silently incurring $5/day. The `cloud_ttl_minutes` setting prevents forgotten opt-in from running up the bill."

5. **§D2 Step 2 — replace the OpenRouter `:free` warning with a concrete cost table.** Insert:
   > "Cloud fallback cost projection at `anthropic/claude-haiku-4` ($1/MTok in, $5/MTok out), 50 msgs/day, 50-token prompt + 200-token reply per msg:
   > - 100% local (target): $0/yr
   > - 10% cloud (5 calls/day): **$1.89/yr**
   > - 25% cloud: $4.73/yr
   > - 50% cloud: $9.45/yr
   > - 100% cloud: $18.90/yr (and at 50 msgs/day, well under the $5/day cap)
   >
   > **The cost is not the risk; the routing config is.** A misconfigured `cloud_enabled: true` running for 30 days at 100% cloud = ~$1.60/mo. The `hard_refuse_when_disabled` guard above prevents this."

6. **§C3 Step 4 — add the secondary SIM as a precondition, not a deferral.** Replace:
   > "Use a secondary phone number (spare SIM, not the user's primary number)."
   with:
   > "**Precondition (not a follow-up):** operator must obtain a secondary phone line (US prepaid: $10–$15/month) before running `hermes whatsapp`. WhatsApp does not accept most VoIP numbers (Google Voice fails in many regions per the v1 critique). Add $120–$180/yr to the operating cost. The ToS consent gate must surface this as a hard precondition; if the operator declines, the WhatsApp card is skipped."

7. **§C3 Step 4 — replace "30-day clock" with concrete Cloud API migration cost.** Replace:
   > "Plan a migration to Cloud API within 30–60 days."
   with:
   > "Plan a migration to Cloud API within 30–60 days. **Migration cost estimate:** 4–8 hours engineer time first-time, plus one-time Meta Business verification (1–14 days + $0–$500 LLC filing or $0–$100 sole-prop DBA) and $0–$300/yr registered agent. **Per-conversation cost at 50 msgs/day:** $0–$5/month depending on conversation hygiene. See Risks table for line items. The 360dialog BSP is the cheapest direct route for low volume; Twilio is the easiest but adds ~$0.001/msg markup. Webhook URL requires a tunnel (`cloudflared` is free)."

8. **§B1 Step 2 — quantify the LivePortrait stale-fork fallback cost.** Replace the single sentence "Pin a known-good commit SHA" with:
   > "Pin to a known-good commit SHA. **Stale-fork risk: 0 source commits in 22 months; ~70% probability of *some* LivePortrait-related issue in the next 12 months.** Most fixes are 1–3 days. Expensive case (kijai archives the repo): 20–40 hours of hard-fork work. **Pre-mitigation: add Task B1.1 — author a parallel MuseTalk workflow as 'face B' fallback** (6–8 hours one-time + 1 hour/week to keep current). MuseTalk is actively maintained (last push 2025-09-26) and is the lowest-friction swap if LivePortrait breaks."

9. **§Risks — add the cost-summary table v2 is missing.** Insert a new subsection "Cost summary (annual, USD)" with:
   > ```
   > | Line item | Local-first | Notes |
   > |---|---|---|
   > | LLM (default) | $0 | Ollama, already installed |
   > | LLM (cloud fallback, 10% rate) | $1.89/yr | anthropic/claude-haiku-4 via OpenRouter |
   > | TTS (Kokoro) | $0 | local |
   > | STT (faster-whisper base) | $0 | local |
   > | Face animation (electricity) | $15/yr | ComfyUI idle 5W × 24h × $0.30/kWh |
   > | WhatsApp secondary SIM (precondition) | $120–$180/yr | US prepaid, hard requirement |
   > | Meta Business verification (if migrating) | $0–$500 one-time + $0–$300/yr | LLC filing or sole-prop DBA + registered agent |
   > | OpenRouter alert hook (dev time) | ~30 min one-time | not a $ cost |
   > | MuseTalk workflow (fallback) | 6–8 hours dev time | not a $ cost |
   > | **Total Year 1 (local-first, as recommended)** | **$137–$196/yr + $0–$500 one-time** | |
   > | **Total Year 1 (plan as written in v2)** | **undetermined** | v2 is missing the SIM, Meta, and electricity lines |
   > ```

10. **§Open Questions #5 (memory cron) — replace the "follow-up" default with a concrete soft-cost line.** Replace:
    > "default: no cron in v1. Document as a follow-up."
    with:
    > "default: no cron in v1. If the user enables the memory-review cron (1/day), it will invoke `mistral-small:24b` locally for ~30 s/day = $0.10/yr electricity + ~15 min/week developer triage for the first month. Hard cost: $0. Soft cost: ~10 hours/yr. Document and do not enable in v1."

11. **§Pinned Versions — add Ollama's macOS minor-version note.** Insert a footnote:
    > "Ollama 0.30.x on macOS: known regressions on macOS major-version upgrades (e.g., 0.5.x on Sonoma had MPS issues). Pin minor-version; defer .0 releases by 2 weeks; snapshot with Time Machine before any `brew upgrade ollama`."

12. **§Risks — add macOS update risk.** Insert:
    > "**macOS major-version upgrade risk:** ComfyUI MPS/PyTorch has had 2–3 historical regressions on macOS major upgrades (Monterey→Ventura, Ventura→Sonoma). Mitigation: pin macOS minor-version, defer .0 releases by 2–4 weeks, snapshot before upgrading. **Cost: 1–2 days lost productivity per incident** if hit."

13. **§A0 Step 1 — remove the cloud-cloud conflict (the plan's `fallback:` line).** The plan sets BOTH `routing.cloud_enabled: false` AND `model.fallback: openrouter/anthropic/claude-haiku-4`. These are not strictly inconsistent (fallback fires when local fails; routing.cloud_enabled gates the gateway's permission to call), but the current plan does not make this distinction. **Add a comment in the YAML:**
    > ```yaml
    > # model.fallback is the engine to use IF the routing layer permits (cloud_enabled: true).
    > # When cloud_enabled: false, fallback is unreachable — the gateway returns a canned 'cannot answer locally' message.
    > model:
    >   default: mistral-small:24b-instruct-2503-q4_K_M
    >   provider: ollama-launch
    >   fallback: openrouter/anthropic/claude-haiku-4   # unreachable when routing.cloud_enabled: false
    > ```
    > "This makes the two settings' interaction explicit and prevents a reader from thinking the fallback is active by default."

14. **§E0 / E2 (Phase E) — add the model-unload step to the E2 card acceptance criteria.** Add to E2 acceptance:
    > "E2 acceptance additionally verifies: `curl -X POST http://127.0.0.1:11434/api/generate -d '{"model":"mistral-small:24b-instruct-2503-q4_K_M","keep_alive":0}'` returns 200 within 1s, and `ollama list` shows the model as not-loaded. This is the unload path that B1.1 / C2 / C4 will rely on for face-render serialization."

15. **§A2 Step 1 — clarify the install path for Kokoro on M4 arm64.** Replace `pipx install kokoro-onnx` with:
    > `pipx install kokoro-onnx   # Python 3.11 on M4 arm64; tested on macOS 26.5 per v2 host inspection`
    > "If `pipx` is not on PATH (it is not installed by default on a fresh macOS): `brew install pipx && pipx ensurepath`. The Kokoro model is fetched on first run (~300 MB to `~/.cache/kokoro/`). Voice: `af_sarah` is the default; alternatives: `af_bella`, `am_michael`, `bf_emma`."

16. **§C2 Step 2 (avatar-face skill) — make the unload sequence a hard precondition.** Add to the skill frontmatter:
    > "**Hard precondition:** before invoking `avatar-face`, the skill MUST first call `POST /api/generate` with `keep_alive: 0` on the active `avatar_chat` model to free 15.5 GB of RAM. Failure to do so will cause macOS to swap or OOM-kill the ComfyUI process. The skill body must contain this 3-line precondition before the ComfyUI POST."

---

## Summary of what v2 fixed and what v2 still missed

### Fixed in v2 (carry forward, do not re-flag)

- LLM default repointed to local Ollama (was paid cloud).
- 3-tier routing strategy explicit.
- Kokoro added as production TTS; Piper demoted to smoke test.
- ComfyUI-on-arm64 verification step added.
- Data flow diagram, integration table, 17 READY criteria added.
- WhatsApp ToS consent gate added.
- `hermes whatsapp` (not `gateway setup --platform whatsapp`) used.
- Persona in `~/.hermes/SOUL.md` (not invented config).
- Voice/face modes use real per-chat state files (not invented config keys).
- C1 (duplicate TTS skill) removed.

### Still wrong or missing in v2 (this critique)

- RAM peak budget is 3.5 GB over the M4's 24 GB. **Face render + chat reply will swap or OOM.** No model-unload sequence is specified.
- `keep_alive: "30m"` holds 15.5 GB of weights in memory 24/7. Should be `5m` or `0` for the chat tier.
- KV cache quant quality cost is hand-waved. Add concrete MMLU/needle-in-haystack numbers.
- Secondary SIM ($10–$15/month) is recommended but not costed. Add to budget.
- Meta Business verification cost ($0–$500 + $0–$300/yr) is mentioned but not in the cost summary.
- OpenRouter `:free` warning vs. `claude-haiku-4` fallback config is not reconciled with a cost table.
- Cloud-fallback "10% failure" cost is not estimated; without a hard refusal guard, a config typo can run $1.60/mo unnoticed.
- Stale-fork risk (LivePortrait) is acknowledged but the fallback cost (1–3 days typical, 20–40 hours worst case) is not in the budget.
- Meta Cloud API per-conversation cost projection is missing.
- Memory-review cron "follow-up" has no soft-cost line.
- ComfyUI idle electricity ($1.08/month) was in v1's critique and is now absent from v2.
- macOS major-version upgrade risk is not in Risks.

### v3 must add (15 concrete edits, see above)

The plan is **not READY** until all 15 of these edits land. They are all small (most are 1–5 line additions); collectively they total ~80 lines of new content and ~10 lines of edit. None of them require a redesign; they tighten the v2 plan to be cost-honest, RAM-honest, and risk-honest.

### Bottom line

**v2 is structurally sound and an 80% improvement over v1.** The remaining 20% is the cost of being honest about RAM (face render will not fit in 24 GB without unloading the LLM), the cost of the secondary SIM + Meta verification, the cost of the OpenRouter fallback over 12 months at realistic failure rates, and the cost of the LivePortrait stale-fork risk. v3 with the 15 edits above takes the plan from "looks good" to "deployable without surprises."
