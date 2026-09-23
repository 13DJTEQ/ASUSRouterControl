# Next steps (operator)

Mac A (`adminisorsMBP14`) collector **succeeded**. Manifest ingested into MOE docs.
**`.report.md` / `.report.json` still missing from chat/uploads** — H1–H4 evidence scores cannot be classified from the manifest alone.

## 1. Paste Mac A reports (required for H1–H4)

Manifest already recorded:

| Field | Value |
|-------|-------|
| incident_id | `hermes-incident-adminisorsMBP14-20260923T203559Z` |
| archive | `hermes-incident-adminisorsMBP14-20260923T203559Z.zip` |
| collected_at_utc | `20260923T203559Z` |
| hostname | `adminisorsMBP14` |
| collector `primary_hypothesis` | `H1_desktop_python_gateway` *(asserted only — no scores yet)* |
| plan_pipe_format | `hermes-desktop-ops/report-v1` |

On Mac A, open or cat and **upload/paste** into chat / into `moe-rca-hermes-desktop.md` → **Findings from collectors**:

```bash
cat ~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.md
# optional machine feed:
cat ~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.json
```

Exact paths:

- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.md`
- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.json`

Do not invent hypothesis scores without these files. Manifest `primary_hypothesis` is not a substitute for scored evidence.

## 2. Run the same bootstrap on Mac B

One line (Terminal on Mac B):

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh
```

Then paste Mac B's `*.report.md` (and ideally `*.report.json` / manifest) the same way.

## 3. Optional — re-bootstrap Mac A for newer ops

Mac A's installed drop listed **no** `hermes-desktop-monitor.sh` (older snapshot). Collector still worked. To pick up monitor + 20s live heartbeats for future runs, re-run the same bootstrap one-liner on Mac A (`--skip-collect` if you only want tools refresh):

```bash
/bin/bash ~/Desktop/bootstrap-desktop-collector.sh --skip-collect
```

Or re-run the full curl one-liner from `README.md`.
