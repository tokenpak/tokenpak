# TokenPak v1.0 Deployment Checklist

**Release Date:** TBD  
**Release Manager:** [Name]  
**Last Updated:** 2026-03-22

This checklist ensures a smooth, risk-free deployment of TokenPak v1.0 to production.

---

## Pre-Release (1-2 days before)
**Estimated time: ~45 minutes**

- [ ] All critical P0/P1 tests passing
  ```bash
  cd ~/Projects/tokenpak && pytest tests/ -v --tb=short
  # Expected exit code: 0
  ```

- [ ] Code linting passes (ruff, mypy)
  ```bash
  ruff check . && mypy tokenpak/
  ```

- [ ] Security audit clean (bandit)
  ```bash
  bandit -r tokenpak/ -ll
  ```

- [ ] Dependencies up to date
  ```bash
  pip audit
  # No critical vulnerabilities
  ```

- [ ] Documentation is complete (no TODO stubs)
  - [ ] README.md reviewed
  - [ ] docs/ folder has all sections
  - [ ] API documentation generated
  - [ ] Quickstart guide is clear

- [ ] Changelog finalized and reviewed
  - [ ] `CHANGELOG_v1.0.md` lists all features
  - [ ] Breaking changes clearly marked
  - [ ] Migration guide (if needed) provided

- [ ] Release notes match actual feature list
  - [ ] Cross-check against merged PRs
  - [ ] No exaggerated claims

---

## Staging Deployment (24 hours before)
**Estimated time: ~30 minutes**

- [ ] Deploy to staging environment
  ```bash
  # (if staging exists)
  cd /staging/tokenpak && git pull origin v1.0.0
  ```

- [ ] Run smoke tests against staging
  ```bash
  curl -X POST http://staging:8766/v1/compress \
    -H "Content-Type: application/json" \
    -d '{"text": "sample prompt", "format": "compact"}'
  # Expected: 200 OK with compression result
  ```

- [ ] Load test with 10x expected traffic
  ```bash
  ab -n 1000 -c 50 http://staging:8766/health
  # Should sustain <100ms latency at p99
  ```

- [ ] Check error logs for new warnings
  ```bash
  tail -100 /var/log/tokenpak/error.log | grep WARN
  ```

- [ ] Verify provider adapters respond
  - [ ] OpenAI adapter health check
  - [ ] Anthropic adapter health check
  - [ ] Google adapter health check

- [ ] Test fallback chains (provider A fails → use provider B)
  ```bash
  # Kill OpenAI endpoint, verify fallback to Anthropic
  # Should not error; should route to fallback
  ```

---

## PyPI Release
**Estimated time: ~20 minutes**

- [ ] Version bumped in `pyproject.toml`
  ```bash
  # Should read: version = "1.0.0"
  grep "^version" pyproject.toml
  ```

- [ ] Git tag created
  ```bash
  git tag -a v1.0.0 -m "Release TokenPak v1.0.0"
  git push origin v1.0.0
  ```

- [ ] Build wheel
  ```bash
  python -m build
  ls -lh dist/tokenpak-1.0.0-py3-none-any.whl
  ```

- [ ] Verify wheel contents
  ```bash
  unzip -l dist/tokenpak-1.0.0-py3-none-any.whl | head -30
  # Should contain: tokenpak/, docs/, README.md, etc.
  ```

- [ ] Upload to PyPI
  ```bash
  twine upload dist/tokenpak-1.0.0-py3-none-any.whl
  # Watch for: "Uploading tokenpak-1.0.0-py3-none-any.whl (XXX)"
  ```

- [ ] Verify PyPI page
  - [ ] https://pypi.org/project/tokenpak/ shows v1.0.0
  - [ ] Download links working
  - [ ] Readme renders correctly

- [ ] Create GitHub release
  - [ ] Tag: `v1.0.0`
  - [ ] Title: "TokenPak v1.0.0 — Production Ready"
  - [ ] Body: Full `CHANGELOG_v1.0.md` content
  - [ ] Attach: `dist/tokenpak-1.0.0-py3-none-any.whl`

---

## Post-Release (first 24 hours)
**Estimated time: ~15 minutes per check**

- [ ] Monitor PyPI downloads
  ```bash
  # Check after 2 hours, 12 hours, 24 hours
  curl https://pepy.tech/api/v2/projects/tokenpak | grep downloads
  ```

- [ ] Check GitHub issues for "upgrade broke my code" reports
  - [ ] Filter by label: `regression`
  - [ ] Response time: < 4 hours for P0 issues

- [ ] Monitor error logs for new failure patterns
  ```bash
  tail -f /var/log/tokenpak/error.log
  # Watch for: ImportError, AttributeError, unexpected exceptions
  ```

- [ ] Verify documentation site updated
  - [ ] https://tokenpak.dev shows v1.0.0
  - [ ] All links working

- [ ] Announce release
  - [ ] Tweet from @tokenpak account (if exists)
  - [ ] Email beta testers / early adopters
  - [ ] Post in relevant communities (HN, Reddit, Discord)

- [ ] Provide support for common questions
  - [ ] Have upgrade FAQ ready
  - [ ] Monitor Discord/Slack for help requests

---

## Ongoing (weekly for first month)
**Estimated time: ~30 minutes/week**

- [ ] Review bug reports and triage
  - [ ] P0/critical: assign immediately
  - [ ] P1/high: assign within 24 hours
  - [ ] P2/medium: backlog

- [ ] Plan v1.0.1 patch release if critical bugs found
  - [ ] Test fix against issue reproduction case
  - [ ] Update changelog for patch
  - [ ] Repeat PyPI upload process

- [ ] Track performance metrics
  - [ ] Compression ratio (target: 50%+ reduction)
  - [ ] Latency at p99 (target: <100ms)
  - [ ] Error rate (target: <0.1%)

- [ ] Solicit user feedback
  - [ ] GitHub Discussions: "What's working? What's not?"
  - [ ] Collect feature requests
  - [ ] Plan v1.1 based on feedback

---

## Emergency Rollback (if critical issue found post-release)

If a critical bug is discovered after release:

```bash
# Remove from PyPI (requires PyPI admin access)
pip index versions tokenpak

# Yank v1.0.0 release (mark as unavailable)
# (contact PyPI support or use twine)

# Create hotfix on main
git checkout main
git pull origin main
# Fix the bug
git add -A && git commit -m "fix: critical issue in v1.0.0"
git tag -a v1.0.1 -m "Hotfix for v1.0.0"

# Re-upload
python -m build && twine upload dist/tokenpak-1.0.1-py3-none-any.whl
```

---

**Release sign-off:** ____________________  
**Date:** ____________________
