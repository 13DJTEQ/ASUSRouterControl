---
name: implement
description: |
  Use this Skill to open a draft PR implementing a GitHub issue for the
  ASUSRouterControl factory. Trigger phrases: "implement this issue",
  "open PR for #{N}", or when invoked by the `factory-implement` GitHub
  Action on `issues.labeled` with label `ready-to-implement`. Reads the
  triage marker comment produced by the `triage` Skill and produces a
  draft PR gated by `bash scripts/validate.sh` (and `make verify-dev-app`
  if macOS app files are touched).
---

# Implement Skill — ASUSRouterControl factory

This Skill is the action surface. It only runs after the `triage` Skill
has:

1. Applied the `ready-to-implement` label to the issue.
2. Written the idempotent triage marker comment.
3. Posted a triage summary comment.

It runs as a five-stage pipeline. Each stage has hard preconditions; if
they fail, the run aborts with a specific reason and a specific label.
The Skill **never** auto-merges. It opens a draft PR with branch
protection and human review as the only forward path.

## 0. Inputs and outputs

```
inputs: {
  "issue_number":   int,
  "triage_marker": {
    "comment_id":    int,
    "label":         "ready_to_implement",
    "risk_class":    "critical" | "high" | "medium" | "low",
    "confidence":    float,
    "files_likely":  [string],
    "loc_estimate":  int,
    "macos_rebuild": bool
  },
  "repo_root":      string,
  "max_loc_budget": int = 250,
  "max_files_budget": int = 6,
  "max_cost_usd":   float = 5.00,
  "model_tier":     "cloud"|"local" = "cloud"
}

outputs: {
  "branch_name":      "factory/{issue_number}-{slug}",
  "pr_url":           string?,
  "cost_usd":         float,
  "duration_s":       int,
  "test_results":     { "passed": int, "failed": int, "new_tests_added": int },
  "provenance_embedded": bool,
  "outcome":          "success" | "needs_human" | "failed" | "skipped"
}

side_effects:
  - git checkout -b factory/{N}-{slug}
  - apply diff
  - run scripts/validate.sh on touched dirs
  - if macos_rebuild: run make build-dev-app + make verify-dev-app
  - run gitleaks pre-push
  - gh pr create --draft --label factory,needs-review --body-file provenance_body.md
  - gh issue comment <N> --body "<summary>"

error_modes:
  - preconditions_failed    → label issue "needs_human"; abort
  - diff_budget_exceeded    → open draft PR with human_required_reviewers flag
  - no_tests_added          → label PR "needs-tests"; do NOT flag review-ready
  - touched_forbidden_file  → abort immediately; comment "factory_refused:secret_file_touched"
  - macos_verify_failed     → label "verify-failed"; do NOT open PR
  - network_unavailable     → mark partial; post human instructions
```

## 1. Preconditions (Stage 1 — Preflight)

Abort with a specific reason if any precondition fails:

```
read: triage_marker_comment + original_issue_body

assert:
  1. issue label is exactly "ready-to-implement"
  2. triage_marker present (comment_id resolves)
  3. risk_class is one of: low, medium, high_with_human_review_mandatory
  4. no_active_factory_lock (factory_acquire_lock.sh returned 0 AND no existing lock comment)
  5. intended_touched_files ⊆ allowlist_globs
  6. intended_touched_files ∩ forbidden_globs == ∅
  7. estimated_loc ≤ max_loc_budget
  8. file_count_estimate ≤ max_files_budget
  9. macos_rebuild implies make build-dev-app and make verify-dev-app will run
```

If any fails, abort and post:

```
echo "factory_refused:precondition_failed::{failed_check}"
gh issue comment <N> --body "..."
gh issue edit <N> --add-label "needs-human"
gh issue edit <N> --remove-label "ready-to-implement"  # restore clean state
```

Do **not** leave a `ready-to-implement` label on an aborted run.
Humans re-add the label after fixing the precondition.

### 1a. Lock acquisition

Run `scripts/factory_acquire_lock.sh <issue_number>` after all asserts
pass but before any code edits. The lock is a comment on the issue with
the format:

```
<!-- factory_lock:run_id={run_id} expires={iso_ts} agent={agent_name} -->
```

A successful acquire returns 0; a refused acquire (existing active lock)
returns 0 as a no-op idem path; an internal error returns non-zero.

## 2. Plan stage (Stage 2)

Produce `implementation_plan.md` with:

```markdown
# Factory plan for issue #{N}

## Files to touch
- path/reason per file (one line each)

## Test strategy
- what new test to add
- which test file
- failure-path coverage plan

## Expected diff
- files_to_touch: int
- total_loc_added: int
- has_schema_migration: bool

## Risk notes
- any hotspots that need extra eyes
- macos app rebuild required: bool

## Pre-flight results
- assertions passed: [...]
- lock acquired: true
- run_id: ...
```

Enforce:

