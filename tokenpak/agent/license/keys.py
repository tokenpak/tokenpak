"""
TokenPak License Key Cryptography — RSA-based signing and verification.

Key format:  TPAK-XXXX-XXXX-XXXX
Underlying:  RSA-4096, SHA-256, base64url payload + signature
"""

from __future__ import annotations

import base64
import json
import secrets
import string
from dataclasses import asdict, dataclass
from typing import Optional

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

TPAK_PREFIX = "TPAK"
KEY_SEGMENT_CHARS = string.ascii_uppercase + string.digits
KEY_SEGMENT_LEN = 4
KEY_SEGMENTS = 3  # produces TPAK-XXXX-XXXX-XXXX


@dataclass
class LicensePayload:
    """The decoded payload embedded in a license."""

    key_id: str
    tier: str  # oss | pro | team | enterprise
    seats: int  # 0 = unlimited
    issued_at: str  # ISO-8601
    expires_at: Optional[str]  # ISO-8601 or None = perpetual
    features: list[str]
    customer_id: Optional[str] = None  # opaque customer hash (never plaintext PII)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LicensePayload":
        return cls(
            key_id=d["key_id"],
            tier=d["tier"],
            seats=d.get("seats", 0),
            issued_at=d["issued_at"],
            expires_at=d.get("expires_at"),
            features=d.get("features", []),
            customer_id=d.get("customer_id"),
        )


# ─────────────────────────────────────────────
# Keypair generation
# ─────────────────────────────────────────────


def generate_keypair() -> tuple[bytes, bytes]:
    """
    Generate a new RSA-2048 keypair.
    Returns (private_pem, public_pem) as bytes.
    Requires `cryptography` package.
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("pip install cryptography to use RSA license keys")

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
        backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ─────────────────────────────────────────────
# License key format helpers
# ─────────────────────────────────────────────


def _random_segment(length: int = KEY_SEGMENT_LEN) -> str:
    return "".join(secrets.choice(KEY_SEGMENT_CHARS) for _ in range(length))


def format_license_key() -> str:
    """
    Generate a fresh human-readable license key token:  TPAK-XXXX-XXXX-XXXX
    This is just the *identifier* part; the full license is encoded separately.
    """
    segments = [_random_segment() for _ in range(KEY_SEGMENTS)]
    return f"{TPAK_PREFIX}-" + "-".join(segments)


# ─────────────────────────────────────────────
# Sign / verify
# ─────────────────────────────────────────────


def sign_license(payload: LicensePayload, private_pem: bytes) -> str:
    """
    Sign a LicensePayload with the private key.
    Returns a compact token:  <b64url_payload>.<b64url_signature>
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("pip install cryptography to sign licenses")

    payload_bytes = json.dumps(payload.to_dict(), separators=(",", ":")).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()

    _raw_private_key = serialization.load_pem_private_key(
        private_pem, password=None, backend=default_backend()
    )
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from typing import cast as _cast
    private_key = _cast(RSAPrivateKey, _raw_private_key)
    signature = private_key.sign(
        payload_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{payload_b64}.{sig_b64}"


def verify_license(token: str, public_pem: bytes) -> LicensePayload:
    """
    Verify a signed license token.
    Returns decoded LicensePayload on success.
    Raises ValueError on tampered/invalid token.
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("pip install cryptography to verify licenses")

    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("Malformed license token")

    payload_b64, sig_b64 = parts

    # Re-pad base64url
    def _pad(s: str) -> str:
        return s + "=" * (4 - len(s) % 4) if len(s) % 4 else s

    try:
        payload_bytes = base64.urlsafe_b64decode(_pad(payload_b64))
        signature = base64.urlsafe_b64decode(_pad(sig_b64))
    except Exception as exc:
        raise ValueError(f"Invalid base64 in license: {exc}") from exc

    _raw_public_key = serialization.load_pem_public_key(public_pem, backend=default_backend())
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from typing import cast as _cast
    public_key = _cast(RSAPublicKey, _raw_public_key)
    try:
        public_key.verify(
            signature,
            payload_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise ValueError(f"License signature invalid: {exc}") from exc

    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"License payload not valid JSON: {exc}") from exc

    return LicensePayload.from_dict(data)
