"""Regression tests for public demo honesty labels."""

from types import SimpleNamespace

from tokenpak import _cli_core
from tokenpak.compression import pipeline as compression_pipeline


def test_compression_demo_labels_fixture_output(monkeypatch, capsys):
    class FakePipeline:
        def __init__(self, enable_instruction_table):
            assert enable_instruction_table is False

        def run(self, messages):
            assert messages
            return SimpleNamespace(
                tokens_saved=245,
                tokens_raw=747,
                tokens_after=502,
                savings_pct=32.8,
                stages_run=["dedup", "alias"],
            )

    monkeypatch.setattr(compression_pipeline, "CompressionPipeline", FakePipeline)

    _cli_core._run_compression_demo()

    out = capsys.readouterr().out
    assert "Offline Fixture Demo" in out
    assert "built-in sample fixture" in out
    assert "Fixture delta" in out
    assert "Fixture cost delta" in out
    assert "not a savings receipt" in out
    assert "Live Compression Demo" not in out
    assert "Cost saved (est.)" not in out

    # Having labelled its own numbers as fixture data, demo must then point at
    # the measured path. This asserted the literal "receipt-backed savings",
    # which pinned one wording of the next-steps block and said nothing about
    # whether the route it offered was real — the same block also advertised
    # `tokenpak serve … (zero-config)`, which contradicted setup and doctor.
    assert "tokenpak setup" in out, "next-steps must start at setup"
    assert "zero-config" not in out, "there is no zero-config path; setup is required"
    assert any(verb in out for verb in ("tokenpak savings", "tokenpak cost")), (
        "next-steps must route to a measured-savings command"
    )
    assert "measured" in out, "next-steps must distinguish the measured path from this fixture"
