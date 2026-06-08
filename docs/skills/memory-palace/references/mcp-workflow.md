# Memory Palace MCP workflow

## Purpose
Use this workflow to make Memory Palace a repeatable part of daily development for this repository during planning, implementation, and review.

## Session start (required)
1. Call `read_memory("system://boot")` before the first real memory operation.
2. If the target URI is known, call `read_memory(uri)` directly.
3. If the target URI is unknown, call `search_memory(query, include_session=true)` and then read the selected URI.

## Standard URI layout
Use a stable URI pattern so all contributors can find task context quickly:
- `project://asusroutercontrol/tasks/<yyyy-mm-dd>-<slug>/plan`
- `project://asusroutercontrol/tasks/<yyyy-mm-dd>-<slug>/implementation`
- `project://asusroutercontrol/tasks/<yyyy-mm-dd>-<slug>/review`
- `project://asusroutercontrol/standards/<topic>`

Use `update_memory` instead of creating a duplicate when an existing URI already covers the same task.

## Planning cycle
1. Boot context (`system://boot`) and recall related task memory.
2. Record assumptions, constraints, and success criteria in the task `.../plan` URI.
3. Link related prior decisions or standards URIs in the same entry.

## Implementation cycle
1. Read `.../plan` before coding.
2. Update `.../implementation` at each significant checkpoint with:
   - decisions made,
   - files touched,
   - validation commands and outcomes.
3. Keep entries concise and factual; do not store secrets.

## Review cycle
1. Read `.../plan` and `.../implementation`.
2. Write `.../review` with:
   - what was verified,
   - open risks or follow-ups,
   - rollback path if changes are high impact.
3. Add/refresh durable standards under `project://asusroutercontrol/standards/<topic>` when a pattern should be reused.

## Mutation safety rules
- Read before any mutation (`create_memory`, `update_memory`, `delete_memory`, `add_alias`).
- If `guard_action=NOOP|UPDATE|DELETE`, stop and inspect `guard_target_uri` / `guard_target_id` before continuing.
- Prefer `update_memory` on the guard target instead of creating near-duplicate records.

## Maintenance operations
- Use `compact_context(force=false)` only after long/noisy sessions where the thread needs distillation.
- Run `index_status()` before `rebuild_index(wait=true)` unless immediate rebuild is explicitly required.
