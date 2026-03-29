# TokenPak Security Audit — 2026-03-26

**Auditor:** Cali  
**Scope:** TokenPak `packages/core` — Phase 2 (dependency CVEs + secret scan)  
**Tools:** pip-audit 2.10.0, detect-secrets 1.5.0

---

## 1. Dependency CVE Scan (`pip-audit`)

**Command:**
```bash
pip-audit -r packages/core/requirements.txt --desc
```

**Result:** 1 known vulnerability found

| Package | Version | CVE | Severity | Fix Available |
|---------|---------|-----|----------|---------------|
| pygments | 2.19.2 | CVE-2026-4539 | LOW | None yet |

**CVE-2026-4539 Details:**
- **Description:** ReDoS (inefficient regex complexity) in `AdlLexer` in `pygments/lexers/archetype.py`
- **Attack vector:** Local access only (not remotely exploitable)
- **Impact:** DoS via crafted input to the ADL lexer
- **Status:** Disclosed; upstream has not released a fix yet

**Assessment:** ✅ ACCEPTED — Low severity, local-only, transitive dependency (not directly required by TokenPak). Pygments is used by dev tooling (syntax highlighting). TokenPak proxy does not use `AdlLexer` in any request path.

**Action:** Monitor upstream for fix. No immediate action required.

---

## 2. Secret Detection Scan (`detect-secrets`)

**Command:**
```bash
detect-secrets scan . --exclude-files ".*\.pyc$|\.git|venv/|\.venv/|build/|htmlcov/|__pycache__"
```

**Result:** 1 finding (false positive)

| File | Line | Type | Assessment |
|------|------|------|------------|
| `README.md` | 69 | Secret Keyword | ✅ FALSE POSITIVE — placeholder `"your-anthropic-api-key"` in code example |

**No real secrets detected.** The single finding is documentation placeholder text in a quickstart code block:
```python
api_key="your-anthropic-api-key",  # README.md line 69
```

---

## 3. Summary

| Check | Result |
|-------|--------|
| Direct dependency CVEs | ✅ Clean |
| Transitive dependency CVEs | ⚠️ 1 LOW (pygments, local-only ReDoS) |
| Secrets in codebase | ✅ Clean (1 false positive in README placeholder) |
| HIGH/CRITICAL CVEs | ✅ None |

**Overall: PASS** — No HIGH or CRITICAL CVEs. No real secrets. One accepted LOW-severity transitive CVE.

---

## 4. Acceptance Decisions

| Finding | Decision | Rationale |
|---------|----------|-----------|
| CVE-2026-4539 (pygments) | ACCEPTED | Local-only, transitive, not in request path |
| README.md:69 keyword | FALSE POSITIVE | Placeholder text in docs |

---

*Audit complete. Re-run before OSS release to check for upstream fix to CVE-2026-4539.*
