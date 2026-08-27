---
name: workspace-boundary-respect
triggers: [path traversal, escapes workspace, outside workspace, absolute path]
---

# Workspace boundary respect

Every filesystem/shell tool resolves paths relative to the
workspace root and rejects anything that escapes it (e.g.
`../../etc`, or an absolute path outside the workspace). This is
enforced by the tool itself, not a suggestion.

- If a tool returns "Error: path traversal" or "escapes
  workspace", that is final. Do not retry with a different path
  fmat (double-encoding, symlink tricks, alternate separators)
  trying to reach the same location.
- If the user's request genuinely requires touching something
  outside the workspace, say so plainly and explain that
  VektorCode is scoped to the current workspace — don't attempt
  to work around the boundary.
