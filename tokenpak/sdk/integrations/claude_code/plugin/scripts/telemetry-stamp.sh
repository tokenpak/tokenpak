#!/usr/bin/env bash
# telemetry-stamp.sh — PostToolUse hook for tokenpak-claude-code plugin
#
# PURPOSE
#   Writes one JSONL line per tool call to:
#     ${CLAUDE_PLUGIN_DATA}/telemetry/YYYY-MM-DD.jsonl
#   Fields: {session_id, ts, tool_name, file_path, exit_code, duration_ms}
#   Default-on. Optionally POSTs the same line to the tokenpak proxy.
#
# ATOMICITY (TMUX concurrent-write safety)
#   Uses '>>' (O_APPEND) for appending. POSIX.1-2017 §write guarantees that
#   writes < PIPE_BUF (4096 bytes on Linux) to a file opened with O_APPEND
#   are atomic and serialized — no torn writes between concurrent appenders.
#   This is safe for TMUX multi-pane mode where multiple Claude Code sessions
#   on the same host append to the same daily JSONL file.
#   Lines are hard-capped at 4096 bytes before append. If the JSONL line
#   exceeds this (e.g. very long file_path), the file_path field is
#   truncated with a "[truncated]" suffix so the line stays under PIPE_BUF.
#   Reference: POSIX.1-2017 §write rationale; CCP-22 matrix row
#   "telemetry-stamp hook" / TMUX column.
#
# LOSS-TOLERANT
#   Always exits 0, even on disk-full, permission-denied, or
#   network-unreachable errors.
#
# SCHEMA (proposed by CCP-17; must match CCI-09 plugin-telemetry ingest)
#   {
#     "session_id":  string  -- CLAUDE_SESSION_ID or from hook context
#     "ts":          string  -- ISO-8601 UTC timestamp (e.g. "2026-04-08T17:00:00Z")
#     "tool_name":   string  -- name of the tool called (e.g. "Edit", "Bash")
#     "file_path":   string  -- primary file path if available, else ""
#     "exit_code":   number  -- tool exit code (0 = success)
#     "duration_ms": number  -- tool execution duration in ms (0 if unavailable)
#   }
#   CCI-09 COORDINATION NOTE: CCI-09 (plugin-telemetry ingest endpoint at
#   /v1/plugin-telemetry) had not shipped as of CCP-17 authoring (2026-04-08).
#   Schema above is proposed by CCP-17. CCI-09 must match this exact shape
#   when implemented. No sensitive data is included (no license keys, no raw
#   prompt text, no file contents).
#
# PROXY POST
#   Gated behind ALL of:
#     enable_telemetry_hook=true   (pluginConfigs master switch)
#     enable_proxy_telemetry_post=true  (separate opt-in for network sharing)
#     proxy reachable              (HEAD check, 200ms timeout)
#   POST endpoint: http://${tokenpak_proxy_url}/v1/plugin-telemetry
#   POST timeout:  0.5s (curl -m 0.5) — never blocks tool completion

# --- Read hook context from stdin ------------------------------------------
context="$(cat 2>/dev/null)" || context="{}"

# --- Extract fields from hook context --------------------------------------
_jq_field() {
  printf '%s' "$context" | jq -r "$1" 2>/dev/null || echo ""
}

tool_name="$(_jq_field '.tool_name // ""')"
# file_path may be at .file_path or nested in .tool_input.file_path
file_path="$(_jq_field '.file_path // .tool_input.file_path // ""')"
exit_code="$(_jq_field '.exit_code // 0')"
duration_ms="$(_jq_field '.duration_ms // 0')"

# Sanitize exit_code and duration_ms to integers
[[ "$exit_code" =~ ^-?[0-9]+$ ]] || exit_code=0
[[ "$duration_ms" =~ ^[0-9]+$ ]] || duration_ms=0

# session_id: prefer env var, fall back to hook context
session_id="${CLAUDE_SESSION_ID:-}"
if [[ -z "$session_id" ]]; then
  session_id="$(_jq_field '.session_id // ""')"
fi

# Timestamp (UTC, ISO-8601)
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || ts="1970-01-01T00:00:00Z"

