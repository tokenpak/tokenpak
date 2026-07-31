---
---

fix(proxy): restore vault context injection for byte-preserved provider requests.

The byte-preserved path now carries retrieved injection text through the
pipeline while keeping the public three-value injection API unchanged. Requests
with relevant vault matches can insert context into the existing system array
without reserializing the rest of the request body.
