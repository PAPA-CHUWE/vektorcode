---
name: tool-argument-precision
triggers: [bad arguments, missing argument, unexpected keyword, TypeError, run_command, write_file, read_file]
---

# Tool argument precision

Tool calls fail when arguments don't match what the tool actually
accepts. Before calling a tool:

- `read_file` takes only `path`. There is no `start`, `offset`, or
  `line_range` argument — if you need part of a file, read the whole
  thing and reason over the portion you need, or use `search_code`
  with a pattern near what you're looking for.
- `write_file` requires both `path` AND `content` — never call it
  with just a path.
- `run_command` requires `command` as a plain string — never call it
  with no arguments, and never call it twice in a row with the exact
  same command expecting a different result. If a tool call fails with
  "Bad arguments for X: ...", that error message includes the tool's
  actual expected parameters. Read it and fix the call — don't guess
  a different set of arguments and try again blind, and don't repeat
  the same malformed call.
