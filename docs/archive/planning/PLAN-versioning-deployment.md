# PLAN: TokenPak Versioning & Deployment — Task Breakdown

**Status:** DRAFT
**Depends on:** SPEC-versioning-deployment.md
**Date:** 2026-03-24
**Agents:** Trix (execution), Cali (testing/docs)

---

## Phases Overview

```
Phase 1: Foundation (proxy bundling + version constants)     ~2 days
Phase 2: CLI commands (update, rollback, version)            ~2 days
Phase 3: CHANGELOG + release process                         ~1 day
Phase 4: QA + rollout to fleet                               ~1 day
```

Total: ~6 days

---

## Phase 1: Foundation

### P1-A: Bundle proxy.py inside pip package
**Agent:** Trix
**Priority:** p1
**Effort:** 4h

- Create `packages/core/tokenpak/runtime/__init__.py`
- Move canonical `proxy.py` → `packages/core/tokenpak/runtime/proxy.py`
- Add to `MANIFEST.in`: `include tokenpak/runtime/proxy.py`
- Update `packages/core/pyproject.toml` to include runtime dir
- Update proxy search path in `cli.py`:
  `Path(tokenpak.__file__).parent / "runtime" / "proxy.py"` first
- **Vault sync:** Remove standalone `proxy.py` from vault root (now in package)
- Test: `pip install -e .` → `python -c "from tokenpak.runtime import proxy"`

**Acceptance:**
- [ ] `~/vault/01_PROJECTS/tokenpak/packages/core/tokenpak/runtime/proxy.py` exists
- [ ] `pip install tokenpak` includes the proxy file
- [ ] `tokenpak serve` finds and launches it

---

### P1-B: Add PROXY_VERSION + version consistency check
**Agent:** Trix
**Priority:** p1
**Effort:** 2h

- `proxy.py` already has `PROXY_VERSION = "1.0.0"` ✅ (done today)
- Add `version` field to `/health` response ✅ (done today)
- Add pre-release version check script: `scripts/check-versions.sh`
  ```bash
  #!/bin/bash
  PKG=$(grep 'version = ' pyproject.toml | head -1 | cut -d'"' -f2)
  PROXY=$(grep 'PROXY_VERSION' proxy.py | head -1 | cut -d'"' -f2)
  INIT=$(grep '__version__' tokenpak/__init__.py | head -1 | cut -d'"' -f2)
  [[ "$PKG" == "$PROXY" && "$PKG" == "$INIT" ]] && echo "✓ Versions consistent: $PKG" || (echo "✗ Mismatch: pkg=$PKG proxy=$PROXY init=$INIT"; exit 1)
  ```

**Acceptance:**
- [ ] `bash scripts/check-versions.sh` passes
- [ ] `/health` returns `"version": "1.0.0"`

---

### P1-C: Version rollback cache
**Agent:** Trix
**Priority:** p1
**Effort:** 3h

Create `~/.tokenpak/versions/` management in CLI:
- `_backup_current_proxy(version)` — saves proxy to versions cache
- `_prune_versions(keep=3)` — removes oldest backups beyond 3
- `tokenpak rollback [--version X.Y.Z]` command

**Acceptance:**
- [ ] `tokenpak update` creates backup before replacing
- [ ] `tokenpak rollback` restores previous version
- [ ] Max 3 backups kept (oldest pruned)

---

## Phase 2: CLI Commands

### P2-A: tokenpak update — full rewrite
**Agent:** Trix
**Priority:** p1
**Effort:** 4h

Implement spec from `SPEC.md §4.2`:
- 12-step sequential update flow
- Protected files check (never overwrite user data)
- Systemd restart with health poll (10s timeout)
- Changelog snippet from PyPI metadata or local CHANGELOG.md
- Lock file write on success

**Acceptance:**
- [ ] `tokenpak update --check` shows versions, no changes
- [ ] `tokenpak update --dry-run` shows what would change
- [ ] `tokenpak update` applies successfully
- [ ] Config files untouched after update
- [ ] Proxy restarts and `/health` confirms new version
- [ ] Lock file written to `~/.tokenpak/tokenpak.lock.json`

---

### P2-B: tokenpak version — enhanced output
**Agent:** Trix
**Priority:** p2
**Effort:** 2h

Output per SPEC §5:
- Package version (installed + PyPI latest)
- Proxy version (running + on-disk)
- Proxy hash
- Installed date + by whom
- Previous version info from lock file

**Acceptance:**
- [ ] Shows all 5 data points
- [ ] Handles proxy unreachable gracefully (shows "not running")
- [ ] Handles no lock file gracefully

---

### P2-C: tokenpak rollback
**Agent:** Trix
**Priority:** p2
**Effort:** 3h

