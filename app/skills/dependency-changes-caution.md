---
name: dependency-changes-caution
triggers: [requirements.txt, pyproject.toml, pip install, add a dependency, new package]
---

# Dependency changes caution

Editing pyproject.toml or requirements.txt changes what the
entire project needs to run, not just one file:

- State explicitly why a dependency is being added or removed — 
  "adding `requests` for the new HTTP tool" — in your summary,
  not just silently in the diff.
- Don't upgrade or pin an unrelated dependency's version as a side
  effect of an unrelated change.
- If a dependency addition requires installing something via
  `pip install ...`, do that as a visible,
  explained step — don't assume it's already available.
