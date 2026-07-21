"""Deterministic planner and managed-state tests for MemoryGuard optimization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tokenpak.services import memory_optimization as optimizer


def _facts(memory_mib: int = 8192) -> optimizer.HostFacts:
    return optimizer.HostFacts(
        platform="linux",
        cpu_count=8,
        physical_memory_bytes=memory_mib * optimizer.MIB,
        cgroup_memory_limit_bytes=None,
        effective_memory_bytes=memory_mib * optimizer.MIB,
        memory_limit_source="physical",
    )


def test_canonical_json_is_stable_and_rejects_floats() -> None:
    left = optimizer.canonical_json_bytes({"z": 1, "a": [True, None, "x"]})
    right = optimizer.canonical_json_bytes({"a": [True, None, "x"], "z": 1})
    assert left == right == b'{"a":[true,null,"x"],"z":1}'
    with pytest.raises(TypeError, match="non-canonical"):
        optimizer.canonical_json_bytes({"share": 0.3})


def test_balanced_plan_golden_vector() -> None:
    plan = optimizer.build_plan(_facts(), profile="balanced", mode="auto")
    assert plan.as_dict() == {
        "facts": {
            "cgroup_limits": [],
            "cgroup_memory_limit_bytes": None,
            "cpu_count": 8,
            "effective_memory_bytes": 8589934592,
            "memory_limit_source": "physical",
            "physical_memory_bytes": 8589934592,
            "platform": "linux",
        },
        "memory_guard": {
            "budget_mb": 2048,
            "ceiling_mb": 1843,
            "check_interval_secs": 30,
            "cooldown_secs": 300,
            "enabled": True,
            "mode": "auto",
            "sys_low_mb": 655,
            "target_mb": 1433,
        },
        "mode": "auto",
        "policy_version": "memory-optimizer-1",
        "profile": "balanced",
        "schema_version": 1,
        "scope": "process",
        "support_reason": None,
        "supported": True,
    }
    wrapper = optimizer.wrap_plan(plan)
    assert (
        wrapper["plan_sha256"]
        == hashlib.sha256(optimizer.canonical_json_bytes(wrapper["plan"])).hexdigest()
    )


def test_low_memory_boundary_is_pinned_after_mib_quantization() -> None:
    boundary = optimizer.build_plan(_facts(640), profile="conservative", mode="observe")
    below = optimizer.build_plan(_facts(639), profile="conservative", mode="observe")
    assert boundary.supported is True
    assert (boundary.budget_mb, boundary.target_mb, boundary.ceiling_mb) == (128, 89, 115)
    assert below.supported is False
    assert below.budget_mb == 127


def test_off_plan_is_supported_without_thresholds() -> None:
    plan = optimizer.build_plan(_facts(128), profile="balanced", mode="off")
    assert plan.supported is True
    assert plan.as_dict()["memory_guard"] == {
        "budget_mb": None,
        "ceiling_mb": None,
        "check_interval_secs": 30,
        "cooldown_secs": 300,
        "enabled": False,
        "mode": "off",
        "sys_low_mb": None,
        "target_mb": None,
    }


def test_cgroup_v2_walks_ancestors_and_uses_smallest_limit(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    leaf = root / "user.slice" / "session.scope"
    leaf.mkdir(parents=True)
    (root / "memory.max").write_text(str(4 * 1024 * optimizer.MIB))
    (root / "user.slice" / "memory.max").write_text("max")
    (leaf / "memory.max").write_text(str(2 * 1024 * optimizer.MIB))
    membership = tmp_path / "cgroup-membership"
    membership.write_text("0::/user.slice/session.scope\n")

    facts = optimizer.probe_host_facts(
        physical_memory_bytes=8 * 1024 * optimizer.MIB,
        cpu_count=4,
        platform_name="linux",
        cgroup_root=root,
        proc_self_cgroup=membership,
    )

    assert facts.cgroup_memory_limit_bytes == 2 * 1024 * optimizer.MIB
    assert facts.effective_memory_bytes == 2 * 1024 * optimizer.MIB
    assert facts.memory_limit_source == "cgroup_v2"
    assert [item.limit_bytes for item in facts.cgroup_limits] == [
        2 * 1024 * optimizer.MIB,
        4 * 1024 * optimizer.MIB,
    ]


def test_cgroup_v2_limit_above_physical_keeps_physical_effective(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "memory.max").write_text(str(16 * 1024 * optimizer.MIB))
    membership = tmp_path / "cgroup-membership"
    membership.write_text("0::/\n")
    facts = optimizer.probe_host_facts(
        physical_memory_bytes=8 * 1024 * optimizer.MIB,
        platform_name="linux",
        cgroup_root=root,
        proc_self_cgroup=membership,
    )
    assert facts.cgroup_memory_limit_bytes == 16 * 1024 * optimizer.MIB
    assert facts.effective_memory_bytes == 8 * 1024 * optimizer.MIB
    assert facts.memory_limit_source == "physical"


def test_cgroup_v1_exact_sentinel_is_unlimited(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    leaf = root / "memory" / "session"
    leaf.mkdir(parents=True)
    (leaf / "memory.limit_in_bytes").write_text("9223372036854771712")
    (root / "memory" / "memory.limit_in_bytes").write_text(str(1024 * optimizer.MIB))
    membership = tmp_path / "cgroup-membership"
    membership.write_text("7:memory:/session\n")
    facts = optimizer.probe_host_facts(
        physical_memory_bytes=8 * 1024 * optimizer.MIB,
        platform_name="linux",
        cgroup_root=root,
        proc_self_cgroup=membership,
    )
    assert facts.cgroup_memory_limit_bytes == 1024 * optimizer.MIB
    assert len(facts.cgroup_limits) == 1


def test_wrapper_validation_rejects_hash_and_schema_drift() -> None:
    wrapper = optimizer.wrap_plan(optimizer.build_plan(_facts()))
    wrapper["plan"]["profile"] = "throughput"
    with pytest.raises(optimizer.CorruptManagedConfigError, match="SHA-256"):
        optimizer.validate_plan_wrapper(optimizer.canonical_json_bytes(wrapper))

    wrapper = optimizer.wrap_plan(optimizer.build_plan(_facts()))
    wrapper["plan"]["schema_version"] = 99
    wrapper["plan_sha256"] = optimizer.sha256_bytes(optimizer.canonical_json_bytes(wrapper["plan"]))
    with pytest.raises(optimizer.CorruptManagedConfigError, match="schema_version"):
        optimizer.validate_plan_wrapper(optimizer.canonical_json_bytes(wrapper))


def test_apply_status_and_rollback_absent_preimage(tmp_path: Path) -> None:
    result = optimizer.apply_plan(home=tmp_path, facts=_facts(), profile="balanced", mode="auto")
    paths = optimizer.managed_paths(tmp_path)
    assert result["changed"] is True
    assert paths.config.exists()
    assert paths.preimage.exists()
    assert paths.lock.exists()
    status = optimizer.optimizer_status(home=tmp_path)
    assert status["state"] == "clean"
    assert status["config"]["valid"] is True
    assert status["lock"]["held"] is False

    rolled_back = optimizer.rollback_plan(home=tmp_path)
    assert rolled_back == {"changed": True, "restored": "absent"}
    assert not paths.config.exists()
    assert not paths.preimage.exists()
    assert paths.lock.exists()


def test_apply_is_idempotent_and_preserves_receipt(tmp_path: Path) -> None:
    optimizer.apply_plan(home=tmp_path, facts=_facts())
    paths = optimizer.managed_paths(tmp_path)
    receipt_before = paths.preimage.read_bytes()
    result = optimizer.apply_plan(home=tmp_path, facts=_facts())
    assert result["changed"] is False
    assert paths.preimage.read_bytes() == receipt_before


def test_interrupted_apply_is_classified_and_rollback_retires_receipt(tmp_path: Path) -> None:
    optimizer.apply_plan(home=tmp_path, facts=_facts())
    paths = optimizer.managed_paths(tmp_path)
    paths.config.unlink()
    assert optimizer.optimizer_status(home=tmp_path)["state"] == "interrupted_apply"
    result = optimizer.rollback_plan(home=tmp_path)
    assert result == {"changed": False, "restored": "absent"}
    assert not paths.preimage.exists()


def test_external_drift_refuses_rollback_until_forced(tmp_path: Path) -> None:
    optimizer.apply_plan(home=tmp_path, facts=_facts())
    paths = optimizer.managed_paths(tmp_path)
    paths.config.write_text("external change\n")
    assert optimizer.optimizer_status(home=tmp_path)["state"] == "external_drift"
    with pytest.raises(optimizer.RollbackRefusedError, match="--force"):
        optimizer.rollback_plan(home=tmp_path)
    result = optimizer.rollback_plan(home=tmp_path, force=True)
    assert result == {"changed": True, "restored": "absent"}
    assert not paths.config.exists()
    assert not paths.preimage.exists()


def test_apply_expect_hash_refuses_changed_plan(tmp_path: Path) -> None:
    with pytest.raises(optimizer.ApplyRefusedError, match="expect-hash"):
        optimizer.apply_plan(
            home=tmp_path,
            facts=_facts(),
            expect_hash="0" * 64,
        )
    assert not optimizer.managed_paths(tmp_path).config.exists()


def test_status_is_read_only_when_home_is_absent(tmp_path: Path) -> None:
    home = tmp_path / "missing"
    before = list(tmp_path.rglob("*"))
    status = optimizer.optimizer_status(home=home)
    after = list(tmp_path.rglob("*"))
    assert status["state"] == "absent"
    assert before == after


def test_apply_refuses_corrupt_existing_config(tmp_path: Path) -> None:
    paths = optimizer.managed_paths(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    paths.config.write_text("not-json\n")
    with pytest.raises(optimizer.ApplyRefusedError, match="corrupt_config"):
        optimizer.apply_plan(home=tmp_path, facts=_facts())
    assert paths.config.read_text() == "not-json\n"


def test_plan_file_bytes_round_trip() -> None:
    data = optimizer.plan_file_bytes(optimizer.build_plan(_facts(), mode="observe"))
    plan = optimizer.validate_plan_wrapper(data)
    assert plan["mode"] == "observe"
    assert json.loads(data)["plan_sha256"] == optimizer.sha256_bytes(
        optimizer.canonical_json_bytes(plan)
    )
