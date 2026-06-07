#!/usr/bin/env bash
# tokenpak-uninstall.sh — Remove TokenPak ↔ OpenClaw integration artifacts.
#
# CURRENT SCOPE (2026-04-28):
#   - Removes the openclaw-adapter hook bundle installed by the inject step:
#       * ~/.openclaw/hooks/openclaw-adapter/ (directory)
#       * hooks.internal.entries.openclaw-adapter (in ~/.openclaw/openclaw.json)
#   - Idempotent — re-running on a clean host is a safe no-op.
#   - Backs up openclaw.json with .bak.<timestamp> before mutation.
#   - Does NOT touch tokenpak-telemetry uninstall behavior.
#   - Does NOT remove tokenpak-* providers / auth / allowlist entries
#     installed by tokenpak-inject.sh — those are out of scope here.
#     A broader uninstaller (covering provider revert, systemd drop-ins,
#     and ~/.tokenpak/ caches) once lived at integrations/openclaw/
#     pre-restructure but is not part of this scope; future changes can
#     restore that scope on top of this skeleton.
#
# DOCS:
#   https://docs.tokenpak.ai/integrations/openclaw#uninstall

set -euo pipefail

log() { printf '[tokenpak-uninstall] %s\n' "$*"; }

uninstall_openclaw_adapter() {
    local target_dir="${HOME}/.openclaw/hooks/openclaw-adapter"
    local oc_json="${HOME}/.openclaw/openclaw.json"

    # 1. Remove bundle dir
    if [ -d "${target_dir}" ]; then
        rm -rf "${target_dir}"
        echo "[openclaw-adapter] hook directory removed"
    else
        echo "[openclaw-adapter] hook directory not present (skipped)"
    fi

    # 2. Remove openclaw.json entry
    if [ ! -f "${oc_json}" ]; then
        echo "[openclaw-adapter] WARN: ${oc_json} not found; skipping JSON cleanup" >&2
        return 0
    fi

    local has_entry
    has_entry=$(jq 'has("hooks") and (.hooks.internal.entries // {} | has("openclaw-adapter"))' "${oc_json}" 2>/dev/null || echo false)
    if [ "${has_entry}" = "true" ]; then
        local backup="${oc_json}.bak.$(date +%Y%m%d%H%M%S)"
        cp "${oc_json}" "${backup}"
        jq 'del(.hooks.internal.entries["openclaw-adapter"])' "${oc_json}" > "${oc_json}.tmp" \
            && mv "${oc_json}.tmp" "${oc_json}"
        echo "[openclaw-adapter] openclaw.json entry removed (backup: ${backup})"
    else
        echo "[openclaw-adapter] openclaw.json entry not present (skipped)"
    fi
}

main() {
    log "Starting tokenpak ↔ OpenClaw uninstall"
    uninstall_openclaw_adapter
    log "Done"
}

main "$@"
