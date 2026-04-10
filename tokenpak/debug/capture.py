# SPDX-License-Identifier: MIT
"""tokenpak.debug.capture — Encrypted debug blob storage for regulated environments.

Modes (TOKENPAK_DEBUG_CAPTURE env var):
  off        — no capture (default)
  encrypted  — AES-256-GCM encrypted blobs in ~/.tokenpak/debug/<trace_id>.enc
  hash_only  — SHA-256 content hash, no plaintext, in ~/.tokenpak/debug/<trace_id>.hash

Key management (TOKENPAK_DEBUG_CAPTURE_KEY env var):
  - If set: use as hex-encoded 32-byte key
  - If unset: auto-generate and persist to ~/.tokenpak/debug/.key

Blob format (encrypted):
  [4-byte magic "TPKD"] [1-byte version=1] [12-byte nonce] [N-byte ciphertext+16-byte tag]
  Ciphertext decrypts to JSON: {"meta": {...}, "request": ..., "response": ...}

Hash-only format:
  JSON: {"meta": {...}, "request_hash": "sha256:...", "response_hash": "sha256:..."}
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import secrets
import struct
import time
from pathlib import Path
from typing import Any, Optional

_BLOB_DIR = Path.home() / ".tokenpak" / "debug"
_KEY_FILE = _BLOB_DIR / ".key"
_MAGIC = b"TPKD"
_VERSION = 1


class CaptureMode(enum.Enum):
    OFF = "off"
    ENCRYPTED = "encrypted"
    HASH_ONLY = "hash_only"


def get_capture_mode() -> CaptureMode:
    """Read TOKENPAK_DEBUG_CAPTURE env var and return the active CaptureMode."""
    val = os.environ.get("TOKENPAK_DEBUG_CAPTURE", "off").lower().replace("-", "_")
    try:
        return CaptureMode(val)
    except ValueError:
        return CaptureMode.OFF


# ── Key management ─────────────────────────────────────────────────────────────


def _load_or_create_key() -> bytes:
    """Return the 32-byte encryption key from env or auto-generated key file."""
    env_key = os.environ.get("TOKENPAK_DEBUG_CAPTURE_KEY", "")
    if env_key:
        raw = bytes.fromhex(env_key)
        if len(raw) != 32:
            raise ValueError(
                f"TOKENPAK_DEBUG_CAPTURE_KEY must be 64 hex chars (32 bytes), got {len(raw)} bytes"
            )
        return raw

    _BLOB_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        return bytes.fromhex(_KEY_FILE.read_text().strip())

    key = secrets.token_bytes(32)
    _KEY_FILE.write_text(key.hex())
    _KEY_FILE.chmod(0o600)
    return key


# ── Encryption ─────────────────────────────────────────────────────────────────


def encrypt_blob(payload: dict, key: Optional[bytes] = None) -> bytes:
    """Encrypt *payload* dict with AES-256-GCM. Returns raw bytes.

    Args:
        payload: dict to encrypt (will be JSON-serialised).
        key: 32-byte key. If None, uses _load_or_create_key().

    Returns:
        Bytes: magic(4) + version(1) + nonce(12) + ciphertext+tag
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if key is None:
        key = _load_or_create_key()
    if len(key) != 32:
        raise ValueError(f"Key must be 32 bytes, got {len(key)}")

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)  # includes 16-byte GCM tag

    header = _MAGIC + struct.pack("B", _VERSION) + nonce
    return header + ciphertext