- `len(files_to_touch) ≤ max_files_budget`
- `files_to_touch ∩ forbidden_globs == ∅`
- `sum(loc_added) ≤ max_loc_budget`

Post `implementation_plan.md` as an issue comment with label
`factory-planned`. The plan MUST exist before any code is edited; if
Implement Skill cannot produce a plan, abort.

## 3. Execute stage (Stage 3)

```
git_checkout: branch "factory/{N}-{slug}"
apply_changes: ONLY files in implementation_plan.md

local_preflight (fast feedback):
  - python3 -m ruff check src tests
  - python3 -m pytest tests/<touched_dirs>/ -x
  - python3 -m compileall src tests

macos_rebuild_path (only when plan.macos_app_rebuild_required == true):
  - make build-dev-app
  - make verify-dev-app
  - require jq '.smoke_check_ok == true' of verify_dev_app.sh output

gitleaks:
  - gitleaks detect --source . --no-git --redact
  - on_hit: abort, post "factory_refused:secret_detected", label "needs_human"

on_test_failure:
  - iterate up to N=2 (test-only fixes; never modify production logic in retry)
  - if still failing: open draft PR labeled "needs-human-fix" with full pytest output

on_all_green:
  - git commit -am "factory: implement #{N} - {title}"
  - proceed to provenance stage
```

Branch name slug: replace non-alphanumeric with `-`, lower-case, max 40 chars.

## 4. Provenance stage (Stage 4)

The PR body is the audit trail. Embed:

```markdown
<!-- factory:run_id={run_id} agent={agent_name} cost=${cost_usd} duration={duration_s}s -->

## What changed
{auto_summary_from_git_diff}

## Risk class
{risk_class}

## Test plan
{test_strategy_summary}

## Provenance
- Run: {run_id}
- Agent: {agent_name}
- Model: {model_tier}
- Cost: ${cost_usd}
- Duration: {duration_s}s
- Triage marker: comment#{triage_marker.comment_id}

Co-Authored-By: Factory-Bot <factory-bot@warp.dev>
```

PR flags:
- `--draft` (never non-draft)
- `--label factory`
- `--label needs-review`
- If macos rebuild: `--label factory+macos-app`
- Branch protection rules in force: require CODEOWNERS review, min 1 human approval before merge

## 5. Report-back stage (Stage 5)

After pushing the branch (and CI has been triggered automatically by the
existing `.github/workflows/ci.yml`):

```
CI will run. The factory does NOT duplicate validation cycles.

factory_post_pr_behavior:
  - push branch to origin
  - do NOT pre-merge; let ci.yml run
  - watch CI status via gh pr checks
  - when green: comment "factory: PR ready for review (CI green)"
  - when red:
      - auto-iterate test-only once
      - if still red: label "needs-human-fix"
```

Then post on the original issue:

```markdown
**Factory PR**: {pr_url}
**Run**: {run_id}
**Outcome**: {outcome}
**Cost**: ${cost_usd}
**Duration**: {duration_s}s
**Tests**: {passed} passed, {failed} failed, {new_tests_added} new
**Provenance**: comment#{provenance_marker_id}

{any_human_actions_required_list}
```

Finally, update `.factory/factory.db`:

```sql
INSERT INTO factory_runs (
  ts_iso, issue_number, action, agent_name, run_id,
  cost_usd, duration_s, label_applied, risk_class,
  confidence, outcome, pr_url
) VALUES (...);
```

## 6. Failure modes (brutal honesty)

1. **Agent will refactor unrelated files while "passing tests".**
   Mitigation: explicit `files_to_touch` plan; `git diff --stat`
   must match plan exactly; any drift = abort.
2. **Tests will be added that don't exercise new code.**
   Mitigation: require test file to import or call the new symbol;
   v3 (Verifier Skill) will tighten this further with import graph
   analysis.
3. **`needs-review` label will be ignored.**
   Mitigation: branch protection enforced via `.github/workflows/ci.yml`
   + CODEOWNERS auto-assignment.
4. **Cost creates runaway risk on retries.**
   Mitigation: $5 hard cap per implement run ($7 for macOS path),
   enforced by the runner, not the model.
5. **Agent will mutate `.env` opportunistically.**
   Mitigation: deny-list enforced *before* the model is shown the
   file list; even prompt injection cannot cat the file.
6. **macOS rebuild timeouts.**
   Mitigation: separate per-run cap of $7 + 30min wall-clock; if
   `make verify-dev-app` exits non-zero, do NOT open PR; comment
   verbatim output excerpt.

## 7. Forbidden actions

- Never auto-merge.
- Never touch forbidden_globs (red paths from triage §1a).
- Never run destructive git commands (force push, reset --hard, branch -D on protected branches).
- Never read or echo credentials, keychain items, `.env`, or 1Password items.
- Never bypass `bash scripts/validate.sh` by running scripts out of order.
- Never create a PR without the provenance marker.
- Never skip the macOS verify path when the plan flags it required.
