---
name: triage
description: |
  Use this skill to classify and route a GitHub issue in the ASUSRouterControl
  cloud software factory. Trigger phrases: "triage this issue", "classify
  this issue", "route this issue", or when invoked by the `factory-triage`
  GitHub Action on `issues.opened` / `issues.edited`. Produces a single
  JSON decision (label + risk_class + confidence + scope) and writes an
  idempotent marker comment so re-runs are safe.
---

# Triage Skill — ASUSRouterControl factory

This Skill is the gatekeeper for what gets auto-implemented. It exists so
that simple, low-risk changes flow to the Implement Skill without human
intervention, while Ambiguous, Risky, or Spec-worthy issues are routed to
the correct non-implementation path.

The Skill **never** edits code. It only:
1. Classifies the issue's blast radius (risk class).
2. Routes to one of four labels.
3. Writes an idempotent marker comment so re-runs do not flap.
4. Posts a human-readable triage summary on the issue.

## 0. Inputs and outputs (JSON contract)

```
inputs: {
  "issue_number": int,
  "issue_title": string,
  "issue_body": string (truncated to 2000 chars),
  "repo_root_path": string,
  "recent_commits_n": int = 20,
  "repo_tree_hash": string?,
  "model_tier": "local"|"cloud" = "local"
}

outputs: {
  "label":             "ready_to_implement" | "ready_to_spec" | "needs_info" | "wait_to_implement",
  "risk_class":        "critical" | "high" | "medium" | "low",
  "confidence":        float (0.0 - 1.0),
  "scope_estimate": {
    "files_likely_touched": [string],
    "loc_added_estimate":   int
  },
  "macos_app_rebuild_required": bool,
  "body_completeness_score":    float,
  "reasoning_short":            string (<= 240 chars; shown in comment)
}

side_effects:
  - gh issue edit <N> --add-label <label>
  - gh issue comment <N> --body "<marker>"
  - gh issue comment <N> --body "<summary>"

error_modes:
  - input_insufficient → label="needs_info", confidence=0.0, halt
  - rate_limited       → label unchanged, post comment "triage_skipped:rate_limit"
  - infra_failure      → label unchanged, no comment (caller retries)
```

The output JSON is the only thing Implement Skill trusts. If you cannot
emit a structurally valid output JSON, **abort** and post a `needs_info`
marker instead — do not silently degrade.

## 1. Risk gate (ASUS-specific)

Before choosing a label, run the risk gate. This is a hard precondition:
if risk_class = critical, the rest of the pipeline aborts.

### 1a. Red paths — always `needs_human`

Any file referenced by the body, or any path the change is *likely* to
touch, that matches these globs forces `risk_class = critical`:

- `src/asusroutercontrol/credentials.py`
- `src/asusroutercontrol/ssh.py`
- `pyproject.toml`
- `uv.lock`
- `.github/workflows/**`
- `Makefile`
- `scripts/build_macos_app.sh`
- `scripts/verify_dev_app.sh`

Also force `risk_class = critical` when the body mentions any of:
credentials, passwords, API keys, OAuth tokens, 1Password, SSH host key
trust, payment flows, or production deploys.

### 1b. High paths — manual review required, `ready_to_spec`

- `src/asusroutercontrol/backends/**` — write paths reach router firmware
- `src/asusroutercontrol/models.py` — schema ripples through datastore
- `src/asusroutercontrol/datastore.py` — migration risk

### 1c. Medium paths — auto-PR with provenance, human reviews

- `src/asusroutercontrol/analyzer.py`
- `src/asusroutercontrol/optimizer.py`
- `src/asusroutercontrol/reporting.py`
- `src/asusroutercontrol/probes.py`
- `src/asusroutercontrol/dhcp_*.py`
- `src/asusroutercontrol/speedtest*.py`

### 1d. Low paths — auto-PR after green CI

- `tests/**`
- typos, docstrings, type hints
- `src/asusroutercontrol/notifications.py` (no-op wiring changes only)

### 1e. MacOS app layer

If any of these files is intended to be touched, set
`macos_app_rebuild_required = true` in the output JSON:

- `src/asusroutercontrol/menubar.py`
- `scripts/build_macos_app.sh`
- `scripts/verify_dev_app.sh`

The Implement Skill will refuse to open a PR until `make build-dev-app`
and `make verify-dev-app` both succeed.

## 2. Routing decision table

Apply in order; first match wins.

```
if risk_class == "critical":                           label = "needs_human"
elif body mentions credentials|auth|payment|deploy:    risk_class = "critical", label = "needs_human"
elif macos_app_rebuild_required and scope > 200 LOC:   label = "ready_to_spec"
elif scope_estimate.loc_added_estimate > 200:          label = "ready_to_spec"
elif body_completeness_score < 0.66:                   label = "needs_info"
elif flag == "low_value_or_vague":                     label = "wait_to_implement"
else:                                                  label = "ready_to_implement"
```

If `confidence < 0.7`, **force** `label = "needs_info"` regardless of
the routing result above. Confidence is a first-class signal, not a vibe.

### Body completeness formula

```
body_completeness_score =
  ( 1.0 if has_acceptance_criteria else 0.0
  + 1.0 if has_explicit_file_paths else 0.0
  + 1.0 if has_reproduction_steps else 0.0
  ) / 3.0
```

Heuristics:

