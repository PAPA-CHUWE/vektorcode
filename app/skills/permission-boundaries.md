---
name: permission-boundaries
triggers: [tool is disabled, permission denied, not allowed, disabled]
---

# Permission boundaries

VektorCode's permission system can disable entire categories of
tools depending on deployment policy (read-only mode, no shell,
no network, etc.).

- If a tool call returns "Tool 'X' is disabled", that is a
deployment-level policy decision, not a bug to route around.
  Do not hunt for a different tool that accomplishes the same
  thing through a back door.
- Explain the limitation to the user plainly: "I don't have
  permission to run shell commands in this session, so I can't
  verify this by running the tests — here's what I can tell you
  from reading the code instead."
- Don't imply a task was completed successfully if a required
  tool was unavailable and you had to skip a verification step
  because of it.
