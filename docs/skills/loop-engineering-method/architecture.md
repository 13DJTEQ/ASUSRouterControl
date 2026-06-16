# Loop Engineering Method Skill Architecture
## Purpose
`loop-engineering-method` is a reusable execution-design skill for software delivery loops. It converts vague implementation goals into closed-loop plans with deterministic completion checks, independent verification, and explicit retry/escalation behavior.

## Design goals
- Minimize false "done" states.
- Keep retries cheap and bounded.
- Support safe parallelism without merge chaos.
- Produce concise leadership-facing status output.

## Architecture
### 1) Trigger and scope selection
The skill is intended for requests about:
- loop engineering / agentic loops
- multi-agent execution architecture
- reliability hardening and quality-gate design
- verifier patterns and executive status summaries

### 2) Planning core (loop compiler)
The planning core maps task input into a 4-phase loop:
1. **Analyze**: outcome, constraints, objective done criteria.
2. **Optimize**: single-loop vs fleet-loop, ownership boundaries, retry policy.
3. **Execute/Verify**: implement, validate, classify failures, re-loop.
4. **Institutionalize**: convert repeated failures into rules and track metrics.

### 3) Verification contract
All plans must use deterministic gates (never model confidence).  
The contract requires:
- objective pass/fail checks
- independent verifier sign-off
- explicit merge conditions
- explicit retry + escalation policy

### 4) Gate-order invariant
Every execution-plan response must include:
- `Fast→Slow Gate Order: ...`

Unordered gate lists are invalid. The gate chain must be explicitly sequenced from cheapest/fastest checks to slowest/highest-cost checks.

### 5) Output interface (Spartan Exec Summary)
Required fields:
- Goal
- Loop Type
- Workstreams
- Gates
- Fast→Slow Gate Order
- Verifier
- Status
- Risks
- Rule Updates
- Next Actions

## Operational guardrails
- Do not mark completion without objective evidence.
- Do not parallelize colliding file ownership without isolation strategy.
- Do not bypass independent verifier approval.
- Do not hide uncertainty; escalate when done criteria are ambiguous.

## Validation status
Current eval pack status after gate-order patch:
- Prior run: `11/12` expectations passed (`91.67%`)
- Rerun: `12/12` expectations passed (`100%`)
- Delta: `+1` expectation, `+8.33` percentage points

