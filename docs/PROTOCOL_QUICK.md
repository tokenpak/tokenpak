# TokenPak Protocol v1.0 — Quick Reference

> Full spec: `docs/PROTOCOL.md` · Schema: `schemas/tokenpak-v1.0.json`

---

## Minimal Valid Pack

```json
{
  "header": {"version": "1.0", "id": "pak_abc123", "created": "2026-03-07T00:00:00Z"},
  "metadata": {"task": "my task", "source": "agent:my_agent"},
  "blocks": [{"type": "knowledge", "id": "ctx", "content": "Context goes here."}]
}
```

---

## Required Sections

| Section    | Fields Required                        |
|------------|----------------------------------------|
| `header`   | `version`, `id`, `created`             |
| `metadata` | `task`, `source`                       |
| `blocks`   | At least 1 block with `type`, `id`, `content` |

---

## Block Types (Quick)

| Type           | Use For                      | Compress? |
|----------------|------------------------------|-----------|
| `instructions` | System prompts, rules        | Never     |
| `code`         | Source code, configs         | Smart     |
| `knowledge`    | Docs, facts                  | OK        |
| `memory`       | Agent state                  | Capsule   |
| `conversation` | Chat history                 | Old turns |
| `evidence`     | RAG results                  | Aggressive|
| `system`       | Routing (hidden from LLM)    | N/A       |

---

## Priority Values

`critical` → `high` → `medium` → `low` → `internal`

---

## Compaction Modes

| Mode         | Reduction | Deterministic |
|--------------|-----------|---------------|
| `lossless`   | 0–10%     | ✅            |
| `balanced`   | 30–50%    | ✅            |
| `aggressive` | 50–70%    | ✅            |
| `semantic`   | 60–80%    | ❌            |

---

## Trust Levels

`verified` · `unverified` · `generated`

---

## Wire Format (compact)

```
TOKENPAK/1.0
ID: pak_abc123
BUDGET: 8000/500
MODE: balanced

[instructions:sys] priority=critical tokens=50
You are an assistant.

[knowledge:docs] priority=high tokens=450
Context content...

---END---
```

---

## Validate

```bash
tokenpak validate pack.json          # validate
tokenpak validate pack.json --verbose # detailed output
tokenpak validate pack.json --json    # machine-readable
```

---

## ID Format

Pack IDs: `pak_` + 12 random hex chars → `pak_a1b2c3d4e5f6`

---

## Expires

ISO 8601 UTC: `"2026-03-08T08:30:00Z"` — receivers should reject expired packs.

---

## Source/Target Format

`agent:<id>` · `user:<id>` · `system:<id>` · `service:<name>`

---

## Version Rules

- Unknown major version → **reject**
- Unknown minor version → **warn, continue**
- Unknown optional fields → **ignore** (forward compat)

---

*See `examples/` for ready-to-use templates.*
