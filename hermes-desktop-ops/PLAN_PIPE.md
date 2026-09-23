# Plan pipe — collector → MOE RCA

After running `hermes-desktop-archive.sh` on **each Mac**, feed the Desktop sidecars into the plan:

## How to pipe

1. Open `~/Desktop/hermes-incident-<host>-<ts>.report.md`
2. Paste the full markdown under **Findings from collectors** in the Hermes Desktop MOE RCA plan (one subsection per host).
3. Optionally attach `*.report.json` (`format: hermes-desktop-ops/report-v1`) so ranked hypotheses merge into the expert table.

## JSON schema (report-v1)

Top-level keys: `format`, `incident_id`, `hostname`, `collected_at_utc`, `os`, `arch`, `signals`, `hypotheses_ranked`, `primary_hypothesis`, `plan_pipe`.

Each hypothesis: `id`, `title`, `score`, `upstream_refs`, `evidence`, `plan_section`, `repair_hint`.

## Template for the plan

```markdown
## Findings from collectors

### Mac A — <hostname>
<!-- paste report.md here -->

### Mac B — <hostname>
<!-- paste report.md here -->

### Combined primary hypotheses
<!-- take max score per id across both reports -->
```

## Status (2026-09-23)

| Host | Status |
|------|--------|
| Mac A `adminisorsMBP14` | Collector **SUCCEEDED**; **reports ingested**. MOE primary **`H1_desktop_python_gateway`** (score 7). |
| Mac B | Still pending — run bootstrap one-liner (see `NEXT_STEPS.md`). |

Canonical findings live in [`moe-rca-hermes-desktop.md`](moe-rca-hermes-desktop.md). Operator runbook: [`NEXT_STEPS.md`](NEXT_STEPS.md).