# TokenPak v1.0 Deployment Checklist

> **Purpose:** Step-by-step release gate for TokenPak v1.0.0. Complete each section in order. Check off items as you go. Don't skip staging — release day has high cognitive load and this list exists so your brain doesn't have to.

---

## Phase 1 — Pre-Release (1–2 days before) · ~45 min

### Code Quality
- [ ] All tests pass with exit code 0
  ```bash
  cd ~/tokenpak && pytest tests/ -v --tb=short
  ```
- [ ] No ruff lint errors
  ```bash
  ruff check tokenpak/
  ```
- [ ] No obvious security issues
  ```bash
  pip audit
  bandit -r tokenpak/ -ll
  ```
- [ ] Dependencies up to date (no known CVEs blocking release)
  ```bash
  pip list --outdated
  ```

### Documentation
- [ ] `CHANGELOG.md` — merge `[Unreleased]` into `[1.0.0]` section, date it
- [ ] `RELEASE_NOTES_v1.0.md` — final review, no TODOs remaining
- [ ] `README.md` — version badges, install command, and feature list are accurate
- [ ] `DEPLOYMENT.md` — no broken commands or stale config examples
- [ ] No `TODO` stubs left in any `.md` file
  ```bash
  grep -r "TODO" docs/ *.md --include="*.md" | grep -v CHANGELOG
  ```

### Version Consistency Check
- [ ] `pyproject.toml` version is `1.0.0`
  ```bash
  grep '^version' pyproject.toml
  ```
- [ ] `tokenpak/__init__.py` `__version__` matches
  ```bash
  python -c "import tokenpak; print(tokenpak.__version__)"
  ```
- [ ] Release notes version matches pyproject.toml

---

## Phase 2 — Staging Dry Run (24 hours before) · ~30 min

- [ ] Start proxy in test mode, confirm it boots clean
  ```bash
  tokenpak serve --port 8765 --config tokenpak.yaml
  ```
- [ ] Run smoke test against local staging proxy
  ```bash
  curl -s http://localhost:8765/health | python -m json.tool
  ```
- [ ] Verify all three provider adapters respond (may require live API keys)
  ```bash
  # OpenAI
  curl -X POST http://localhost:8765/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'

  # Anthropic
  curl -X POST http://localhost:8765/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-haiku-3","messages":[{"role":"user","content":"ping"}]}'

  # Google
  curl -X POST http://localhost:8765/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gemini-2.0-flash","messages":[{"role":"user","content":"ping"}]}'
  ```
- [ ] Test fallback chain — force a provider failure, confirm fallback activates
- [ ] Check error logs — no new warnings or tracebacks at startup
  ```bash
  tokenpak serve --log-level debug 2>&1 | head -50
  ```
- [ ] Load test — simulate burst traffic
  ```bash
  # 50 concurrent requests, 100 total
  ab -n 100 -c 50 http://localhost:8765/health
  ```

---

## Phase 3 — PyPI Release · ~20 min

- [ ] Create and push Git tag
  ```bash
  git tag v1.0.0
  git push origin v1.0.0
  ```
- [ ] Build distribution wheel + sdist
  ```bash
  python -m build
  ls -lh dist/
  ```
- [ ] Verify wheel contents look right (no accidental files included)
  ```bash
  unzip -l dist/tokenpak-1.0.0-py3-none-any.whl | head -40
  ```
- [ ] Test install from wheel in a clean venv
  ```bash
  python -m venv /tmp/tp-release-test
  source /tmp/tp-release-test/bin/activate
  pip install dist/tokenpak-1.0.0-py3-none-any.whl
  tokenpak --version
  deactivate
  ```
- [ ] Upload to PyPI
  ```bash
  twine upload dist/tokenpak-1.0.0*
  ```
- [ ] Verify PyPI listing: https://pypi.org/project/tokenpak/
  - Version shows `1.0.0`
  - Description renders correctly
  - Install command works: `pip install tokenpak==1.0.0`
- [ ] Create GitHub Release at `v1.0.0` tag
  - Title: `TokenPak v1.0.0 — First Stable Release`
  - Body: paste `RELEASE_NOTES_v1.0.md`
  - Attach wheel as a binary artifact

---

## Phase 4 — Post-Release Watch (first 24 hours) · ~15 min active

- [ ] Monitor PyPI downloads (check next day): https://pypistats.org/packages/tokenpak
- [ ] Check GitHub Issues for `[bug]` labels — any "upgrade broke my code" reports?
- [ ] Search PyPI/GitHub for integration breakage (search `tokenpak` on GitHub)
- [ ] Confirm docs site reflects new version (if hosted separately)
- [ ] Send release announcement
  - Tweet / X: short + punchy, link to PyPI
  - Email beta testers / supporters with release notes
  - Post in relevant Discord/Slack communities

---

## Phase 5 — Ongoing (weekly, first month) · ~30 min/week

- [ ] Review open GitHub Issues each Monday
- [ ] Triage bug reports: P0 (crash) → hotfix same day, P1 (data loss) → within 48h
- [ ] Track performance in production: median latency, compression ratio, error rate
  ```bash
  tokenpak stats --since 7d
  ```
- [ ] Plan v1.0.1 patch if any critical bugs found — don't let them sit
- [ ] Solicit structured feedback from early adopters (GitHub Discussions or form)

---

## Emergency Rollback (if PyPI release is broken)

```bash
# Yank the broken release (hides it from pip install, doesn't delete)
twine upload --skip-existing dist/tokenpak-0.9.0*  # republish last stable
# OR use PyPI web UI: Manage → Yank version
```

> **Note:** PyPI doesn't allow re-uploading the same version. If `1.0.0` is broken, you must release `1.0.1`.

---

## Quick Reference

| Action | Command |
|---|---|
| Run tests | `pytest tests/ -v` |
| Lint | `ruff check tokenpak/` |
| Security scan | `pip audit && bandit -r tokenpak/ -ll` |
| Build | `python -m build` |
| Upload | `twine upload dist/tokenpak-1.0.0*` |
| Tag | `git tag v1.0.0 && git push origin v1.0.0` |
| Smoke test | `curl http://localhost:8765/health` |
| Stats | `tokenpak stats --since 7d` |

---

*Last updated: 2026-03-08 · Maintained by Trix*
