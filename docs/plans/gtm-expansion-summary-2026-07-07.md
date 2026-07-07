# ASUSRouterControl — GTM Expansion Plan Summary

**Date**: 2026-07-07
**Status**: Finalized (3 adversarial iterations complete)
**Decision**: PROCEED with conditions

## Executive Summary

ASUSRouterControl is a Python-based ASUS router monitoring and optimization tool with SSH probes, NVRAM tuning, incident rollback, and multi-provider speed testing. After 3 iterations of market analysis and adversarial pricing/revenue critique, the plan is validated as a **lifestyle business / community project** — not venture-scale. Target: $500–2,000 MRR by Year 2.

## Current State

- **Hardware**: Targets RT-AC68U only (2014, EOL)
- **Platform**: macOS-only (CLI + menubar app)
- **Library**: `asusrouter` v1.21.3 supports 80+ models (WiFi 5/6/6E/7, ZenWiFi mesh)
- **Competitors**: HA integration (basic), FreeSDN (vendor-agnostic), OpenWISP (OpenWrt), ISPApp ($0.60/device/mo), **Ubiquiti UniFi (free, massive mindshare)**
- **Differentiation**: No standalone tool offers SSH probes + NVRAM optimization + incident rollback + multi-provider speed testing for ASUS routers

## Positioning Pivot

**"ASUS Router Optimization & Tuning Tool"** — NOT "network management dashboard."

Compete on what UniFi can't do: SSH probes, NVRAM optimization, firmware health monitoring, incident rollback. Niche optimization for existing ASUS owners.

## 4-Phase Roadmap

### Phase 1 — TAM Explosion (Weeks 1–4)
- Multi-model support declaration (10x TAM, near-zero effort)
- AiMesh mesh network monitoring
- Web dashboard + Docker (cross-platform)
- **Investment**: ~$1,500 | **Revenue**: Cost center

### Phase 2 — Differentiation Moat (Weeks 5–10)
- Home Assistant integration (highest-ROI acquisition channel)
- Prometheus/Grafana export (organic viral growth)
- Mobile push notifications (ntfy.sh / Pushover)
- **Investment**: ~$3,000 | **Revenue**: Cost center

### Phase 3 — Prosumer/Small Business (Weeks 11–18)
- Multi-router fleet management (2–20 routers)
- Cloud telemetry (opt-in remote access)
- REST API + Plugin SDK
- **Investment**: ~$12,000 | **Revenue**: Paid tier launch

### Phase 4 — Premium Intelligence (Weeks 19–26)
- ML anomaly detection (local scikit-learn/ONNX)
- Predictive maintenance
- Plugin marketplace
- **Investment**: ~$8,000 | **Revenue**: Churn reduction + premium pricing

## Feature Priority (GTM Value Order)

1. Multi-model support declaration — 10x TAM
2. Web dashboard / Docker — removes macOS lock-in
3. AiMesh monitoring — fastest-growing ASUS segment
4. Home Assistant integration — access to existing user base
5. Prometheus/Grafana export — organic viral growth
6. Multi-router fleet management — opens paid tier
7. Mobile push notifications — daily engagement
8. Cloud telemetry / remote access — recurring revenue
9. REST API + Plugin SDK — ecosystem lock-in
10. ML anomaly detection — premium differentiation
11. Predictive maintenance — premium stickiness
12. Plugin marketplace — long-term ecosystem

## Pricing (Revised After Critique)

| Tier | Price | Routers | Key Features |
|------|-------|---------|--------------|
| Community | Free | 1 | Self-hosted, CLI + web, core telemetry |
| Pro | $2.99/mo ($29/yr) | 3 | Cloud telemetry, push alerts, Grafana |
| Business | $9.99/mo ($99/yr) | 10 | REST API, plugin SDK, priority support |
| MSP | $99/mo ($990/yr) | 100 | White-label, SLA, multi-tenant (post-Phase 4) |
| Enterprise | $299–999/mo | 100–500 | SSO/SAML, audit, on-premise option |

Tier split: 85% Pro / 12% Business / 3% MSP. Blended ARPU: ~$5.50/mo.

