---
name: network-request-caution
triggers: [request, fetch, api call, external url, webhook]
---

# Network request caution

`http_request` can reach the open internet, unlike every other tool
in this project (filesystem/search/shell are workspace-scoped):

- Non-localhost requests require human confirmation — that's
enforced by the runtime, not optional. Don't describe this as an
obstacle to route around; explain to the user what request you
want to make and why, and let the confirmation happen naturally.
- Don't send credentials, API keys, or file contents to an external
URL unless the user explicitly asked you to and the destination is
one they named.
- A failed request is not automatically retried — decide deliberately
whether retrying makes sense (transient network issue) or whether
the failure indicates something that won't change on retry (bad
URL, wrong auth, malformed request body).
