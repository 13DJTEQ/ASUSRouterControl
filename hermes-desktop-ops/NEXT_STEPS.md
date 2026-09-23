# Next steps (operator)

Mac A (`adminisorsMBP14`) collector **succeeded**; **`.report.md` / `.report.json` ingested**.  
MOE Mac-A primary: **`H1_desktop_python_gateway`** (score 7). Mac B still needed for dual-system RCA.

## 1. Mac A — optional interim H1 repair (supported now)

H1 is clearly leading on Mac A (`port_9130_closed` + system Python cannot import `hermes_cli` + venv OK). Interim repair is reasonable **without** waiting for Mac B; dual-Mac confirmation remains preferred before treating H1 as fleet-wide.

On Mac A — paste **as one line** (refreshes full Desktop toolset, then dry-run repair). Do not paste comment lines with parentheses.

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh --skip-collect && /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh
```

Apply only after dry-run looks right (one line):

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh --skip-collect && /bin/bash ~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh --apply
```

Manual H1 commands if repair script is unavailable:

```bash
hermes doctor
hermes gateway status
# Ensure Desktop/gateway uses venv Python, not /usr/bin/python3
launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"
# Recheck:
nc -z 127.0.0.1 9130 && echo "9130 open" || echo "9130 still closed"
```

**Do not** lead with `hermes auth add nous --type oauth` on Mac A yet — H2 is secondary (score 3; refresh token not null; no quarantine hint).

## 2. Run the same bootstrap on Mac B (still needed)

One line (Terminal on Mac B):

```bash
curl -fsSL https://raw.githubusercontent.com/13DJTEQ/ASUSRouterControl/cursor/hermes-desktop-ops-cfe4/hermes-desktop-ops/bootstrap-desktop-collector.sh -o ~/Desktop/bootstrap-desktop-collector.sh && chmod +x ~/Desktop/bootstrap-desktop-collector.sh && /bin/bash ~/Desktop/bootstrap-desktop-collector.sh
```

Then upload/paste Mac B's `*.report.md` and `*.report.json` the same way as Mac A.

## 3. After both reports

- Merge max scores into `moe-rca-hermes-desktop.md` → Combined primary hypotheses
- Gate Fast→Slow repair on the dual-Mac leading H*
- If H2 leads on either host: `hermes auth add nous --type oauth` (no external OAuth token health-checks)

## Mac A ingest reference

| Field | Value |
|-------|-------|
| incident_id | `hermes-incident-adminisorsMBP14-20260923T203559Z` |
| hostname | `adminisorsMBP14` |
| collected_at_utc | `20260923T203559Z` |
| primary_hypothesis | `H1_desktop_python_gateway` |
| H1 / H2 / H4 / H3 scores | 7 / 3 / 3 / 2 |
| plan_pipe_format | `hermes-desktop-ops/report-v1` |
