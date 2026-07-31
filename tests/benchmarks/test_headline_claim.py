"""
tests/benchmarks/test_headline_claim.py — Headline token-reduction benchmark.

Regression guard on the compression pipeline: the fixture below must keep
reducing within a fixed band.

This pins *pipeline behavior against a deterministic corpus*, not a public
claim. The README makes no percentage promise, and the messaging standard
prohibits savings claims outright — so a red result here means the pipeline or
the fixture moved, never that public copy needs a number restored.

The unit is also not a model token. The pipeline counts ``len(content) // 4``
— a character proxy, named ``heuristic-chars-per-token-4`` in
``services/preview.py``. On this very fixture the proxy reports ~38% while a
real BPE tokenizer (``cl100k_base``) reports ~21%: a ~17 point divergence, and
below the floor asserted here. The gap sits in the compressed output, not the
input — alias substitution strips many characters but few BPE tokens, because
BPE already encodes repeated CamelCase compactly, and this fixture is built to
exercise exactly that stage. ``test_real_tokenizer_witness`` keeps both numbers
in the same CI log so the proxy is never read alone.

Standard 21 §9.8 — process-enforced blocking job.
Do NOT merge a PR to main if this test is red.

Fixture: tests/fixtures/headline_corpus.txt
  A deterministic 9-message DevOps agent conversation (~8 kB) designed to
  exercise the alias compressor (repeated CamelCase service names, file paths,
  env vars) and the dedup/directives pipeline stages.

Reproducible locally: make benchmark-headline
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from tokenpak.compression.pipeline import CompressionPipeline  # noqa: E402

try:
    import tiktoken  # noqa: F401

    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False

# Per-test skip marker, not a module-level importorskip, so the rest of this
# file still runs without the optional extra. The reason names the companion
# test that covers the load-bearing fact unconditionally — a skip that hides a
# claim is worse than no test, and CI installs `.[dev]`, which does not carry
# tiktoken, so this witness skips there.
_REQUIRES_TIKTOKEN = pytest.mark.skipif(
    not _HAS_TIKTOKEN,
    reason="tiktoken not installed (optional 'tokens' extra). The durable claim — "
    "that these numbers are a character proxy and not model tokens — is asserted "
    "unconditionally by test_pipeline_counts_characters_not_model_tokens.",
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "headline_corpus.txt"

# Inclusive band for the fixture below — a measured property of this corpus, not
# a published figure. Move it only alongside a re-measured fixture.
REDUCTION_MIN = 30.0
REDUCTION_MAX = 50.0

_ROLES = {"system", "user", "assistant"}


def _load_messages() -> list[dict]:
    """Parse headline_corpus.txt into a messages list.

    Format: lines starting with [role] (role in system/user/assistant) open a
    new message; all lines until the next header are its content.
    """
    text = FIXTURE.read_text(encoding="utf-8")
    messages: list[dict] = []
    current_role: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and stripped[1:-1] in _ROLES:
            if current_role is not None:
                messages.append({"role": current_role, "content": "\n".join(current_lines).strip()})
            current_role = stripped[1:-1]
            current_lines = []
        else:
            current_lines.append(line)

    if current_role is not None:
        messages.append({"role": current_role, "content": "\n".join(current_lines).strip()})

    return messages


def test_headline_claim(tmp_path: Path) -> None:
    """Compression on the headline corpus must land in [30, 50]%.

    Uses a per-invocation instruction table (tmp_path) so results are
    identical on every run regardless of prior test history.
    """
    messages = _load_messages()
    assert len(messages) >= 5, (
        f"Corpus parse error: expected >= 5 messages, got {len(messages)}. "
        f"Check tests/fixtures/headline_corpus.txt format."
    )

    pipeline = CompressionPipeline(
        instruction_table_path=str(tmp_path / "instruction_table.json"),
    )
    result = pipeline.run(messages)

    reduction_pct = result.savings_pct

    print(
        f"\nheadline benchmark: {reduction_pct:.1f}% reduction "
        f"({result.tokens_raw}→{result.tokens_after} chars//4 units, not model tokens)"
    )

    assert REDUCTION_MIN <= reduction_pct <= REDUCTION_MAX, (
        f"Compression regression: {reduction_pct:.1f}% not in "
        f"[{REDUCTION_MIN}, {REDUCTION_MAX}] on the deterministic fixture. "
        f"This band is a measured property of that corpus, not a published "
        f"figure — do not 'fix' it by changing public copy. Investigate the "
        f"pipeline change, or re-measure and move the fixture and band together."
    )


@_REQUIRES_TIKTOKEN
def test_real_tokenizer_witness(tmp_path: Path) -> None:
    """Record real-BPE reduction beside the character proxy, in the same CI log.

    Asserts no savings figure — choosing one would itself be a claim, and
    savings claims are prohibited. This exists so the proxy number above can
    never be read, quoted, or promoted without the real number beside it.

    The one hard assertion is the one that cannot become a claim: that the
    proxy has not inverted relative to real BPE, which would mean the
    estimator's character semantics changed without anyone noticing.
    """
    import tiktoken

    messages = _load_messages()
    pipeline = CompressionPipeline(
        instruction_table_path=str(tmp_path / "instruction_table.json"),
    )
    result = pipeline.run(messages)
    compressed = getattr(result, "messages", None)
    if compressed is None:  # pragma: no cover - pipeline shape guard
        pytest.skip("pipeline result exposes no compressed messages")

    enc = tiktoken.get_encoding("cl100k_base")

    def _bpe(msgs: list[dict]) -> int:
        total = 0
        for m in msgs:
            content = m.get("content")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += len(enc.encode(part["text"]))
        return total

    raw_bpe, out_bpe = _bpe(messages), _bpe(compressed)
    bpe_pct = (raw_bpe - out_bpe) / raw_bpe * 100 if raw_bpe else 0.0
    proxy_pct = result.savings_pct

    print(
        f"\ntokenizer witness on the same fixture:"
        f"\n  character proxy (chars//4)   {result.tokens_raw:>6} -> {result.tokens_after:<6}"
        f" = {proxy_pct:.2f}%   <-- the band above pins THIS"
        f"\n  real BPE (cl100k_base)       {raw_bpe:>6} -> {out_bpe:<6}"
        f" = {bpe_pct:.2f}%"
        f"\n  divergence                   {proxy_pct - bpe_pct:.2f} percentage points"
        f"\n  note: cl100k_base is not Anthropic's tokenizer; a real-BPE reference"
        f" point, not a per-provider figure."
    )

    assert raw_bpe > 0, "witness corpus tokenized to nothing — fixture or parser broke"
    assert proxy_pct >= bpe_pct, (
        f"The character proxy ({proxy_pct:.2f}%) now reports LESS reduction than real "
        f"BPE ({bpe_pct:.2f}%). That inverts the known relationship and means the "
        f"estimator's character semantics changed. Investigate before trusting either."
    )


def test_pipeline_counts_characters_not_model_tokens() -> None:
    """The unconditional companion to the tokenizer witness.

    The witness above needs an optional extra and therefore skips in CI. The
    fact it exists to defend must not skip with it: everything this benchmark
    reports is a **character proxy**, and quoting it as a token-reduction
    figure is the error the witness was written to prevent.

    This asserts the premise directly, with no optional dependency, so the day
    someone swaps in a real tokenizer the surrounding claims are forced to be
    revisited rather than silently inheriting a new meaning.
    """
    from tokenpak.compression.pipeline import _estimate_tokens
    from tokenpak.services.preview import TOKENIZER_ID

    assert TOKENIZER_ID == "heuristic-chars-per-token-4", (
        f"The estimator identity changed to {TOKENIZER_ID!r}. Every number this "
        f"benchmark pins, and every doc citing them, assumes chars//4 — revisit "
        f"those before changing this assertion."
    )

    # 400 characters of a single-token-per-4-chars shape → exactly 100 units.
    probe = [{"role": "user", "content": "a" * 400}]
    assert _estimate_tokens(probe) == 100, (
        "The estimator no longer counts len(content)//4. The band in "
        "test_headline_claim and the divergence figures recorded alongside it "
        "are expressed in those units and must be re-derived."
    )
