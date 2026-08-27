---
name: ambiguous-instruction-defaults
triggers: [clean this up, fix it, improve, make it better, refactor, tidy]
---

# Ambiguous instruction defaults

When the user's instruction is ambiguous, pick the most reasonable interpretation
and execute it immediately. For coding agents, "reasonable" means:

- "Clean this up" → remove commented-out code, fix formatting, delete unused
  imports/variables, apply linter fixes. Does not mean rewriting logic or
  changing architecture unless those are clearly broken.
- "Fix it" → address the specific error or failure mode mentioned; if none,
  look for obvious bugs in the relevant area.
- "Improve performance" → first identify bottlenecks via profiling or
  reasoning; optimize hot paths, not micro-optimizations everywhere.
- "Make it more secure" → fix known vulnerabilities (e.g. SQL injection paths),
  add input validation where data comes from untrusted sources, not a
  ground-up security audit.
- "Add tests" → write unit tests for the core functionality; aim for
  covering edge cases, not 100% line coverage if that would require
  testing trivial getters/setters.

If still unsure after applying these heuristics, choose the interpretation
that involves the least change that still plausibly addresses the request.
