#!/usr/bin/env bash
# tokenpak-inject.sh — Auto-inject TokenPak provider routing into OpenClaw config
#
# Two modes:
#
#   DEFAULT (additive — 2026-04-25):
#     Mirrors providers, copies auth profiles, adds tokenpak-* entries
#     to the model allowlist. The user's primary model, fallback chains,
#     and per-agent model selections are LEFT UNTOUCHED. Result: users
#     see tokenpak-* providers as new options in their dropdown but
#     keep their existing routing intact. They opt in to tokenpak by
#     manually selecting a tokenpak-* model in OpenClaw's UI/config.
#
#   EXCLUSIVE (--exclusive flag or TOKENPAK_INJECT_EXCLUSIVE=1):
#     Additionally rewrites primary models to their tokenpak-* version
#     and clears fallback chains, so ALL agent traffic auto-routes
#     through the proxy. Use this for hosts dedicated to tokenpak
#     (single-purpose bots, testbeds). Destructive — overwrites the
#     user's chosen primary/fallback selections.
#
# Either way:
#   - Idempotent — safe to run repeatedly.
#   - Existing non-tokenpak providers are NEVER removed from the config.
#   - Restoring previous behavior is a `tokenpak-uninstall.sh` away
#     (uses the .pre-tokenpak-backup snapshot).
#
# Runs as ExecStartPre before openclaw-gateway starts.
#
# DOCS:
#   Full integration reference (install, doctor compatibility, troubleshooting):
#     https://docs.tokenpak.ai/integrations/openclaw
#     source: tokenpak-docs/docs/integrations/openclaw.md
#
# DOCTOR COMPATIBILITY (summary):
#   `openclaw doctor` and `doctor --repair` are SAFE to run alongside this
#   integration. Provider entries, auth profiles, and the X-TokenPak-Backend
#   header are preserved. If `doctor --repair` normalises per-model
#   contextWindow values, the next gateway restart re-runs this script and
#   restores them automatically. Don't try to use `$include` indirection to
#   isolate TokenPak settings — OpenClaw's config writer expands and
#   inlines includes on every write-back; the ExecStartPre re-injection
#   pattern is the only resilient strategy on current OpenClaw releases.

set -euo pipefail

PROXY_URL="${TOKENPAK_URL:-http://localhost:8766}"

# ── Mode flag ────────────────────────────────────────────────────────
# Default = additive. Pass --exclusive (or set
# TOKENPAK_INJECT_EXCLUSIVE=1) to also rewrite primary models +
# clear fallbacks. The flag-style and env-style trigger the same mode.
INJECT_EXCLUSIVE="${TOKENPAK_INJECT_EXCLUSIVE:-0}"
for arg in "$@"; do
    case "$arg" in
        --exclusive|-x) INJECT_EXCLUSIVE=1 ;;
    esac
done
export INJECT_EXCLUSIVE

python3 << 'PYEOF'
import json, os, shutil
from pathlib import Path
from glob import glob

PROXY_URL = os.environ.get("TOKENPAK_URL", "http://localhost:8766")
PREFIX = "tokenpak"
EXCLUSIVE = os.environ.get("INJECT_EXCLUSIVE", "0").strip() in {"1", "true", "yes"}

# Honor the OpenClaw-native env var that systemd units set per service
# (governor → ~/.openclaw-governor, gateway → ~/.openclaw, etc.). Falls
# back to ~/.openclaw for single-instance hosts.
_OC_CONFIG_ENV = os.environ.get("OPENCLAW_CONFIG_PATH", "").strip()
if _OC_CONFIG_ENV:
    MAIN_CONFIG = Path(_OC_CONFIG_ENV).expanduser()
    OPENCLAW_DIR = MAIN_CONFIG.parent
else:
    OPENCLAW_DIR = Path.home() / ".openclaw"
    MAIN_CONFIG = OPENCLAW_DIR / "openclaw.json"

def log(msg): print(f"[tokenpak-inject] {msg}")
def load_json(p): return json.loads(p.read_text()) if p.exists() else {}
def save_json(p, d):
    # Two backup files:
    #   .tokenpak-backup       — most recent pre-mutation snapshot, rotated
    #                            on every run. Useful for "what did the last
    #                            run change?" diffing.
    #   .pre-tokenpak-backup   — created ONCE on the very first run. This is
    #                            the user's clean pre-tokenpak state, which
    #                            tokenpak-uninstall.sh restores. Never
    #                            overwritten so the original config is
    #                            always recoverable.
    shutil.copy2(p, str(p) + ".tokenpak-backup")
    pre_path = Path(str(p) + ".pre-tokenpak-backup")
    if not pre_path.exists():
        shutil.copy2(p, str(pre_path))
        log(f"Saved first-install backup: {pre_path}")
    p.write_text(json.dumps(d, indent=2))