Per SPEC §4.3:
- List available rollback targets
- Restore proxy from `~/.tokenpak/versions/`
- Reinstall pip package at previous version
- Restart proxy + health check

**Acceptance:**
- [ ] `tokenpak rollback` with no args rolls to last version
- [ ] `tokenpak rollback --version 1.0.0` rolls to specific
- [ ] Graceful error if no rollback available

---

## Phase 3: CHANGELOG + Release Process

### P3-A: Initial CHANGELOG.md
**Agent:** Cali
**Priority:** p2
**Effort:** 3h

Create `CHANGELOG.md` at repo root per Keep a Changelog format:
- Document all versions from git history (1.0.0, 1.0.1, 1.0.2)
- Include today's changes (proxy rename, version system)
- Set up `[Unreleased]` section

**Acceptance:**
- [ ] CHANGELOG.md at `packages/core/CHANGELOG.md`
- [ ] Covers 1.0.0 through current
- [ ] Format validates against keepachangelog.com spec

---

### P3-B: Release checklist doc
**Agent:** Cali
**Priority:** p2
**Effort:** 2h

Create `docs/release-checklist.md` per PRD §11. Runnable as a script:
```bash
bash scripts/pre-release-check.sh 1.0.2
```

**Acceptance:**
- [ ] Checklist covers all 10 items from PRD §11
- [ ] Script automates the verifiable ones

---

### P3-C: Version bump script
**Agent:** Trix
**Priority:** p2
**Effort:** 2h

`scripts/bump-version.sh <major|minor|patch>`
- Reads current version from pyproject.toml
- Bumps the appropriate part
- Updates `pyproject.toml`, `proxy.py:PROXY_VERSION`, `tokenpak/__init__.py:__version__`
- Creates git tag `v{version}`
- Generates CHANGELOG stub for new version

**Acceptance:**
- [ ] `bash scripts/bump-version.sh patch` → all 3 files updated consistently
- [ ] `bash scripts/check-versions.sh` passes after bump

---

## Phase 4: QA + Rollout

### P4-A: Test suite for update system
**Agent:** Cali
**Priority:** p1
**Effort:** 4h

Tests in `tests/test_update_system.py`:

```python
# Required tests
def test_version_check_detects_current_version()
def test_version_check_detects_drift()
def test_update_check_only_makes_no_changes()
def test_update_dry_run_prints_plan()
def test_update_never_touches_config_json()
def test_update_never_touches_vault_md_files()
def test_update_proxy_sync_copies_file()
def test_update_writes_lockfile()
def test_rollback_restores_previous_version()
def test_rollback_no_backup_handles_gracefully()
def test_version_command_output_format()
def test_versions_consistent_across_files()
```

**Acceptance:**
- [ ] All 12 tests pass
- [ ] Config protection test uses real temp dir

---

### P4-B: Fleet rollout
**Agent:** Sue (Governor)
**Priority:** p1
**Effort:** 2h

After Phase 1-3 complete:
1. Bump version to 1.1.0 (first proper versioned release)
2. Run `tokenpak update` on Sue → verify
3. Deploy to Trix via `tokenpak update`
4. Deploy to Cali via `tokenpak update`
5. Verify all agents show `1.1.0` in `/health`
6. Archive sync-tokenpak-proxy.sh (replaced by update command)

**Acceptance:**
- [ ] All 3 agents on same version
- [ ] `tokenpak version` shows identical output on all 3
- [ ] No configuration files changed on any agent

---

## Task Packet Files

| Phase | Task | File | Agent | Priority |
|-------|------|------|-------|----------|
| 1 | Bundle proxy in pip | `p1-tokenpak-bundle-proxy-runtime.md` | Trix | p1 |
| 1 | Version consistency check | `p1-tokenpak-version-consistency.md` | Trix | p1 |
| 1 | Rollback cache | `p1-tokenpak-rollback-cache.md` | Trix | p1 |
| 2 | tokenpak update rewrite | `p1-tokenpak-update-cmd.md` | Trix | p1 |
| 2 | tokenpak version enhanced | `p2-tokenpak-version-cmd.md` | Trix | p2 |
| 2 | tokenpak rollback cmd | `p2-tokenpak-rollback-cmd.md` | Trix | p2 |
| 3 | CHANGELOG.md | `p2-tokenpak-changelog.md` | Cali | p2 |
| 3 | Release checklist | `p2-tokenpak-release-checklist.md` | Cali | p2 |
| 3 | bump-version.sh | `p2-tokenpak-bump-version-script.md` | Trix | p2 |
| 4 | Test suite | `p1-tokenpak-update-tests.md` | Cali | p1 |
| 4 | Fleet rollout | `p1-tokenpak-fleet-rollout.md` | Sue | p1 |

---

*Next: Sue creates task packets and assigns*
