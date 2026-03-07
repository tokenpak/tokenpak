# TokenPak Dependencies

*Last audited: 2026-03-06 by Cali (pip-audit 2.10.0)*

## Core Package

**Runtime dependencies:** None — TokenPak's core uses Python stdlib only.

**Optional runtime dependency:**

| Package   | Version Spec  | Purpose                          | Installed |
|-----------|--------------|----------------------------------|-----------|
| tiktoken  | >=0.5.0      | Accurate token counting (OpenAI) | 0.12.0 ✅  |

## Portal (portal/requirements.txt)

| Package   | Version Spec  | Purpose                | Audit Status |
|-----------|--------------|------------------------|--------------|
| flask     | >=3.0.0      | Web framework          | ✅ No CVEs   |
| stripe    | >=7.0.0      | Payment processing     | ✅ No CVEs   |
| gunicorn  | >=21.0.0     | WSGI production server | ✅ No CVEs   |

## Documentation (docs/requirements.txt)

| Package         | Version Spec  | Purpose              | Audit Status |
|-----------------|--------------|----------------------|--------------|
| mkdocs          | >=1.5.0      | Docs site generator  | ✅ No CVEs   |
| mkdocs-material | >=9.5.0      | Docs theme           | ✅ No CVEs   |

## Dev Dependencies (pyproject.toml)

| Package  | Version Spec  | Purpose         |
|----------|--------------|-----------------|
| pytest   | >=7.0        | Test runner     |

## Dependency Pinning Rationale

TokenPak uses **floor-pinned** dependencies (`>=version`) rather than exact pins.

**Why floor-pinned:**
- Core has zero runtime deps — nothing to pin
- Portal/docs dependencies are low-churn, well-maintained packages
- Allows users to upgrade for security patches without waiting for a TokenPak release
- Avoids pip resolution conflicts in complex environments

**Tradeoff:** Users could pull in breaking major versions. Mitigated by:
- Semantic versioning on all deps (`flask>=3.0.0` excludes Flask 2.x incompatibilities)
- CI runs against latest compatible versions
- Major version bumps tracked manually

## Audit History

| Date       | Tool      | Result                        |
|------------|-----------|-------------------------------|
| 2026-03-06 | pip-audit 2.10.0 | ✅ No known vulnerabilities |

## Third-Party Services

| Service   | Required | Credentials          | Data Sent            |
|-----------|----------|---------------------|----------------------|
| OpenAI    | Optional | `OPENAI_API_KEY` env | Prompts/completions via proxy |
| Anthropic | Optional | `ANTHROPIC_API_KEY` env | Prompts/completions via proxy |
| Stripe    | Portal only | Set in portal config | Payment data         |
| GitHub    | Optional | `GITHUB_TOKEN` env   | Repo file fetching   |
| Notion    | Optional | `NOTION_API_TOKEN` env | Page content fetching |

**Data flow note:** TokenPak acts as a local proxy/compiler. API keys are passed through from clients to upstream providers — they are never stored by TokenPak itself.
