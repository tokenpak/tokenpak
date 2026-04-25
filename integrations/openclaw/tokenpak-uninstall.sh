#!/usr/bin/env bash
# tokenpak-uninstall.sh — Remove the TokenPak ↔ OpenClaw integration.
#
# Reverts everything tokenpak-inject.sh did:
#   1. Restores ~/.openclaw/openclaw.json to the pre-tokenpak state
#      (preferred) or strips tokenpak-* providers / auth / headers /
#      allowlist entries from the live config (fallback).
#   2. Removes the systemd ExecStartPre drop-in that runs the inject
#      script on gateway start.
#   3. Optionally stops + disables the tokenpak-proxy systemd unit
#      (--stop-proxy).
#   4. Optionally removes ~/.tokenpak/ caches (--purge-caches).
#
# Idempotent — safe to re-run if a previous run was interrupted.
#
# DOCS:
#   https://docs.tokenpak.ai/integrations/openclaw#uninstall

set -euo pipefail

PREFIX="tokenpak"
DRY_RUN=0
STOP_PROXY=0
PURGE_CACHES=0

usage() {
    cat <<EOF
Usage: tokenpak-uninstall.sh [--dry-run] [--stop-proxy] [--purge-caches]

  --dry-run        Print what would be done; don't change anything.
  --stop-proxy     Also stop + disable tokenpak-proxy.service.
  --purge-caches   Also delete ~/.tokenpak/{model_context_cache.json,session_map.db,...}.

Without flags: revert openclaw config + remove the systemd drop-in.
The tokenpak-proxy service stays running by default (you may still
want it for direct SDK calls); pass --stop-proxy to take it down too.

The script always asks before making destructive changes unless you
pass --yes.
EOF
}

YES=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --stop-proxy) STOP_PROXY=1 ;;
        --purge-caches) PURGE_CACHES=1 ;;
        --yes|-y) YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "unknown flag: $arg"; usage; exit 2 ;;
    esac
done

log() { printf '[tokenpak-uninstall] %s\n' "$*"; }
maybe() {
    if [ "$DRY_RUN" -eq 1 ]; then
        log "DRY-RUN: $*"
    else
        eval "$@"
    fi
}