## Market Sizing (Revised)

- **TAM**: ~100K–500K engaged ASUS router users (NOT 15M total shipments)
- **SAM**: ~50K–200K in English-speaking technical communities
- **SOM Y1**: 200–2,000 reachable users
- **TAM ceiling**: `asusrouter` library gets 38K PyPI downloads/month (includes HA installs)

## Revenue Scenarios

| Metric | Conservative | Moderate | Aggressive |
|--------|-------------|----------|------------|
| Y1 free users | 200 | 800 | 2,000 |
| Y1 conversion | 0.5% | 1.5% | 3% |
| Y1 paying (net) | 1 | 12 | 60 |
| MRR month 12 | $5 | $66 | $330 |
| MRR month 24 | $50 | $400 | $1,200 |
| MRR month 36 | $200 | $1,500 | $3,000 |
| Break-even | Month 30+ | Month 18–24 | Month 12–15 |

## Unit Economics

- **LTV**: $55 per paid user (10% monthly churn)
- **CAC**: UNLIMITED (community-driven; no paid acquisition budget)
- **Monthly churn**: 10–15%
- **Gross margin**: ~85%

## Critical Blockers (Must Address Before Proceeding)

1. **Fork `asusrouter` library NOW** — single maintainer (Vaskivskyi, 651/651 contributions) is a blocker. Build abstraction layer. Contribute upstream.
2. **Security audit before paid tier** — tool stores credentials, makes SSH connections, writes NVRAM. Never store router credentials in cloud. Threat model required.
3. **MSP tier deferred** — until post-Phase 4. Requires sales/onboarding/support resources a 1–2 person team doesn't have.

## Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| ASUS builds this | 12–18 month window; focus on features ASUS won't build |
| HA integration covers features | Go deeper (SSH, NVRAM, rollback) where HA stays shallow |
| Library breaks on firmware | Fork NOW; abstraction layer; contribute upstream |
| FreeSDN/OpenWISP expand to ASUS | Remain ASUS-specialized with deeper telemetry |
| UniFi is real competitor | Don't compete on network management; compete on ASUS optimization |
| Security breach | Never store router creds in cloud; self-hosted default |
| Single maintainer burnout | Fork library, sponsor Vaskivskyi, build abstraction layer |
| Cloud costs scale with users | Self-hosted is DEFAULT; limits infra exposure |
| No paid acquisition budget | Community-driven growth is binary; optimize for it |

## Go/No-Go Decision

**PROCEED** if all conditions met:
1. ✅ Fork `asusrouter` library before Phase 1
2. ✅ Security audit before Phase 3 paid tier
3. ✅ Positioning pivoted to "ASUS router optimization/tuning"
4. ✅ Self-hosted is default, cloud is optional
5. ✅ Revenue expectations set to lifestyle business ($500–2,000 MRR by Y2)

**DO NOT PROCEED** if:
- Library fork not feasible (no maintainer capacity)
- Security audit cannot be completed
- Expectations are venture-scale ($76K/mo by Y3)
- Team cannot commit 12–18 months

## Success Criteria

- **Year 1**: 200–2,000 free users, 1–60 paying, $5–330 MRR
- **Year 2**: 1,000–5,000 free users, 50–300 paying, $400–1,200 MRR
- **Year 3**: 3,000–10,000 free users, 200–800 paying, $1,500–3,000 MRR

## Iteration History

**Iteration 1** — Initial model: TAM 15M (fabricated), pricing $5/$15, Y1 MRR $349–$8,720, conversion 2–8%, churn 5%.

**Iteration 2** — Adversarial critique: TAM revised to 100K–500K, pricing to $2.99/$9.99/$99, Y1 MRR to $5–$330, conversion to 0.5–3%, churn to 10–15%. Critical blockers identified. Positioning pivot mandated.

**Iteration 3** — Final synthesis: All critique incorporated. Pricing tiers finalized with enterprise option. Revenue projections grounded in reality. Risk mitigations specified.

---

*Full plan document: `docs/plans/gtm-expansion-plan-final.md`*
