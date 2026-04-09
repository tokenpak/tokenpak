#!/usr/bin/env bash
# tokenpak-status check.sh — shell-level probes for the /tokenpak-status skill
# Outputs KEY=VALUE lines; Claude reads these to populate the status sections.
# Exit 0 always — never block the skill.

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Plugin version (from plugin.json next to this script's skill dir)
# ---------------------------------------------------------------------------
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
PLUGIN_VERSION="unknown"
if [[ -n "$PLUGIN_ROOT" && -f "${PLUGIN_ROOT}/plugin.json" ]]; then
    PLUGIN_VERSION=$(python3 -c "
import json, sys
try:
    d = json.load(open('${PLUGIN_ROOT}/plugin.json'))
    print(d.get('version', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
fi
echo "PLUGIN_VERSION=${PLUGIN_VERSION}"

# ---------------------------------------------------------------------------
# 2. Package version
# ---------------------------------------------------------------------------
PKG_VERSION=$(python3 -c "import tokenpak; print(tokenpak.__version__)" 2>/dev/null || echo "unknown")
echo "PKG_VERSION=${PKG_VERSION}"

# ---------------------------------------------------------------------------
# 3. Vault root resolution
# ---------------------------------------------------------------------------
VAULT_ROOT="${TOKENPAK_VAULT_ROOT:-}"
if [[ -z "$VAULT_ROOT" && -n "$PLUGIN_ROOT" ]]; then
    VAULT_ROOT=$(python3 -c "
import json, os, pathlib
try:
    settings = pathlib.Path('${PLUGIN_ROOT}').parent.parent.parent / 'settings.json'
    d = json.loads(settings.read_text())
    vr = d.get('pluginConfigs', {}).get('tokenpak-claude-code', {}).get('vault_root', '')
    print(vr.strip())
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

VAULT_STATUS="unset"
INDEX_STATUS="unset"
if [[ -n "$VAULT_ROOT" ]]; then
    if [[ -d "$VAULT_ROOT" ]]; then
        VAULT_STATUS="$VAULT_ROOT"
        if [[ -d "${VAULT_ROOT}/.tokenpak" ]]; then
            INDEX_STATUS="present"
        else
            INDEX_STATUS="absent"
        fi
    else
        VAULT_STATUS="invalid (not a directory)"
    fi
fi
echo "VAULT_ROOT=${VAULT_STATUS}"
echo "INDEX_STATUS=${INDEX_STATUS}"

# ---------------------------------------------------------------------------
# 4. Proxy ping
# ---------------------------------------------------------------------------
PROXY_URL="${ANTHROPIC_BASE_URL:-}"
if [[ -z "$PROXY_URL" && -n "$PLUGIN_ROOT" ]]; then
    PROXY_URL=$(python3 -c "
import json, pathlib
try:
    settings = pathlib.Path('${PLUGIN_ROOT}').parent.parent.parent / 'settings.json'
    d = json.loads(settings.read_text())
    print(d.get('pluginConfigs', {}).get('tokenpak-claude-code', {}).get('proxy_url', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

PROXY_PING="unconfigured"
if [[ -n "$PROXY_URL" ]]; then
    HEALTH_URL="${PROXY_URL%/}/health"
    START_MS=$(date +%s%3N 2>/dev/null || echo "0")
    if curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
        END_MS=$(date +%s%3N 2>/dev/null || echo "0")
        ELAPSED=$((END_MS - START_MS))
        PROXY_PING="ok (${ELAPSED}ms)"
    else
        PROXY_PING="offline"
    fi
fi
echo "PROXY_URL=${PROXY_URL:-unconfigured}"
echo "PROXY_PING=${PROXY_PING}"

# ---------------------------------------------------------------------------
# 5. Hooks status (parse hooks.json)
# ---------------------------------------------------------------------------
HOOKS_FILE="${PLUGIN_ROOT:-}/hooks/hooks.json"
if [[ -f "$HOOKS_FILE" ]]; then
    python3 -c "
import json
try:
    hooks = json.load(open('${HOOKS_FILE}'))
    known = {
        'protect-paths': 'HOOK_PROTECT_PATHS',
        'post-edit-validation': 'HOOK_POST_EDIT',
        'telemetry-stamp': 'HOOK_TELEMETRY',
        'review-prep': 'HOOK_REVIEW_PREP',
        'session-start-banner': 'HOOK_SESSION_BANNER',
    }
    declared = {}
    for h in hooks.get('hooks', []):
        hname = h.get('name', '')
        declared[hname] = 'enabled' if h.get('enabled', True) else 'disabled'
    for hname, envkey in known.items():
        print(f'{envkey}={declared.get(hname, \"not found\")}')
except Exception as e:
    for envkey in ['HOOK_PROTECT_PATHS', 'HOOK_POST_EDIT', 'HOOK_TELEMETRY', 'HOOK_REVIEW_PREP', 'HOOK_SESSION_BANNER']:
        print(f'{envkey}=parse-error')
" 2>/dev/null
else
    echo "HOOK_PROTECT_PATHS=not found"
    echo "HOOK_POST_EDIT=not found"
    echo "HOOK_TELEMETRY=not found"
    echo "HOOK_REVIEW_PREP=not found"
    echo "HOOK_SESSION_BANNER=not found"
fi

# ---------------------------------------------------------------------------
# 6. Mode detection
# ---------------------------------------------------------------------------
MODE="CLI"

if [[ -n "${CRON_INVOCATION:-}" ]]; then
    MODE="cron"
elif [[ "${TERM_PROGRAM:-}" == "cursor" || "${TERM_PROGRAM:-}" == "Windsurf" ]]; then
    MODE="IDE-unsupported"
elif [[ "${TERM_PROGRAM:-}" == "vscode" ]]; then
    MODE="IDE-VSCode"
elif [[ -n "${TMUX:-}" ]]; then
    MODE="TMUX"
elif [[ ! -t 0 ]]; then
    MODE="non-interactive"
fi
echo "MODE=${MODE}"
