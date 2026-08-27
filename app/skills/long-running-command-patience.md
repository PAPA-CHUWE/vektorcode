---
name: long-running-command-patience
triggers: [still running, taking a while, no output, hanging, timeout]
---

# Long-running command patience

`run_command` has a 60-second timeout — a legitimately slow
build, install, or test run can take a real chunk of that without
being stuck.

- Don't interpret "no output printed yet" as failure. Some
  commands buffer output and print nothing until they finish.
- If a command actually times out (the tool returns "Error:
  command timed out after 60 seconds"), that's a real signal —
  don't blindly retry the identical command expecting a different
  outcome; consider whether the task needs a longer-running
  background approach (e.g. the watcher tool) instead of a single
  blocking call.
- Distinguish "the command is slow" from "the command is stuck"
  by what it's actually doing, not by how long it's been running.
