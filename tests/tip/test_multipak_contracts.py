# SPDX-License-Identifier: Apache-2.0
"""Contract tests for MultiPak Pro Phase 0 OSS surface.

Covers the schema contract that the closed-source Pro daemon will consume:

- ``tokenpak.tip.capabilities`` — the 10 new MultiPak capability constants.
- ``tokenpak.tip.pak`` — Pak schema, subtype taxonomy, deprecation aliases.
- ``tokenpak.tip.context_package`` — Context Package, levels, coverage states.

Standards:
- ``32-multipak-pro-architecture.md §1.3`` — OSS/Pro boundary.
- ``32-multipak-pro-architecture.md §2`` — Pak taxonomy.
- ``32-multipak-pro-architecture.md §3`` — capability set.
- ``32-multipak-pro-architecture.md §6`` — context delivery levels.
- ``31-tip-versioning-strategy.md §2`` — additive capability rule.
- ``08-naming-glossary.md`` (Pak entry) — canonical taxonomy.
"""

from __future__ import annotations

import dataclasses
import json
import warnings

import pytest

from tokenpak.tip import (
    ALL_OPTIMIZATION_CAPABILITIES,
    MULTIPAK_CAPABILITIES,
    TIP_CONTEXT_COVERAGE,
    TIP_CONTEXT_HANDOFF,
    TIP_CONTEXT_PACKAGE,
    TIP_CONTEXT_POLICY,
    TIP_CONTEXT_RESUME,
    TIP_PAK_CAPTURE,
    TIP_PAK_HYDRATE,
    TIP_PAK_INDEX,
    TIP_PAK_PROMOTE,
    TIP_PAK_RECALL,
    AnchorBlockPosition,
    ContextLevel,
    ContextPackage,
    ContextScope,
    CoverageConfidence,
    CoverageReport,
    CoverageState,
    OrderingHints,
    Pak,
    PakAnchor,
    PakAuthority,
    PakConfidence,
    PakPrivacy,
    PakPrivacyClass,
    PakRelationships,
    PakRetention,
    PakRetentionPolicy,
    PakScope,
    PakSource,
    PakSourceType,
    PakStatus,
    PakSubtype,
    PolicyDecision,
    all_subtypes,
    context_level_label,
    default_retention_for,
    parse_context_level,
)

# ---------------------------------------------------------------------------
# Capability constants (Std 32 §3 + Std 31 §2)
# ---------------------------------------------------------------------------


class TestCapabilityConstants:
    """The 10 MultiPak capability labels are unique, well-formed, and additive."""

    def test_pak_capability_string_values(self):
        assert TIP_PAK_CAPTURE == "tip.pak.capture"
        assert TIP_PAK_INDEX == "tip.pak.index"
        assert TIP_PAK_RECALL == "tip.pak.recall"
        assert TIP_PAK_HYDRATE == "tip.pak.hydrate"
        assert TIP_PAK_PROMOTE == "tip.pak.promote"

    def test_context_capability_string_values(self):
        assert TIP_CONTEXT_PACKAGE == "tip.context.package"
        assert TIP_CONTEXT_HANDOFF == "tip.context.handoff"
        assert TIP_CONTEXT_RESUME == "tip.context.resume"
        assert TIP_CONTEXT_COVERAGE == "tip.context.coverage"
        assert TIP_CONTEXT_POLICY == "tip.context.policy"

    def test_multipak_set_size_is_10(self):
        # If this fails, either (a) a capability was added/removed without a
        # paired update to the registry catalog, or (b) the count drifted
        # from Std 32 §3. Fix both, not just this assertion.
        assert len(MULTIPAK_CAPABILITIES) == 10

    def test_multipak_capabilities_are_unique(self):
        # Sanity check against accidental duplicates in the convenience set.
        assert len(MULTIPAK_CAPABILITIES) == len(set(MULTIPAK_CAPABILITIES))

    def test_multipak_subset_of_all_optimization_capabilities(self):
        # Std 31 §2 additive rule: new capabilities land in the aggregated
        # set without disturbing existing entries.
        assert MULTIPAK_CAPABILITIES.issubset(ALL_OPTIMIZATION_CAPABILITIES)

    def test_no_capability_clash_with_existing(self):
        # The MultiPak labels use the tip.pak.* / tip.context.* prefixes
        # which must not collide with any pre-MultiPak capability.
        non_multipak = ALL_OPTIMIZATION_CAPABILITIES - MULTIPAK_CAPABILITIES
        for cap in non_multipak:
            assert not cap.startswith("tip.pak.")
            assert not cap.startswith("tip.context.")

    def test_label_format_is_dot_separated(self):
        for cap in MULTIPAK_CAPABILITIES:
            parts = cap.split(".")
            assert parts[0] == "tip"
            assert len(parts) >= 3