def is_tp(name): return name.startswith(f"{PREFIX}-") or name == PREFIX
def tp_name(name): return f"{PREFIX}-{name}"

def tp_ref(ref, pm):
    """Get tokenpak version of a model ref, or None"""
    if not ref or "/" not in ref: return None
    prov, model = ref.split("/", 1)
    if is_tp(prov) or prov not in pm: return None
    return f"{pm[prov]}/{model}"

# ── 1. Mirror providers ──────────────────────────────────────────────

def mirror_providers(providers):
    changed, pm = False, {}
    for orig, cfg in list(providers.items()):
        if is_tp(orig) or not cfg.get("models"): continue
        tpn = tp_name(orig)
        pm[orig] = tpn
        for m in cfg.get("models", []):
            m.setdefault("cost", {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})
        # Copy api format from original if set; omit if not (let OpenClaw infer)
        is_anthropic = (cfg.get("api") == "anthropic-messages") or orig.startswith("anthropic")
        target_base = PROXY_URL  # ALL tokenpak providers route through proxy
        new = {"baseUrl": target_base, "models": cfg["models"].copy()}
        if cfg.get("api"):
            new["api"] = cfg["api"]
        if cfg.get("apiKey"): new["apiKey"] = cfg["apiKey"]
        for k in ["headers", "authHeader"]:
            if k in cfg: new[k] = cfg[k]
        if providers.get(tpn, {}).get("baseUrl") != target_base or len(providers.get(tpn, {}).get("models", [])) != len(cfg["models"]):
            providers[tpn] = new; changed = True
            route = "proxy" if is_anthropic else "direct"
            log(f"Mirrored: {orig} -> {tpn} ({len(cfg['models'])} models, {route})")
    return changed, pm

# ── 2. Interleave model chains ───────────────────────────────────────

def interleave(model_cfg, pm):
    """
    EXCLUSIVE-mode-only: Promote primary to tokenpak version + clear fallbacks.

    Input:  primary=opus4.6, fallbacks=[anything]
    Output: primary=tp-opus4.6, fallbacks=[]

    DEFAULT MODE (not EXCLUSIVE): returns (model_cfg, False) without
    mutation — the user's primary + fallback selections are left as-is.
    Tokenpak-* providers are still added to the allowlist by
    update_allowlist() so they appear as options; the user opts in by
    manually selecting one.

    Direct provider refs are always kept in the config so the user can
    manually switch to non-tokenpak routing whenever they want.
    """
    if not EXCLUSIVE:
        # Additive default: do not mutate primary or fallbacks.
        # Caller may still see (cfg, False) and skip the write.
        return model_cfg, False

    # Normalize to dict
    if isinstance(model_cfg, str):
        model_cfg = {"primary": model_cfg, "fallbacks": []}
    if not isinstance(model_cfg, dict):
        return model_cfg, False

    primary = model_cfg.get("primary", "")

    # Find the original (non-tokenpak) primary
    prov = primary.split("/", 1)[0] if "/" in primary else ""
    if is_tp(prov):
        # Already tokenpak — just clear fallbacks
        if model_cfg.get("fallbacks", []):
            model_cfg["fallbacks"] = []
            return model_cfg, True
        return model_cfg, False

    # Promote to tokenpak version
    tp_p = tp_ref(primary, pm)
    new_primary = tp_p if tp_p else primary

    # Check if anything changed
    if new_primary == primary and not model_cfg.get("fallbacks", []):
        return model_cfg, False

    model_cfg["primary"] = new_primary
    model_cfg["fallbacks"] = []  # NO fallbacks — proxy-only routing
    return model_cfg, True

def interleave_defaults(config, pm):
    """Interleave agents.defaults.model"""
    changed = False
    defaults = config.get("agents", {}).get("defaults", {})
    model = defaults.get("model")
    if not model: return changed
    new, did = interleave(model, pm)
    if did:
        defaults["model"] = new
        changed = True
        log(f"Interleaved defaults: {new.get('primary')} + {len(new.get('fallbacks',[]))} fallbacks")
    return changed

