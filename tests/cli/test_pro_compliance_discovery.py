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

    assert "tokenpak upgrade --print-url" in text
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


def test_cli_reference_labels_audit_and_compliance_as_surfaces():
    text = _read("docs/cli-reference.md")

    assert "Audit log surface (Pro/Enterprise)" in text
    assert "Compliance report surface (Pro/Enterprise)" in text


def test_compliance_stub_points_to_docs(capsys):
    parser = _cli_core.build_parser()
    args = parser.parse_args(["compliance"])

    assert args.func(args) is None
    out = capsys.readouterr().out
    assert "Pro/Enterprise" in out
    assert "docs/guides/enterprise/compliance-mapping.md" in out


def test_audit_stub_points_to_security_docs(capsys):
    parser = _cli_core.build_parser()
    args = parser.parse_args(["audit"])

    assert args.func(args) is None
    out = capsys.readouterr().out
    assert "Pro/Enterprise" in out
    assert "docs/guides/enterprise/security-architecture.md" in out


def test_registry_describes_pro_enterprise_report_surfaces():
    payload = json.loads(_read("tokenpak/core/registry/commands.json"))
    commands = {
        item["command"]: item
        for item in payload["commands"]
    }

    compliance = commands["compliance"]
    audit = commands["audit"]
    assert "Pro/Enterprise" in compliance["description"]
    assert "compliance-mapping.md" in compliance["detail"]
    assert "does not unlock compliance reports" in compliance["detail"]
    assert "Pro/Enterprise" in audit["description"]
    assert "security-architecture.md" in audit["detail"]
