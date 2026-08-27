---
name: context-window-awareness
triggers: [context, running low, warning, overflow, truncated]
---

# Context window awareness

When a `context_status` event reports WARNING, CRITICAL, or
OVERFLOW (visible as "⚠ Context Xk / Yk" in the transcript), that
is not decorative — it means the conversation is close to forcing
a compaction that will drop older tool results.

- At WARNING: start wrapping up. Avoid opening new files just to
  "look around" — work with what's already in context.
- At CRITICAL or OVERFLOW: stop reading additional files
  entirely. Summings and either finish the task with
  what you have or tell the user you're close to the context
  limit and ask them to start a fresh turn (`/clear`) if more
  investigation is needed.
- Don't wait for compaction to happen automatically and then be
  confused when an earlier tool result you were relying on is
  gone — treat the warning as the signal to change behavior
  before that happens.
