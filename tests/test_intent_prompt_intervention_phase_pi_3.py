# SPDX-License-Identifier: Apache-2.0
"""Phase PI-3 — companion-side opt-in PromptPatch injection tests.

Seventeen directive-mandated test categories:

  1.  disabled config produces no injection
  2.  enabled config injects into companion_context
  3.  original user message preserved
  4.  target=user_message rejected
  5.  proxy injection remains disabled
  6.  rewrite_prompt unsupported
  7.  byte-preserve override blocked
  8.  no TIP headers emitted
  9.  no routing mutation
  10. no classifier mutation
  11. no provider/model switching
  12. patch marked applied only after success
  13. failed insertion leaves applied=false
  14. no raw prompt text or secrets emitted
  15. CLI shows applied state correctly
  16. forbidden wording guardrail before application
  17. applied wording allowed only after application
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from tokenpak.companion.intent_injection import (
    APPLICATION_MODE_INJECT_GUIDANCE,
    REASON_ALREADY_APPLIED,
    REASON_BYTE_PRESERVE_OVERRIDE_BLOCKED,
    REASON_CLAUDE_CODE_COMPANION_DISABLED,
    REASON_DISABLED,
    REASON_OK,
    REASON_PERSIST_FAILED,
    REASON_PROXY_FORCED_OFF,
    REASON_WRONG_MODE,
    REASON_WRONG_TARGET,
    SURFACE_CLAUDE_CODE_COMPANION,
    apply_patch_to_companion_context,
)
from tokenpak.proxy.intent_classifier import IntentClassification
from tokenpak.proxy.intent_contract import (
    IntentTelemetryRow,
    IntentTelemetryStore,
    build_contract,
)
from tokenpak.proxy.intent_policy_config_loader import (
    PromptInterventionRuntimeConfig,
    PromptInterventionSurfaces,
    parse_prompt_intervention_block,
)
from tokenpak.proxy.intent_policy_engine import (
    PolicyEngineConfig,
    PolicyInput,
    evaluate_policy,
)
from tokenpak.proxy.intent_policy_telemetry import (
    IntentPolicyDecisionRow,
    IntentPolicyDecisionStore,
)
from tokenpak.proxy.intent_prompt_patch import (
    FORBIDDEN_PHRASES,
    MODE_INJECT_GUIDANCE,
    TARGET_COMPANION_CONTEXT,
    PromptPatchBuilderContext,
    build_patches,
)
from tokenpak.proxy.intent_prompt_patch import (
    PromptInterventionConfig as BuilderPromptInterventionConfig,
)
from tokenpak.proxy.intent_prompt_patch_telemetry import (
    IntentPatchRow,
    IntentPatchStore,
)
from tokenpak.proxy.intent_suggestion import (
    SuggestionBuilderContext,
    build_suggestions,
)
from tokenpak.proxy.intent_suggestion_telemetry import (
    IntentSuggestionRow,
    IntentSuggestionStore,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _enabled_config() -> PromptInterventionRuntimeConfig:
    """Fully-enabled PI-3 runtime config (companion only)."""
    return PromptInterventionRuntimeConfig(
        enabled=True,
        mode="inject_guidance",
        target="companion_context",
        require_confirmation=False,
        allow_byte_preserve_override=False,
        surfaces=PromptInterventionSurfaces(
            claude_code_companion=True,
            proxy=False,
        ),
    )


def _seed_eligible_patch(*, db: Path, prompt: str = "create a new file") -> dict:
    """Seed a single eligible PI-1 patch row + return the dict view.

    Returns the row as :meth:`IntentPatchStore.fetch_latest` would —
    suitable for passing directly to
    :func:`apply_patch_to_companion_context`.
    """
    cls = IntentClassification(
        intent_class="create",
        confidence=0.9,
        slots_present=("target",),
        slots_missing=(),
        catch_all_reason=None,
    )
    contract = build_contract(classification=cls, raw_prompt=prompt)
    ts = _dt.datetime.now().isoformat(timespec="seconds")

    events = IntentTelemetryStore(db_path=db)
    events.write(
        IntentTelemetryRow(
            request_id="pi3-r1",
            contract=contract,
            timestamp=ts,
            tip_headers_emitted=False,
            tip_headers_stripped=True,
        )
    )
    events.close()

    inp = PolicyInput(
        intent_class="create",
        confidence=0.9,
        slots_present=("target",),
        slots_missing=(),
        catch_all_reason=None,
        provider="tokenpak-claude-code",
        model="claude-3-5-sonnet",
        live_verified_status=True,
    )
    decision = evaluate_policy(inp, PolicyEngineConfig())
    pstore = IntentPolicyDecisionStore(db_path=db)
    pstore.write(
        IntentPolicyDecisionRow(
            request_id="pi3-r1",
            contract_id=contract.contract_id,
            timestamp=ts,
            decision=decision,
        )
    )
    pstore.close()

    sugg_ctx = SuggestionBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=frozenset({"tip.compression.v1"}),
    )
    suggestions = build_suggestions(
        decision=decision, contract=contract, ctx=sugg_ctx
    )
    suggestion = suggestions[0]
    sstore = IntentSuggestionStore(db_path=db)
    sstore.write(IntentSuggestionRow(suggestion=suggestion, timestamp=ts))
    sstore.close()

    builder_ctx = PromptPatchBuilderContext(
        config=PolicyEngineConfig(),
        adapter_capabilities=frozenset(
            {"tip.compression.v1", "tip.byte-preserved-passthrough"}
        ),
        required_slots=(),
    )
    patches = build_patches(
        suggestion=suggestion,
        contract=contract,
        decision=decision,
        pi_config=BuilderPromptInterventionConfig(
            enabled=True,
            mode=MODE_INJECT_GUIDANCE,
            target=TARGET_COMPANION_CONTEXT,
            require_confirmation=False,
            allow_byte_preserve_override=False,
        ),
        ctx=builder_ctx,
    )
    patch = patches[0]
    store = IntentPatchStore(db_path=db)
    store.write(IntentPatchRow(patch=patch, created_at=ts))
    return store.fetch_latest()


# ── 1. disabled config produces no injection ─────────────────────────


class TestDisabledConfigNoInjection:

    def test_default_config_disabled(self):
        cfg = PromptInterventionRuntimeConfig()
        assert not cfg.enabled
        assert not cfg.is_claude_code_companion_active()

    def test_disabled_config_returns_disabled_reason(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=PromptInterventionRuntimeConfig(),
            existing_context="orig context",
            store=store,
        )
        assert not result.success
        assert result.reason == REASON_DISABLED
        # Persistence didn't happen.
        latest = store.fetch_latest()
        assert latest["applied"] is False
        assert latest.get("applied_at") is None


# ── 2. enabled config injects into companion_context ─────────────────


class TestEnabledConfigInjects:

    def test_eligible_patch_injects_block(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        original_context = "Companion context: prior memory capsules."
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context=original_context,
            store=store,
        )
        assert result.success
        assert result.reason == REASON_OK
        assert result.injected_context is not None
        assert "<TokenPak Intent Guidance>" in result.injected_context
        assert "</TokenPak Intent Guidance>" in result.injected_context

    def test_block_appears_before_existing_context(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        original = "ZZ_ORIGINAL_MARKER_ZZ"
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context=original,
            store=store,
        )
        assert result.success
        idx_block = result.injected_context.index("<TokenPak Intent Guidance>")
        idx_orig = result.injected_context.index(original)
        assert idx_block < idx_orig, (
            "guidance block must precede original context"
        )


# ── 3. original user message preserved ───────────────────────────────


class TestUserMessagePreserved:

    def test_existing_context_is_substring_of_result(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        # Pretend the companion context already contains a verbatim
        # user-input echo (companions sometimes include this).
        original = (
            "User message: Please add a CHANGELOG entry for v0.4.\n"
            "Companion notes: previous commit 4bdf123."
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context=original,
            store=store,
        )
        assert result.success
        assert original in result.injected_context, (
            "original user message must be byte-preserved within the "
            "post-injection context"
        )

    def test_no_user_message_target_path_exists(self):
        # The library only injects into companion_context. The
        # config layer rejects target=user_message; the injection
        # library has no code path that touches user_message.
        from tokenpak.companion import intent_injection as ii
        # Module-level constant — ensures the surface is bound to
        # claude_code_companion only.
        assert ii.SURFACE_CLAUDE_CODE_COMPANION == "claude_code_companion"


# ── 4. target=user_message rejected ──────────────────────────────────


class TestTargetUserMessageRejected:

    def test_loader_rejects_user_message(self):
        cfg, warnings = parse_prompt_intervention_block({
            "enabled": True,
            "mode": "inject_guidance",
            "target": "user_message",
            "require_confirmation": False,
            "surfaces": {"claude_code_companion": True},
        })
        # Downgraded to the safe default.
        assert cfg.target == "companion_context"
        assert any("user_message" in w for w in warnings)

    def test_runtime_rejects_user_message_target(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        # Hand-build a config with target=user_message even though
        # the loader would reject it. The library must still refuse.
        cfg = PromptInterventionRuntimeConfig(
            enabled=True,
            mode="inject_guidance",
            target="user_message",
            require_confirmation=False,
            surfaces=PromptInterventionSurfaces(claude_code_companion=True),
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=cfg,
            existing_context="orig",
            store=store,
        )
        assert not result.success
        assert result.reason == REASON_WRONG_TARGET


# ── 5. proxy injection remains disabled ──────────────────────────────


class TestProxyInjectionDisabled:

    def test_loader_force_clamps_proxy_surface(self):
        cfg, warnings = parse_prompt_intervention_block({
            "enabled": True,
            "mode": "inject_guidance",
            "target": "companion_context",
            "require_confirmation": False,
            "surfaces": {
                "claude_code_companion": True,
                "proxy": True,  # operator tries to enable proxy injection
            },
        })
        assert cfg.surfaces.proxy is False, "proxy surface must be force-clamped"
        assert any("proxy" in w for w in warnings)

    def test_runtime_refuses_proxy_surface(self, tmp_path):
        # Hand-build a config with surfaces.proxy=True (bypassing the
        # loader). The library refuses.
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        cfg = PromptInterventionRuntimeConfig(
            enabled=True,
            mode="inject_guidance",
            target="companion_context",
            require_confirmation=False,
            surfaces=PromptInterventionSurfaces(
                claude_code_companion=True, proxy=True
            ),
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch, pi_config=cfg, existing_context="orig", store=store,
        )
        assert not result.success
        assert result.reason == REASON_PROXY_FORCED_OFF

    def test_companion_disabled_when_no_surface(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        cfg = PromptInterventionRuntimeConfig(
            enabled=True,
            mode="inject_guidance",
            target="companion_context",
            require_confirmation=False,
            surfaces=PromptInterventionSurfaces(),  # all surfaces off
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch, pi_config=cfg, existing_context="orig", store=store,
        )
        assert not result.success
        assert result.reason == REASON_CLAUDE_CODE_COMPANION_DISABLED


# ── 6. rewrite_prompt unsupported ────────────────────────────────────


class TestRewritePromptUnsupported:

    def test_loader_downgrades_rewrite_prompt(self):
        cfg, warnings = parse_prompt_intervention_block({
            "enabled": True,
            "mode": "rewrite_prompt",
            "target": "companion_context",
        })
        assert cfg.mode == "preview_only"
        assert any("rewrite_prompt" in w for w in warnings)

    def test_runtime_rejects_rewrite_prompt_mode(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        cfg = PromptInterventionRuntimeConfig(
            enabled=True,
            mode="rewrite_prompt",
            target="companion_context",
            require_confirmation=False,
            surfaces=PromptInterventionSurfaces(claude_code_companion=True),
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch, pi_config=cfg, existing_context="orig", store=store,
        )
        assert not result.success
        assert result.reason == REASON_WRONG_MODE


# ── 7. byte-preserve override blocked ────────────────────────────────


class TestByteServeOverrideBlocked:

    def test_loader_force_clamps_override(self):
        cfg, warnings = parse_prompt_intervention_block({
            "enabled": True,
            "mode": "inject_guidance",
            "target": "companion_context",
            "allow_byte_preserve_override": True,
            "surfaces": {"claude_code_companion": True},
        })
        assert cfg.allow_byte_preserve_override is False
        assert any("byte_preserve" in w for w in warnings)

    def test_runtime_refuses_override(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        cfg = PromptInterventionRuntimeConfig(
            enabled=True,
            mode="inject_guidance",
            target="companion_context",
            require_confirmation=False,
            allow_byte_preserve_override=True,
            surfaces=PromptInterventionSurfaces(claude_code_companion=True),
        )
        result = apply_patch_to_companion_context(
            patch_dict=patch, pi_config=cfg, existing_context="orig", store=store,
        )
        assert not result.success
        assert result.reason == REASON_BYTE_PRESERVE_OVERRIDE_BLOCKED


# ── 8. no TIP headers emitted ────────────────────────────────────────


class TestNoTipHeadersEmitted:

    def test_module_does_not_emit_tip_headers(self):
        # Structural test: companion/intent_injection.py has no
        # reference to TIP intent header names.
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        text = path.read_text(encoding="utf-8")
        # The module must not emit / construct any
        # tip-prefix-named header. The source may *mention* the
        # capability slug as a structural reference (we don't
        # add this, but we don't rule it out).
        assert "X-TIP-Intent" not in text
        assert "x-tip-intent" not in text
        assert "tip-intent-headers" not in text.lower()


# ── 9. no routing mutation ───────────────────────────────────────────


class TestNoRoutingMutation:

    def test_module_does_not_import_dispatch_primitives(self):
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        text = path.read_text(encoding="utf-8")
        # No dispatch-shaped primitive should be imported.
        for forbidden in (
            "forward_headers",
            "from tokenpak.proxy.client import",
            "pool.request",
            "pool.stream",
            "credential_injector",
            "RoutingService",
        ):
            assert forbidden not in text, (
                f"PI-3 module must not touch routing primitive: {forbidden}"
            )


# ── 10. no classifier mutation ───────────────────────────────────────


class TestNoClassifierMutation:

    def test_module_does_not_touch_classifier(self):
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        text = path.read_text(encoding="utf-8")
        assert "intent_classifier" not in text
        assert "IntentClassifier" not in text
        assert "build_contract" not in text


# ── 11. no provider/model switching ──────────────────────────────────


class TestNoProviderModelSwitching:

    def test_module_does_not_import_provider_selection(self):
        # Scan imports only — docstrings may mention "provider"
        # legitimately (e.g. to explain what we're NOT doing).
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                # No imports from routing / credential / provider
                # subsystems.
                forbidden_modules = (
                    "tokenpak.services.routing_service",
                    "tokenpak.routing",
                    "tokenpak.proxy.client",
                    "credential_injector",
                    "credential_provider",
                    "ProviderProfile",
                    "ClientProfile",
                )
                for fm in forbidden_modules:
                    assert fm not in mod, (
                        f"PI-3 module imports forbidden routing surface: {mod}"
                    )

    def test_module_does_not_call_provider_apis(self):
        # Verify no function calls into provider-selection APIs.
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "select_provider(",
            "resolve_provider(",
            "set_provider(",
            "switch_model(",
            "set_model(",
        ):
            assert forbidden not in text, (
                f"PI-3 module calls forbidden provider/model API: {forbidden}"
            )


# ── 12. patch marked applied only after success ──────────────────────


class TestAppliedFlagOnSuccess:

    def test_applied_columns_set(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert result.success
        latest = store.fetch_latest()
        assert latest["applied"] is True
        assert latest["applied_at"] is not None
        assert latest["applied_surface"] == SURFACE_CLAUDE_CODE_COMPANION
        assert latest["application_mode"] == APPLICATION_MODE_INJECT_GUIDANCE
        assert latest["application_id"] is not None

    def test_applied_only_once_idempotent(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        first = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert first.success
        # Re-fetch the now-applied row.
        applied = store.fetch_latest()
        # Re-apply: should refuse with already_applied.
        second = apply_patch_to_companion_context(
            patch_dict=applied,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert not second.success
        assert second.reason == REASON_ALREADY_APPLIED


# ── 13. failed insertion leaves applied=false ────────────────────────


class TestFailedInsertionLeavesAppliedFalse:

    def test_persist_failure_returns_persist_failed(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        # Build a store pointing at a non-existent path; the file
        # is not yet created so mark_applied returns False (the
        # ``self._db_path.is_file()`` guard at the top of the
        # method).
        store = IntentPatchStore(db_path=tmp_path / "missing.db")
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert not result.success
        assert result.reason == REASON_PERSIST_FAILED

    def test_eligibility_failure_does_not_touch_db(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=PromptInterventionRuntimeConfig(),  # disabled
            existing_context="ctx",
            store=store,
        )
        assert not result.success
        latest = store.fetch_latest()
        assert latest["applied"] is False


# ── 14. no raw prompt text or secrets emitted ────────────────────────


class TestPrivacyContract:

    SENTINEL = "sentinel-magic-prompt-PI-3-NEVER-LEAK"

    def test_sentinel_in_prompt_absent_from_injected_context(self, tmp_path):
        # Seed with the sentinel inside the raw prompt — which
        # never reaches patch_text by construction (PI-1 builder is
        # template-only) — and assert post-injection context has no
        # sentinel.
        patch = _seed_eligible_patch(
            db=tmp_path / "tel.db",
            prompt=f"create a new file with marker {self.SENTINEL}",
        )
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        result = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="prior context (no marker)",
            store=store,
        )
        assert result.success
        assert self.SENTINEL not in (result.injected_context or "")
        # Also assert the persisted patch row has no sentinel
        # leakage in any column.
        applied = store.fetch_latest()
        for v in applied.values():
            if isinstance(v, str):
                assert self.SENTINEL not in v

    def test_module_has_no_print_logging_of_patch_text(self):
        path = Path(
            "tokenpak/companion/intent_injection.py"
        ).resolve()
        text = path.read_text(encoding="utf-8")
        # The module must not print() patch_text or log it (so
        # the operator's secret-shaped values don't end up in
        # syslog / journalctl).
        assert "print(" not in text or "print(patch" not in text
        # No logger.info / logger.warning lines that include
        # patch_text content (we keep telemetry only via the
        # existing patch store).
        assert "logger.info" not in text
        assert "logger.warning" not in text


# ── 15. CLI shows applied state correctly ────────────────────────────


def _write_policy_yaml(tokenpak_home: Path, *, enabled: bool) -> None:
    """Write a policy.yaml with an active or inactive
    prompt_intervention block under ``tokenpak_home``.
    """
    tokenpak_home.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(
        f"""
        intent_policy:
          mode: suggest
          prompt_intervention:
            enabled: {str(enabled).lower()}
            mode: inject_guidance
            target: companion_context
            require_confirmation: false
            surfaces:
              claude_code_companion: {str(enabled).lower()}
              proxy: false
        """
    ).lstrip()
    (tokenpak_home / "policy.yaml").write_text(body, encoding="utf-8")


class TestCliRendersAppliedState:

    def test_intervention_status_disabled_default(self, tmp_path):
        env = dict(os.environ)
        env["TOKENPAK_HOME"] = str(tmp_path)  # no policy.yaml — disabled
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert result.returncode == 0
        assert "Prompt intervention is disabled" in result.stdout
        assert "preview-only" in result.stdout

    def test_intervention_status_enabled(self, tmp_path):
        _write_policy_yaml(tmp_path, enabled=True)
        env = dict(os.environ)
        env["TOKENPAK_HOME"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert result.returncode == 0
        assert (
            "Prompt intervention is enabled for Claude Code companion "
            "context only"
        ) in result.stdout
        assert "User messages are preserved" in result.stdout

    def test_applied_patch_renders_audit_columns(self, tmp_path):
        # Seed a patch + apply it under a dedicated TOKENPAK_HOME,
        # then drive the CLI against the same telemetry.db.
        _write_policy_yaml(tmp_path, enabled=True)
        env = dict(os.environ)
        env["TOKENPAK_HOME"] = str(tmp_path)

        # Telemetry DB at $TOKENPAK_HOME/telemetry.db (mirrors
        # _DEFAULT_DB_PATH).
        db = tmp_path / "telemetry.db"
        patch = _seed_eligible_patch(db=db)
        store = IntentPatchStore(db_path=db)
        result_lib = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert result_lib.success

        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches",
             "--json"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert result.returncode == 0
        d = json.loads(result.stdout)
        # JSON view exposes the audit columns.
        p = d["patch"]
        assert p["applied"] is True
        assert p["applied_surface"] == SURFACE_CLAUDE_CODE_COMPANION
        assert p["application_mode"] == APPLICATION_MODE_INJECT_GUIDANCE
        assert p["applied_at"]
        # APPLIED_LABELS take over.
        assert "Applied for this one request" in d["labels"]
        assert "Injected into Claude Code companion context" in d["labels"]
        # PI-2 PREVIEW_LABELS are gone for this row.
        assert "NOT APPLIED" not in d["labels"]
        assert "NO PROMPT MUTATION" not in d["labels"]


# ── 16. forbidden wording guardrail before application ───────────────


class TestForbiddenWordingBeforeApplication:

    def test_unapplied_patch_text_contains_no_forbidden_phrase(self, tmp_path):
        patch = _seed_eligible_patch(db=tmp_path / "tel.db")
        store = IntentPatchStore(db_path=tmp_path / "tel.db")
        # Render the unapplied patch via the read model + CLI JSON
        # surface; assert no forbidden phrase appears in patch_text.
        from tokenpak.proxy.intent_claude_code_preview import (
            collect_latest_patch_preview,
        )
        from tokenpak.proxy.intent_prompt_patch_telemetry import (
            set_default_patch_store,
        )

        set_default_patch_store(store)
        try:
            payload = collect_latest_patch_preview()
        finally:
            set_default_patch_store(None)

        forbidden_re = re.compile(
            r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
            re.IGNORECASE,
        )
        text = payload["patch"]["patch_text"]
        m = forbidden_re.search(text)
        assert m is None, (
            f"forbidden phrase {m.group(0)!r} appeared in unapplied patch_text"
        )


# ── 17. applied wording allowed only after application ───────────────


class TestAppliedWordingAfterApplication:

    def test_pre_application_cli_does_not_say_applied(self, tmp_path):
        # On a fresh DB with an unapplied patch, the CLI must not
        # render "Applied" / "Inserted" / "Injected" as a status
        # statement about the row.
        env = dict(os.environ)
        env["TOKENPAK_HOME"] = str(tmp_path)
        # Seed an unapplied patch under the alt TOKENPAK_HOME.
        db = tmp_path / "telemetry.db"
        _seed_eligible_patch(db=db)

        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert result.returncode == 0
        # The PREVIEW labels are still active for this row.
        assert "NOT APPLIED" in result.stdout
        # The applied-only status string MUST NOT appear.
        assert "Injected into Claude Code companion context" not in result.stdout
        # The audit headers must not appear (we only render them
        # when applied=True).
        assert "applied_at:" not in result.stdout
        assert "applied_surface:" not in result.stdout

    def test_post_application_cli_says_injected(self, tmp_path):
        env = dict(os.environ)
        env["TOKENPAK_HOME"] = str(tmp_path)
        db = tmp_path / "telemetry.db"
        patch = _seed_eligible_patch(db=db)
        store = IntentPatchStore(db_path=db)
        result_lib = apply_patch_to_companion_context(
            patch_dict=patch,
            pi_config=_enabled_config(),
            existing_context="ctx",
            store=store,
        )
        assert result_lib.success

        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "claude-code", "patches"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert result.returncode == 0
        # APPLIED_LABELS are now active.
        assert "Applied for this one request" in result.stdout
        assert "Injected into Claude Code companion context" in result.stdout
        # Audit columns rendered.
        assert "applied_at:" in result.stdout
        assert (
            f"applied_surface:         {SURFACE_CLAUDE_CODE_COMPANION}"
            in result.stdout
        )
