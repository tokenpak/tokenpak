# TokenPak Protocol Specification v1.0 — The Context Standard

> **Status:** Stable · **Version:** 1.0 · **Schema:** `https://tokenpak.dev/schema/v1.0.json`

---

## Overview

TokenPak is an open, vendor-neutral context packaging format that lets any AI system — agents, LLM pipelines, RAG systems, MCP servers — create and exchange structured context packs. Two completely different systems can exchange TokenPaks without requiring the TokenPak proxy.

**Design goals:**
- Interoperability: LangChain generates a pack → CrewAI reads it, no glue code
- Compactness: Budget-aware, compaction policies built in
- Traceability: Full provenance and chain of custody
- Extensibility: MCP/skills support, optional embeddings, cryptographic signatures

---

## Pack Structure

```
TokenPak
 │
 ├─ header          (required)  Version, ID, timestamp, schema
 ├─ metadata        (required)  Task, source, target, tags, TTL
 ├─ blocks[]        (required)  Content: knowledge, code, instructions...
 ├─ capabilities    (optional)  Tools, skills, MCP servers, resources
 ├─ constraints     (optional)  Model reqs, permissions, guardrails
 ├─ state           (optional)  Workflow tracking, checkpoints
 ├─ provenance      (optional)  Origin, transforms, trust, signatures
 ├─ policies        (optional)  Compaction modes, token budgets
 └─ embeddings      (optional)  Block vectors for semantic search
```

---

## Section 1: Header (Required)

Uniquely identifies the pack and its schema version.

```json
{
  "header": {
    "version": "1.0",
    "id": "pak_a1b2c3d4e5f6",
    "created": "2026-03-07T08:30:00Z",
    "schema": "https://tokenpak.dev/schema/v1.0.json"
  }
}
```

| Field    | Type   | Required | Description                          |
|----------|--------|----------|--------------------------------------|
| version  | string | ✅        | Protocol version. Always `"1.0"`.    |
| id       | string | ✅        | Unique pack ID. Prefix `pak_` + hex. |
| created  | string | ✅        | ISO 8601 UTC creation timestamp.     |
| schema   | string | ❌        | JSON Schema URL for validation.      |

---

## Section 2: Metadata (Required)

Describes what the pack is for and who it's between.

```json
{
  "metadata": {
    "task": "debug_proxy_issue",
    "source": "agent:research_agent",
    "target": "agent:coding_agent",
    "tags": ["debugging", "proxy", "urgent"],
    "expires": "2026-03-08T08:30:00Z"
  }
}
```

| Field   | Type           | Required | Description                                       |
|---------|----------------|----------|---------------------------------------------------|
| task    | string         | ✅        | Human-readable task description.                  |
| source  | string         | ✅        | Creator identifier. Format: `agent:id`, `user:id`.|
| target  | string         | ❌        | Intended recipient. Same format as `source`.      |
| tags    | array[string]  | ❌        | Searchable labels.                                |
| expires | string         | ❌        | ISO 8601 TTL. Recipients should reject stale packs.|

---

## Section 3: Blocks (Required, min 1)

The primary content of the pack. Each block has a `type`, `id`, and `content`.

### Block Types

| Type         | Purpose                           | Priority Default | Compaction         |
|--------------|-----------------------------------|------------------|--------------------|
| instructions | System prompts, rules             | critical         | Never compress     |
| code         | Source code, configs              | high             | Treesitter-aware   |
| knowledge    | Documentation, facts              | high             | Semantic OK        |
| memory       | Agent state, preferences          | medium           | Capsule compress   |
| conversation | Chat history, turns               | medium           | Older turns OK     |
| evidence     | Retrieved documents, citations    | low              | Aggressive OK      |
| system       | Internal routing (not sent to LLM)| internal         | Not sent to model  |

### Block Schema

```json
{
  "blocks": [
    {
      "type": "instructions",
      "id": "system_prompt",
      "content": "You are a debugging assistant...",
      "tokens": 150,
      "priority": "critical",
      "compacted": false
    },
    {
      "type": "knowledge",
      "id": "api_docs",
      "content": "API documentation...",
      "tokens": 420,
      "priority": "high",
      "quality": 0.92,
      "compacted": false
    },
    {
      "type": "evidence",
      "id": "retrieved_doc_001",
      "content": "Search result content...",
      "tokens": 310,
      "priority": "medium",
      "quality": 0.85,
      "compacted": true,
      "provenance": {
        "source_type": "pinecone",
        "source_id": "doc_abc123",
        "retrieved_at": "2026-03-07T08:25:00Z"
      }
    }
  ]
}
```

### Block Fields

