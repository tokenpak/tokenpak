# SPDX-License-Identifier: Apache-2.0
"""
Encrypted debug capture for regulated environments.

Usage (via environment variables):
    TOKENPAK_DEBUG_CAPTURE=off        — disabled (default)
    TOKENPAK_DEBUG_CAPTURE=encrypted  — AES-256-GCM encrypted blobs
    TOKENPAK_DEBUG_CAPTURE=hash_only  — SHA-256 hashes, no plaintext stored

    TOKENPAK_DEBUG_CAPTURE_KEY=<hex>  — 32-byte key (64 hex chars); auto-generated if absent

Blob storage:
    ~/.tokenpak/debug/<trace_id>.enc   — encrypted blobs
    ~/.tokenpak/debug/<trace_id>.hash  — hash-only records

Blob wire format (encrypted):
    [4 bytes magic "TPKD"][1 byte version 0x01][12 bytes nonce][16 bytes GCM tag][ciphertext]
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

_BLOB_DIR = Path.home() / ".tokenpak" / "debug"
_KEY_FILE = _BLOB_DIR / ".key"

_MAGIC = b"TPKD"
_VERSION = b"\x01"
_HEADER_LEN = 5   # magic(4) + version(1)
_NONCE_LEN = 12
_TAG_LEN = 16
_MIN_BLOB_LEN = _HEADER_LEN + _NONCE_LEN + _TAG_LEN  # 33


# ── CaptureMode ───────────────────────────────────────────────────────────────


class CaptureMode(enum.Enum):
    OFF = "off"
    ENCRYPTED = "encrypted"
    HASH_ONLY = "hash_only"

    @classmethod
    def from_env(cls) -> "CaptureMode":
        raw = os.environ.get("TOKENPAK_DEBUG_CAPTURE", "off").lower().strip()
        _map = {
            "off": cls.OFF,
            "encrypted": cls.ENCRYPTED,
            "hash_only": cls.HASH_ONLY,
        }
        return _map.get(raw, cls.OFF)


def get_capture_mode() -> CaptureMode:
    """Return the current capture mode from the environment."""
    return CaptureMode.from_env()


# ── Key management ────────────────────────────────────────────────────────────


def _load_or_generate_key() -> bytes:
    """Return 32-byte AES key from env var, key file, or auto-generate + persist."""
    env_key = os.environ.get("TOKENPAK_DEBUG_CAPTURE_KEY", "").strip()
    if env_key:
        raw = bytes.fromhex(env_key)
        if len(raw) != 32:
            raise ValueError(
                "TOKENPAK_DEBUG_CAPTURE_KEY must be exactly 64 hex chars (32 bytes)"
            )
        return raw

    _BLOB_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        raw = bytes.fromhex(_KEY_FILE.read_text().strip())
        if len(raw) != 32:
            raise ValueError(f"Corrupt key file at {_KEY_FILE}; delete and retry")
        return raw

    # Auto-generate
    new_key = secrets.token_bytes(32)
    _KEY_FILE.write_text(new_key.hex())
    _KEY_FILE.chmod(0o600)
    return new_key


# ── AES-256-GCM encrypt / decrypt ─────────────────────────────────────────────


def _to_bytes(value: Any) -> bytes:
    """Serialize *value* to bytes for encryption/hashing."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return json.dumps(value).encode()


def encrypt_blob(plaintext: Any, key: bytes | None = None) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM.

    The blob wire format is:
        [4 bytes magic "TPKD"][1 byte version 0x01][12 bytes nonce][16 bytes GCM tag][ciphertext]

    Args:
        plaintext: Data to encrypt.  Dicts/lists are JSON-serialised; strings
                   are UTF-8 encoded; bytes are passed through unchanged.
        key: 32-byte AES key.  Loaded from env / key-file if *None*.

    Returns:
        Encrypted bytes with magic header.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if key is None:
        key = _load_or_generate_key()
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes")

    data = _to_bytes(plaintext)
    nonce = secrets.token_bytes(_NONCE_LEN)
    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext || tag (16 bytes)
    ct_with_tag = aesgcm.encrypt(nonce, data, None)
    tag = ct_with_tag[-_TAG_LEN:]
    ciphertext = ct_with_tag[:-_TAG_LEN]
    return _MAGIC + _VERSION + nonce + tag + ciphertext