# ---------------------------------------------------------------------------
# Pak subtype taxonomy (Std 32 §2)
# ---------------------------------------------------------------------------


class TestPakSubtype:
    """The 5 canonical subtypes per Decision #2=A (ratified 2026-05-07)."""

    def test_canonical_subtype_count(self):
        # Exactly 5 — Vault / Interaction / Decision / Recall / Handoff.
        assert len(list(all_subtypes())) == 5

    def test_canonical_subtype_values(self):
        canonical = {s.value for s in all_subtypes()}
        assert canonical == {"vault", "interaction", "decision", "recall", "handoff"}

    @pytest.mark.parametrize("name", ["vault", "interaction", "decision", "recall", "handoff"])
    def test_canonical_parse(self, name):
        assert PakSubtype.parse(name) == PakSubtype(name)

    def test_canonical_parse_is_silent(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            # No warning should be emitted for canonical names.
            PakSubtype.parse("vault")

    @pytest.mark.parametrize(
        "alias,canonical",
        [
            ("project", PakSubtype.VAULT),
            ("context", PakSubtype.RECALL),
            ("memory", PakSubtype.INTERACTION),
        ],
    )
    def test_legacy_alias_resolves_with_warning(self, alias, canonical):
        # Decision #2=A: legacy 4-subtype names map to canonical 5; emit
        # DeprecationWarning until v3.0.0.
        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter("always")
            assert PakSubtype.parse(alias) == canonical
        assert any(issubclass(r.category, DeprecationWarning) for r in records), (
            f"expected DeprecationWarning for legacy alias {alias!r}"
        )

    def test_unknown_subtype_raises(self):
        with pytest.raises(ValueError):
            PakSubtype.parse("nonexistent_subtype")

    def test_parse_strips_and_lowers(self):
        assert PakSubtype.parse("  Vault  ") == PakSubtype.VAULT


# ---------------------------------------------------------------------------
# Default-retention table (dynamic discovery — feedback_always_dynamic.md)
# ---------------------------------------------------------------------------


class TestDefaultRetention:
    """Consumers MUST consult ``default_retention_for`` rather than hardcoding."""

    @pytest.mark.parametrize(
        "subtype,expected",
        [
            (PakSubtype.VAULT, PakRetention.SOURCE_LIFETIME),
            (PakSubtype.DECISION, PakRetention.PERSISTENT),
            (PakSubtype.INTERACTION, PakRetention.DAYS_180),
            (PakSubtype.RECALL, PakRetention.SESSION),
            (PakSubtype.HANDOFF, PakRetention.DAYS_30),
        ],
    )
    def test_default_retention_per_subtype(self, subtype, expected):
        assert default_retention_for(subtype) == expected


# ---------------------------------------------------------------------------
# Pak round-trip + immutability (Std 32 §2)
# ---------------------------------------------------------------------------


def _make_decision_pak() -> Pak:
    return Pak(
        pak_id="pak_dec_test_01",
        pak_type=PakSubtype.DECISION,
        title="Test Decision",
        summary="A ratified test decision.",
        scope=PakScope(user="alice", project="tokenpak", topic="multipak"),
        source=PakSource(
            platform="vscode",
            source_type=PakSourceType.MANUAL,
            created_at="2026-05-07T22:00:00Z",
            source_hash="0" * 64,
        ),
        status=PakStatus.ACCEPTED,
        authority=PakAuthority.USER_APPROVED,
        confidence=PakConfidence.HIGH,
        retention=PakRetentionPolicy(ttl=PakRetention.PERSISTENT),
        anchors=(
            PakAnchor(anchor_id="a1", source_hash="a" * 64, snippet_available=True),
        ),
        relationships=PakRelationships(supersedes=("pak_old_01",)),
    )


class TestPakRoundTrip:
    """Pak should JSON-round-trip without loss."""

    def test_to_dict_renders_enum_values(self):
        pak = _make_decision_pak()
        d = pak.to_dict()
        assert d["pak_type"] == "decision"
        assert d["status"] == "accepted"
        assert d["authority"] == "user_approved"
        assert d["confidence"] == "high"
        assert d["retention"]["ttl"] == "persistent"
        assert d["source"]["source_type"] == "manual"
        assert d["privacy"] == {"class": "local_only"}

    def test_round_trip_equality(self):
        pak = _make_decision_pak()
        restored = Pak.from_dict(pak.to_dict())
        assert restored == pak

    def test_json_serializable(self):
        # ``to_dict`` output must be JSON-safe end-to-end.
        pak = _make_decision_pak()
        encoded = json.dumps(pak.to_dict(), sort_keys=True)
        assert isinstance(encoded, str)
        decoded = json.loads(encoded)
        assert Pak.from_dict(decoded) == pak

    def test_pak_is_frozen(self):
        pak = _make_decision_pak()
        with pytest.raises(dataclasses.FrozenInstanceError):
            pak.title = "mutated"  # type: ignore[misc]

    def test_replace_creates_new_instance(self):
        pak = _make_decision_pak()
        updated = dataclasses.replace(pak, title="Updated")
        assert pak.title == "Test Decision"
        assert updated.title == "Updated"
        assert pak != updated


class TestPakSubtypeParseInFromDict:
    def test_legacy_subtype_in_wire_form_resolves(self):
        # A Pak written with the legacy ``project`` subtype string should
        # round-trip as a ``vault`` Pak (Decision #2=A migration path).
        d = _make_decision_pak().to_dict()
        d["pak_type"] = "project"
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            restored = Pak.from_dict(d)
        assert restored.pak_type == PakSubtype.VAULT


class TestPakPrivacy:
    def test_default_privacy_is_local_only(self):
        pak = _make_decision_pak()
        assert pak.privacy.class_ == PakPrivacyClass.LOCAL_ONLY

    def test_explicit_privacy_local_only(self):
        pak = dataclasses.replace(_make_decision_pak(), privacy=PakPrivacy(class_=PakPrivacyClass.LOCAL_ONLY))
        assert pak.privacy.class_ == PakPrivacyClass.LOCAL_ONLY


# ---------------------------------------------------------------------------
# Context delivery levels (Std 32 §6)
# ---------------------------------------------------------------------------


class TestContextLevel:
    def test_six_levels(self):
        # Std 32 §6: levels 0-5 inclusive.
        assert {l.value for l in ContextLevel} == {0, 1, 2, 3, 4, 5}

    def test_ordering(self):
        assert ContextLevel.NO_MEMORY < ContextLevel.POINTER_ONLY
        assert ContextLevel.POINTER_ONLY < ContextLevel.RECALL_SUMMARY
        assert ContextLevel.RECALL_SUMMARY < ContextLevel.HANDOFF_PAK
        assert ContextLevel.HANDOFF_PAK < ContextLevel.HYDRATED_HANDOFF_PAK
        assert ContextLevel.HYDRATED_HANDOFF_PAK < ContextLevel.FULL_RESTORE

    @pytest.mark.parametrize(
        "level,label",
        [
            (ContextLevel.NO_MEMORY, "no_memory"),
            (ContextLevel.POINTER_ONLY, "pointer_only"),
            (ContextLevel.RECALL_SUMMARY, "recall_summary"),
            (ContextLevel.HANDOFF_PAK, "handoff_pak"),
            (ContextLevel.HYDRATED_HANDOFF_PAK, "hydrated_handoff_pak"),
            (ContextLevel.FULL_RESTORE, "full_restore"),
        ],
    )
    def test_label_round_trip(self, level, label):
        assert context_level_label(level) == label
        assert parse_context_level(label) == level

    def test_parse_int_form(self):
        assert parse_context_level(3) == ContextLevel.HANDOFF_PAK

    def test_parse_unknown_label_raises(self):
        with pytest.raises(ValueError):
            parse_context_level("nonexistent_level")


# ---------------------------------------------------------------------------
# Coverage states (Std 32 §10)
# ---------------------------------------------------------------------------


class TestCoverageState:
    def test_six_states(self):
        # Std 32 §10 + PRD §28: six coverage states.
        assert {s.value for s in CoverageState} == {
            "complete",
            "partial",
            "low_confidence",
            "missing_required_context",
            "blocked_by_policy",
            "not_found",
        }


# ---------------------------------------------------------------------------
# ContextPackage round-trip
# ---------------------------------------------------------------------------


def _make_context_package() -> ContextPackage:
    return ContextPackage(
        package_id="ctxpkg_test_01",
        scope=ContextScope(
            user_scope="local_user",
            project_scope="tokenpak",
            target_platform="vscode",
            target_task="implement",
        ),
        recall_query="proposal about MultiPak from last month",
        context_level=ContextLevel.HYDRATED_HANDOFF_PAK,
        included_pak_ids=("pak_a", "pak_b", "pak_c"),
        hydrated_anchor_ids=("anchor_1", "anchor_2"),
        coverage=CoverageReport(
            state=CoverageState.COMPLETE,
            required_paks=3,
            included_paks=3,
            hydrated_anchors=2,
            confidence=CoverageConfidence.HIGH,
        ),
        policy=PolicyDecision(),
        generated_at="2026-05-07T22:00:00Z",
    )


class TestContextPackageRoundTrip:
    def test_to_dict_renders_strings(self):
        pkg = _make_context_package()
        d = pkg.to_dict()
        assert d["context_level"] == "hydrated_handoff_pak"
        assert d["coverage"]["state"] == "complete"
        assert d["coverage"]["confidence"] == "high"

    def test_round_trip_equality(self):
        pkg = _make_context_package()
        restored = ContextPackage.from_dict(pkg.to_dict())
        assert restored == pkg

    def test_json_serializable(self):
        pkg = _make_context_package()
        encoded = json.dumps(pkg.to_dict(), sort_keys=True)
        decoded = json.loads(encoded)
        assert ContextPackage.from_dict(decoded) == pkg

    def test_is_frozen(self):
        pkg = _make_context_package()
        with pytest.raises(dataclasses.FrozenInstanceError):
            pkg.recall_query = "mutated"  # type: ignore[misc]

    def test_is_empty(self):
        pkg = ContextPackage(
            package_id="ctxpkg_empty",
            scope=ContextScope(),
            recall_query="dinner ideas",
            context_level=ContextLevel.NO_MEMORY,
            included_pak_ids=(),
            hydrated_anchor_ids=(),
            coverage=CoverageReport(state=CoverageState.NOT_FOUND),
            policy=PolicyDecision(),
            generated_at="2026-05-07T22:00:00Z",
        )
        assert pkg.is_empty()
        assert not pkg.has_complete_coverage()

    def test_complete_coverage(self):
        pkg = _make_context_package()
        assert pkg.has_complete_coverage()


class TestPolicyDecision:
    def test_default_policy_is_permissive(self):
        # Default = no blocks; a fresh PolicyDecision means nothing was
        # rejected by the policy gate.
        pol = PolicyDecision()
        assert not pol.cross_project_blocked
        assert not pol.sensitive_blocked
        assert not pol.hydration_budget_exceeded
        assert pol.blocked_pak_ids == ()

    def test_blocked_state_round_trips_through_package(self):
        pkg = dataclasses.replace(
            _make_context_package(),
            coverage=CoverageReport(
                state=CoverageState.BLOCKED_BY_POLICY,
                required_paks=3,
                included_paks=2,
            ),
            policy=PolicyDecision(
                cross_project_blocked=True,
                blocked_pak_ids=("pak_other_project_01",),
                reasons=("scope mismatch: cooking vs tokenpak",),
            ),
        )
        restored = ContextPackage.from_dict(pkg.to_dict())
        assert restored.policy.cross_project_blocked
        assert restored.policy.blocked_pak_ids == ("pak_other_project_01",)


# ---------------------------------------------------------------------------
# Privacy contract (Std 32 §7.1)
# ---------------------------------------------------------------------------


class TestPrivacyContract:
    """Ensures the OSS-side schema does not bleed memory content fields into
    surfaces that ``25 §4.4`` forbids on the license-validation egress.

    The full enforcement test lives in the Pro daemon's privacy contract
    suite (per Std 32 §10). The OSS-side check here is a structural one:
    the Pak schema MUST NOT carry any field literally named like a license
    payload field, so a reviewer scanning the schema can verify that no
    accidental coupling exists.
    """

    FORBIDDEN_FIELD_PREFIXES = (
        "license_token",
        "tenant_id",
        "fingerprint",
        "issuer",
        "signature",
    )

    def test_pak_fields_are_disjoint_from_license_payload(self):
        pak_field_names = {f.name for f in dataclasses.fields(Pak)}
        for forbidden in self.FORBIDDEN_FIELD_PREFIXES:
            assert not any(name.startswith(forbidden) for name in pak_field_names), (
                f"Pak schema field starts with reserved license-payload prefix {forbidden!r}"
            )

    def test_context_package_fields_are_disjoint_from_license_payload(self):
        pkg_field_names = {f.name for f in dataclasses.fields(ContextPackage)}
        for forbidden in self.FORBIDDEN_FIELD_PREFIXES:
            assert not any(name.startswith(forbidden) for name in pkg_field_names)


# ---------------------------------------------------------------------------
# OrderingHints (Std 32 §5.6 addendum) — additive within TIP-1.x
# ---------------------------------------------------------------------------


class TestOrderingHintsDefaultsAndRoundTrip:
    """``ordering_hints`` is optional and additive on ``ContextPackage``.

    Std 32 §5.6 + Std 31 §2: receivers that don't recognise this field
    ignore it and produce a valid (just unoptimised) package — so the
    pre-addendum byte shape must be preserved when ``ordering_hints``
    is ``None``.
    """

    def test_default_is_none_and_dict_omits_field(self):
        pkg = _make_context_package()
        assert pkg.ordering_hints is None
        d = pkg.to_dict()
        assert "ordering_hints" not in d, (
            "to_dict must omit the field entirely when None — preserves the "
            "pre-addendum byte shape per Std 31 §2 additive-receiver rule."
        )

    def test_round_trip_with_default_hints(self):
        hints = OrderingHints()
        pkg = dataclasses.replace(_make_context_package(), ordering_hints=hints)
        restored = ContextPackage.from_dict(pkg.to_dict())
        assert restored == pkg
        assert restored.ordering_hints == OrderingHints()
        assert restored.ordering_hints.anchor_block_position is AnchorBlockPosition.END

    def test_round_trip_with_explicit_hints(self):
        hints = OrderingHints(
            stable_first=False,
            task_delta_after_stable_context=False,
            output_requirements_near_end=False,
            cache_sensitive_blocks=("block_a", "block_b"),
            anchor_block_position=AnchorBlockPosition.INLINE,
        )
        pkg = dataclasses.replace(_make_context_package(), ordering_hints=hints)
        d = pkg.to_dict()
        assert d["ordering_hints"]["anchor_block_position"] == "inline"
        assert d["ordering_hints"]["cache_sensitive_blocks"] == ["block_a", "block_b"]
        restored = ContextPackage.from_dict(d)
        assert restored == pkg

    def test_round_trip_omit_anchors(self):
        hints = OrderingHints(anchor_block_position=AnchorBlockPosition.OMIT)
        pkg = dataclasses.replace(_make_context_package(), ordering_hints=hints)
        restored = ContextPackage.from_dict(pkg.to_dict())
        assert restored.ordering_hints.anchor_block_position is AnchorBlockPosition.OMIT

    def test_json_serializable_with_hints(self):
        hints = OrderingHints(
            cache_sensitive_blocks=("blk_release_notes",),
        )
        pkg = dataclasses.replace(_make_context_package(), ordering_hints=hints)
        encoded = json.dumps(pkg.to_dict(), sort_keys=True)
        decoded = json.loads(encoded)
        assert ContextPackage.from_dict(decoded) == pkg

    def test_unknown_anchor_block_position_raises(self):
        """Closed enum within a TIP minor: unknown value is a receiver-side bug."""
        with pytest.raises(ValueError) as exc:
            OrderingHints.from_wire({"anchor_block_position": "middle"})
        assert "anchor_block_position" in str(exc.value)

    def test_unknown_outer_field_does_not_break_receiver(self):
        """Field set is additive — unrecognised siblings of ``ordering_hints``
        in the wire form are silently ignored (just dropped by ``from_dict``).

        This pins the Std 31 §2 promise: future addendums that add new
        optional fields to ``ContextPackage`` will not break consumers
        on the current TIP minor.
        """
        pkg = _make_context_package()
        d = pkg.to_dict()
        d["future_addendum_field_xyz"] = "ignored-by-current-receivers"
        restored = ContextPackage.from_dict(d)
        assert restored == pkg

    def test_hints_field_is_frozen_on_dataclass(self):
        """``OrderingHints`` is frozen — receivers can pass instances safely."""
        h = OrderingHints()
        with pytest.raises(dataclasses.FrozenInstanceError):
            h.stable_first = False  # type: ignore[misc]

    def test_anchor_block_position_string_enum_is_closed(self):
        """The enum is closed within a TIP minor (Std 32 §5.6)."""
        values = {v.value for v in AnchorBlockPosition}
        assert values == {"end", "inline", "omit"}
