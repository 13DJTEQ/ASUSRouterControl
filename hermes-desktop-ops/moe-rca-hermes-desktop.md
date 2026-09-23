# MOE RCA — Hermes Desktop (dual-Mac)

Four-expert RCA for Hermes Desktop failures on Apple Silicon Macs.
Collector evidence pipes in via `PLAN_PIPE.md` / Desktop `*.report.md` sidecars.

## Ranked hypotheses (MOE table)

| ID | Expert | Working title | Mac A score | Confirm with |
|----|--------|---------------|-------------|--------------|
| H1 | Desktop/runtime | Wrong Python / gateway never binds `127.0.0.1:9130` | **7** | doctor, which python, port probe |
| H2 | Auth/Portal | Nous OAuth refresh revoked (`invalid_grant`) | 3 | auth fingerprint, logs (no raw tokens) |
| H4 | Resource | RAM/swap pressure (chat+face budget) | 3 | DiagnosticReports, system load hints |
| H3 | Ops/stability | Gateway crash loop / stale bootstrap / config drift | 2 | launchd, log tails, gateway status |

**Mac A MOE primary (report-ingested):** `H1_desktop_python_gateway`  
**Combined dual-Mac primary:** _(pending Mac B)_ — do not finalize cross-host max-score merge until Mac B `report.json` is pasted.

## Evidence checklist

- [x] Mac A collector manifest ingested
- [x] Mac A `*.report.md` + `*.report.json` pasted / classified
- [ ] Mac B `*.report.md` + `*.report.json` pasted
- [ ] Combined max-score merge across hosts
- [x] Fast→Slow repair gated on classified H* *(Mac A interim H1 path allowed; dual-system confirmation still preferred)*

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

**Status: report-ingested** (uploads `*.report_d816.md` + `*.report_1d79.json`, 2026-09-23).  
**MOE-confirmed primary (Mac A only):** `H1_desktop_python_gateway` — from report-v1 `primary_hypothesis` + ranked evidence (not invented).

| Field | Value |
|-------|-------|
| hostname | `adminisorsMBP14` |
| incident_id | `hermes-incident-adminisorsMBP14-20260923T203559Z` |
| collected_at_utc | `20260923T203559Z` |
| os / arch | Darwin / arm64 |
| hermes_home | `/Users/administrator/.hermes` |
| plan_pipe_format | `hermes-desktop-ops/report-v1` |
| hermes bin | `/Users/administrator/.local/bin/hermes` |
| Status | collector **SUCCEEDED**; zip + report sidecars on Desktop; **reports ingested** |

#### Signals (from report-v1)

| Signal | Value |
|--------|-------|
| hermes on PATH | yes |
| hermes bin | `/Users/administrator/.local/bin/hermes` |
| port 9130 | closed |
| auth.json | present |
| nous mentioned | true |
| refresh_token null | false |
| quarantine/revoke hint | false |

#### Ranked hypotheses (from report.json)

| Rank | ID | Score | Evidence |
|------|----|-------|----------|
| 1 | `H1_desktop_python_gateway` | **7** | `port_9130_closed`, `system_python_cannot_import_hermes_cli`, `venv_ok_but_backend_down_suggests_launcher_uses_wrong_python` |
| 2 | `H2_nous_oauth_rt` | 3 | `nous_mentioned_in_auth`, `doctor_or_gateway_mentions_portal_auth_failure` |
| 3 | `H4_resource_oom` | 3 | `oom_hint_in_gateway.log` |
| 4 | `H3_gateway_ops` | 2 | `gateway_status_unhealthy` |

Upstream for H1: https://github.com/NousResearch/hermes-agent/issues/43913

#### Recommended repair mapping (leading H1)

| Step | Action |
|------|--------|
| 1 | Ensure Desktop / gateway launch uses **venv Python** (not system `/usr/bin/python3`) — matches collector evidence that venv imports `hermes_cli` OK while system Python fails |
| 2 | Restart gateway: `launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"` (or `hermes gateway` helpers) |
| 3 | Recheck `127.0.0.1:9130` open; `hermes gateway status` / `hermes doctor` |
| 4 | Dry-run first: `~/Desktop/hermes-desktop-ops/hermes-desktop-repair.sh` then `--apply` if agreed |
| 5 | **Do not** lead with Nous re-auth on Mac A — H2 is secondary (score 3; `refresh_token_null=false`, quarantine hint false). Revisit H2 only if H1 repair leaves portal auth failures |

Terminal note (Dave): Mac A previously installed an **older** ops drop (no `hermes-desktop-monitor.sh`). Optional re-bootstrap for monitor + live heartbeats; not required for this classification.

### Mac B — _(pending)_

Collector not yet run. Same bootstrap one-liner as Mac A (see `NEXT_STEPS.md` / `README.md`).

```markdown
<!-- paste Mac B report.md contents here -->
```

### Combined primary hypotheses

| ID | Mac A | Mac B | Combined (max) |
|----|-------|-------|----------------|
| H1 | 7 | — | 7 *(Mac A only)* |
| H2 | 3 | — | 3 |
| H4 | 3 | — | 3 |
| H3 | 2 | — | 2 |

**Interim primary (Mac A):** `H1_desktop_python_gateway`  
**Final dual-system primary:** pending Mac B report ingest.