def decrypt_blob(blob: bytes, key: bytes | None = None) -> Any:
    """Decrypt a blob produced by :func:`encrypt_blob`.

    Args:
        blob: Encrypted bytes with magic header.
        key: 32-byte AES key.  Loaded from env / key-file if *None*.

    Returns:
        Decrypted value — a dict/list if the payload was JSON, otherwise raw bytes.

    Raises:
        ValueError: On bad magic, blob too short, or decryption failure.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < _HEADER_LEN or blob[:4] != _MAGIC:
        raise ValueError(
            f"Bad magic: expected {_MAGIC!r}, got {blob[:4]!r}"
        )
    if len(blob) < _MIN_BLOB_LEN:
        raise ValueError(
            f"Blob too short to be a valid encrypted record "
            f"(got {len(blob)} bytes, need >= {_MIN_BLOB_LEN})"
        )

    if key is None:
        key = _load_or_generate_key()

    nonce = blob[_HEADER_LEN : _HEADER_LEN + _NONCE_LEN]
    tag = blob[_HEADER_LEN + _NONCE_LEN : _HEADER_LEN + _NONCE_LEN + _TAG_LEN]
    ciphertext = blob[_HEADER_LEN + _NONCE_LEN + _TAG_LEN :]

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
    except InvalidTag as exc:
        raise ValueError("Decryption failed: authentication tag mismatch") from exc

    try:
        return json.loads(plaintext.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return plaintext


# ── Hash-only mode ────────────────────────────────────────────────────────────


def hash_blob(content: Any) -> str:
    """Return ``sha256:<hex>`` of *content* without storing the body.

    Args:
        content: Bytes, string, or JSON-serialisable value to hash.

    Returns:
        String of the form ``sha256:<64-hex-chars>``.
    """
    data = _to_bytes(content)
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


# ── Blob serialisation ────────────────────────────────────────────────────────


def _build_record(
    trace_id: str,
    request: dict[str, Any],
    response: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    mode: CaptureMode = CaptureMode.ENCRYPTED,
) -> dict[str, Any]:
    """Build a capture record dict (plaintext JSON before encryption)."""
    meta: dict[str, Any] = {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode.value,
    }
    if metadata:
        meta.update(metadata)

    if mode == CaptureMode.HASH_ONLY:
        req_body = json.dumps(request, default=str)
        resp_body = json.dumps(response, default=str)
        return {
            "meta": meta,
            "request_hash": hash_blob(req_body),
            "response_hash": hash_blob(resp_body),
        }
    else:
        return {
            "meta": meta,
            "request": request,
            "response": response,
        }


# ── Public capture API ────────────────────────────────────────────────────────


def capture(
    trace_id: str,
    request: dict[str, Any],
    response: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    mode: CaptureMode | None = None,
    key: bytes | None = None,
) -> Path | None:
    """Capture a request/response pair according to *mode*.

    Does nothing when mode is OFF.  Writes to ``_BLOB_DIR``.

    Args:
        trace_id: Unique identifier for this trace (used as filename).
        request: Request dict (will be JSON-serialised).
        response: Response dict (will be JSON-serialised).
        metadata: Optional extra fields merged into blob header.
        mode: Override capture mode; reads ``TOKENPAK_DEBUG_CAPTURE`` env if *None*.
        key: Override the AES key used for encrypted mode.

    Returns:
        Path to the written file, or *None* if capture is disabled.
    """
    if mode is None:
        mode = CaptureMode.from_env()
    if mode == CaptureMode.OFF:
        return None

    _BLOB_DIR.mkdir(parents=True, exist_ok=True)
    record = _build_record(trace_id, request, response, metadata, mode)

    if mode == CaptureMode.HASH_ONLY:
        dest = _BLOB_DIR / f"{trace_id}.hash"
        dest.write_text(json.dumps(record, indent=2))
        return dest

    # ENCRYPTED mode
    blob = encrypt_blob(record, key=key)
    dest = _BLOB_DIR / f"{trace_id}.enc"
    dest.write_bytes(blob)
    return dest


# ── List / export ─────────────────────────────────────────────────────────────


def list_captures(debug_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return list of capture metadata dicts sorted by filename.

    Each dict has keys: ``trace_id``, ``path``, ``mode``, ``size_bytes``.
    For hash-only captures, ``timestamp`` is also included (parsed from the
    JSON header without decrypting encrypted blobs).
    """
    base = debug_dir or _BLOB_DIR
    if not base.exists():
        return []

    results: list[dict[str, Any]] = []
    for p in sorted(base.iterdir()):
        if p.suffix not in (".enc", ".hash"):
            continue
        trace_id = p.stem
        mode = "encrypted" if p.suffix == ".enc" else "hash_only"
        entry: dict[str, Any] = {
            "trace_id": trace_id,
            "path": str(p),
            "mode": mode,
            "size_bytes": p.stat().st_size,
        }
        if p.suffix == ".hash":
            try:
                data = json.loads(p.read_text())
                meta = data.get("meta", data)
                entry["timestamp"] = meta.get("timestamp", "")
            except Exception:
                entry["timestamp"] = ""
        results.append(entry)
    return results


def export_capture(
    trace_id: str,
    debug_dir: Path | None = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    """Decrypt and return the capture record for *trace_id*.

    For hash-only captures, returns the stored metadata (no decryption needed).

    Args:
        trace_id: The trace ID to retrieve.
        debug_dir: Override the default debug directory.
        key: Override the decryption key.

    Returns:
        Parsed JSON record as a dict.

    Raises:
        FileNotFoundError: If no capture exists for *trace_id*.
        ValueError: On decryption failure.
    """
    base = debug_dir or _BLOB_DIR
    enc_path = base / f"{trace_id}.enc"
    hash_path = base / f"{trace_id}.hash"

    if hash_path.exists():
        return json.loads(hash_path.read_text())

    if enc_path.exists():
        blob = enc_path.read_bytes()
        return decrypt_blob(blob, key=key)

    raise FileNotFoundError(
        f"No capture found for trace_id={trace_id!r} in {base}"
    )
