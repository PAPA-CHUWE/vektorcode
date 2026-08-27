---
name: secrets-and-credentials-handling
triggers: [api key, secret, token, credentials, .env, password]
---

# Secrets and credentials handling

`get_config()` already masks API keys in its own output
(`sk-a...b3c9` style) — follow that same discipline everywhere:

- Never print a full, unmasked API key, token, or password to
  console output, even if you read one from a `.env` file or
  config to answer a question. Mask it the same way get_config()
  does, or just confirm it exists without displaying the value.
- Never write a secret into a file that could plausibly get
  committed (README, code comments, test fixtures) — if a task
  requires a real credential for testing, use an obvious
  placeholder and say so.
- Reading `.env` contents to answer "what's my model set to" is
  fine; reading it to display the whole file verbatim is not,
  when it also contains API keys.
