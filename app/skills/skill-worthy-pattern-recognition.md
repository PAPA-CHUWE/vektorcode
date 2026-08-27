---
name: skill-worthy-pattern-recognition
triggers: []
---

# Recognizing skill-worthy patterns

This skill has no triggers — it's intentionally never
keyword-matched by the loader. If you want the agent to
self-nudge toward flagging repeated explanations as candidates
for new skills, this needs a different injection mechanism than
keyword matching (e.g. always included, or triggered by a
session-level counter of repeated explanations) — decide
deliberately whether you want this behavior before wiring it in,
since it changes what the agent volunteers unprompted rather than
just how it responds to a given request.

# Body (for when/if you do wire it in):

If you notice you're explaining the same workaround, convention,
or caveat to the user more than once in a session, say so
explicitly at the end of your answer — "this seems like something
worth turning into a skill" — rather than silently repeating the
same explanation each time it comes up.
