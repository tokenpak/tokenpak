# TokenPak runtime-artifact schemas

JSON Schemas for the runtime artifacts the `tokenpak` package produces. Canonical form per `01-architecture-standard.md §11.7`: host `docs.tokenpak.ai`, group `tokenpak/`, version `-v<MAJOR>`, filename `<name>.schema.json`.

| File | `$id` | Describes |
|---|---|---|
| [`tokenpak/block.schema.json`](tokenpak/block.schema.json) | `https://docs.tokenpak.ai/schemas/tokenpak/block-v1.json` | Block — a single content unit the compiler consumes |
| [`tokenpak/compiled.schema.json`](tokenpak/compiled.schema.json) | `https://docs.tokenpak.ai/schemas/tokenpak/compiled-v1.json` | Compiled artifact — the assembled, ordered context bundle |
| [`tokenpak/evidence.schema.json`](tokenpak/evidence.schema.json) | `https://docs.tokenpak.ai/schemas/tokenpak/evidence-v1.json` | Evidence pack — extractive spans with provenance + integrity |

TIP protocol schemas (`tip/`) and manifest schemas (`manifests/`) live in the sibling `tokenpak/registry` repo, not here. Linking: always by canonical `$id` URL; see `06-docs-style-guide.md §8`.
