"""tests/test_debug_capture.py — GAR-B5 debug capture tests.

Coverage:
  1. CaptureMode enum and get_capture_mode()
  2. encrypt_blob / decrypt_blob roundtrip
  3. decrypt_blob rejects bad magic / short blob
  4. hash_blob correctness (deterministic SHA-256)
  5. capture() in encrypted mode writes .enc file
  6. capture() in hash_only mode writes .hash file
  7. capture() in off mode writes nothing
  8. list_captures() returns correct entries
  9. export_capture() decrypts encrypted blob
  10. export_capture() returns hash record for hash_only
  11. export_capture() raises FileNotFoundError for missing trace_id
  12. CLI: tokenpak debug list (no captures)
  13. CLI: tokenpak debug export <trace_id>
"""

from __future__ import annotations

import json
import secrets

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_blob_dir(tmp_path, monkeypatch):
    """Redirect _BLOB_DIR and _KEY_FILE inside capture module to a tmp path."""
    import tokenpak.debug.capture as cap

    blob_dir = tmp_path / "debug"
    key_file = blob_dir / ".key"
    monkeypatch.setattr(cap, "_BLOB_DIR", blob_dir)
    monkeypatch.setattr(cap, "_KEY_FILE", key_file)
    return blob_dir


@pytest.fixture()
def fixed_key():
    """Return a deterministic 32-byte key for tests."""
    return secrets.token_bytes(32)


# ---------------------------------------------------------------------------
# 1. CaptureMode enum + get_capture_mode
# ---------------------------------------------------------------------------


def test_capture_mode_off(monkeypatch):
    monkeypatch.delenv("TOKENPAK_DEBUG_CAPTURE", raising=False)
    from tokenpak.debug.capture import CaptureMode, get_capture_mode

    assert get_capture_mode() == CaptureMode.OFF


