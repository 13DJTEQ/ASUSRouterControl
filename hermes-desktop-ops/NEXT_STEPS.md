# Next steps (operator)

Mac A (`adminisorsMBP14`) collector **succeeded**. Zip + report sidecars are on that Mac's Desktop.

## 1. Paste Mac A reports (required for H1–H4)

On Mac A, open or cat and paste into chat / into `moe-rca-hermes-desktop.md` → **Findings from collectors**:

```bash
cat ~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.md
# optional machine feed:
cat ~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.json
```

Exact paths:

- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.md`
- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.json`

Do not invent hypothesis scores without these files.

## 2. Run the same bootstrap on Mac B

One line (Terminal on Mac B):

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh
```

Then paste Mac B's `*.report.md` the same way.

## 3. Optional — re-bootstrap Mac A for newer ops

Mac A's installed drop listed **no** `hermes-desktop-monitor.sh` (older snapshot). Collector still worked. To pick up monitor + 20s live heartbeats for future runs, re-run the same bootstrap one-liner on Mac A (`--skip-collect` if you only want tools refresh):

```bash
/bin/bash ~/Desktop/bootstrap-desktop-collector.sh --skip-collect
```

Or re-run the full curl one-liner from `README.md`.
