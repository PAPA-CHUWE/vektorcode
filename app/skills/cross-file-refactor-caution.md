---
name: cross-file-refactor-caution
triggers: [move, rename, relocate, restructure folder, reorganize]
---

# Cross-file refactor caution

Moving or renaming a file/folder can leave other files referring
to the old location or name.

- After moving/renaming something, `search_code` for the old
  path or name across the workspace before reporting the move as
  done — imports, config references, and docs are easy to miss.
- If you find lingering references, either update them as part of
  the same task or tell the user explicitly what you found and
  left unchanged, rather than silently leaving a partially-broken
  reference.
- A move/rename is not "done" just because the file exists at the
  new location — it's done when nothing still expects the old one.
