"""DLP mode executors — 1.3.0-β acceptance."""

from __future__ import annotations

from tokenpak.security.dlp import DLPScanner, apply_mode


def _scan(text: str):
    return DLPScanner().scan(text)


SECRET_BODY = b'{"model":"x","prompt":"AKIAIOSFODNN7EXAMPLE"}'


def test_off_mode_is_noop():
    out = apply_mode("off", SECRET_BODY, _scan(SECRET_BODY.decode()))
    assert out.blocked is False
    assert out.new_body == SECRET_BODY


def test_warn_mode_forwards_unchanged():
    out = apply_mode("warn", SECRET_BODY, _scan(SECRET_BODY.decode()))
    assert out.blocked is False
    assert out.new_body == SECRET_BODY
    assert len(out.findings) >= 1


def test_redact_mode_rewrites_body():
    out = apply_mode("redact", SECRET_BODY, _scan(SECRET_BODY.decode()))
    assert out.blocked is False
    assert b"AKIAIOSFODNN7EXAMPLE" not in out.new_body
    assert b"<REDACTED:aws_access_key>" in out.new_body


def test_block_mode_halts_on_finding():
    out = apply_mode("block", SECRET_BODY, _scan(SECRET_BODY.decode()))
    assert out.blocked is True
    # Body is unchanged even on block — the caller formats the error.
    assert out.new_body == SECRET_BODY


def test_empty_findings_never_blocks():
    clean = b'{"model":"x","prompt":"hello"}'
    out = apply_mode("block", clean, [])
    assert out.blocked is False
    assert out.new_body == clean


def test_unknown_mode_fails_open():
    out = apply_mode("bogus", SECRET_BODY, _scan(SECRET_BODY.decode()))
    # Typo shouldn't block traffic silently.
    assert out.blocked is False
    assert out.mode == "off"


def test_multiple_findings_redact_in_reverse_order():
    text = "GHP: ghp_" + "a" * 36 + " AWS: AKIAIOSFODNN7EXAMPLE"
    body = text.encode("utf-8")
    out = apply_mode("redact", body, _scan(text))
    # Both redacted.
    assert b"<REDACTED:github_pat>" in out.new_body
    assert b"<REDACTED:aws_access_key>" in out.new_body
    # Original secrets gone.
    assert b"AKIAIOSFODNN7EXAMPLE" not in out.new_body
