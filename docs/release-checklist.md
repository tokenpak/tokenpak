# TokenPak Release Checklist

**Run before every release.** Every section must be ✓ before publishing.

---

## 1️⃣ Version Consistency

- [ ] **pyproject.toml** — `version = "X.Y.Z"` is bumped
- [ ] **proxy.py** — `PROXY_VERSION = "X.Y.Z"` matches
- [ ] **tokenpak/__init__.py** — `__version__ = "X.Y.Z"` matches
- [ ] **Run check script:** `bash scripts/pre-release-check.sh X.Y.Z` passes

---

## 2️⃣ Quality Gates

- [ ] **Tests pass:** `pytest tests/ -q` → 0 failures, 0 errors
- [ ] **Version command works:** `tokenpak version` shows the correct version
- [ ] **Update detection:** `tokenpak update --check` detects the new version as available
- [ ] **Update applies cleanly:** `tokenpak update --dry-run` runs without errors
- [ ] **Rollback works:** After a test update, `tokenpak rollback` restores previous version

---

## 3️⃣ Config Safety ⚠️

These **cannot be automated** — verify manually:

- [ ] **Config preservation:** `~/.tokenpak/config.json` is NOT modified by update
- [ ] **Vault files untouched:** No `.md` or `.json` files in `~/vault/` are modified
- [ ] **System files untouched:** No system-level config files (`.bashrc`, systemd, etc.) are modified

**How to verify:**
```bash
# Before update
cp ~/.tokenpak/config.json ~/.tokenpak/config.json.backup
tokenpak update --dry-run
diff ~/.tokenpak/config.json ~/.tokenpak/config.json.backup  # Should be identical
```

---

## 4️⃣ Documentation

- [ ] **CHANGELOG.md** has an entry for this version with:
  - Version number and release date
  - Summary of new features/fixes
  - Any breaking changes or migration notes
- [ ] **README.md** reflects any API changes (if applicable)
- [ ] **Migration guide** written (only if config schema or CLI changed)

---

## 5️⃣ Git & Release

- [ ] **Commit message:** All changes committed with clear message
  ```bash
  git commit -m "Release v X.Y.Z: [summary of changes]"
  ```
- [ ] **Git tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`
- [ ] **PyPI/Cloudsmith:** Package published
  ```bash
  # For PyPI:
  pip install build twine
  python -m build
  twine upload dist/*
  ```

---

## ✅ Pre-Release Script

Use `bash scripts/pre-release-check.sh X.Y.Z` to automate checks 1-2 and document 4.

This script will:
1. Verify version consistency
2. Run full test suite
3. Check for CHANGELOG entry
4. Report results and halt on first failure

**Manual sections (3, 5)** must still be done by hand.

---

## 🚀 Release Complete

After publishing:
1. Close any related GitHub issues
2. Announce in release notes
3. Update external documentation (if applicable)

---

## Notes

- **No half-releases:** If any check fails, fix it before moving to the next section.
- **Dry-run first:** Always test `update --dry-run` before committing to a real update.
- **Rollback tested:** If rollback fails, **do not ship** — fix the rollback mechanism first.