# --- Read userConfig from ~/.claude/settings.json --------------------------
settings_file="${HOME}/.claude/settings.json"
cfg_key='.pluginConfigs["tokenpak-claude-code"]'

enable_telemetry="true"
enable_proxy_post="false"
proxy_url="http://localhost:8766"

if [[ -f "$settings_file" ]]; then
  _cfg() { jq -r "${cfg_key}.${1} // ${2}" "$settings_file" 2>/dev/null || echo "${2//\"/}"; }
  enable_telemetry="$(_cfg enable_telemetry_hook true)"
  enable_proxy_post="$(_cfg enable_proxy_telemetry_post false)"
  proxy_url_cfg="$(_cfg tokenpak_proxy_url '""')"
  # Strip surrounding quotes if jq returned a JSON string
  proxy_url_cfg="${proxy_url_cfg//\"/}"
  [[ -n "$proxy_url_cfg" ]] && proxy_url="$proxy_url_cfg"
fi

# --- Build JSONL line -------------------------------------------------------
_build_line() {
  local fp="$1"
  jq -nc \
    --arg     session_id  "$session_id" \
    --arg     ts          "$ts" \
    --arg     tool_name   "$tool_name" \
    --arg     file_path   "$fp" \
    --argjson exit_code   "$exit_code" \
    --argjson duration_ms "$duration_ms" \
    '{session_id:$session_id,ts:$ts,tool_name:$tool_name,file_path:$file_path,exit_code:$exit_code,duration_ms:$duration_ms}' \
    2>/dev/null
}

line="$(_build_line "$file_path")"
if [[ -z "$line" ]]; then
  # jq unavailable or failed — construct minimal safe line manually
  # Escape the fields to avoid JSON injection (keep it simple: strip quotes/backslashes)
  safe_tool="${tool_name//[\"\\]/}"
  safe_fp="${file_path//[\"\\]/}"
  safe_sid="${session_id//[\"\\]/}"
  line="{\"session_id\":\"${safe_sid}\",\"ts\":\"${ts}\",\"tool_name\":\"${safe_tool}\",\"file_path\":\"${safe_fp}\",\"exit_code\":${exit_code},\"duration_ms\":${duration_ms}}"
fi

# Enforce 4096-byte cap (PIPE_BUF atomicity guarantee)
if [[ ${#line} -ge 4096 ]]; then
  line="$(_build_line "[truncated]")"
  # If still somehow >= 4096 (degenerate: other fields very long), hard-truncate
  if [[ -z "$line" ]] || [[ ${#line} -ge 4096 ]]; then
    line="{\"session_id\":\"${session_id:0:64}\",\"ts\":\"${ts}\",\"tool_name\":\"${tool_name:0:64}\",\"file_path\":\"[truncated]\",\"exit_code\":${exit_code},\"duration_ms\":${duration_ms}}"
  fi
fi

# --- Write JSONL ------------------------------------------------------------
date_str="$(date -u +%Y-%m-%d 2>/dev/null)" || date_str="0000-00-00"
plugin_data_dir="${CLAUDE_PLUGIN_DATA:-${HOME}/.claude/plugin-data/tokenpak-claude-code}"
telemetry_dir="${plugin_data_dir}/telemetry"
jsonl_file="${telemetry_dir}/${date_str}.jsonl"

{
  mkdir -p "$telemetry_dir"
  printf '%s\n' "$line" >> "$jsonl_file"
} 2>/dev/null || true   # loss-tolerant: disk-full, permission-denied → continue silently

# --- Proxy POST (optional, gated) ------------------------------------------
if [[ "$enable_telemetry" == "true" ]] && [[ "$enable_proxy_post" == "true" ]]; then
  # Reachability check: short HEAD probe, 200ms timeout
  if curl -sfm 0.2 -o /dev/null --head "${proxy_url}/" 2>/dev/null; then
    curl -sfm 0.5 \
      -X POST \
      -H "Content-Type: application/json" \
      -d "$line" \
      "${proxy_url}/v1/plugin-telemetry" 2>/dev/null || true
  fi
fi

exit 0
