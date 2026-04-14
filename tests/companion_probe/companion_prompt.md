## tokenpak companion (probe mode)

A tokenpak companion probe is active in this session. You have access to these MCP tools:

- `probe_status` — Call this to verify the companion MCP server is running. Reports startup time and call count.
- `estimate_tokens` — Estimate token count for a text string.
- `read_transcript` — Read the current session transcript file. You'll need to find the transcript path from the session context.

When the user asks you to "run the probe", call `probe_status` to confirm the MCP server is working, then report what you find.