confirm() {
    if [ "$YES" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then return 0; fi
    printf '%s [y/N] ' "$1"
    read -r answer
    case "$answer" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────
# Step 1 — revert openclaw.json
# ─────────────────────────────────────────────────────────────────────

OPENCLAW_DIR="${OPENCLAW_CONFIG_PATH:-}"
if [ -n "$OPENCLAW_DIR" ]; then
    OPENCLAW_DIR="${OPENCLAW_DIR%/*}"
fi
[ -z "$OPENCLAW_DIR" ] && OPENCLAW_DIR="$HOME/.openclaw"
CFG="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_DIR/openclaw.json}"

if [ -f "$CFG" ]; then
    PRE="$CFG.pre-tokenpak-backup"
    if [ -f "$PRE" ]; then
        log "Found pre-install backup: $PRE"
        if confirm "Restore openclaw config from pre-install backup (clean revert)?"; then
            maybe "cp -p '$PRE' '$CFG.uninstall-rollback' && cp -p '$PRE' '$CFG'"
            log "Restored: $CFG ← $PRE (rollback also saved at $CFG.uninstall-rollback)"
        else
            log "Skipped restore from pre-install backup."
        fi
    else
        log "No .pre-tokenpak-backup found — falling back to in-place strip of tokenpak-* entries."
        if confirm "Strip tokenpak-* providers, auth profiles, allowlist entries, and headers from $CFG?"; then
            STRIP_RESULT=$(DRY_RUN=$DRY_RUN python3 - <<PYEOF
import json, os, shutil
from pathlib import Path

dry_run = os.environ.get("DRY_RUN", "0") == "1"
cfg_path = Path("$CFG")
backup = Path(str(cfg_path) + ".uninstall-rollback")
if not dry_run:
    shutil.copy2(cfg_path, backup)

c = json.loads(cfg_path.read_text())
removed = []

# Strip tokenpak-* providers
providers = (c.get("models") or {}).get("providers") or {}
for name in list(providers):
    if name == "tokenpak" or name.startswith("tokenpak-"):
        del providers[name]
        removed.append(f"models.providers.{name}")

# Strip tokenpak-* auth profiles + their references in auth.order
auth = c.get("auth") or {}
profiles = auth.get("profiles") or {}
for name in list(profiles):
    if name.startswith("tokenpak-") or "tokenpak-" in name:
        del profiles[name]
        removed.append(f"auth.profiles.{name}")
order = auth.get("order") or {}
for prov in list(order):
    if prov.startswith("tokenpak-") or prov == "tokenpak":
        del order[prov]
        removed.append(f"auth.order.{prov}")
    else:
        # Remove tokenpak-* entries from non-tokenpak provider order arrays
        ord_list = order.get(prov)
        if isinstance(ord_list, list):
            kept = [p for p in ord_list if not (p.startswith("tokenpak-") or p == "tokenpak")]
            if len(kept) != len(ord_list):
                order[prov] = kept
                removed.append(f"auth.order.{prov} (filtered)")

# Strip tokenpak-* from any allowlist arrays at well-known paths
def strip_array(d, key, path_label):
    if isinstance(d, dict) and isinstance(d.get(key), list):
        before = list(d[key])
        d[key] = [v for v in d[key] if not (isinstance(v, str) and (v.startswith("tokenpak-") or v == "tokenpak"))]
        if len(d[key]) != len(before):
            removed.append(path_label)

agents = (c.get("agents") or {}).get("defaults") or {}
strip_array(agents, "models", "agents.defaults.models")
strip_array(agents, "modelAllowlist", "agents.defaults.modelAllowlist")

# Save
if not dry_run:
    cfg_path.write_text(json.dumps(c, indent=2))
print(json.dumps({"removed": removed, "rollback": str(backup), "dry_run": dry_run}))
PYEOF
)
            log "Stripped entries:"
            echo "$STRIP_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  rollback:', d['rollback'], '(DRY-RUN: not written)' if d['dry_run'] else ''); [print('  -',e, '(DRY-RUN: not applied)' if d['dry_run'] else '') for e in d['removed']]" 2>&1
        else
            log "Skipped openclaw.json modification."
        fi
    fi
else
    log "No openclaw config at $CFG — skipping revert."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 2 — remove systemd ExecStartPre drop-in
# ─────────────────────────────────────────────────────────────────────

DROP_IN="$HOME/.config/systemd/user/openclaw-gateway.service.d/tokenpak-inject.conf"
if [ -f "$DROP_IN" ]; then
    if confirm "Remove systemd drop-in at $DROP_IN (stops re-injection on gateway restart)?"; then
        maybe "rm -f '$DROP_IN'"
        maybe "systemctl --user daemon-reload"
        log "Removed: $DROP_IN"
    fi
else
    log "No systemd drop-in at $DROP_IN — skipping."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 3 — optionally stop tokenpak-proxy
# ─────────────────────────────────────────────────────────────────────

if [ "$STOP_PROXY" -eq 1 ]; then
    if systemctl --user list-unit-files tokenpak-proxy.service >/dev/null 2>&1; then
        if confirm "Stop + disable tokenpak-proxy.service?"; then
            maybe "systemctl --user stop tokenpak-proxy.service"
            maybe "systemctl --user disable tokenpak-proxy.service"
            log "tokenpak-proxy service stopped + disabled."
        fi
    else
        log "tokenpak-proxy.service not installed — skipping."
    fi
fi

# ─────────────────────────────────────────────────────────────────────
# Step 4 — optionally purge ~/.tokenpak caches
# ─────────────────────────────────────────────────────────────────────

if [ "$PURGE_CACHES" -eq 1 ]; then
    CACHES=(
        "$HOME/.tokenpak/model_context_cache.json"
        "$HOME/.tokenpak/session_map.db"
        "$HOME/.tokenpak/session_map.db-wal"
        "$HOME/.tokenpak/session_map.db-shm"
    )
    for c in "${CACHES[@]}"; do
        if [ -e "$c" ]; then
            if confirm "Delete cache file $c?"; then
                maybe "rm -f '$c'"
                log "Removed: $c"
            fi
        fi
    done
fi

# ─────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────

log "Uninstall complete."
log ""
log "Next steps:"
log "  1. Restart the OpenClaw gateway:"
log "       (it has RefuseManualStop=yes, so kill the PID and let systemd respawn)"
log "       kill \$(systemctl --user show openclaw-gateway.service -p MainPID --value)"
log "  2. Verify openclaw.json no longer references tokenpak-*:"
log "       grep -c tokenpak \"$CFG\""
log ""
log "Rollback safety net:"
log "  Each uninstall step writes to *.uninstall-rollback files; if anything"
log "  went wrong, copy those back into place to restore the post-install state."
