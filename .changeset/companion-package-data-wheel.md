---
---

fix(packaging): ship companion shell hooks, Codex skills, and companion guide in the wheel.

The published wheel omitted every companion data file not covered by
`[tool.setuptools.package-data]`: the five Codex hook shell scripts
(`companion/codex/hooks_{session_start,pre_send,pre_tool_use,post_tool_use,stop}.sh`),
the Codex skill definitions (`companion/codex/skills/*/SKILL.md`), the Claude
Code companion hook scripts (`companion/hooks/*.sh`), and `companion/GUIDE.md`.
Because `tokenpak codex install` writes hooks.json commands that point at the
scripts in-place inside site-packages, every clean pip install produced hook
commands referencing nonexistent files — all five Codex hooks failed with
exit 127 — and the skills installer had nothing to install.

- `pyproject.toml`: declare the four companion data groups under
  `[tool.setuptools.package-data]` so wheel and sdist both ship them.
- Release gate: `scripts/check-dist-contents.py` now asserts the five Codex
  hook scripts (plus the companion guide) are present by exact path in both
  built artifacts, and that each declared companion data glob ships at least
  one file. The gate inspects built-dist contents, so an in-repo-only check
  can no longer mask a packaging regression.
