from __future__ import annotations

import json
import time
from pathlib import Path

from tokenpak.post_run import PostRunProcessor


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_large_output_stored_as_artifact(tmp_path: Path) -> None:
    p = PostRunProcessor(
        artifacts_dir=tmp_path / "artifacts",
        log_path=tmp_path / "logs" / "post_run.jsonl",
        retrieval_rules_path=tmp_path / "rules" / "retrieval.json",
    )

    large_text = "A" * 2400  # ~600 tokens estimate
    result = p.process(
        response_text=large_text,
        tokens_in=100,
        tokens_out=620,
        tier="pro",
        injected_chunks=["chunk-1"],
        latency_ms=320.5,
    )

    assert result.artifact_id is not None
    assert result.artifact_path is not None
    assert result.artifact_path.exists()


def test_artifact_indexed_for_retrieval(tmp_path: Path) -> None:
    p = PostRunProcessor(
        artifacts_dir=tmp_path / "artifacts",
        log_path=tmp_path / "logs" / "post_run.jsonl",
        retrieval_rules_path=tmp_path / "rules" / "retrieval.json",
        index_path=tmp_path / "index" / "artifact_index.jsonl",
    )

    p.process(
        response_text="B" * 2600,
        tokens_in=120,
        tokens_out=650,
        tier="fast",
        injected_chunks=["chunk-2"],
        latency_ms=190.0,
    )

    rows = _read_jsonl(tmp_path / "index" / "artifact_index.jsonl")
    assert len(rows) == 1
    assert rows[0]["artifact_id"].startswith("art-")
    assert Path(rows[0]["path"]).exists()


def test_retrieval_boost_applied_after_need_file_signal(tmp_path: Path) -> None:
    p = PostRunProcessor(
        artifacts_dir=tmp_path / "artifacts",
        log_path=tmp_path / "logs" / "post_run.jsonl",
        retrieval_rules_path=tmp_path / "rules" / "retrieval.json",
    )

    result = p.process(
        response_text="I need file tokenpak/core.py and also missing tokenpak/pack.py for full fix.",
        tokens_in=90,
        tokens_out=80,
        tier="free",
        injected_chunks=["chunk-a"],
        latency_ms=99.0,
    )

    rules = json.loads((tmp_path / "rules" / "retrieval.json").read_text(encoding="utf-8"))
    assert "tokenpak/core.py" in rules["boost_files"]
    assert "tokenpak/pack.py" in rules["boost_files"]
    assert set(result.retrieval_boosts) == {"tokenpak/core.py", "tokenpak/pack.py"}


def test_logging_captures_required_fields(tmp_path: Path) -> None:
    p = PostRunProcessor(
        artifacts_dir=tmp_path / "artifacts",
        log_path=tmp_path / "logs" / "post_run.jsonl",
        retrieval_rules_path=tmp_path / "rules" / "retrieval.json",
    )

    p.process(
        response_text="Need more context. chunk-7 was useful.",
        tokens_in=33,
        tokens_out=44,
        tier="pro",
        injected_chunks=["chunk-7", "chunk-8"],
        latency_ms=555.2,
    )

    rows = _read_jsonl(tmp_path / "logs" / "post_run.jsonl")
    assert len(rows) == 1
    row = rows[0]
    for key in [
        "tokens_in",
        "tokens_out",
        "tier",
        "chunks_injected",
        "latency_ms",
        "need_more_context",
        "useful_chunks",
    ]:
        assert key in row
    assert row["need_more_context"] is True
    assert row["useful_chunks"] == ["chunk-7"]


def test_short_ttl_iteration_cache_works(tmp_path: Path) -> None:
    p = PostRunProcessor(
        artifacts_dir=tmp_path / "artifacts",
        log_path=tmp_path / "logs" / "post_run.jsonl",
        retrieval_rules_path=tmp_path / "rules" / "retrieval.json",
        cache_ttl_seconds=0.05,
    )

    p.cache_retrieval("loop:1", {"chunks": ["c1", "c2"]})
    assert p.get_cached_retrieval("loop:1") == {"chunks": ["c1", "c2"]}

    time.sleep(0.08)
    assert p.get_cached_retrieval("loop:1") is None
