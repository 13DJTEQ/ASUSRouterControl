# MOE RCA — Hermes Desktop (dual-Mac)

Four-expert RCA for Hermes Desktop failures on Apple Silicon Macs.
Collector evidence pipes in via `PLAN_PIPE.md` / Desktop `*.report.md` sidecars.

## Ranked hypotheses (pre-evidence)

| ID | Expert | Working title | Confirm with |
|----|--------|---------------|--------------|
| H1 | Desktop/runtime | Wrong Python / gateway never binds `127.0.0.1:9130` | doctor, which python, port probe |
| H2 | Auth/Portal | Nous OAuth refresh revoked (`invalid_grant`) | auth fingerprint, logs (no raw tokens) |
| H3 | Ops/stability | Gateway crash loop / stale bootstrap / config drift | launchd, log tails, gateway status |
| H4 | Resource | RAM/swap pressure (chat+face budget) | DiagnosticReports, system load hints |

**Do not set `primary_hypothesis` until both Macs' `report.json` are available.**

## Evidence checklist

- [ ] Mac A `*.report.md` + `*.report.json` pasted
- [ ] Mac B `*.report.md` + `*.report.json` pasted
- [ ] Combined max-score merge across hosts
- [ ] Fast→Slow repair gated on classified H*

## Fast → Slow gates

1. Preflight (`hermes doctor`, gateway status)
2. Archive (already done per host before repair)
3. Stop gateway → targeted doctor fixes
4. Nous re-auth only if H2 confirmed
5. `hermes update` if needed → restart → verify chat + portal

## Findings from collectors

Paste each Mac's Desktop `*.report.md` below. Format expected: `hermes-desktop-ops/report-v1`.
**Do not invent report.json contents.**

### Mac A — adminisorsMBP14

| Field | Value |
|-------|-------|
| hostname | `adminisorsMBP14` |
| incident_id | `hermes-incident-adminisorsMBP14-20260923T203559Z` |
| os / arch | Darwin / arm64 |
| hermes_home | `/Users/administrator/.hermes` |
| Status | **collector SUCCEEDED** — zip + report sidecars written on Desktop |
| Plan status | **completed pending report paste** — terminal evidence only; report body not yet in chat |

Terminal evidence (Dave):

- bootstrap installed to `/Users/administrator/Desktop/hermes-desktop-ops`
- archive ran `hermes backup --quick`, wrote zip, finished
- `report.md` and `report.json` on Desktop; `[bootstrap] collector finished`
- Installed ops listing: `bootstrap`, `archive`, `install-to-desktop`, `lib`, `PLAN_PIPE`, `README` — **no `hermes-desktop-monitor.sh`**
- Interpretation: Mac A pulled an **older tarball snapshot** (pre-monitor / pre-20s-heartbeat). Collector still completed successfully.

**Dave: paste/share these exact files so MOE can classify H1–H4:**

- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.md`
- `~/Desktop/hermes-incident-adminisorsMBP14-20260923T203559Z.report.json`

```markdown
<!-- paste Mac A report.md contents here -->
```

`primary_hypothesis`: **not claimed** (awaiting report paste).

### Mac B — _(pending)_

Collector not yet run. Same bootstrap one-liner as Mac A (see `NEXT_STEPS.md` / `README.md`).

```markdown
<!-- paste Mac B report.md contents here -->
```

### Combined primary hypotheses

<!-- take max score per id across both reports after paste -->

_(empty until Mac A report paste + Mac B collect)_