def interleave_agents(config, pm):
    """Interleave model refs in agents.list, heartbeat, subagent, imageModel, pdfModel"""
    changed = False

    for agent in config.get("agents", {}).get("list", []):
        aid = agent.get("id", "?")

        # Agent model
        model = agent.get("model")
        if model:
            new, did = interleave(model, pm)
            if did:
                agent["model"] = new; changed = True
                log(f"Interleaved agent {aid}: {new.get('primary') if isinstance(new, dict) else new}")

        # Heartbeat model (single string — just promote to tp, original stays as heartbeat fallback isn't supported)
        hb_model = agent.get("heartbeat", {}).get("model")
        if hb_model and isinstance(hb_model, str):
            tp_hb = tp_ref(hb_model, pm)
            if tp_hb and tp_hb != hb_model:
                agent["heartbeat"]["model"] = tp_hb; changed = True
                log(f"Remapped agent {aid} heartbeat: {hb_model} -> {tp_hb}")

        # Subagent model
        sa = agent.get("subagents", {}).get("model")
        if sa:
            new, did = interleave(sa, pm)
            if did: agent["subagents"]["model"] = new; changed = True

    # Defaults: heartbeat, subagent, imageModel, pdfModel
    defaults = config.get("agents", {}).get("defaults", {})

    hb = defaults.get("heartbeat", {}).get("model")
    if hb and isinstance(hb, str):
        tp_hb = tp_ref(hb, pm)
        if tp_hb and tp_hb != hb:
            defaults["heartbeat"]["model"] = tp_hb; changed = True
            log(f"Remapped defaults heartbeat: {hb} -> {tp_hb}")

    for field in ["subagents"]:
        sa = defaults.get(field, {}).get("model")
        if sa:
            new, did = interleave(sa, pm)
            if did: defaults[field]["model"] = new; changed = True

    for field in ["imageModel", "pdfModel"]:
        ref = defaults.get(field)
        if ref:
            new, did = interleave(ref, pm)
            if did: defaults[field] = new; changed = True; log(f"Interleaved {field}")

    return changed

# ── 3. Allowlist ──────────────────────────────────────────────────────

def update_allowlist(config, pm):
    changed = False
    allowlist = config.setdefault("agents", {}).setdefault("defaults", {}).setdefault("models", {})
    providers = config.get("models", {}).get("providers", {})

    # Add tp version of existing entries
    for old_ref in list(allowlist.keys()):
        tp_r = tp_ref(old_ref, pm)
        if tp_r and tp_r not in allowlist:
            allowlist[tp_r] = allowlist[old_ref].copy() if isinstance(allowlist[old_ref], dict) else {}
            changed = True

    # Add all tp provider models
    for pname, pcfg in providers.items():
        if not is_tp(pname): continue
        for m in pcfg.get("models", []):
            ref = f"{pname}/{m['id']}"
            if ref not in allowlist:
                allowlist[ref] = {}; changed = True

    return changed

# ── 4. Auth ───────────────────────────────────────────────────────────

def copy_auth(config, pm):
    changed = False
    auth = config.setdefault("auth", {})
    profiles = auth.setdefault("profiles", {})
    order = auth.setdefault("order", {})

    for orig, tpn in pm.items():
        for key, prof in list(profiles.items()):
            if prof.get("provider") == orig:
                new_key = key.replace(f"{orig}:", f"{tpn}:")
                if new_key not in profiles:
                    profiles[new_key] = {k: v for k, v in {**prof, "provider": tpn}.items() if k != "apiKey"}; changed = True
                    log(f"Copied auth: {key} -> {new_key}")
        if orig in order and tpn not in order:
            order[tpn] = [k.replace(f"{orig}:", f"{tpn}:") for k in order[orig]]
            changed = True

    # Claude Code backend uses anthropic auth (same OAuth token)
    cc_name = "tokenpak-claude-code"
    cc_profile = f"{cc_name}:manual"
    if cc_profile not in profiles:
        # Copy from tokenpak-anthropic or anthropic
        for src in ["tokenpak-anthropic:manual", "anthropic:manual"]:
            if src in profiles:
                profiles[cc_profile] = {**profiles[src], "provider": cc_name}
                changed = True
                log(f"Copied auth: {src} -> {cc_profile}")
                break
    if cc_name not in order:
        order[cc_name] = [cc_profile]
        changed = True

    return changed

