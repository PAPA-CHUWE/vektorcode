---
name: test-runner-output-interpretation
triggers: [tests pass, test results, all tests, pytest output]
---

# Test runner output interpretation

The test runner's pass/fail parsing is a simple heuristic (scans
the last couple of output lines for the word "passed"/"failed") — it
can misparse unusual pytest output formats.

- If parsed results show 0 passed AND 0 failed, or don't look
  like they match what you'd expect from the command you ran,
  treat that as UNVERIFIED, not as "no tests exist" or "all
  passed by default."
- When in doubt, look at the raw stdout/stderr the tool actually
  returned, not just the parsed passed/failed numbers, before
  reporting a result to the user.
- Never report "all tests pass" based on a parse you haven't
  sanity-checked against the actual command output at least once
  in the conversation.
