# SPEC: TokenPak Versioning & Deployment System

**Status:** DRAFT
**Depends on:** PRD-versioning-deployment.md
**Date:** 2026-03-24

---

## 1. Version Sources of Truth

### 1.1 Package version
```
packages/core/pyproject.toml  →  [project] version = "X.Y.Z"
packages/core/tokenpak/__init__.py  →  __version__ = "X.Y.Z"
```
Both must match. `pyproject.toml` is canonical; `__init__.py` can derive it.

### 1.2 Proxy version
```
proxy.py  →  PROXY_VERSION = "X.Y.Z"
```
Must always equal the package version.

### 1.3 Lock file (runtime state)
```
~/.tokenpak/tokenpak.lock.json
{
  "version": "1.0.2",
  "proxyHash": "627c250b8ab4",
  "installedAt": "2026-03-24T20:00:00Z",
  "installedBy": "tokenpak-update",
  "previous": {
    "version": "1.0.1",
    "proxyHash": "2bb4643af8c7",
    "installedAt": "2026-03-23T15:00:00Z"
  }
}
```

---

## 2. Proxy Bundling

### 2.1 Location in package
```
packages/core/
  tokenpak/
    runtime/
      proxy.py          ← canonical proxy, bundled in pip
      __init__.py
```

`MANIFEST.in` includes `tokenpak/runtime/proxy.py`.

### 2.2 How `tokenpak serve` finds proxy.py
```python
# Priority order (first found wins):
PROXY_SEARCH_PATHS = [
    Path(__file__).parent / "runtime" / "proxy.py",   # bundled (preferred)
    Path.home() / "tokenpak" / "proxy.py",            # user-deployed legacy
    Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "proxy.py",  # dev
]
```

### 2.3 Deployment on update
`tokenpak update` copies from the bundled location to `~/tokenpak/proxy.py`:
```python
src = Path(tokenpak.__file__).parent / "runtime" / "proxy.py"
dst = Path.home() / "tokenpak" / "proxy.py"
shutil.copy2(src, dst)
```

---

## 3. Protected Files Specification

```python
PROTECTED_PATTERNS = [
    "~/.tokenpak/config.json",
    "~/.tokenpak/config.yaml",
    "~/.tokenpak/*.db",
    "~/.tokenpak/vault/**",
    "~/.tokenpak/keys/**",
    "~/.tokenpak/rules/**",
    "~/.tokenpak/templates/**",
    "~/vault/**/*.md",
    "~/vault/**/*.json",
    "~/vault/**/*.sql",
    "~/vault/**/*.db",
]
```

`tokenpak update` must check each write against this list and abort if match.

---

## 4. `tokenpak update` Specification

### 4.1 Flags
| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--check` | `-c` | false | Check only, no changes |
| `--force` | `-f` | false | Apply even if versions match |
| `--proxy-only` | | false | Skip pip upgrade |
| `--pip-only` | | false | Skip proxy sync |
| `--dry-run` | | false | Print what would change |
| `--yes` | `-y` | false | Skip confirmation prompt |
| `--quiet` | `-q` | false | Minimal output |

### 4.2 Update steps (sequential, abort on error)
1. Resolve current versions (pkg, proxy running, proxy file)
2. Fetch latest from PyPI (timeout 5s, skip on failure with warning)
3. Compute proxy drift (MD5 hash of running vs canonical)
4. Present summary + changelog snippet
5. Prompt for confirmation (unless `--yes` or `--quiet`)
6. Backup current proxy to `~/.tokenpak/versions/proxy-{version}.py`
7. `pip install --upgrade tokenpak` (unless `--proxy-only`)
8. Copy `tokenpak/runtime/proxy.py` → `~/tokenpak/proxy.py` (unless `--pip-only`)
9. `systemctl --user restart tokenpak-proxy` OR `tokenpak restart`
10. Wait for health check (max 10s, ping `/health` every 1s)
11. Update lockfile
12. Print final status

### 4.3 Rollback specification
```
~/.tokenpak/versions/
  proxy-1.0.0.py
  proxy-1.0.1.py   ← up to last 3
  proxy-1.0.2.py   ← current
  pkg-1.0.1.tar.gz ← pip sdist backup (if available)
```

`tokenpak rollback [--version X.Y.Z]` — restores from this cache.

---

## 5. `tokenpak version` Output Spec

```
TokenPak 1.0.2

  Package (installed) : 1.0.2
  Package (PyPI)      : 1.0.2  ✓ up to date
  Proxy   (running)   : 1.0.2
  Proxy   (on disk)   : 1.0.2  ✓ in sync
  Proxy   (hash)      : 627c250b8ab4

  Installed : 2026-03-24 20:00 UTC  by tokenpak-update
  Previous  : 1.0.1  (2026-03-23 15:00 UTC)
```

---

## 6. CHANGELOG.md Format

Location: `CHANGELOG.md` at repo root.

```markdown
# Changelog

All notable changes to TokenPak are documented here.
Format: [Keep a Changelog](https://keepachangelog.com)
Versioning: [Semantic Versioning](https://semver.org)

## [Unreleased]

## [1.0.2] - 2026-03-24

### Fixed
- Proxy file renamed from `proxy_v4.py` to `proxy.py` fleet-wide (#152)
- `tokenpak update` now syncs proxy.py and restarts via systemd

### Added
- `PROXY_VERSION` constant in proxy.py exposed in `/health` response
- `tokenpak update --proxy-only` and `--pip-only` flags
- Version rollback cache at `~/.tokenpak/versions/`

## [1.0.1] - 2026-03-22

### Fixed
- Circular import in tokenpak/__init__.py (absolute→relative)
...
```

---

## 7. Release Automation (future)

GitHub Actions workflow: `.github/workflows/release.yml`

```yaml
on:
  push:
    tags: ['v*.*.*']

jobs:
  release:
    steps:
      - Validate version consistency (pyproject.toml == proxy.py == __init__.py)
      - Run test suite (must pass 100%)
      - Build dist/
      - Publish to PyPI
      - Create GitHub release with CHANGELOG entry as body
      - Notify fleet (webhook or vault commit)
```

---

## 8. Migration System

For breaking config changes (MAJOR bumps):

```python
# tokenpak/migrations/v2_0_0.py
def migrate(config: dict) -> dict:
    """Migrate config from 1.x to 2.0."""
    ...
```

`tokenpak migrate` runs all pending migrations in order, creates backup first.

---

## 9. Internal Fleet Protocol

```bash
# In Sue's heartbeat (daily):
for agent in trix cali; do
  ssh $agent@${agent}bot "tokenpak update --check --quiet" 2>/dev/null
done
```

If drift detected → create a task in agent's queue to run `tokenpak update`.
For PATCH updates → auto-apply silently.
For MINOR/MAJOR → notify Kevin.

---

*Next: PLAN.md → task breakdown and timeline*