- `has_acceptance_criteria`: matches `Expected:`, `Acceptance:`, `Should:`, or numbered behavior list.
- `has_explicit_file_paths`: matches `/[\w/]+\.py\b|\.jsonl?|\.ya?ml/` outside fenced code blocks where the path looks repo-relative (`src/`, `tests/`, `docs/`).
- `has_reproduction_steps`: matches `Repro:`, `Steps:`, numbered "1)" list, fenced shell block, or `Expected:` paired with `Actual:`.

### Flag heuristics

- `low_value_or_vague` when the title is short, the body is empty, and there are no file references and no acceptance criteria. Phrases that trigger: "should look at", "investigate sometime", "eventually", "make it better".

## 3. Confidence scoring

```
confidence = clamp(
  0.5
  + 0.5 * body_completeness_score
  - 0.3 * (scope_estimate.loc_added_estimate > 100 ? 1 : 0)
  - 0.2 * (risk_class == "high" or risk_class == "critical" ? 1 : 0)
  + 0.1 * (model_tier == "cloud" ? 1 : 0),
  0.0, 1.0
)
```

If `confidence < 0.5`, escalate to `model_tier = "cloud"` once and
re-score. If still below 0.5 after escalation, post the marker as
`needs_info` with confidence reported, do not retry.

## 4. Idempotency marker

### 4a. Marker format

```
<!-- triage:v2 label={label} risk={risk_class} confidence={conf:.2f} scope={files}:~{loc} macos={macos_app_rebuild_required} -->
```

Always the **first comment** matching `/<!-- triage:v2 /` is treated as
the canonical marker. Read it before computing a new decision so
re-runs are safe.

### 4b. Re-run rules

- **No existing marker**: write one; apply label; post summary.
- **Existing marker with same label**: no-op, exit success.
- **Existing marker with different label**:
  - Post a `triage_changed:{old}->{new}` comment with reasoning.
  - Do **not** self-escalate to Implement. Wait for a human to re-add
    the new label.

### 4c. Triage summary comment

After the marker, post a human-readable summary:

```
**Triage**: `ready_to_implement` (risk: low, confidence: 0.86)
**Scope**: ~80 LOC across 2 files: `src/asusroutercontrol/analyzer.py`, `tests/test_analyzer.py`
**Reasoning**: Body includes acceptance criteria, explicit file paths, repro steps.

cc @maintainers — please review the rationale above and re-label if wrong.
```

Keep it factual. Do not apologize. Do not promise implementation.

## 5. Output JSON — strict schema

Implement Skill consumes the output JSON, not the comment. Validate
before posting:

```
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "required": ["label", "risk_class", "confidence", "scope_estimate", "macos_app_rebuild_required", "body_completeness_score", "reasoning_short"],
  "properties": {
    "label":            { "enum": ["ready_to_implement", "ready_to_spec", "needs_info", "wait_to_implement"] },
    "risk_class":       { "enum": ["critical", "high", "medium", "low"] },
    "confidence":       { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "scope_estimate": {
      "type": "object",
      "required": ["files_likely_touched", "loc_added_estimate"],
      "properties": {
        "files_likely_touched": { "type": "array", "items": { "type": "string" } },
        "loc_added_estimate":   { "type": "integer", "minimum": 0 }
      }
    },
    "macos_app_rebuild_required": { "type": "boolean" },
    "body_completeness_score":    { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "reasoning_short":            { "type": "string", "maxLength": 240 }
  }
}
```

If the model generates malformed JSON, retry once with the schema
inline in the prompt. If still malformed, post `needs_info` with
confidence = 0.0 and the marker still — never silently drop.

## 6. Cost controls

```
default_model:        local_ollama_qwen2.5-coder-7b    # local-first project rule
escalation_model:     claude-haiku                       # only on parse-fail OR confidence<0.5
prompt_cache:
  repo_tree_hash:     24h TTL
  recent_commits:     1h sliding window
token_budget:
  per_triage_call:    4000 input, 500 output
  weekly_cap_usd:     75.0 (factory-wide; coordinated with implement)
cap_hit_behavior:
  - post "triage_skipped:weekly_cap_reached" comment
  - notify via repo variable alert channel
  - DO NOT silently downgrade model or skip triage
```

## 7. Eval cases (week-1 seed)

Twelve hand-labeled issues run on day 1. Goal: ≥ 75% label match
before enabling `FACTORY_ENABLED=true`. The eval set lives at
`.factory/eval/triage_cases.jsonl` (one JSON object per line,
fields: `id, title, body, expected_label, expected_risk,
expected_files`). Re-run nightly until precision ≥ 0.85 over a
rolling 30-case window.

## 8. Failure modes (brutal honesty)

1. **Local model will be confidently wrong on novel repos.**
   Mitigation: weekly 30-case eval; gate local model on
   precision ≥ 0.85 before allowing decisions; otherwise force
   cloud escalation.
2. **`needs_info` becomes a black hole.** Humans don't re-engage.
   Mitigation: weekly digest comment
   `# factory-needs-info-stale: {N} issues > 7 days` auto-posted.
3. **Confidence will be over-calibrated on small data.**
   Report ECE (Expected Calibration Error) monthly; ECE > 0.15
   → fall back to cloud model for everything until recalibrated.

## 9. Forbidden actions

- Never edit code.
- Never modify files outside `skills/`, `.factory/`, `.github/`, `docs/factory/`, and `scripts/`.
- Never read or echo any credential, keychain item, or `.env` content.
- Never swallow a parse error by writing `ready_to_implement`.
- Never apply a label without writing the marker first.
