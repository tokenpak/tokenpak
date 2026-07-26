# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from tokenpak import _cli_core

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _flat(path: str) -> str:
    return " ".join(_read(path).split())


def test_quickstart_surfaces_trust_paths_without_overclaiming():
    text = _read("docs/quickstart.md")
    flat = _flat("docs/quickstart.md")

    # No enrollment CTA: public Pro enrollment is not open, so quickstart
    # must not send readers to a command that opens nothing.
    assert "tokenpak upgrade" not in text
    assert "tokenpak.ai/pro" not in text
    assert "TokenPak Pro" in text
    assert "./multipak.md" in text
    assert "./guides/enterprise/security-architecture.md" in text
    assert "./guides/enterprise/compliance-mapping.md" in text
    assert "do not change the beta support model" in flat
    assert "hosted processing by the OSS package" in flat


def test_docs_index_has_trust_and_editions_section():
    text = _read("docs/README.md")

    assert "## Trust & Editions" in text
    assert "MultiPak / Pro boundary" in text
    assert "guides/enterprise/security-architecture.md" in text
    assert "guides/enterprise/compliance-mapping.md" in text
    assert "KNOWN_LIMITATIONS.md" in text


def test_cli_reference_documents_only_the_supported_surface():
    """The published reference is a promise that what it lists was verified.

    `audit` and `compliance` are classified outside the beta surface, so they
    are absent here — and reachable, with their reason, under `help --all`.
    """
    from tokenpak.core.registry import beta_surface

    text = _read("docs/cli-reference.md")

    for command in ("audit", "compliance"):
        assert not beta_surface.is_supported(command)
        assert f"### `tokenpak {command}`" not in text
        assert beta_surface.exclusion_reason(command)

    for command in ("preview", "status", "setup"):
        assert f"### `tokenpak {command}`" in text


def test_compliance_stub_points_to_docs(capsys):
    parser = _cli_core.build_parser()
    args = parser.parse_args(["compliance"])

    assert args.func(args) is None
    out = capsys.readouterr().out
    assert "(Pro)" in out
    assert "docs/guides/enterprise/compliance-mapping.md" in out


def test_audit_stub_points_to_security_docs(capsys):
    parser = _cli_core.build_parser()
    args = parser.parse_args(["audit"])

    assert args.func(args) is None
    out = capsys.readouterr().out
    assert "(Pro)" in out
    assert "docs/guides/enterprise/security-architecture.md" in out


def test_registry_describes_pro_report_surfaces():
    payload = json.loads(_read("tokenpak/core/registry/commands.json"))
    commands = {item["command"]: item for item in payload["commands"]}

    compliance = commands["compliance"]
    audit = commands["audit"]
    assert "(Pro)" in compliance["description"]
    assert "compliance-mapping.md" in compliance["detail"]
    assert "does not unlock compliance reports" in compliance["detail"]
    assert "(Pro)" in audit["description"]
    assert "security-architecture.md" in audit["detail"]

    # No user-facing string anywhere in the registry sells Team or Enterprise.
    for item in payload["commands"]:
        for field in ("description", "detail", "usage"):
            value = item.get(field, "").replace("guides/enterprise/", "")
            assert "Team" not in value, f"{item['command']}.{field} names Team as an offering"
            assert "Enterprise" not in value, (
                f"{item['command']}.{field} names Enterprise as an offering"
            )
