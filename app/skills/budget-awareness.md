---
name: budget-awareness
triggers: [budget, cost, expensive, how much, token usage, cheaper]
---

# Budget awareness

VektorCode runs against a daily dollar budget (default $1/day). Keep
this in mind without being asked:

- Prefer `search_code` over reading many files one by one — it costs
  far fewer tokens to find the right file first, then read only that
  one.
- Don't re-read a file you already have in context this turn.
- If a task is genuinely large (e.g. "review the whole codebase"),
  say so up front and propose a narrower first step, rather than
  trying to do it all in one exhaustive tool-call chain.
- If you're told the daily budget has been exhausted, that's a hard
  stop, not a suggestion — don't attempt workarounds like retrying
  or switching models mid-task to route around it.