def copy_auth_files(pm):
    changed = False
    for af in glob(str(OPENCLAW_DIR / "agents/*/agent/auth-profiles.json")):
        try:
            auth = load_json(Path(af))
            profiles = auth.setdefault("profiles", {})
            mod = False
            for orig, tpn in pm.items():
                for key, prof in list(profiles.items()):
                    if prof.get("provider") == orig:
                        new_key = key.replace(f"{orig}:", f"{tpn}:")
                        if new_key not in profiles:
                            profiles[new_key] = {k: v for k, v in {**prof, "provider": tpn}.items() if k != "apiKey"}; mod = True
            # Claude Code backend auth — copy from anthropic
            cc_profile = "tokenpak-claude-code:manual"
            if cc_profile not in profiles:
                for src in ["tokenpak-anthropic:manual", "anthropic:manual"]:
                    if src in profiles:
                        profiles[cc_profile] = {**profiles[src], "provider": "tokenpak-claude-code"}
                        mod = True
                        break
            if mod: save_json(Path(af), auth); changed = True
        except Exception as e:
            log(f"Warning: {af}: {e}")
    return changed

# ── 5. Process ────────────────────────────────────────────────────────

def inject_claude_code_provider(providers):
    """Add tokenpak-claude-code provider — routes OpenClaw through Claude Code CLI.

    Dynamically copies models from tokenpak-anthropic (or anthropic) so new
    models are picked up automatically. Each model gets a "(Claude Code)"
    suffix in the display name.
    """
    name = "tokenpak-claude-code"

    # Copy models from the anthropic provider (tokenpak-anthropic or anthropic)
    source = providers.get("tokenpak-anthropic") or providers.get("anthropic") or {}
    source_models = source.get("models", [])

    models = []
    for m in source_models:
        cc_model = dict(m)
        # Add "(Claude Code)" suffix to display name
        orig_name = cc_model.get("name", cc_model["id"])
        if "(Claude Code)" not in orig_name:
            cc_model["name"] = f"{orig_name} (Claude Code)"
        # Zero cost — subscription billing
        cc_model["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        models.append(cc_model)

    if not models:
        log(f"Skipped {name}: no anthropic models to copy")
        return False

    want = {
        "baseUrl": PROXY_URL,
        "api": "anthropic-messages",
        "headers": {"X-TokenPak-Backend": "claude-code"},
        "models": models,
    }

    existing = providers.get(name, {})
    existing_ids = {m["id"] for m in existing.get("models", [])}
    want_ids = {m["id"] for m in models}
    if (existing.get("baseUrl") == PROXY_URL
            and existing.get("headers", {}).get("X-TokenPak-Backend") == "claude-code"
            and existing_ids == want_ids):
        return False  # already up to date

    providers[name] = want
    log(f"Injected: {name} (Claude Code backend, {len(models)} models — synced from anthropic)")
    return True

def process_main(path):
    config = load_json(path)
    if not config: return False, {}

    log(f"Mode: {'EXCLUSIVE (rewrites primary + clears fallbacks)' if EXCLUSIVE else 'additive (mirrors providers + allowlist; leaves user routing untouched)'}")

    config.setdefault("models", {}).setdefault("providers", {})
    config["models"]["mode"] = "merge"

    c1, pm = mirror_providers(config["models"]["providers"])
    c1b = inject_claude_code_provider(config["models"]["providers"])
    c2 = interleave_defaults(config, pm)  # no-op when not EXCLUSIVE
    c3 = interleave_agents(config, pm)    # no-op when not EXCLUSIVE
    c4 = update_allowlist(config, pm)
    c5 = copy_auth(config, pm)

    if any([c1, c1b, c2, c3, c4, c5]):
        save_json(path, config)
        log(f"Saved: {path}")

    return any([c1, c1b, c2, c3, c4, c5]), pm

def process_agent_models():
    changed = False; all_pm = {}
    for mf in glob(str(OPENCLAW_DIR / "agents/*/agent/models.json")):
        mp = Path(mf)
        agent = mp.parent.parent.name
        log(f"Processing agent: {agent}")
        config = load_json(mp)
        if not config: continue
        providers = config.setdefault("providers", {})
        c1, pm = mirror_providers(providers)
        all_pm.update(pm)
        if c1: save_json(mp, config); changed = True
    return changed, all_pm

def _load_codex_auth_jwt():
    """Return (access_token, refresh_token, account_id, exp_ms) from ~/.codex/auth.json.

    Returns (None, None, None, None) when the file is missing or the JWT
    can't be decoded. exp_ms is the JWT's inner `exp` claim * 1000 so it
    matches the ms-precision that OpenClaw stores in profile `expires`.
    """
    import base64
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.is_file():
        return (None, None, None, None)
    try:
        d = json.loads(auth_path.read_text())
        tokens = d.get("tokens", {}) or {}
        access = tokens.get("access_token") or d.get("access_token") or ""
        refresh = tokens.get("refresh_token") or d.get("refresh_token") or ""
        if not access or access.count(".") != 2:
            return (None, None, None, None)
        pad = lambda s: s + "=" * (4 - len(s) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad(access.split(".")[1])))
        exp = int(payload.get("exp", 0)) * 1000
        account = payload.get("https://api.openai.com/auth", {}).get(
            "chatgpt_account_id", ""
        )
        return (access, refresh, account, exp)
    except Exception:
        return (None, None, None, None)


