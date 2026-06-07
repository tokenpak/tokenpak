#!/bin/bash
# session-banner.sh — SessionStart hook for tokenpak-claude-code plugin
#
# PURPOSE
#   Prints a one-line banner at the start of every Claude Code session:
#     tokenpak: active (mode=offline|with-proxy, proxy=<URL|unset>, vault=<path|unset>)
#   Works even when the proxy is not running.
#
# NON-INTERACTIVE MODE
#   In non-interactive mode, banner is written to stderr only so that
#   --output-format json / pipe-to-jq stdout stays clean.
#   Non-interactive is detected by:
#     - [[ ! -t 1 ]]  (no TTY on stdout — covers: claude -p, cron, piped output)
#     - CLAUDE_OUTPUT_FORMAT is set and is not "text"
#   Reference: the plugin mode matrix.
#
# CONSTRAINTS
#   - Proxy ping timeout: 0.1s (100ms max) — never blocks session start
#   - No sensitive values in banner (no license keys)
#   - Always exits 0 (loss-tolerant)

# --- Read userConfig from ~/.claude/settings.json ---------------------------
settings_file="${HOME}/.claude/settings.json"
cfg_key='.pluginConfigs["tokenpak-claude-code"]'

proxy_url=""
vault_root=""

if [[ -f "$settings_file" ]]; then
  _cfg() { jq -r "${cfg_key}.${1} // ${2}" "$settings_file" 2>/dev/null || echo "${2//\"/}"; }
  proxy_url_cfg="$(_cfg tokenpak_proxy_url '""')"
  proxy_url_cfg="${proxy_url_cfg//\"/}"
  [[ -n "$proxy_url_cfg" ]] && proxy_url="$proxy_url_cfg"
  vault_root_cfg="$(_cfg vault_root '""')"
  vault_root_cfg="${vault_root_cfg//\"/}"
  [[ -n "$vault_root_cfg" ]] && vault_root="$vault_root_cfg"
fi

# --- Determine mode (proxy reachability check, 100ms max) -------------------
mode="offline"
if [[ -n "$proxy_url" ]]; then
  if curl -sfm 0.1 "${proxy_url}/healthz" -o /dev/null 2>/dev/null; then
    mode="with-proxy"
  fi
fi

# --- Build banner line -------------------------------------------------------
banner="tokenpak: active (mode=${mode}, proxy=${proxy_url:-unset}, vault=${vault_root:-unset})"

# --- Output: stderr-only in non-interactive mode ----------------------------
non_interactive=0
if [[ ! -t 1 ]]; then
  non_interactive=1
elif [[ -n "${CLAUDE_OUTPUT_FORMAT:-}" ]] && [[ "${CLAUDE_OUTPUT_FORMAT}" != "text" ]]; then
  non_interactive=1
fi

if [[ "$non_interactive" -eq 1 ]]; then
  printf '%s\n' "$banner" >&2
else
  printf '%s\n' "$banner"
fi

exit 0
