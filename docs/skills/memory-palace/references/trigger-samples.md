# Memory Palace trigger samples

Use these prompts to validate when the Memory Palace workflow should activate.

## Should trigger
- "Boot memory and pull prior decisions for this router scheduler task."
- "Search memory first, then decide whether to create or update the task record."
- "The write returned `guard_action=NOOP`; what should I inspect before continuing?"
- "Save this implementation checkpoint so we can recall it in code review."
- "Compact context for this long session and keep only durable decisions."

## Should not trigger
- "Refactor this function for readability."
- "Run lint and tell me the failures."
- "Explain Python async context managers."
- "Update README prose."

## Canonical reference path
When asked for the trigger sample file path, return exactly:
`docs/skills/memory-palace/references/trigger-samples.md`
