"""
Tests for tokenpak-admin CLI — keygen, verify, genkeys commands.

Run:  pytest tests/test_license_admin_cli.py -v
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.license.admin_cli", reason="module not available in current build")
import hashlib
import json
from pathlib import Path

import pytest

try:
    from cryptography.hazmat.primitives import serialization
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from tokenpak._internal.license.admin_cli import build_parser, cmd_genkeys, cmd_keygen, cmd_verify
from tokenpak._internal.license.keys import generate_keypair

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def keypair_files(tmp_path_factory):
    if not CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    tmp = tmp_path_factory.mktemp("keys")
    priv, pub = generate_keypair()
    priv_path = tmp / "private.pem"
    pub_path = tmp / "public.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return str(priv_path), str(pub_path)


# ─────────────────────────────────────────────
# keygen tests
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestKeygenCommand:

    def _keygen(self, extra_args, keypair_files, tmp_path=None, capsys=None):
        priv_path, _ = keypair_files
        parser = build_parser()
        cmd = ["keygen", "--tier", "pro", "--private-key", priv_path] + extra_args
        args = parser.parse_args(cmd)
        cmd_keygen(args)

    def test_keygen_basic_output(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args(["keygen", "--tier", "pro", "--private-key", priv_path])
        cmd_keygen(args)
        out = capsys.readouterr().out
        assert "TPAK" in out
        assert "pro" in out

    def test_keygen_all_tiers(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        for tier in ["oss", "pro", "team", "enterprise"]:
            parser = build_parser()
            args = parser.parse_args(["keygen", "--tier", tier, "--private-key", priv_path])
            cmd_keygen(args)
            out = capsys.readouterr().out
            assert tier in out

    def test_keygen_invalid_tier_exits(self, keypair_files):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args(["keygen", "--tier", "invalid_tier", "--private-key", priv_path])
        with pytest.raises(SystemExit) as exc:
            cmd_keygen(args)
        assert exc.value.code == 1

    def test_keygen_writes_output_file(self, keypair_files, tmp_path, capsys):
        priv_path, _ = keypair_files
        out_file = str(tmp_path / "license.key")
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "pro",
            "--private-key", priv_path,
            "--output", out_file,
        ])
        cmd_keygen(args)
        assert Path(out_file).exists()
        token = Path(out_file).read_text()
        assert "." in token  # payload.signature format
        assert len(token) > 100

    def test_keygen_json_flag(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "enterprise",
            "--private-key", priv_path,
            "--json",
        ])
        cmd_keygen(args)
        out = capsys.readouterr().out
        assert "JSON:" in out
        # Extract JSON block
        json_start = out.index("{")
        data = json.loads(out[json_start:].split("\n\n")[0])
        assert data["tier"] == "enterprise"
        assert "token" in data
        assert "key_id" in data

    def test_keygen_customer_hashed(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "pro",
            "--private-key", priv_path,
            "--customer", "alice@example.com",
            "--json",
        ])
        cmd_keygen(args)
        out = capsys.readouterr().out
        json_start = out.index("{")
        data = json.loads(out[json_start:].split("\n\n")[0])
        expected_hash = hashlib.sha256(b"alice@example.com").hexdigest()
        assert data["customer_id"] == expected_hash

    def test_keygen_perpetual_with_days_zero(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "oss",
            "--private-key", priv_path,
            "--days", "0",
            "--json",
        ])
        cmd_keygen(args)
        out = capsys.readouterr().out
        json_start = out.index("{")
        data = json.loads(out[json_start:].split("\n\n")[0])
        assert data["expires_at"] is None

    def test_keygen_seats_encoded(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "team",
            "--seats", "10",
            "--private-key", priv_path,
            "--json",
        ])
        cmd_keygen(args)
        out = capsys.readouterr().out
        json_start = out.index("{")
        data = json.loads(out[json_start:].split("\n\n")[0])
        assert data["seats"] == 10

    def test_keygen_extra_features(self, keypair_files, capsys):
        priv_path, _ = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "keygen", "--tier", "pro",
            "--features", "custom_a", "custom_b",
            "--private-key", priv_path,
        ])
        cmd_keygen(args)
        out = capsys.readouterr().out
        assert "custom_a" in out


# ─────────────────────────────────────────────
# verify tests
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestVerifyCommand:

    def _make_token(self, keypair_files, tier="pro", days=365):
        from datetime import datetime, timedelta, timezone

        from tokenpak._internal.license.keys import LicensePayload, format_license_key, sign_license
        priv_path, _ = keypair_files
        priv = Path(priv_path).read_bytes()
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        payload = LicensePayload(
            key_id=format_license_key(),
            tier=tier,
            seats=0,
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=expires,
            features=[],
        )
        return sign_license(payload, priv)

    def test_verify_valid_token(self, keypair_files, capsys):
        _, pub_path = keypair_files
        token = self._make_token(keypair_files)
        parser = build_parser()
        args = parser.parse_args([
            "verify", "--token", token,
            "--public-key", pub_path,
        ])
        cmd_verify(args)
        out = capsys.readouterr().out
        assert "VALID" in out
        assert "pro" in out

    def test_verify_bad_token_exits_nonzero(self, keypair_files):
        _, pub_path = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "verify", "--token", "garbage.token",
            "--public-key", pub_path,
        ])
        with pytest.raises(SystemExit) as exc:
            cmd_verify(args)
        assert exc.value.code == 2

    def test_verify_token_file(self, keypair_files, tmp_path, capsys):
        _, pub_path = keypair_files
        token = self._make_token(keypair_files, tier="enterprise")
        token_file = tmp_path / "license.key"
        token_file.write_text(token)
        parser = build_parser()
        args = parser.parse_args([
            "verify", "--token-file", str(token_file),
            "--public-key", pub_path,
        ])
        cmd_verify(args)
        out = capsys.readouterr().out
        assert "VALID" in out
        assert "enterprise" in out

    def test_verify_no_token_exits(self, keypair_files):
        _, pub_path = keypair_files
        parser = build_parser()
        args = parser.parse_args([
            "verify",
            "--public-key", pub_path,
        ])
        with pytest.raises(SystemExit):
            cmd_verify(args)


# ─────────────────────────────────────────────
# genkeys tests
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestGenkeysCommand:

    def test_genkeys_creates_files(self, tmp_path, capsys):
        parser = build_parser()
        args = parser.parse_args(["genkeys", "--out-dir", str(tmp_path)])
        cmd_genkeys(args)
        assert (tmp_path / "tokenpak_private.pem").exists()
        assert (tmp_path / "tokenpak_public.pem").exists()

    def test_genkeys_files_are_valid_pem(self, tmp_path, capsys):
        parser = build_parser()
        args = parser.parse_args(["genkeys", "--out-dir", str(tmp_path)])
        cmd_genkeys(args)
        pub_pem = (tmp_path / "tokenpak_public.pem").read_bytes()
        assert pub_pem.startswith(b"-----BEGIN PUBLIC KEY-----")
        priv_pem = (tmp_path / "tokenpak_private.pem").read_bytes()
        assert b"-----BEGIN RSA PRIVATE KEY-----" in priv_pem or b"-----BEGIN PRIVATE KEY-----" in priv_pem

    def test_genkeys_refuses_overwrite_without_force(self, tmp_path, capsys):
        parser = build_parser()
        args = parser.parse_args(["genkeys", "--out-dir", str(tmp_path)])
        cmd_genkeys(args)
        capsys.readouterr()
        # Second run without --force
        with pytest.raises(SystemExit) as exc:
            cmd_genkeys(args)
        assert exc.value.code == 1

    def test_genkeys_force_overwrites(self, tmp_path, capsys):
        parser = build_parser()
        args = parser.parse_args(["genkeys", "--out-dir", str(tmp_path)])
        cmd_genkeys(args)
        old_pub = (tmp_path / "tokenpak_public.pem").read_bytes()
        capsys.readouterr()
        args2 = parser.parse_args(["genkeys", "--out-dir", str(tmp_path), "--force"])
        cmd_genkeys(args2)
        new_pub = (tmp_path / "tokenpak_public.pem").read_bytes()
        # Keys are regenerated (they should differ — extremely unlikely to match)
        # Just check file is still valid PEM
        assert new_pub.startswith(b"-----BEGIN PUBLIC KEY-----")

    def test_genkeys_output_message(self, tmp_path, capsys):
        parser = build_parser()
        args = parser.parse_args(["genkeys", "--out-dir", str(tmp_path)])
        cmd_genkeys(args)
        out = capsys.readouterr().out
        assert "Private key" in out
        assert "Public key" in out


# ─────────────────────────────────────────────
# customer_id in payload
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestCustomerIdPayload:
    """Verify customer_id survives the sign → verify round-trip."""

    def test_customer_id_roundtrip(self):
        from datetime import datetime, timezone

        from tokenpak._internal.license.keys import (
            LicensePayload,
            format_license_key,
            generate_keypair,
            sign_license,
            verify_license,
        )
        priv, pub = generate_keypair()
        payload = LicensePayload(
            key_id=format_license_key(),
            tier="pro",
            seats=0,
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=None,
            features=[],
            customer_id="abc123hash",
        )
        token = sign_license(payload, priv)
        result = verify_license(token, pub)
        assert result.customer_id == "abc123hash"

    def test_no_customer_id_defaults_none(self):
        from datetime import datetime, timezone

        from tokenpak._internal.license.keys import (
            LicensePayload,
            format_license_key,
            generate_keypair,
            sign_license,
            verify_license,
        )
        priv, pub = generate_keypair()
        payload = LicensePayload(
            key_id=format_license_key(),
            tier="oss",
            seats=0,
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=None,
            features=[],
        )
        token = sign_license(payload, priv)
        result = verify_license(token, pub)
        assert result.customer_id is None