def refresh_native_codex_profile():
    """Refresh openai-codex:default from ~/.codex/auth.json when stale.

    OpenClaw's internal refresher only touches an agent's native codex
    profile when that agent is actively used. For agents that have been
    idle for days, the profile's inner JWT goes stale and OpenClaw's
    pre-flight `extract accountId from token` fails — taking every
    tokenpak-openai-codex request down with it since the tokenpak-*
    profile is mirrored from this one. Fixing the root (~/.codex/auth.json
    IS the canonical source of truth) lets the mirror-downstream step
    produce a fresh result.
    """
    access, refresh, account, exp_ms = _load_codex_auth_jwt()
    if not access:
        return False

    changed = 0
    for af in glob(str(OPENCLAW_DIR / "agents/*/agent/auth-profiles.json")):
        try:
            path = Path(af)
            auth = load_json(path)
            profiles = auth.get("profiles", {})
            native = profiles.get("openai-codex:default")
            if not native:
                continue
            if native.get("access") == access:
                continue  # already fresh
            native["access"] = access
            if refresh:
                native["refresh"] = refresh
            if account:
                native["accountId"] = account
            if exp_ms:
                native["expires"] = exp_ms
            save_json(path, auth)
            log(f"Refreshed native codex profile from ~/.codex/auth.json ({path})")
            changed += 1
        except Exception as e:
            log(f"Warning: {af}: {e}")
    return changed > 0


def sync_codex_jwt():
    """Mirror fresh JWT from openai-codex:default → tokenpak-openai-codex:default.

    OpenClaw's built-in refresher auto-syncs `openai-codex:default` from
    `~/.codex/auth.json` but ignores custom-named profiles like
    `tokenpak-openai-codex:default`. When the tokenpak-* JWT goes stale,
    OpenClaw's `extract accountId from token` step fails and every codex
    request dies at pre-flight — even though the tokenpak proxy would
    inject a fresh JWT on the wire. Re-running this sync on every
    ExecStartPre keeps the two profiles aligned.
    """
    changed = 0
    for af in glob(str(OPENCLAW_DIR / "agents/*/agent/auth-profiles.json")):
        try:
            path = Path(af)
            auth = load_json(path)
            profiles = auth.get("profiles", {})
            native = profiles.get("openai-codex:default")
            tpk = profiles.get("tokenpak-openai-codex:default")
            if not native or not tpk:
                continue
            native_access = native.get("access", "")
            if not native_access:
                continue
            if tpk.get("access") == native_access and tpk.get("accountId") == native.get("accountId"):
                continue  # already in sync
            for field in ("access", "refresh", "accountId", "expires"):
                if field in native:
                    tpk[field] = native[field]
            tpk["type"] = native.get("type", "oauth")
            save_json(path, auth)
            log(f"Synced codex JWT: openai-codex → tokenpak-openai-codex ({path})")
            changed += 1
        except Exception as e:
            log(f"Warning: {af}: {e}")
    return changed > 0

def main():
    log(f"Proxy: {PROXY_URL}")
    total = False; all_pm = {}

    if MAIN_CONFIG.exists():
        log(f"Processing: {MAIN_CONFIG}")
        c, pm = process_main(MAIN_CONFIG)
        total = total or c; all_pm.update(pm)

    c, pm = process_agent_models()
    total = total or c; all_pm.update(pm)

    if all_pm:
        c = copy_auth_files(all_pm)
        total = total or c

    # Codex profile refresh chain:
    #   ~/.codex/auth.json  →  openai-codex:default  →  tokenpak-openai-codex:default
    # Step 1 covers idle agents whose native profile OpenClaw never auto-refreshed.
    # Step 2 covers the permanently-stale tokenpak-* mirror (custom name,
    # outside OpenClaw's auto-refresh set).
    if refresh_native_codex_profile():
        total = True
    if sync_codex_jwt():
        total = True

    log(f"Done — mirrored {len(all_pm)} providers" if total else "No changes needed")

main()
PYEOF