def decrypt_blob(data: bytes, key: Optional[bytes] = None) -> dict:
    """Decrypt bytes produced by encrypt_blob(). Returns the original dict.

    Args:
        data: raw blob bytes.
        key: 32-byte key. If None, uses _load_or_create_key().

    Returns:
        The decrypted payload dict.

    Raises:
        ValueError: on bad magic, unsupported version, or decryption failure.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(data) < 4 + 1 + 12 + 16:
        raise ValueError("Blob too short to be valid")
    magic = data[:4]
    if magic != _MAGIC:
        raise ValueError(f"Bad magic bytes: {magic!r}")
    version = data[4]
    if version != _VERSION:
        raise ValueError(f"Unsupported blob version: {version}")
    nonce = data[5:17]
    ciphertext = data[17:]

    if key is None:
        key = _load_or_create_key()

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError(f"Decryption failed: {exc}") from exc
    return json.loads(plaintext.decode())


# ── Hashing ────────────────────────────────────────────────────────────────────


def hash_blob(content: Any) -> str:
    """Return 'sha256:<hex>' of the JSON-serialised content."""
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"sha256:{digest}"


# ── Storage ────────────────────────────────────────────────────────────────────


def _blob_path(trace_id: str, mode: CaptureMode) -> Path:
    ext = ".enc" if mode == CaptureMode.ENCRYPTED else ".hash"
    return _BLOB_DIR / f"{trace_id}{ext}"


def _build_meta(trace_id: str, meta: Optional[dict]) -> dict:
    base = {
        "trace_id": trace_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capture_mode": get_capture_mode().value,
    }
    if meta:
        base.update(meta)
    return base


def capture(
    trace_id: str,
    request: Any,
    response: Any,
    meta: Optional[dict] = None,
    key: Optional[bytes] = None,
) -> Optional[Path]:
    """Capture a request/response pair according to TOKENPAK_DEBUG_CAPTURE mode.

    Args:
        trace_id: Unique trace identifier (used as filename).
        request:  Request object/dict to capture.
        response: Response object/dict to capture.
        meta:     Optional metadata dict (model, provider, token counts, etc.).
        key:      Encryption key (encrypted mode only). None = auto.

    Returns:
        Path to written blob file, or None if mode is OFF.
    """
    mode = get_capture_mode()
    if mode == CaptureMode.OFF:
        return None

    _BLOB_DIR.mkdir(parents=True, exist_ok=True)
    built_meta = _build_meta(trace_id, meta)
    out_path = _blob_path(trace_id, mode)

    if mode == CaptureMode.ENCRYPTED:
        payload = {"meta": built_meta, "request": request, "response": response}
        blob = encrypt_blob(payload, key=key)
        out_path.write_bytes(blob)

    elif mode == CaptureMode.HASH_ONLY:
        record = {
            "meta": built_meta,
            "request_hash": hash_blob(request),
            "response_hash": hash_blob(response),
        }
        out_path.write_text(json.dumps(record, indent=2))

    return out_path


# ── CLI helpers ────────────────────────────────────────────────────────────────


def list_captures() -> list[dict]:
    """Return a sorted list of capture records found in the blob directory.

    Each entry: {"trace_id": str, "path": str, "mode": str, "mtime": str}
    """
    if not _BLOB_DIR.exists():
        return []
    entries = []
    for path in sorted(_BLOB_DIR.iterdir()):
        if path.suffix == ".enc":
            mode = "encrypted"
        elif path.suffix == ".hash":
            mode = "hash_only"
        else:
            continue
        trace_id = path.stem
        mtime = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)
        )
        entries.append({"trace_id": trace_id, "path": str(path), "mode": mode, "mtime": mtime})
    return entries


def export_capture(trace_id: str, key: Optional[bytes] = None) -> dict:
    """Decrypt and return the payload for *trace_id*.

    For hash_only blobs, returns the hash record as-is (no decryption needed).

    Raises:
        FileNotFoundError: if no blob exists for the given trace_id.
        ValueError: on decryption failure.
    """
    enc_path = _BLOB_DIR / f"{trace_id}.enc"
    hash_path = _BLOB_DIR / f"{trace_id}.hash"

    if enc_path.exists():
        return decrypt_blob(enc_path.read_bytes(), key=key)
    if hash_path.exists():
        return json.loads(hash_path.read_text())
    raise FileNotFoundError(f"No capture found for trace_id: {trace_id}")
