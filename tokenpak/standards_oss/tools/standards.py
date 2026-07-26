#!/usr/bin/env python3
"""Baseline Standards validator — one tool, four functions.

    validate          schema + cross-file semantic validation
    compile           resolve the effective profile, every field explicit
    generate          write the generated mode tables
    check-generated   fail if generated output has drifted from its sources

Scope bound (deliberate): this is one reviewable script. Growth beyond it is a scope flag requiring
a recorded decision, not drift.

What this tool does NOT do: enforce anything at runtime. It checks that your declarations are
internally consistent. See GOVERNANCE.md section 7 — this corpus provides `declared` and
`validated` only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

try:
    import jsonschema  # type: ignore

    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "controls" / "GENERATED-mode-tables.md"

# Tightening lattices: index 0 is loosest. Moving right is tightening (always allowed).
LATTICE = {
    "authorizer": ["none", "delegate", "reviewer", "operator"],
    "authorization_type": ["none-required", "standing-envelope", "explicit"],
    "independence_requirement": ["none", "separate-actor", "independent"],
    "authorization_timing": ["after-within-sla", "before", "before-and-revalidated"],
}

CANONICAL_FIELDS = [
    "executor",
    "authorizer",
    "authorization_type",
    "authorization_timing",
    "independence_requirement",
    "scope_or_envelope",
    "expiration",
    "required_evidence",
    "reversibility_class",
    "fallback",
    "mode_override_allowed",
]

FIELD_DEFAULTS = {
    "authorization_timing": "before",
    "scope_or_envelope": "unbounded",
    "expiration": "none",
    "required_evidence": [],
    "fallback": "stop-and-escalate",
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.decisions: list[tuple[str, str, str]] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def decision(self, missing: str, why: str, who: str) -> None:
        """An absent answer, surfaced for the operator.

        Never accompanied by a suggested value where the choice carries legal, financial, or
        ownership consequence (GOVERNANCE.md R48). Reported as absent, never as a default in
        force (R49), and non-blocking unless it gates a protected action (R50).
        """
        self.decisions.append((missing, why, who))

    def emit(self) -> int:
        for n in self.notes:
            print(f"note:    {n}")
        for w in self.warnings:
            print(f"WARNING: {w}")
        for e in self.errors:
            print(f"ERROR:   {e}")

        if self.decisions:
            print(
                "\nOPEN DECISIONS — these are yours to make; nothing here has been decided for you:"
            )
            for missing, why, who in self.decisions:
                print(f"  · {missing}")
                print(f"      why it matters: {why}")
                print(f"      decided by:     {who}")

        print(
            f"\n{len(self.errors)} error(s), {len(self.warnings)} warning(s), "
            f"{len(self.decisions)} open decision(s). {'FAIL' if self.errors else 'OK'}"
        )
        return 1 if self.errors else 0


# --------------------------------------------------------------------------- loading


def load_yaml(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_controls() -> dict:
    data = load_yaml(ROOT / "controls" / "controls.yaml")
    if data is None:
        sys.exit("controls/controls.yaml not found")
    return {c["id"]: c for c in data.get("controls", [])}


def corpus_version() -> str | None:
    """The single version fact lives in GOVERNANCE.md frontmatter (GOVERNANCE.md R43)."""
    fm = read_frontmatter(ROOT / "GOVERNANCE.md")
    return fm.get("standards_version") if fm else None


def read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def discover_standards() -> dict[str, Path]:
    """Build the standard-ID map by scanning, never by a hardcoded list."""
    found: dict[str, Path] = {}
    for md in sorted(ROOT.rglob("*.md")):
        if "templates" in md.parts:
            continue
        fm = read_frontmatter(md)
        sid = fm.get("id")
        if sid:
            found[sid] = md
    return found


def load_instances() -> list[dict]:
    out = []
    for inst_file in sorted(ROOT.glob("domains/*/instances.yaml")):
        data = load_yaml(inst_file) or {}
        for inst in data.get("instances", []):
            inst["_source"] = str(inst_file.relative_to(ROOT))
            out.append(inst)
    return out


# --------------------------------------------------------------------------- schema


def schema_validate(rep: Report, allow_unvalidated: bool = False) -> None:
    pairs = [
        (ROOT / "controls" / "controls.yaml", "control.schema.json"),
        *[
            (p, "coverage-profile.schema.json")
            for p in sorted((ROOT / "profiles" / "coverage").glob("*.yaml"))
        ],
        *[
            (p, "authority-profile.schema.json")
            for p in sorted((ROOT / "profiles" / "authority").glob("*.yaml"))
        ],
    ]
    adoption = ROOT / "profiles" / "project-adoption.yaml"
    if adoption.exists():
        pairs.append((adoption, "project-adoption.schema.json"))

    if not HAVE_JSONSCHEMA:
        # not-run is not pass (BS-CORE-TRUTH-AND-EVIDENCE R2) -- and the exit status is what CI
        # consumes, so stating it in prose while exiting 0 is exactly the false assurance
        # GOVERNANCE.md R24 forbids. Schema validation is also the independent second check on the
        # protected-control invariant, so losing it silently removes real redundancy.
        if allow_unvalidated:
            rep.warn(
                "jsonschema not installed — full schema validation NOT RUN, permitted explicitly "
                "for this run. The protected-control invariant was checked once, not twice."
            )
        else:
            rep.error(
                "jsonschema not installed — full schema validation NOT RUN. That is "
                "'not-measured', not 'pass', so it fails rather than reporting green. Install it "
                "(pip install jsonschema), or pass --allow-unvalidated-schema to accept a "
                "single-layer check deliberately."
            )
        return

    for data_path, schema_name in pairs:
        schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        data = load_yaml(data_path)
        rel = data_path.relative_to(ROOT)
        try:
            jsonschema.validate(data, schema)
            rep.note(f"schema OK: {rel}")
        except jsonschema.ValidationError as exc:
            loc = "/".join(str(p) for p in exc.absolute_path) or "<root>"
            rep.error(f"{rel}: schema violation at {loc}: {exc.message}")


# --------------------------------------------------------------------------- semantics


def semantic_validate(rep: Report) -> None:
    controls = load_controls()
    standards = discover_standards()
    version = corpus_version()

    if not version:
        rep.error("GOVERNANCE.md frontmatter has no standards_version (R43)")

    # R43: authoritative in GOVERNANCE.md only. Every other copy is a compatibility declaration and
    # MUST equal it. Discovered by scan so a newly-added copy cannot slip through unchecked.
    for vf in sorted(ROOT.rglob("*")):
        if not vf.is_file() or vf.suffix not in {".yaml", ".yml"}:
            continue
        vdata = load_yaml(vf)
        if not isinstance(vdata, dict) or "standards_version" not in vdata:
            continue
        if vdata["standards_version"] != version:
            rep.error(
                f"{vf.relative_to(ROOT)}: standards_version {vdata['standards_version']!r} != "
                f"authoritative {version!r} in GOVERNANCE.md (R43)"
            )

    # --- protected-control integrity (GOVERNANCE.md R11, R18)
    for cid, ctl in controls.items():
        protected = ctl.get("protected", False)
        if protected and ctl.get("mode_override_allowed") is not False:
            rep.error(f"control {cid}: protected but mode_override_allowed is not false (R11)")
        if protected and not ctl.get("protected_category"):
            rep.error(f"control {cid}: protected but no protected_category")
        if ctl.get("protected_category") == "non-delegable":
            if ctl.get("baseline", {}).get("executor") != "operator":
                rep.error(
                    f"control {cid}: non-delegable but executor is not operator (GOVERNANCE 4a)"
                )
        needs_full = protected or ctl.get("reversibility_class") == "irreversible"
        if needs_full:
            for field in (
                "scope_or_envelope",
                "expiration",
                "required_evidence",
                "fallback",
                "enforcement",
            ):
                if field not in ctl:
                    rep.error(f"control {cid}: protected/irreversible but missing '{field}' (R18)")
            if "authorization_timing" not in ctl.get("baseline", {}):
                rep.error(
                    f"control {cid}: protected/irreversible but baseline lacks authorization_timing (R18)"
                )

    # --- authority profiles must not touch protected controls (R20)
    for prof_path in sorted((ROOT / "profiles" / "authority").glob("*.yaml")):
        prof = load_yaml(prof_path) or {}
        rel = prof_path.relative_to(ROOT)
        if prof.get("standards_version") != version:
            rep.error(
                f"{rel}: standards_version {prof.get('standards_version')} != corpus {version} (R45)"
            )
        for cid, overrides in (prof.get("controls") or {}).items():
            if cid not in controls:
                rep.error(f"{rel}: unknown control '{cid}'")
                continue
            if controls[cid].get("mode_override_allowed") is False:
                rep.error(
                    f"{rel}: control '{cid}' is not profile-overridable — remove it. "
                    f"Tighten protected controls via local_tightening in the adoption file (R20/R22)."
                )
                continue
            for field in overrides:
                if field not in CANONICAL_FIELDS:
                    rep.error(f"{rel}: control '{cid}' sets unknown field '{field}'")

        # Executors never accept their own work (R5).
        accept = (prof.get("controls") or {}).get("work.accept", {})
        if accept.get("independence_requirement") == "none":
            rep.error(
                f"{rel}: work.accept independence 'none' — an executor would accept its own work (R5)"
            )

    # --- coverage profiles reference real standards
    coverage_ids = set()
    for prof_path in sorted((ROOT / "profiles" / "coverage").glob("*.yaml")):
        prof = load_yaml(prof_path) or {}
        rel = prof_path.relative_to(ROOT)
        coverage_ids.add(prof.get("id"))
        if prof.get("standards_version") != version:
            rep.error(f"{rel}: standards_version mismatch with corpus {version} (R45)")
        for sid in (prof.get("standards") or []) + (prof.get("mandatory_emphasis") or []):
            if sid not in standards:
                rep.error(f"{rel}: references unknown standard '{sid}'")
    for prof_path in sorted((ROOT / "profiles" / "coverage").glob("*.yaml")):
        prof = load_yaml(prof_path) or {}
        parent = prof.get("extends")
        if parent and parent not in coverage_ids:
            rep.error(f"{prof_path.relative_to(ROOT)}: extends unknown profile '{parent}'")

    # --- every standard is selected by at least one coverage profile
    selected = set()
    for prof_path in sorted((ROOT / "profiles" / "coverage").glob("*.yaml")):
        prof = load_yaml(prof_path) or {}
        selected.update(prof.get("standards") or [])
    for sid in standards:
        if sid == "BS-GOVERNANCE":
            continue
        if sid not in selected:
            rep.error(f"standard {sid} is in no coverage profile — it would never apply to anyone")

    # --- control points referenced by standards must exist
    for sid, path in standards.items():
        fm = read_frontmatter(path)
        for cid in fm.get("control_points") or []:
            if cid not in controls:
                rep.error(
                    f"{path.relative_to(ROOT)}: control_points references unknown control '{cid}'"
                )

    # --- domain instances map onto real classes and only tighten (domains/README M3)
    for inst in load_instances():
        src, iid = inst.get("_source"), inst.get("id")
        cls = inst.get("class")
        if cls not in controls:
            rep.error(f"{src}: instance '{iid}' maps to unknown control class '{cls}'")
            continue
        base = controls[cls]
        merged = {
            **base.get("baseline", {}),
            **{k: v for k, v in base.items() if k in CANONICAL_FIELDS},
        }
        for field, value in (inst.get("tightens") or {}).items():
            if field in LATTICE:
                order = LATTICE[field]
                current = merged.get(field)
                if (
                    current in order
                    and value in order
                    and order.index(value) < order.index(current)
                ):
                    rep.error(
                        f"{src}: instance '{iid}' loosens {field} from '{current}' to '{value}' — "
                        f"instances may only tighten (M3)"
                    )

    # --- adoption file, if present
    adoption_path = ROOT / "profiles" / "project-adoption.yaml"
    if adoption_path.exists():
        validate_adoption(rep, adoption_path, controls, version)
    else:
        rep.note(
            "no profiles/project-adoption.yaml — copy project-adoption.example.yaml to create one"
        )

    surface_open_decisions(rep, controls)

    rep.note(f"{len(standards)} standards, {len(controls)} controls, corpus version {version}")


PROJECT_MARKERS = {".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Makefile"}


def find_project_root() -> Path:
    """The directory this corpus governs.

    Walks up looking for a project marker so that a corpus nested inside a package directory
    reports against the real project root rather than its immediate parent. Falls back to the
    immediate parent — reported as such, never guessed further.
    """
    for candidate in list(ROOT.parents)[:4]:
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return ROOT.parent


def standing_orders() -> dict[str, dict]:
    """Operator standing orders, keyed by the subject they settle (GOVERNANCE.md section 13b)."""
    data = load_yaml(ROOT / "profiles" / "project-adoption.yaml") or {}
    return {o["covers"]: o for o in (data.get("standing_orders") or []) if o.get("covers")}


def surface_open_decisions(rep: Report, controls: dict) -> None:
    """Report absent governing answers. Never synthesize one (GOVERNANCE.md R47-R50).

    A standing order covering the subject takes precedence, and the fact that it did is recorded
    rather than applied silently (R51, R54).
    """
    orders = standing_orders()

    # Standing orders govern defaults and recommendations, not protected boundaries (R53).
    for subject, order in orders.items():
        if subject in controls and controls[subject].get("protected"):
            rep.error(
                f"standing order '{order.get('id')}' covers protected control '{subject}' — "
                f"standing orders do not weaken protected boundaries (R53). Use local_tightening "
                f"to tighten, or make a structural change under R40."
            )

    project_root = find_project_root()

    def settled(subject: str) -> bool:
        order = orders.get(subject)
        if not order:
            return False
        rep.note(
            f"'{subject}' governed by standing order '{order.get('id')}' "
            f"({order.get('author')}, {order.get('effective')}) — not surfaced (R51/R54)"
        )
        return True

    # --- licensing
    if not settled("licensing"):
        found = [
            p.name
            for p in project_root.iterdir()
            if p.is_file() and p.name.split(".")[0].upper() in {"LICENSE", "LICENCE", "COPYING"}
        ]
        if found and project_root != ROOT.parent:
            rep.decision(
                f"A licence exists at {project_root} ({', '.join(sorted(found))}), but it belongs "
                f"to an enclosing project, not to the work this corpus governs",
                "Inheriting an enclosing project's licence by proximity is not the same as choosing "
                "one. Confirm it is intended to govern this work, or state this work's licence "
                "explicitly.",
                "You.",
            )
        elif not found:
            rep.decision(
                f"No licence file found in {project_root}",
                "Without a licence, others have no stated permission to use, modify, or "
                "redistribute this work. Absence is not a permissive default, and it is not a "
                "deliberate 'no licence' either — it is simply unanswered.",
                "You. This choice has legal consequence and is not delegable to tooling or an "
                "agent, so no option is suggested here.",
            )
        else:
            rep.note(f"licence file present in {project_root}: {', '.join(sorted(found))}")

    # --- escalation contact
    adoption = load_yaml(ROOT / "profiles" / "project-adoption.yaml")
    if adoption and not settled("escalation"):
        esc = adoption.get("escalation") or {}
        if not esc.get("primary_contact") or esc.get("primary_contact") == "REPLACE_ME":
            rep.decision(
                "No escalation contact named",
                "Work that hits a boundary has nowhere to go, and the escalation target must not "
                "become a default blocker (R8).",
                "You.",
            )

    # --- retention
    if adoption and not settled("retention"):
        if not (adoption.get("budgets") or {}).get("hard_limit"):
            rep.decision(
                "No hard resource limit declared",
                "Unbounded consumption cannot fail; it can only be noticed and stopped by hand.",
                "You.",
            )


def validate_adoption(rep: Report, path: Path, controls: dict, version: str | None) -> None:
    data = load_yaml(path) or {}
    rel = path.relative_to(ROOT)

    if data.get("standards_version") != version:
        rep.error(f"{rel}: standards_version mismatch with corpus {version} (R45)")

    # Unresolved placeholders never become policy (R28).
    raw = path.read_text(encoding="utf-8")
    for match in set(re.findall(r"REPLACE_ME", raw)):
        rep.error(f"{rel}: unresolved placeholder '{match}' — fill it in or remove the field (R28)")

    # `unknown` on a protected-action capability blocks the action (R14).
    protected_caps = {
        "publishes_irreversibly": "publish.external-irreversible",
        "handles_third_party_data": "data.disclose-external",
        "holds_credentials_or_keys": "access.credential-change",
        "authorizes_payments": "finance.payment-authorize",
        "enters_legal_agreements": "legal.commitment",
    }
    for cap, cid in protected_caps.items():
        entry = (data.get("capabilities") or {}).get(cap)
        if not entry:
            continue
        if entry.get("value") == "unknown":
            rep.error(
                f"{rel}: capability '{cap}' is unknown — '{cid}' MUST NOT proceed until resolved (R14)"
            )

    # Local tightening may tighten, never loosen (R22).
    for cid, overrides in ((data.get("local_tightening") or {}).get("controls") or {}).items():
        if cid not in controls:
            rep.error(f"{rel}: local_tightening references unknown control '{cid}'")
            continue
        base = {**controls[cid].get("baseline", {})}
        for field, value in (overrides or {}).items():
            if field not in LATTICE:
                continue
            order, current = LATTICE[field], base.get(field)
            if current in order and value in order and order.index(value) < order.index(current):
                rep.error(
                    f"{rel}: local_tightening loosens {cid}.{field} from '{current}' to '{value}' "
                    f"— local config may tighten only (R22)"
                )

    # Escalation must resolve; "wait indefinitely" is not available (R8).
    esc = data.get("escalation") or {}
    if esc.get("on_no_response") == "proceed-within-envelope" and not esc.get("proceed_envelope"):
        rep.error(f"{rel}: escalation proceeds on no response but declares no envelope (R8)")


# --------------------------------------------------------------------------- compile


def compile_profile(authority_id: str, coverage_id: str) -> dict:
    """Resolve everything. Nothing is left implicitly defaulted at read time (R19)."""
    controls = load_controls()
    auth = load_yaml(ROOT / "profiles" / "authority" / f"{authority_id}.yaml") or {}
    adoption = load_yaml(ROOT / "profiles" / "project-adoption.yaml") or {}
    local = (adoption.get("local_tightening") or {}).get("controls") or {}

    resolved = {}
    for cid, ctl in controls.items():
        eff = {}
        for field in CANONICAL_FIELDS:
            if field in ctl.get("baseline", {}):
                eff[field] = ctl["baseline"][field]
            elif field in ctl:
                eff[field] = ctl[field]
            else:
                eff[field] = FIELD_DEFAULTS.get(field)
        if ctl.get("mode_override_allowed") is not False:
            eff.update({k: v for k, v in (auth.get("controls", {}).get(cid) or {}).items()})
        eff.update({k: v for k, v in (local.get(cid) or {}).items()})
        eff["protected"] = ctl.get("protected", False)
        eff["protected_category"] = ctl.get("protected_category")
        eff["enforcement"] = ctl.get("enforcement", {"level": "procedural"})
        resolved[cid] = eff

    coverage = resolve_coverage(coverage_id)
    return {
        "standards_version": corpus_version(),
        "authority_profile": authority_id,
        "coverage_profile": coverage_id,
        "assurance": "declared+validated (v1 provides no runtime enforcement — GOVERNANCE.md R23)",
        "standards_in_scope": coverage,
        "controls": resolved,
    }


def resolve_coverage(coverage_id: str) -> list[str]:
    seen, chain = set(), []
    current = coverage_id
    while current:
        prof = load_yaml(ROOT / "profiles" / "coverage" / f"{current}.yaml")
        if not prof or current in seen:
            break
        seen.add(current)
        chain.append(prof)
        current = prof.get("extends")
    out: list[str] = ["BS-GOVERNANCE"]
    for prof in reversed(chain):
        for sid in prof.get("standards") or []:
            if sid not in out:
                out.append(sid)
    return out


# --------------------------------------------------------------------------- generate


def source_hash() -> str:
    h = hashlib.sha256()
    paths = [ROOT / "controls" / "controls.yaml"]
    paths += sorted((ROOT / "profiles" / "authority").glob("*.yaml"))
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def render_tables() -> str:
    controls = load_controls()
    profiles = [p.stem for p in sorted((ROOT / "profiles" / "authority").glob("*.yaml"))]
    lines = [
        "<!-- GENERATED FILE — DO NOT EDIT BY HAND. -->",
        f"<!-- generator: tools/standards.py · source-hash: {source_hash()} -->",
        "",
        "# Mode tables (generated)",
        "",
        "Generated from `controls/controls.yaml` and `profiles/authority/*.yaml`.",
        "Regenerate with `python tools/standards.py generate`. Manual edits are overwritten and",
        "fail `check-generated` in the meantime.",
        "",
        "## Protected controls",
        "",
        "These read identically under every authority profile. That is the design, not an omission.",
        "",
        "| Control | Category | Executor | Authorizer | Independence |",
        "|---|---|---|---|---|",
    ]
    for cid, ctl in controls.items():
        if not ctl.get("protected"):
            continue
        b = ctl.get("baseline", {})
        lines.append(
            f"| `{cid}` | {ctl.get('protected_category')} | {b.get('executor')} "
            f"| {b.get('authorizer')} | {b.get('independence_requirement')} |"
        )

    lines += ["", "## Overridable controls by authority profile", ""]
    header = "| Control | " + " | ".join(profiles) + " |"
    lines += [header, "|---" * (len(profiles) + 1) + "|"]
    for cid, ctl in controls.items():
        if ctl.get("mode_override_allowed") is False:
            continue
        cells = []
        for pid in profiles:
            prof = load_yaml(ROOT / "profiles" / "authority" / f"{pid}.yaml") or {}
            ov = (prof.get("controls") or {}).get(cid) or {}
            b = ctl.get("baseline", {})
            authorizer = ov.get("authorizer", b.get("authorizer"))
            atype = ov.get("authorization_type", b.get("authorization_type"))
            indep = ov.get("independence_requirement", b.get("independence_requirement"))
            cells.append(f"{authorizer} / {atype} / {indep}")
        lines.append(f"| `{cid}` | " + " | ".join(cells) + " |")

    lines += [
        "",
        "Cell format: **authorizer / authorization type / independence requirement**.",
        "",
        "Assurance level for every row: `procedural`. This corpus provides `declared` and",
        "`validated` only — no row here is enforced at runtime (GOVERNANCE.md R23).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- cli


def main() -> int:
    parser = argparse.ArgumentParser(description="Baseline Standards validator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="schema + cross-file semantic validation")
    v.add_argument(
        "--allow-unvalidated-schema",
        action="store_true",
        help="continue when jsonschema is unavailable instead of failing (records a warning)",
    )
    c = sub.add_parser("compile", help="resolve and print the effective profile")
    c.add_argument("--authority", default="supervised")
    c.add_argument("--coverage", default="starter")
    sub.add_parser("generate", help="write generated mode tables")
    sub.add_parser("check-generated", help="fail if generated output has drifted")
    args = parser.parse_args()

    if args.cmd == "validate":
        rep = Report()
        schema_validate(rep, allow_unvalidated=args.allow_unvalidated_schema)
        semantic_validate(rep)
        return rep.emit()

    if args.cmd == "compile":
        print(json.dumps(compile_profile(args.authority, args.coverage), indent=2, default=str))
        return 0

    if args.cmd == "generate":
        GENERATED.parent.mkdir(parents=True, exist_ok=True)
        GENERATED.write_text(render_tables(), encoding="utf-8")
        print(f"wrote {GENERATED.relative_to(ROOT)}")
        return 0

    if args.cmd == "check-generated":
        if not GENERATED.exists():
            print("ERROR: generated tables missing — run: python tools/standards.py generate")
            return 1
        if GENERATED.read_text(encoding="utf-8") != render_tables():
            print(
                "ERROR: generated tables have drifted from their sources (regenerate; never edit)"
            )
            return 1
        print("generated output is current.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