| Field      | Type   | Required | Description                                        |
|------------|--------|----------|----------------------------------------------------|
| type       | string | ✅        | One of the block types above.                      |
| id         | string | ✅        | Unique within this pack. Alphanumeric + `_-`.      |
| content    | string | ✅        | Text content of the block.                         |
| tokens     | int    | ❌        | Estimated token count. Computed if omitted.        |
| priority   | string | ❌        | `critical`, `high`, `medium`, `low`, `internal`.   |
| quality    | float  | ❌        | 0.0–1.0 relevance score. Used for compaction order.|
| compacted  | bool   | ❌        | Whether content has been compressed.               |
| provenance | object | ❌        | Source tracking for evidence blocks.               |

### Priority Values

| Value    | Description                             |
|----------|-----------------------------------------|
| critical | Never drop. Instructions, active system.|
| high     | Drop last. Core task context.           |
| medium   | Drop if over budget.                    |
| low      | Drop first. Background context.         |
| internal | Routing metadata. Not forwarded to LLM. |

---

## Section 4: Capabilities (Optional)

Declares what the receiving agent can do — tools, MCP skills, servers, and resources.

```json
{
  "capabilities": {
    "tools": [
      {
        "name": "read_file",
        "description": "Read contents of a file",
        "parameters": {
          "type": "object",
          "properties": {
            "path": {"type": "string", "description": "File path to read"}
          },
          "required": ["path"]
        },
        "provider": "mcp:filesystem"
      }
    ],
    "skills": [
      {
        "id": "code_review",
        "name": "Code Review Skill",
        "description": "Review PRs and suggest improvements",
        "mcp_server": "mcp://localhost:3001/github"
      }
    ],
    "mcp_servers": [
      {
        "uri": "mcp://localhost:3000/filesystem",
        "name": "filesystem",
        "capabilities": ["read", "write", "list"],
        "authenticated": true
      },
      {
        "uri": "mcp://localhost:3001/github",
        "name": "github",
        "capabilities": ["issues", "prs", "repos"]
      }
    ],
    "resources": [
      {
        "uri": "mcp://filesystem/home/user/project",
        "name": "Project Directory",
        "mime_type": "inode/directory",
        "readable": true,
        "writable": false
      }
    ]
  }
}
```

---

## Section 5: Constraints (Optional)

Specifies model requirements, runtime permissions, and safety guardrails.

```json
{
  "constraints": {
    "model": {
      "min_context_window": 100000,
      "requires_vision": false,
      "requires_tools": true,
      "preferred_models": ["claude-3-opus", "gpt-4-turbo"]
    },
    "permissions": {
      "can_execute_code": true,
      "can_access_network": true,
      "can_modify_files": false,
      "can_send_messages": false,
      "allowed_domains": ["github.com", "api.anthropic.com"]
    },
    "guardrails": {
      "max_cost_usd": 5.00,
      "timeout_seconds": 300,
      "max_tokens_output": 4096,
      "require_human_approval": ["file_delete", "send_email"]
    }
  }
}
```

---

## Section 6: State (Optional)

Tracks position in a multi-step workflow for resumable pipelines.

```json
{
  "state": {
    "workflow_id": "wf_research_to_write_001",
    "step_index": 2,
    "total_steps": 4,
    "checkpoints": [
      {"step": 1, "pack_id": "pak_step1_abc", "timestamp": "2026-03-07T08:00:00Z"},
      {"step": 2, "pack_id": "pak_step2_def", "timestamp": "2026-03-07T08:15:00Z"}
    ],
    "parent_pack_id": "pak_original_request",
    "resumable": true,
    "status": "in_progress"
  }
}
```

| Field          | Type          | Description                                  |
|----------------|---------------|----------------------------------------------|
| workflow_id    | string        | Identifier for the overall workflow.         |
| step_index     | int           | Current step (0-indexed).                    |
| total_steps    | int           | Total steps if known.                        |
| checkpoints    | array         | History of prior steps with their pack IDs. |
| parent_pack_id | string        | Pack that originated this workflow.          |
| resumable      | bool          | Whether execution can be resumed mid-step.  |
| status         | string        | `not_started`, `in_progress`, `done`, `failed`|

---

## Section 7: Provenance (Optional)

Full lineage tracking: where did this pack come from, what was done to it, how trusted is it?

```json
{
  "provenance": {
    "source_packs": ["pak_research_001", "pak_retrieval_002"],
    "transforms": [
      {"type": "merge", "timestamp": "2026-03-07T08:20:00Z", "agent": "orchestrator"},
      {"type": "compact", "mode": "balanced", "timestamp": "2026-03-07T08:21:00Z", "agent": "orchestrator"}
    ],
    "trust_level": "verified",
    "signatures": [
      {
        "signer": "agent:orchestrator",
        "algorithm": "ed25519",
        "signature": "base64encodedvalue...",
        "timestamp": "2026-03-07T08:22:00Z"
      }
    ]
  }
}
```

### Trust Levels

| Level      | Description                                      |
|------------|--------------------------------------------------|
| verified   | Content signed and verifiable.                   |
| unverified | Content not signed; treat with caution.          |
| generated  | Content was AI-generated; may contain errors.    |

### Transform Types

