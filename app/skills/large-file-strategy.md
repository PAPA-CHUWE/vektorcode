---
name: large-file-strategy
triggers: [large file, big file, too big, truncation, PRESERVE_HARD_CAP_CHARS, context limit]
---

# Large file strategy

When a file is too large for one `read_file` call to be useful due to
the `PRESERVE_HARD_CAP_CHARS` truncation from context.py:

- Use `search_code` with a pattern near what you're looking for to
  locate the relevant section.
- Read the file only once and work with the truncated content if it
  contains the needed information; otherwise, search for specific
  patterns.
- Avoid trying to page through the file with multiple reads — each
  read will give the same truncated beginning.
- If you need to understand the file's structure, consider reading
  the file's index or summary (e.g., table of contents, class
  definitions) via search or by reading a small portion that likely
  contains them.
