# SPDX-License-Identifier: Apache-2.0
"""Media, observed usage, and held-request approval regressions (offline)."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokenpak.proxy.spend_guard import orchestrator, usage_anchor
from tokenpak.proxy.spend_guard.approvals import ContextApprovals, scope_key
from tokenpak.proxy.spend_guard.estimator import estimate
from tokenpak.proxy.spend_guard.media import document_tokens, image_dimensions, image_tokens
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
from tokenpak.proxy.spend_guard.tip_header import parse_and_strip_tip_header

from .media_fixtures import IMAGE_SHAPES, image_block, synthetic_image, workload

MODEL = "claude-opus-5-5"


def wire(value):
    return json.dumps(value, separators=(",", ":")).encode()


def body(tokens=910, text=None):
    return {
        "model": MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": text if text is not None else "x" * (tokens * 4)}],
    }


def reply(original, text):
    result = json.loads(wire(original))
    result["messages"] += [
        {"role": "assistant", "content": "approval required"},
        {"role": "user", "content": text},
    ]
    return result


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    usage_anchor.reset_for_testing()
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda model: 1000
    )
    config = SpendGuardConfig()
    config.audit_db_path = str(tmp_path / "guard.db")
    config.rolling_caps_enabled = False
    yield config
    usage_anchor.reset_for_testing()


def run(value, cfg, session="session-A", headers=None, model=MODEL):
    return orchestrator.evaluate(wire(value), model, session, headers or {}, config=cfg)


def expire(cfg):
    with sqlite3.connect(cfg.audit_db_path) as db:
        db.execute("UPDATE pending_requests SET expires_at = 0, status = 'expired'")


@pytest.mark.parametrize("kind,width,height,size", IMAGE_SHAPES)
def test_synthetic_header_dimensions_and_size(kind, width, height, size):
    data = synthetic_image(kind, width, height, size)
    assert len(data) == size
    assert image_dimensions(data) == (width, height)


@pytest.mark.parametrize("anthropic,model", [(True, MODEL), (False, "gpt-6-astra")])
def test_image_tokens_do_not_depend_on_encoded_bytes(anthropic, model):
    small, large = image_block(size=38520), image_block(size=385200)
    if not anthropic:
        small = {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + small["source"]["data"],
        }
        large = {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + large["source"]["data"],
        }
    assert image_tokens(small, model, anthropic=anthropic) == image_tokens(
        large, model, anthropic=anthropic
    )


def test_published_image_examples_and_caps():
    for w, h, expected in [(200, 200, 64), (1000, 1000, 1296), (1920, 1080, 1560)]:
        assert (
            image_tokens(
                image_block(width=w, height=h, size=385200), "claude-sonnet-4-5", anthropic=True
            )
            == expected
        )
    assert (
        image_tokens(image_block(width=3840, height=2160, size=385200), MODEL, anthropic=True)
        == 4784
    )
    for w, h, expected in [(1024, 1024, 1229), (2048, 2048, 3000), (4096, 512, 2458)]:
        block = image_block(width=w, height=h, size=385200)
        block = {
            "type": "input_image",
            "detail": "high",
            "image_url": "data:image/jpeg;base64," + block["source"]["data"],
        }
        assert image_tokens(block, "gpt-6-astra", anthropic=False) == expected
    assert image_tokens({}, MODEL, anthropic=True) == 4784


def test_extreme_aspect_ratio_and_unknown_model_use_conservative_caps():
    data = synthetic_image("jpeg", 65535, 33, 385200)
    block = {"type": "input_image", "detail": "high", "image_url": "data:image/jpeg;base64," + data}
    # Published resize formula: 63,549 x 32 => 1,986 patches * 1.2.
    assert image_tokens(block, "gpt-6-astra", anthropic=False) == 2384
    assert image_tokens(block, "gpt-6-unknown", anthropic=False) == 48169


def test_truncated_webp_uses_unknown_dimensions():
    assert image_dimensions(base64.b64encode(b"RIFFxxxxWEBPVP8X").decode()) is None


def test_duplicate_images_are_counted_twice_and_top_level_matches_nested():
    image = image_block()
    top = body(text=[image])
    nested = body(text=[{"type": "tool_result", "content": [image]}])
    double = body(text=[image, image])
    count = estimate(wire(top), MODEL).projected_input_tokens
    assert count > 0
    assert estimate(wire(nested), MODEL).projected_input_tokens >= count
    assert estimate(wire(double), MODEL).projected_input_tokens == 2 * count


def test_documents_count_pages_without_encoded_length():
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Pages /Count 2 >> endobj\n%%EOF"

    def block(raw):
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(raw).decode(),
            },
        }

    assert document_tokens(block(pdf), MODEL, anthropic=True) == 2 * (3000 + 4784)
    assert document_tokens(
        block(pdf.replace(b"%%EOF", b" " * 20000 + b"%%EOF")), MODEL, anthropic=True
    ) == 2 * (3000 + 4784)
    assert document_tokens({}, MODEL, anthropic=True) == 600 * (3000 + 4784)


def test_thinking_and_redacted_thinking_are_counted():
    value = body(
        text=[
            {"type": "thinking", "thinking": "x" * 400},
            {"type": "redacted_thinking", "data": "y" * 400},
        ]
    )
    assert estimate(wire(value), MODEL).projected_input_tokens == 200


def test_media_shaped_tool_arguments_are_counted_as_json_text():
    argument = {"type": "image", "source": {"data": "x" * 100000}}
    value = body(text=[{"type": "tool_use", "input": argument}])
    assert estimate(wire(value), MODEL).projected_input_tokens >= 25000


def test_codex_responses_usage_anchor():
    usage_anchor.reset_for_testing()
    original = {"input": [{"role": "user", "content": "synthetic"}]}
    usage_anchor.record_completed(
        wire(original),
        "gpt-6-astra",
        "codex",
        {"input_tokens": 500},
        provider="openai-codex",
        started_at=1,
    )
    current = {"input": [*original["input"], {"role": "user", "content": "x" * 400}]}
    assert usage_anchor.projection_floor(wire(current), "gpt-6-astra", "codex") >= 600
    usage_anchor.reset_for_testing()


def test_53_image_workload_uses_usage_anchor_and_allows(tmp_path):
    usage_anchor.reset_for_testing()
    value = workload()
    before = {**value, "messages": value["messages"][:-1]}
    usage_anchor.record_completed(
        wire(before),
        MODEL,
        "fixture",
        {
            "input_tokens": 25645,
            "cache_read_input_tokens": 524124,
            "cache_creation_input_tokens": 1616,
        },
        provider="anthropic",
        started_at=1,
    )
    config = SpendGuardConfig()
    config.audit_db_path = str(tmp_path / "guard.db")
    config.rolling_caps_enabled = False
    outcome = run(value, config, session="fixture")
    assert outcome.kind in ("forward", "forward_modified")
    assert 0.8 * 551385 <= outcome.decision.risk.projected_input_tokens <= 1.5 * 551385
    assert sum(row[3] for row in IMAGE_SHAPES) == 2718696
    usage_anchor.reset_for_testing()


def test_usage_anchor_suffix_identity_cache_buckets_and_old_completion():
    usage_anchor.reset_for_testing()
    original = body(tokens=10)
    usage_anchor.record_completed(
        wire(original),
        MODEL,
        "A",
        {"input_tokens": 20, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 80},
        provider="anthropic",
        started_at=2,
    )
    value = reply(original, "x" * 400)
    floor = usage_anchor.projection_floor(wire(value), MODEL, "A")
    assert floor >= 600
    assert (
        estimate(wire(value), MODEL, fallback_session_tokens=floor).projected_input_tokens >= floor
    )
    assert usage_anchor.projection_floor(wire(value), MODEL, "B") == 0
    assert usage_anchor.projection_floor(wire(value), "other-model", "A") == 0
    usage_anchor.record_completed(
        wire(original), MODEL, "A", {"input_tokens": 1}, provider="anthropic", started_at=1
    )
    assert usage_anchor.projection_floor(wire(value), MODEL, "A") == floor
    usage_anchor.record_completed(
        wire(original),
        "gpt-6-astra",
        "A",
        {"input_tokens": 500, "input_tokens_details": {"cached_tokens": 450}},
        provider="openai",
        started_at=3,
    )
    assert usage_anchor.projection_floor(wire(original), "gpt-6-astra", "A") == 500
    usage_anchor.reset_for_testing()


def test_latest_user_tip_and_first_segment_rule(cfg):
    value = reply(body(), "[TIP: allow=once] continue")
    directive, stripped = parse_and_strip_tip_header(wire(value))
    assert directive.allow_scope == "once"
    assert json.loads(stripped)["messages"][-1]["content"] == "continue"
    value["messages"][-1]["content"] = [
        {"type": "text", "text": "human text"},
        {"type": "text", "text": "[TIP: allow=session]"},
    ]
    assert parse_and_strip_tip_header(wire(value))[0] is None
    assert run(reply(body(), "[TIP: allow=once] continue"), cfg).kind == "forward_modified"


@pytest.mark.parametrize(
    "text",
    [
        "<task-notification>yes</task-notification>",
        "<system-reminder>no</system-reminder>",
        [{"type": "tool_result", "content": "yes"}],
    ],
)
def test_background_during_hold_repeats_exact_original_response(cfg, text):
    held = body()
    blocked = run(held, cfg)
    for _ in range(3):
        response = run(reply(held, text), cfg)
        assert response.response_body == blocked.response_body
        assert response.kind == "block"
        assert PendingStore(cfg.audit_db_path).get_by_session("session-A") is not None


def test_late_yes_approves_current_once_and_never_replays_expired_bytes(cfg):
    held = body()
    blocked = run(held, cfg)
    expire(cfg)
    current = reply(held, "yes")
    approved = run(current, cfg)
    assert approved.kind in ("forward", "forward_modified")
    assert approved.body == wire(current)
    assert approved.body != wire(held)
    assert PendingStore(cfg.audit_db_path).get_by_id(blocked.pending_id).status == "consumed"
    assert run(current, cfg).kind == "block"
    assert run(current, cfg).kind == "block"


def test_expired_projection_growth_blocks_and_hard_stop_survives(cfg):
    held = body(tokens=810)
    cfg.default_context_window_percent = 80
    assert run(held, cfg).kind == "block"
    expire(cfg)
    current = reply(body(tokens=930), "yes")
    result = run(current, cfg)
    assert result.kind == "block"
    assert "earlier hold expired" in json.loads(result.response_body)["error"]["message"]
    assert run(current, cfg).kind == "block"
    expire(cfg)
    assert run(reply(body(tokens=1100), "yes"), cfg).kind == "hard_block"


@pytest.mark.parametrize("scope", ["15m", "session"])
def test_persistent_context_grants_and_other_bands(cfg, scope):
    assert run(body(text=f"[TIP: allow={scope}] " + "x" * 3640), cfg).kind == "forward_modified"
    assert run(body(tokens=920), cfg).kind in ("forward", "forward_modified")
    assert run(body(tokens=1000), cfg).kind == "hard_block"
    assert run(body(tokens=920), cfg, session="B").kind == "block"
    cfg.block_cost_usd = 0.000001
    assert run(body(tokens=930), cfg).kind == "block"


def test_15m_expiry_and_cancel_revoke(cfg, monkeypatch):
    run(body(text="[TIP: allow=15m] " + "x" * 3640), cfg)
    now = time.time()
    monkeypatch.setattr("tokenpak.proxy.spend_guard.approvals.time.time", lambda: now + 901)
    assert run(body(tokens=920), cfg).kind == "block"


def test_session_grant_redeems_after_reopening_store_and_tip_cancel(cfg):
    run(body(text="[TIP: allow=session] " + "x" * 3640), cfg)
    assert ContextApprovals(PendingStore(cfg.audit_db_path).path).redeem(
        scope_key("session-A", MODEL, {}), estimate(wire(body()), MODEL)
    )
    run(body(text="[TIP: cancel]"), cfg)
    assert run(body(tokens=920), cfg).kind == "block"


@pytest.mark.parametrize("expired", [False, True])
def test_persistent_tip_on_hold_is_saved_and_preserves_appropriate_bytes(cfg, expired):
    held = body()
    assert run(held, cfg).kind == "block"
    if expired:
        expire(cfg)
    current = reply(held, "[TIP: allow=session] yes")
    result = run(current, cfg)
    assert result.kind == ("forward_modified" if expired else "replay")
    if not expired:
        assert result.body == wire(held)
    assert run(body(tokens=930), cfg).kind in ("forward", "forward_modified")


def test_persistent_tip_on_hold_does_not_cross_session_cost_cap(cfg, monkeypatch):
    held = body()
    assert run(held, cfg).kind == "block"
    cfg.session_block_cost_usd = 1
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.session_state.session_cumulative_cost", lambda *a, **k: 2
    )
    assert run(reply(held, "[TIP: allow=session] yes"), cfg).kind == "block"
    assert not ContextApprovals(PendingStore(cfg.audit_db_path).path).redeem(
        scope_key("session-A", MODEL, {}), estimate(wire(body()), MODEL)
    )


def test_persistent_tip_on_hold_does_not_cross_rolling_cap(cfg, monkeypatch):
    from types import SimpleNamespace

    held = body()
    headers = {"X-TokenPak-Agent": "fixture"}
    assert run(held, cfg, headers=headers).kind == "block"
    cfg.rolling_caps_enabled = True
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.rolling_caps.record_session_agent", lambda *a: None
    )
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.rolling_caps.check_rolling_caps_and_admit",
        lambda **kw: (SimpleNamespace(cap_dimension="cost"), None),
    )
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.block_response.build_rolling_cap_block",
        lambda breach: b"blocked",
    )
    assert run(reply(held, "[TIP: allow=15m] yes"), cfg, headers=headers).kind == "block"


def test_bare_yes_replays_one_held_body_without_session_grant(cfg):
    original = body()
    assert run(original, cfg).kind == "block"
    approved = run(reply(original, "yes"), cfg)
    assert approved.kind == "replay" and approved.body == wire(original)
    assert run(body(tokens=920), cfg).kind == "block"


def test_expired_approval_atomic_redemption(cfg):
    blocked = run(body(), cfg)
    expire(cfg)
    path = PendingStore(cfg.audit_db_path).path

    def redeem(_):
        return ContextApprovals(path).consume_expired(
            scope_key("session-A", MODEL, {}), blocked.pending_id, "synthetic-reply"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(redeem, range(8))) == 1