| Type     | Description                         |
|----------|-------------------------------------|
| merge    | Multiple packs combined.            |
| compact  | Content reduced by compaction mode. |
| filter   | Blocks removed by policy.           |
| enrich   | Blocks added (e.g., RAG retrieval). |
| sign     | Cryptographic signature applied.    |

---

## Section 8: Policies (Optional)

Controls how the pack is compacted when it exceeds token budgets.

```json
{
  "policies": {
    "compaction": {
      "mode": "balanced",
      "max_tokens": 8000,
      "priority_order": ["instructions", "code", "knowledge", "memory", "conversation"],
      "per_type_limits": {
        "evidence": {"max_tokens": 1500, "mode": "aggressive"},
        "conversation": {"max_tokens": 1000, "hot_window": 3}
      }
    },
    "budget": {
      "total": 8000,
      "per_block_max": 2000,
      "reserve_for_output": 2000
    }
  }
}
```

### Compaction Modes

| Mode       | Description                        | Reduction | Deterministic |
|------------|------------------------------------|-----------|---------------|
| lossless   | Whitespace/formatting only         | 0–10%     | ✅             |
| balanced   | Smart compression, preserves facts | 30–50%    | ✅             |
| aggressive | Maximum token reduction            | 50–70%    | ✅             |
| semantic   | Embedding-guided summarization     | 60–80%    | ❌             |

---

## Section 9: Embeddings (Optional)

Pre-computed embedding vectors for semantic search and similarity-based compaction.

```json
{
  "embeddings": {
    "model": "text-embedding-3-small",
    "dimensions": 1536,
    "block_vectors": {
      "system_prompt": [0.023, -0.041, 0.087],
      "retrieved_doc_001": [0.087, 0.012, -0.033]
    }
  }
}
```

---

## Wire Format

Compact text format for bandwidth-efficient transport. All JSON TokenPaks can be serialized to/from wire format.

```
TOKENPAK/1.0
ID: pak_a1b2c3d4e5f6
BUDGET: 8000/1230
MODE: balanced
CAPABILITIES: 3 tools, 1 skill, 2 mcp_servers
TRUST: verified

[instructions:system_prompt] priority=critical tokens=150
You are a debugging assistant...

[knowledge:api_docs] priority=high tokens=420 quality=0.92
API documentation content...

[evidence:search_001] priority=medium tokens=310 compacted
Compressed search results...

---CAPABILITIES---
@tool read_file provider=mcp:filesystem
@tool write_file provider=mcp:filesystem
@skill code_review server=mcp://localhost:3001/github

---END---
```

### Wire Format Rules

1. First line: always `TOKENPAK/1.0`
2. Header fields: `KEY: VALUE` lines before first block
3. Block markers: `[type:id]` with optional `key=value` attributes
4. Block content: lines immediately following the marker
5. Capabilities section: after `---CAPABILITIES---`
6. Pack ends with `---END---`

---

## Versioning & Backward Compatibility

### Current Version: 1.0

- Implementations MUST reject packs with unknown major versions.
- Implementations SHOULD warn on packs with unknown minor versions.
- Unknown fields in optional sections MUST be ignored (forward compatibility).
- Required fields (`header`, `metadata`, `blocks`) MUST be present.

### Version Negotiation

When exchanging packs between systems:
1. Sender MUST include `header.version`.
2. Receiver checks major version. If unsupported → reject with error.
3. Receiver ignores unknown fields in optional sections.

### Future Versions

| Change Type         | Version Bump | Notes                           |
|---------------------|-------------|----------------------------------|
| New optional field  | Minor (1.x) | Backward compatible              |
| New required field  | Major (x.0) | Breaking change, needs migration |
| New block type      | Minor (1.x) | Receivers ignore unknown types   |
| Removed field       | Major (x.0) | Breaking change                  |

### Migration Guide (v1.x → v2.0, future)

Future migration guides will be published at `https://tokenpak.dev/migration/v2.0` and included in `docs/MIGRATION_GUIDE.md`.

---

## Full JSON Example

See `examples/full.tokenpak.json` for a complete pack using all sections.
See `examples/minimal.tokenpak.json` for the minimum valid pack.

---

## Validation

```bash
# Validate a pack file
tokenpak validate my_pack.json

# Validate with verbose output
tokenpak validate my_pack.json --verbose

# Validate and output JSON result
tokenpak validate my_pack.json --json
```

---

## Interoperability Reference

### Interop Checklist

A pack is interoperable when:
- [ ] `header.version` is present and parseable
- [ ] `metadata.task` and `metadata.source` are non-empty
- [ ] At least one block with `type`, `id`, and `content`
- [ ] No required fields missing
- [ ] Expires not in the past (if set)

### Success Scenario

> A LangChain developer creates a TokenPak with MCP skills.  
> A CrewAI developer receives it and can use those skills.  
> Neither needs the TokenPak proxy.  
> The pack validates against the schema.  
> Provenance shows the chain of custody.

---

*TokenPak Protocol Specification v1.0 — Published 2026-03-07*
