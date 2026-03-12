# Teacher Pack Builder

`tokenpak teacher` auto-generates deterministic intent→context recipes from known source trees and CLI command surfaces.

## Commands

```bash
tokenpak teacher generate
tokenpak teacher validate
```

### Repeatable script path

```bash
python3 scripts/generate_teacher_recipes.py
```

## Output

Versioned output is written to:

- `~/Projects/tokenpak/recipes/context/<version>/intent_recipes.json`
- `~/Projects/tokenpak/recipes/context/<version>/validation_report.json`

Each recipe contains:

- `required_blocks`
- `optional_blocks`
- `token_budget` (`target`, `estimated`, `status`)

## Validation gates

Validation report includes:

- missing source coverage (`missing_sources`)
- stale/missing refs (`stale_refs`)
- token budget warnings (`token_budget_warnings`)

## Governance / review workflow

1. Regenerate when vault structure, frontmatter conventions, or command intents change.
2. Review git diff for `recipes/context/<version>/` artifacts.
3. Block publish if validation summary reports non-zero stale refs or missing sources.
4. Keep generator inputs deterministic (stable source roots + sorted traversal) to prevent drift.