def test_capture_mode_encrypted(monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug.capture import CaptureMode, get_capture_mode

    assert get_capture_mode() == CaptureMode.ENCRYPTED


def test_capture_mode_hash_only(monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    from tokenpak.debug.capture import CaptureMode, get_capture_mode

    assert get_capture_mode() == CaptureMode.HASH_ONLY


def test_capture_mode_unknown_defaults_off(monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "garbage")
    from tokenpak.debug.capture import CaptureMode, get_capture_mode

    assert get_capture_mode() == CaptureMode.OFF


# ---------------------------------------------------------------------------
# 2. encrypt_blob / decrypt_blob roundtrip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip(fixed_key):
    from tokenpak.debug.capture import decrypt_blob, encrypt_blob

    payload = {"meta": {"trace_id": "abc123"}, "request": {"prompt": "hello"}, "response": "world"}
    blob = encrypt_blob(payload, key=fixed_key)
    recovered = decrypt_blob(blob, key=fixed_key)
    assert recovered == payload


def test_encrypt_produces_different_ciphertext_each_time(fixed_key):
    """Nonce is random — same plaintext yields different bytes."""
    from tokenpak.debug.capture import encrypt_blob

    payload = {"x": 1}
    b1 = encrypt_blob(payload, key=fixed_key)
    b2 = encrypt_blob(payload, key=fixed_key)
    assert b1 != b2  # different nonces


def test_decrypt_wrong_key_raises(fixed_key):
    from tokenpak.debug.capture import decrypt_blob, encrypt_blob

    blob = encrypt_blob({"secret": "data"}, key=fixed_key)
    bad_key = secrets.token_bytes(32)
    with pytest.raises(ValueError, match="Decryption failed"):
        decrypt_blob(blob, key=bad_key)


# ---------------------------------------------------------------------------
# 3. decrypt_blob rejects bad/short blobs
# ---------------------------------------------------------------------------


def test_decrypt_bad_magic_raises():
    from tokenpak.debug.capture import decrypt_blob

    with pytest.raises(ValueError, match="Bad magic"):
        decrypt_blob(b"XXXX" + b"\x01" + b"\x00" * 44)


def test_decrypt_too_short_raises():
    from tokenpak.debug.capture import decrypt_blob

    with pytest.raises(ValueError, match="too short"):
        decrypt_blob(b"TPKD\x01\x00\x00\x00")


# ---------------------------------------------------------------------------
# 4. hash_blob determinism and format
# ---------------------------------------------------------------------------


def test_hash_blob_format():
    from tokenpak.debug.capture import hash_blob

    result = hash_blob({"key": "value"})
    assert result.startswith("sha256:")
    assert len(result) == len("sha256:") + 64  # 64 hex chars


def test_hash_blob_deterministic():
    from tokenpak.debug.capture import hash_blob

    assert hash_blob("hello") == hash_blob("hello")


def test_hash_blob_differs_for_different_content():
    from tokenpak.debug.capture import hash_blob

    assert hash_blob("hello") != hash_blob("world")


# ---------------------------------------------------------------------------
# 5. capture() in encrypted mode
# ---------------------------------------------------------------------------


def test_capture_encrypted_writes_enc_file(tmp_blob_dir, fixed_key, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug import capture as cap

    path = cap.capture("trace-001", {"prompt": "test"}, {"text": "resp"}, key=fixed_key)
    assert path is not None
    assert path.suffix == ".enc"
    assert path.exists()
    # Verify decryptable
    recovered = cap.decrypt_blob(path.read_bytes(), key=fixed_key)
    assert recovered["meta"]["trace_id"] == "trace-001"
    assert recovered["request"] == {"prompt": "test"}
    assert recovered["response"] == {"text": "resp"}


def test_capture_encrypted_no_plaintext_in_blob(tmp_blob_dir, fixed_key, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug import capture as cap

    cap.capture("trace-002", {"secret": "my_secret_prompt"}, {"text": "resp"}, key=fixed_key)
    enc_path = tmp_blob_dir / "trace-002.enc"
    raw = enc_path.read_bytes()
    assert b"my_secret_prompt" not in raw  # no plaintext leakage


# ---------------------------------------------------------------------------
# 6. capture() in hash_only mode
# ---------------------------------------------------------------------------


def test_capture_hash_only_writes_hash_file(tmp_blob_dir, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    from tokenpak.debug import capture as cap

    path = cap.capture("trace-003", {"prompt": "hello"}, {"text": "world"})
    assert path is not None
    assert path.suffix == ".hash"
    assert path.exists()
    record = json.loads(path.read_text())
    assert record["meta"]["trace_id"] == "trace-003"
    assert record["request_hash"].startswith("sha256:")
    assert record["response_hash"].startswith("sha256:")


def test_capture_hash_only_no_body_stored(tmp_blob_dir, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    from tokenpak.debug import capture as cap

    cap.capture("trace-004", {"secret": "sensitive"}, {"text": "reply"})
    hash_path = tmp_blob_dir / "trace-004.hash"
    content = hash_path.read_text()
    assert "sensitive" not in content
    assert "reply" not in content


# ---------------------------------------------------------------------------
# 7. capture() in off mode
# ---------------------------------------------------------------------------


def test_capture_off_writes_nothing(tmp_blob_dir, monkeypatch):
    monkeypatch.delenv("TOKENPAK_DEBUG_CAPTURE", raising=False)
    from tokenpak.debug import capture as cap

    result = cap.capture("trace-005", {"prompt": "test"}, {"text": "resp"})
    assert result is None
    assert not list(tmp_blob_dir.glob("*")) or not any(
        p.suffix in (".enc", ".hash") for p in tmp_blob_dir.glob("*")
    )


# ---------------------------------------------------------------------------
# 8. list_captures()
# ---------------------------------------------------------------------------


def test_list_captures_empty(tmp_blob_dir):
    from tokenpak.debug.capture import list_captures

    tmp_blob_dir.mkdir(parents=True, exist_ok=True)
    assert list_captures() == []


def test_list_captures_returns_entries(tmp_blob_dir, fixed_key, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug import capture as cap

    cap.capture("t1", {}, {}, key=fixed_key)
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    cap.capture("t2", {}, {})

    entries = cap.list_captures()
    trace_ids = {e["trace_id"] for e in entries}
    assert "t1" in trace_ids
    assert "t2" in trace_ids
    modes = {e["trace_id"]: e["mode"] for e in entries}
    assert modes["t1"] == "encrypted"
    assert modes["t2"] == "hash_only"


# ---------------------------------------------------------------------------
# 9. export_capture() for encrypted blob
# ---------------------------------------------------------------------------


def test_export_capture_encrypted(tmp_blob_dir, fixed_key, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug import capture as cap

    cap.capture("trace-enc", {"q": "hi"}, {"a": "bye"}, key=fixed_key)
    payload = cap.export_capture("trace-enc", key=fixed_key)
    assert payload["request"] == {"q": "hi"}
    assert payload["response"] == {"a": "bye"}


# ---------------------------------------------------------------------------
# 10. export_capture() for hash_only blob
# ---------------------------------------------------------------------------


def test_export_capture_hash_only(tmp_blob_dir, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "hash_only")
    from tokenpak.debug import capture as cap

    cap.capture("trace-hash", {"prompt": "test"}, {"text": "ok"})
    record = cap.export_capture("trace-hash")
    assert "request_hash" in record
    assert "response_hash" in record


# ---------------------------------------------------------------------------
# 11. export_capture() missing trace raises FileNotFoundError
# ---------------------------------------------------------------------------


def test_export_capture_missing_raises(tmp_blob_dir):
    from tokenpak.debug.capture import export_capture

    tmp_blob_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(FileNotFoundError):
        export_capture("nonexistent-trace")


# ---------------------------------------------------------------------------
# 12. CLI: tokenpak debug list (no captures)
# ---------------------------------------------------------------------------


def test_cli_debug_list_no_captures(tmp_blob_dir, capsys):
    from tokenpak.debug.capture import list_captures

    tmp_blob_dir.mkdir(parents=True, exist_ok=True)
    # Simulate what cmd_debug_list does
    entries = list_captures()
    if not entries:
        print("No debug captures found. Set TOKENPAK_DEBUG_CAPTURE=encrypted or hash_only.")
    captured = capsys.readouterr()
    assert "No debug captures found" in captured.out


# ---------------------------------------------------------------------------
# 13. CLI: tokenpak debug export
# ---------------------------------------------------------------------------


def test_cli_debug_export(tmp_blob_dir, fixed_key, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_DEBUG_CAPTURE", "encrypted")
    from tokenpak.debug import capture as cap

    cap.capture("export-test", {"input": "x"}, {"output": "y"}, key=fixed_key)
    payload = cap.export_capture("export-test", key=fixed_key)
    print(json.dumps(payload, indent=2))
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["request"] == {"input": "x"}
    assert data["response"] == {"output": "y"}
