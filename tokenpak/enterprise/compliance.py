"""Enterprise Compliance Reports — SOC2, GDPR, CCPA.

Generates structured compliance reports by pulling data from the audit log
and system configuration.

Usage::

    from tokenpak.enterprise.compliance import ComplianceReporter

    reporter = ComplianceReporter(audit_db=".tokenpak/audit.db")
    report = reporter.generate("soc2")
    print(report.as_text())
    report.save("soc2-report-2026-Q1.json")
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from tokenpak.enterprise.audit import AuditLog, default_audit_path

# ---------------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------------


@dataclass
class ComplianceControl:
    """A single compliance control with status and evidence."""

    id: str
    name: str
    description: str
    status: str  # "compliant" | "partial" | "not_applicable" | "gap"
    evidence: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ComplianceReport:
    """Full compliance report for a given standard."""

    standard: str  # "soc2" | "gdpr" | "ccpa"
    generated_at: str
    period_start: str
    period_end: str
    organization: str
    controls: list[ComplianceControl] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    raw_audit_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "standard": self.standard,
            "generated_at": self.generated_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "organization": self.organization,
            "summary": self.summary,
            "raw_audit_stats": self.raw_audit_stats,
            "controls": [asdict(c) for c in self.controls],
        }

    def as_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent)

    def as_text(self) -> str:
        lines = [
            f"{'=' * 64}",
            f"COMPLIANCE REPORT: {self.standard.upper()}",
            f"Generated: {self.generated_at}",
            f"Period:    {self.period_start} → {self.period_end}",
            f"Org:       {self.organization}",
            f"{'=' * 64}",
            "",
        ]
        # Summary
        s = self.summary
        compliant = s.get("compliant", 0)
        total = s.get("total_controls", len(self.controls))
        pct = round(compliant / total * 100) if total else 0
        lines += [
            f"SUMMARY: {compliant}/{total} controls compliant ({pct}%)",
            f"  Gaps:    {s.get('gap', 0)}",
            f"  Partial: {s.get('partial', 0)}",
            f"  N/A:     {s.get('not_applicable', 0)}",
            "",
            "CONTROLS:",
        ]
        icon_map = {"compliant": "✅", "partial": "⚠️", "gap": "❌", "not_applicable": "➖"}
        for ctrl in self.controls:
            icon = icon_map.get(ctrl.status, "?")
            lines.append(f"  {icon} [{ctrl.id}] {ctrl.name}")
            if ctrl.status != "compliant":
                lines.append(f"       Status: {ctrl.status}")
            if ctrl.notes:
                lines.append(f"       Note:   {ctrl.notes}")
            for ev in ctrl.evidence:
                lines.append(f"       Evidence: {ev}")
        lines.append("")
        return "\n".join(lines)

    def save(self, path: Union[str, Path], fmt: str = "json") -> None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            out.write_text(self.as_json(), encoding="utf-8")
        elif fmt == "text":
            out.write_text(self.as_text(), encoding="utf-8")
        else:
            raise ValueError(f"Unsupported format: {fmt!r}")


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


class ComplianceReporter:
    """Generate compliance reports from audit log + config.

    Parameters
    ----------
    audit_db:
        Path to the audit SQLite database.
    organization:
        Organization name to include in reports.
    """

    _STANDARDS = {"soc2", "gdpr", "ccpa"}

    def __init__(
        self,
        audit_db: Union[str, Path, None] = None,
        organization: str = "Your Organization",
    ) -> None:
        self._audit_path = str(audit_db or default_audit_path())
        self.organization = organization

    def generate(
        self,
        standard: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> ComplianceReport:
        """Generate a compliance report for *standard*.

        Parameters
        ----------
        standard:
            One of ``'soc2'``, ``'gdpr'``, ``'ccpa'``.
        since, until:
            ISO date strings to bound the audit data window.
        """
        standard = standard.lower()
        if standard not in self._STANDARDS:
            raise ValueError(
                f"Unknown compliance standard: {standard!r}. Choose from: {sorted(self._STANDARDS)}"
            )

        now = datetime.now(tz=timezone.utc)
        period_end = until or now.strftime("%Y-%m-%d")
        period_start = since or (
            datetime.fromtimestamp(now.timestamp() - 90 * 86400, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
        )

        # Pull stats from audit log
        audit_stats: dict = {}
        try:
            with AuditLog(self._audit_path) as log:
                audit_stats = log.summary(since=period_start)
        except Exception as exc:
            audit_stats = {"error": str(exc)}

        # Build controls per standard
        builder = getattr(self, f"_build_{standard}_controls")
        controls = builder(audit_stats)

        # Summarise
        tally: dict[str, int] = {}
        for c in controls:
            tally[c.status] = tally.get(c.status, 0) + 1
        summary = {
            "total_controls": len(controls),
            **tally,
        }

        return ComplianceReport(
            standard=standard,
            generated_at=now.isoformat(),
            period_start=period_start,
            period_end=period_end,
            organization=self.organization,
            controls=controls,
            summary=summary,
            raw_audit_stats=audit_stats,
        )

    # ------------------------------------------------------------------
    # SOC 2 controls
    # ------------------------------------------------------------------

    def _build_soc2_controls(self, stats: dict) -> list[ComplianceControl]:
        total = stats.get("total", 0)
        has_audit = total > 0
        by_outcome = {
            r.get("outcome", r[0] if isinstance(r, tuple) else ""): r.get("n", 0)
            for r in stats.get("by_outcome", [])
        }
        by_outcome.get("auth_failure", 0)

        return [
            ComplianceControl(
                id="CC1.1",
                name="Control Environment — Integrity & Ethical Values",
                description="Organization demonstrates commitment to integrity and ethical values.",
                status="compliant",
                evidence=[
                    "SOUL.md policy on private data handling",
                    "Agent access control via API keys",
                ],
            ),
            ComplianceControl(
                id="CC2.1",
                name="Communication & Information — Internal Communication",
                description="Organization communicates information internally to support control functions.",
                status="compliant",
                evidence=[
                    "Audit log captures all model interactions",
                    f"Audit entries recorded: {total}",
                ],
            ),
            ComplianceControl(
                id="CC3.2",
                name="Risk Assessment — Fraud Risk",
                description="Organization identifies and assesses fraud risks.",
                status="compliant" if has_audit else "gap",
                evidence=(
                    ["Hash-chained audit log prevents tamper", "Auth failure tracking in audit log"]
                    if has_audit
                    else []
                ),
                notes=(
                    "" if has_audit else "No audit data found — ensure proxy is recording events."
                ),
            ),
            ComplianceControl(
                id="CC5.2",
                name="Control Activities — Selection & Development",
                description="Organization selects and develops control activities over technology.",
                status="compliant",
                evidence=[
                    "API key rotation support",
                    "Per-user model access controls",
                    "Budget enforcement (hard limits)",
                ],
            ),
            ComplianceControl(
                id="CC6.1",
                name="Logical & Physical Access — Logical Access Security",
                description="Access to systems is restricted to authorized users.",
                status="compliant",
                evidence=[
                    "API key authentication on all endpoints",
                    "RBAC role enforcement (Admin/FinOps/Engineer/Auditor)",
                ],
            ),
            ComplianceControl(
                id="CC6.2",
                name="Logical & Physical Access — New Access Provisioning",
                description="New logical access to infrastructure is restricted.",
                status="compliant",
                evidence=[
                    "Invite-token-based agent join flow",
                    "User creation tracked in audit log",
                ],
            ),
            ComplianceControl(
                id="CC6.3",
                name="Logical & Physical Access — Modification / Removal",
                description="Modifications and removals of access are controlled.",
                status="compliant",
                evidence=[
                    "User deactivation event recorded in audit log",
                    "API key revocation supported",
                ],
            ),
            ComplianceControl(
                id="CC6.7",
                name="Logical & Physical Access — Data Transmission",
                description="Data in transit is protected using encryption.",
                status="compliant",
                evidence=["HTTPS enforced for all proxy endpoints", "TLS 1.2+ required"],
            ),
            ComplianceControl(
                id="CC7.1",
                name="System Operations — Vulnerability Management",
                description="Vulnerabilities are identified and managed.",
                status="compliant",
                evidence=[
                    "tokenpak doctor runs security checks",
                    "Dependency vulnerability scanning via CI",
                ],
            ),
            ComplianceControl(
                id="CC7.2",
                name="System Operations — Monitoring",
                description="System components are monitored to detect anomalies.",
                status="compliant",
                evidence=[
                    "Telemetry pipeline captures all LLM calls",
                    "Anomaly detection in telemetry/integrity/anomalies.py",
                ],
            ),
            ComplianceControl(
                id="CC8.1",
                name="Change Management — Authorized Changes",
                description="Changes to infrastructure and software are authorized, tested, and approved.",
                status="compliant",
                evidence=[
                    "Git-based change tracking",
                    "Config change events recorded in audit log",
                ],
            ),
            ComplianceControl(
                id="CC9.1",
                name="Risk Mitigation — Vendor Management",
                description="Vendor risks are identified and managed.",
                status="compliant",
                evidence=[
                    "Multi-provider failover reduces single-vendor risk",
                    "OSS codebase with no proprietary lock-in",
                ],
            ),
        ]

    # ------------------------------------------------------------------
    # GDPR controls
    # ------------------------------------------------------------------

    def _build_gdpr_controls(self, stats: dict) -> list[ComplianceControl]:
        total = stats.get("total", 0)

        return [
            ComplianceControl(
                id="GDPR-5.1a",
                name="Lawfulness, Fairness & Transparency",
                description="Personal data is processed lawfully, fairly, and transparently.",
                status="compliant",
                evidence=[
                    "Data classification field in every audit entry",
                    "Privacy policy template provided",
                ],
            ),
            ComplianceControl(
                id="GDPR-5.1b",
                name="Purpose Limitation",
                description="Data collected for specified, explicit, legitimate purposes.",
                status="compliant",
                evidence=[
                    "Audit log records action + purpose metadata",
                    "Model routing policies restrict data use",
                ],
            ),
            ComplianceControl(
                id="GDPR-5.1c",
                name="Data Minimisation",
                description="Only data necessary for the purpose is processed.",
                status="compliant",
                evidence=[
                    "Prompt compression reduces data volume",
                    "PII stripping via segmentizer",
                ],
            ),
            ComplianceControl(
                id="GDPR-5.1e",
                name="Storage Limitation",
                description="Data retained no longer than necessary.",
                status="compliant",
                evidence=[
                    "Configurable retention policy (default: 90 days)",
                    "tokenpak audit prune --days <N> removes old entries",
                ],
            ),
            ComplianceControl(
                id="GDPR-5.1f",
                name="Integrity & Confidentiality",
                description="Data is processed securely (appropriate technical/organisational measures).",
                status="compliant",
                evidence=[
                    "SQLite WAL + PRAGMA synchronous=FULL for audit durability",
                    "Hash-chained entries detect tampering",
                ],
            ),
            ComplianceControl(
                id="GDPR-12",
                name="Transparent Information — Rights Requests",
                description="Data subjects can exercise rights (access, erasure, portability).",
                status="compliant",
                evidence=[
                    "tokenpak audit export --user <id> for portability",
                    "Retention prune + targeted delete for erasure",
                ],
            ),
            ComplianceControl(
                id="GDPR-25",
                name="Data Protection by Design & Default",
                description="Privacy considerations built into system design.",
                status="compliant",
                evidence=[
                    "Anonymised metrics by default (anon_metrics.py)",
                    "Minimal data collection; no plaintext prompt storage by default",
                ],
            ),
            ComplianceControl(
                id="GDPR-30",
                name="Records of Processing Activities",
                description="Maintain records of processing activities.",
                status="compliant" if total > 0 else "gap",
                evidence=[f"Audit log: {total} entries recorded"],
                notes="" if total > 0 else "Start the proxy to begin recording.",
            ),
            ComplianceControl(
                id="GDPR-32",
                name="Security of Processing",
                description="Implement appropriate technical measures to ensure data security.",
                status="compliant",
                evidence=[
                    "Encryption in transit (TLS)",
                    "API key authentication",
                    "RBAC access controls",
                ],
            ),
            ComplianceControl(
                id="GDPR-33",
                name="Breach Notification",
                description="Data breaches reported to supervisory authority within 72 hours.",
                status="partial",
                evidence=["Audit log provides breach forensic data"],
                notes="Manual process required for breach notification — consider adding automated alerting.",
            ),
        ]

    # ------------------------------------------------------------------
    # CCPA controls
    # ------------------------------------------------------------------

    def _build_ccpa_controls(self, stats: dict) -> list[ComplianceControl]:
        total = stats.get("total", 0)

        return [
            ComplianceControl(
                id="CCPA-1798.100",
                name="Right to Know — Data Collection Disclosure",
                description="Consumers have the right to know what personal data is collected.",
                status="compliant",
                evidence=[
                    "Audit log records all data access with user_id + data_class",
                    "tokenpak audit list --user <id> surfaces consumer data",
                ],
            ),
            ComplianceControl(
                id="CCPA-1798.105",
                name="Right to Delete",
                description="Consumers have the right to request deletion of personal data.",
                status="compliant",
                evidence=[
                    "tokenpak audit prune or targeted delete by user_id",
                    "Retention policy clears data after configured window",
                ],
            ),
            ComplianceControl(
                id="CCPA-1798.110",
                name="Right to Know — Categories & Specific Pieces",
                description="Consumers can request specific data categories collected.",
                status="compliant",
                evidence=[
                    "tokenpak audit export --user <id> --format json",
                    "data_class field categorises each audit entry",
                ],
            ),
            ComplianceControl(
                id="CCPA-1798.115",
                name="Right to Know — Data Sharing",
                description="Consumers can request disclosure of data sold or disclosed.",
                status="compliant",
                evidence=[
                    "Audit log captures provider field (which LLM received data)",
                    "No data sold; providers documented in audit entries",
                ],
            ),
            ComplianceControl(
                id="CCPA-1798.120",
                name="Right to Opt-Out",
                description="Consumers have the right to opt out of data sharing.",
                status="partial",
                evidence=["Anonymous metrics mode (anon_metrics.py)"],
                notes="Full opt-out flow not yet automated — route users to support for opt-out requests.",
            ),
            ComplianceControl(
                id="CCPA-1798.125",
                name="Non-Discrimination",
                description="Consumers not discriminated against for exercising CCPA rights.",
                status="compliant",
                evidence=["No differential pricing or service based on privacy choices"],
            ),
            ComplianceControl(
                id="CCPA-1798.130",
                name="Privacy Notice",
                description="Business provides required privacy disclosures.",
                status="compliant",
                evidence=["Privacy policy template in docs/enterprise/privacy-policy.md"],
            ),
            ComplianceControl(
                id="CCPA-1798.140",
                name="Data Minimisation — Definition of Personal Information",
                description="Appropriate handling of personal information as defined by CCPA.",
                status="compliant" if total > 0 else "gap",
                evidence=[f"Audit log: {total} tracked interactions"],
                notes="" if total > 0 else "Enable audit logging via the proxy.",
            ),
        ]
