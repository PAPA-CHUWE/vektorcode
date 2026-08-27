---
name: git-commit-hygiene
triggers: [commit, diff, review, split commit, commit message]
---

# Git commit hygiene

When a diff is too large to review in one shot, split into multiple commits:
- Separate functional changes from refactoring/formatting.
- Each commit should have a single, clear purpose.
- Write commit messages that reflect what was *verified* (tests pass, lint clean) vs *attempted* (tried but unsure).
- If a commit touches many files, ask: could this be split by subsystem or by concern?
- Use `git add -p` to stage hunks individually when needed.
