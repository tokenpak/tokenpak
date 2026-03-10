# TokenPak v1.0 Release Notes

**Release date:** 2026-03-10  
**Version:** 1.0.0  
**Audience:** Existing v0.x users, operators, and integrators

---

## Executive Summary

TokenPak v1.0 is the first stable production release focused on reliability, consistent routing behavior, and operational clarity.

Compared to v0.x, v1.0 standardizes provider mirroring, strengthens fallback orchestration, and hardens startup/runtime checks so failures are caught earlier and recover more gracefully.

If you run TokenPak in production, this release reduces surprise failures, improves large-context handling, and provides a cleaner migration path for future upgrades.

---

## What’s New in v1.0

### 1) Provider-Agnostic Routing, Stabilized
- Unified routing behavior across Anthropic/OpenAI-compatible paths.
- Better alias consistency for model addressing and failover behavior.
- Safer default chains to reduce hard-stop incidents under upstream pressure.

### 2) Stronger Fallback and Recovery
- Interleaved fallback chains now prioritize service continuity.
- Better handling for rate-limit and transient upstream errors.
- Clearer diagnostics when failover is activated.

### 3) Compression + Budgeting Pipeline Hardening
- Capsule/router/budget stages tuned for large-context workloads.
- More deterministic processing flow and better pipeline safety checks.
- Better behavior when context size approaches token ceilings.

### 4) Developer & Operator Experience
- Expanded docs for deployment, troubleshooting, and architecture.
- Additional Python SDK examples for common usage recipes.
- Improved CLI diagnostics for incident response.

---

## Key Improvements

- Better uptime behavior during provider instability.
- Less silent misconfiguration risk in runtime/service setup.
- More predictable routing outcomes.
- Cleaner migration narrative from experimental v0.x behavior.

---

## Breaking Changes

1. **Fallback ordering changed**
   - If you depend on exact v0.x fallback order, update your assumptions/config.

2. **Service environment expectations updated**
   - Router/capsule related toggles should be explicitly defined in managed service environments.

3. **CLI output compatibility considerations**
   - Scripts parsing previous CLI text output should be validated against v1.0 behavior.

---

## Migration Guide (v0.x → v1.0)

### Step 1 — Backup current state
- Back up current config, service unit/env files, and any local patches.

### Step 2 — Upgrade package/code
- Pull v1.0 artifacts and install/update dependencies per your normal deployment path.

### Step 3 — Review routing config
- Map any legacy aliases to v1.0 provider-mirroring aliases.
- Confirm chain ordering aligns with your reliability goals.

### Step 4 — Verify service environment
- Ensure runtime env explicitly includes router/capsule feature toggles where required.
- Reload/restart service manager after env/unit updates.

### Step 5 — Run smoke tests
- Health endpoint responds.
- Primary route success on normal requests.
- Fallback route triggers/recovers under induced primary failure.
- Large-context request remains within expected token budget behavior.

### Step 6 — Validate automation
- Re-test any scripts relying on CLI output parsing.
- Confirm alerts/telemetry still trigger on expected signals.

### Step 7 — Roll out safely
- Prefer staged rollout with close monitoring.
- Keep rollback path available for first deployment window.

---

## Known Issues

- Upstream provider-level 429 events can still cascade if all fallback providers are simultaneously constrained.
- Some legacy wrappers may still rely on pre-v1.0 alias naming conventions and require manual mapping cleanup.

---

## Installation / Upgrade

```bash
# Example (adjust to your deployment workflow)
cd ~/Projects/tokenpak
git fetch --all --tags
git checkout v1.0.0 || git checkout master
pip install -r requirements.txt
```

For production service deployments, apply your systemd/runtime updates, then restart and run smoke tests.

---

## GitHub Release (copy-ready)

**Title:** TokenPak v1.0.0 — Stable Routing & Fallback Foundation  
**Tag:** `v1.0.0`

**Highlights**
- Provider-agnostic routing stabilized
- Fallback orchestration hardened
- Compression/budget pipeline improved
- Expanded docs + SDK examples

**Downloads**
- Source code (zip): `https://github.com/<owner>/<repo>/archive/refs/tags/v1.0.0.zip`
- Source code (tar.gz): `https://github.com/<owner>/<repo>/archive/refs/tags/v1.0.0.tar.gz`

**Quick start**
```bash
git clone https://github.com/<owner>/<repo>.git
cd <repo>
git checkout v1.0.0
pip install -r requirements.txt
```

**Migration**
See the migration section in this document and full itemized changes in `CHANGELOG.md`.
