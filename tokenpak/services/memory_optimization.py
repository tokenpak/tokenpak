"""Deterministic, process-local MemoryGuard planning and managed state.

This module owns the pure host-facts -> optimization-plan calculation and the
small reversible state machine used by ``tokenpak config optimize``.  It never
changes operating-system policy.  The only mutable artifacts are TokenPak-owned
files under the resolved TokenPak home.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import platform
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, cast

from tokenpak import _paths

__all__: list[str] = []

MIB = 1024 * 1024
SCHEMA_VERSION = 1
POLICY_VERSION = "memory-optimizer-1"

CONFIG_FILENAME = "memory-optimization.json"
PREIMAGE_FILENAME = "memory-optimization.preimage.json"
LOCK_FILENAME = ".memory-optimization.lock"

EXIT_OK = 0
EXIT_UNSUPPORTED = 2
EXIT_APPLY_REFUSED = 3
EXIT_ROLLBACK_REFUSED = 4
EXIT_CORRUPT = 5

_V1_UNLIMITED_SENTINELS = frozenset(
    {
        9223372036854771712,
        9223372036854775807,
        18446744073709551615,
    }
)

_PROFILE_POLICY: Mapping[str, tuple[int, int, int]] = {
    # name: (host-share numerator, denominator, budget cap MiB)
    "conservative": (20, 100, 1024),
    "balanced": (30, 100, 2048),
    "throughput": (40, 100, 4096),
}
MODES = frozenset({"off", "observe", "auto"})
PROFILES = frozenset(_PROFILE_POLICY)


class OptimizationError(RuntimeError):
    """Base class for deterministic optimizer failures."""


class UnsupportedHostError(OptimizationError):
    """Raised when the host cannot safely support the selected plan."""


class ApplyRefusedError(OptimizationError):
    """Raised when apply would overwrite ambiguous or drifted state."""


class RollbackRefusedError(OptimizationError):
    """Raised when rollback cannot prove ownership of the current bytes."""


class CorruptManagedConfigError(OptimizationError):
    """Raised when managed bytes fail schema, hash, or invariant checks."""


class _MsvcrtLocking(Protocol):
    LK_LOCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int) -> None: ...


def _managed_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CorruptManagedConfigError(f"managed MemoryGuard {field} must be an integer")
    return value


@dataclass(frozen=True)
class CgroupLimit:
    """One finite cgroup memory limit and its on-disk provenance."""

    source: str
    path: str
    limit_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit_bytes": self.limit_bytes,
            "path": self.path,
            "source": self.source,
        }


@dataclass(frozen=True)
class HostFacts:
    """Normalized facts that fully explain a generated plan."""

    platform: str
    cpu_count: int
    physical_memory_bytes: int
    cgroup_memory_limit_bytes: int | None
    effective_memory_bytes: int
    memory_limit_source: str
    cgroup_limits: tuple[CgroupLimit, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "cgroup_limits": [item.as_dict() for item in self.cgroup_limits],
            "cgroup_memory_limit_bytes": self.cgroup_memory_limit_bytes,
            "cpu_count": self.cpu_count,
            "effective_memory_bytes": self.effective_memory_bytes,
            "memory_limit_source": self.memory_limit_source,
            "physical_memory_bytes": self.physical_memory_bytes,
            "platform": self.platform,
        }


@dataclass(frozen=True)
class OptimizationPlan:
    """Immutable deterministic MemoryGuard plan."""

    profile: str
    mode: str
    facts: HostFacts
    supported: bool
    support_reason: str | None
    budget_mb: int | None
    target_mb: int | None
    ceiling_mb: int | None
    sys_low_mb: int | None
    check_interval_secs: int = 30
    cooldown_secs: int = 300

    def as_dict(self) -> dict[str, Any]:
        enabled = self.supported and self.mode in {"observe", "auto"}
        return {
            "facts": self.facts.as_dict(),
            "memory_guard": {
                "budget_mb": self.budget_mb,
                "ceiling_mb": self.ceiling_mb,
                "check_interval_secs": self.check_interval_secs,
                "cooldown_secs": self.cooldown_secs,
                "enabled": enabled,
                "mode": self.mode,
                "sys_low_mb": self.sys_low_mb,
                "target_mb": self.target_mb,
            },
            "mode": self.mode,
            "policy_version": POLICY_VERSION,
            "profile": self.profile,
            "schema_version": SCHEMA_VERSION,
            "scope": "process",
            "support_reason": self.support_reason,
            "supported": self.supported,
        }


@dataclass(frozen=True)
class ManagedPaths:
    """Enumerable TokenPak-owned optimizer artifacts."""

    home: Path
    config: Path
    preimage: Path
    lock: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "config": str(self.config),
            "lock": str(self.lock),
            "preimage": str(self.preimage),
        }


def managed_paths(home: Path | None = None) -> ManagedPaths:
    """Resolve optimizer artifacts without creating anything."""
    root = Path(home) if home is not None else _paths.home()
    return ManagedPaths(
        home=root,
        config=root / CONFIG_FILENAME,
        preimage=root / PREIMAGE_FILENAME,
        lock=root / LOCK_FILENAME,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the pinned canonical JSON representation used for plan hashes."""
    _reject_noncanonical_types(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _reject_noncanonical_types(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_noncanonical_types(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical JSON key at {path} must be a string")
            _reject_noncanonical_types(item, path=f"{path}.{key}")
        return
    raise TypeError(f"non-canonical value at {path}: {type(value).__name__}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wrap_plan(plan: OptimizationPlan | dict[str, Any]) -> dict[str, Any]:
    payload = plan.as_dict() if isinstance(plan, OptimizationPlan) else plan
    return {
        "plan": payload,
        "plan_sha256": sha256_bytes(canonical_json_bytes(payload)),
    }


def plan_file_bytes(plan: OptimizationPlan | dict[str, Any]) -> bytes:
    """Return newline-terminated deterministic managed-file bytes."""
    return canonical_json_bytes(wrap_plan(plan)) + b"\n"


def _read_physical_memory_bytes() -> int:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    value_kib = int(line.split()[1])
                    if value_kib > 0:
                        return value_kib * 1024
        except (OSError, ValueError, IndexError):
            pass

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        total = page_size * pages
        if total > 0:
            return total
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    try:
        import psutil

        total = int(psutil.virtual_memory().total)
        if total > 0:
            return total
    except (ImportError, OSError, ValueError):
        pass
    raise UnsupportedHostError("physical memory could not be measured")


def _parse_finite_limit(raw: str, *, source: str) -> int | None:
    value_text = raw.strip()
    if source == "cgroup_v2" and value_text == "max":
        return None
    try:
        value = int(value_text)
    except ValueError:
        return None
    if value <= 0 or value in _V1_UNLIMITED_SENTINELS:
        return None
    return value


def _bounded_ancestors(start: Path, root: Path) -> Iterator[Path]:
    try:
        current = start.resolve(strict=False)
        boundary = root.resolve(strict=False)
        current.relative_to(boundary)
    except (OSError, ValueError):
        return
    while True:
        yield current
        if current == boundary:
            break
        current = current.parent


def _read_cgroup_membership(path: Path) -> tuple[str | None, str | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    v2_path: str | None = None
    v1_memory_path: str | None = None
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, relative = parts
        if hierarchy == "0" and controllers == "":
            v2_path = relative
        elif "memory" in controllers.split(","):
            v1_memory_path = relative
    return v2_path, v1_memory_path


def _probe_cgroup_limits(
    *,
    cgroup_root: Path,
    proc_self_cgroup: Path,
) -> tuple[CgroupLimit, ...]:
    v2_relative, v1_relative = _read_cgroup_membership(proc_self_cgroup)
    found: list[CgroupLimit] = []

    if v2_relative is not None:
        start = cgroup_root / v2_relative.lstrip("/")
        for directory in _bounded_ancestors(start, cgroup_root):
            candidate = directory / "memory.max"
            try:
                raw = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            limit = _parse_finite_limit(raw, source="cgroup_v2")
            if limit is not None:
                found.append(CgroupLimit("cgroup_v2", str(candidate), limit))
        return tuple(found)

    if v1_relative is not None:
        roots = (cgroup_root / "memory", cgroup_root)
        for controller_root in roots:
            start = controller_root / v1_relative.lstrip("/")
            for directory in _bounded_ancestors(start, controller_root):
                candidate = directory / "memory.limit_in_bytes"
                try:
                    raw = candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
                limit = _parse_finite_limit(raw, source="cgroup_v1")
                if limit is not None:
                    found.append(CgroupLimit("cgroup_v1", str(candidate), limit))
            if found:
                break
    return tuple(found)


def probe_host_facts(
    *,
    physical_memory_bytes: int | None = None,
    cpu_count: int | None = None,
    platform_name: str | None = None,
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    proc_self_cgroup: Path = Path("/proc/self/cgroup"),
) -> HostFacts:
    """Probe normalized host facts with injectable filesystem seams for tests."""
    physical = (
        _read_physical_memory_bytes()
        if physical_memory_bytes is None
        else int(physical_memory_bytes)
    )
    if physical <= 0:
        raise UnsupportedHostError("physical memory must be positive")
    cpus = os.cpu_count() if cpu_count is None else int(cpu_count)
    cpus = max(1, int(cpus or 1))
    system = platform.system().lower() if platform_name is None else platform_name.lower()

    limits: tuple[CgroupLimit, ...] = ()
    if system == "linux":
        limits = _probe_cgroup_limits(
            cgroup_root=Path(cgroup_root),
            proc_self_cgroup=Path(proc_self_cgroup),
        )
    cgroup_limit = min((item.limit_bytes for item in limits), default=None)
    effective = min(physical, cgroup_limit) if cgroup_limit is not None else physical
    source = (
        limits[0].source if cgroup_limit is not None and cgroup_limit < physical else "physical"
    )
    return HostFacts(
        platform=system,
        cpu_count=cpus,
        physical_memory_bytes=physical,
        cgroup_memory_limit_bytes=cgroup_limit,
        effective_memory_bytes=effective,
        memory_limit_source=source,
        cgroup_limits=limits,
    )


def _floor_mib(value_bytes: int) -> int:
    return value_bytes // MIB


def build_plan(
    facts: HostFacts,
    *,
    profile: str = "balanced",
    mode: str = "auto",
) -> OptimizationPlan:
    """Calculate a deterministic process-local plan using integer arithmetic."""
    if profile not in _PROFILE_POLICY:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    if mode == "off":
        return OptimizationPlan(
            profile=profile,
            mode=mode,
            facts=facts,
            supported=True,
            support_reason=None,
            budget_mb=None,
            target_mb=None,
            ceiling_mb=None,
            sys_low_mb=None,
        )

    numerator, denominator, cap_mb = _PROFILE_POLICY[profile]
    budget_bytes = min(
        facts.effective_memory_bytes * numerator // denominator,
        cap_mb * MIB,
    )
    budget_mb = _floor_mib(budget_bytes)
    if budget_mb < 128:
        return OptimizationPlan(
            profile=profile,
            mode=mode,
            facts=facts,
            supported=False,
            support_reason="derived memory budget is below 128 MiB",
            budget_mb=budget_mb,
            target_mb=None,
            ceiling_mb=None,
            sys_low_mb=None,
        )

    target_mb = budget_mb * 70 // 100
    ceiling_mb = budget_mb * 90 // 100
    sys_low_mb = max(64, _floor_mib(facts.effective_memory_bytes * 8 // 100))
    if not 0 < target_mb < ceiling_mb <= budget_mb:
        raise UnsupportedHostError("derived MemoryGuard thresholds violate ordering invariants")

    return OptimizationPlan(
        profile=profile,
        mode=mode,
        facts=facts,
        supported=True,
        support_reason=None,
        budget_mb=budget_mb,
        target_mb=target_mb,
        ceiling_mb=ceiling_mb,
        sys_low_mb=sys_low_mb,
    )


def validate_plan_wrapper(data: bytes | str) -> dict[str, Any]:
    """Validate managed bytes and return the plan payload."""
    try:
        raw = data.decode("utf-8") if isinstance(data, bytes) else data
        wrapper = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptManagedConfigError(f"managed config is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(wrapper, dict) or set(wrapper) != {"plan", "plan_sha256"}:
        raise CorruptManagedConfigError("managed config wrapper has unexpected fields")
    plan = wrapper.get("plan")
    claimed_hash = wrapper.get("plan_sha256")
    if not isinstance(plan, dict) or not isinstance(claimed_hash, str):
        raise CorruptManagedConfigError("managed config wrapper types are invalid")
    actual_hash = sha256_bytes(canonical_json_bytes(plan))
    if claimed_hash != actual_hash:
        raise CorruptManagedConfigError("managed config plan SHA-256 does not match")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise CorruptManagedConfigError(
            f"unsupported managed config schema_version {plan.get('schema_version')!r}"
        )
    if plan.get("policy_version") != POLICY_VERSION:
        raise CorruptManagedConfigError(
            f"unsupported managed config policy_version {plan.get('policy_version')!r}"
        )
    if plan.get("scope") != "process":
        raise CorruptManagedConfigError("managed config scope must be 'process'")
    if plan.get("mode") not in MODES or plan.get("profile") not in PROFILES:
        raise CorruptManagedConfigError("managed config mode/profile is invalid")
    guard = plan.get("memory_guard")
    if not isinstance(guard, dict):
        raise CorruptManagedConfigError("managed config memory_guard block is missing")
    enabled = guard.get("enabled")
    if not isinstance(enabled, bool):
        raise CorruptManagedConfigError("managed config enabled flag must be boolean")
    if enabled:
        target = guard.get("target_mb")
        ceiling = guard.get("ceiling_mb")
        sys_low = guard.get("sys_low_mb")
        interval = guard.get("check_interval_secs")
        cooldown = guard.get("cooldown_secs")
        target_value = _managed_int(target, "target_mb")
        ceiling_value = _managed_int(ceiling, "ceiling_mb")
        sys_low_value = _managed_int(sys_low, "sys_low_mb")
        interval_value = _managed_int(interval, "check_interval_secs")
        cooldown_value = _managed_int(cooldown, "cooldown_secs")
        if not (
            0 < target_value < ceiling_value
            and sys_low_value >= 0
            and interval_value > 0
            and cooldown_value >= interval_value
        ):
            raise CorruptManagedConfigError("managed MemoryGuard thresholds violate invariants")
    return plan


def load_managed_plan(path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Load a validated managed plan and return payload plus plan hash."""
    target = managed_paths().config if path is None else Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise CorruptManagedConfigError(f"cannot read managed config: {exc}") from exc
    plan = validate_plan_wrapper(data)
    return plan, sha256_bytes(canonical_json_bytes(plan))


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            os.chmod(tmp_path, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
        _fsync_directory(target.parent)
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _durable_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    release = None
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)

            def release() -> None:
                fcntl.flock(fd, fcntl.LOCK_UN)

        except ImportError:
            try:
                import msvcrt

                msvcrt_locking = cast(_MsvcrtLocking, msvcrt)

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt_locking.locking(fd, msvcrt_locking.LK_LOCK, 1)

                def release() -> None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt_locking.locking(fd, msvcrt_locking.LK_UNLCK, 1)

            except ImportError as exc:
                raise OptimizationError("platform has no supported file-lock primitive") from exc
        yield
    finally:
        if release is not None:
            with contextlib.suppress(Exception):
                release()
        os.close(fd)


def _lock_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "held": False, "supported": True}
    try:
        import fcntl
    except ImportError:
        return {"exists": True, "held": None, "supported": False}
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return {"exists": True, "held": None, "supported": True}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"exists": True, "held": True, "supported": True}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"exists": True, "held": False, "supported": True}
    finally:
        os.close(fd)


def _preimage_receipt(*, before: bytes | None, applied: bytes) -> dict[str, Any]:
    if before is None:
        preimage = {"bytes_base64": None, "sha256": None, "state": "absent"}
    else:
        preimage = {
            "bytes_base64": base64.b64encode(before).decode("ascii"),
            "sha256": sha256_bytes(before),
            "state": "present",
        }
    return {
        "applied_sha256": sha256_bytes(applied),
        "preimage": preimage,
        "schema_version": SCHEMA_VERSION,
    }


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorruptManagedConfigError(f"preimage receipt is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise CorruptManagedConfigError("preimage receipt schema is invalid")
    preimage = value.get("preimage")
    if not isinstance(preimage, dict) or preimage.get("state") not in {"absent", "present"}:
        raise CorruptManagedConfigError("preimage receipt payload is invalid")
    if not isinstance(value.get("applied_sha256"), str):
        raise CorruptManagedConfigError("preimage receipt applied hash is invalid")
    if preimage["state"] == "present":
        if not isinstance(preimage.get("bytes_base64"), str) or not isinstance(
            preimage.get("sha256"), str
        ):
            raise CorruptManagedConfigError("preimage receipt bytes/hash are invalid")
        try:
            decoded = base64.b64decode(preimage["bytes_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise CorruptManagedConfigError("preimage receipt base64 is invalid") from exc
        if sha256_bytes(decoded) != preimage["sha256"]:
            raise CorruptManagedConfigError("preimage receipt hash does not match bytes")
    return value


def _classify_receipt_state(paths: ManagedPaths, receipt: dict[str, Any]) -> tuple[str, str | None]:
    current = paths.config.read_bytes() if paths.config.exists() else None
    actual_hash = sha256_bytes(current) if current is not None else None
    if actual_hash == receipt["applied_sha256"]:
        return "clean", actual_hash
    preimage = receipt["preimage"]
    if preimage["state"] == "absent" and current is None:
        return "interrupted_apply", actual_hash
    if preimage["state"] == "present" and actual_hash == preimage["sha256"]:
        return "interrupted_apply", actual_hash
    return "external_drift", actual_hash


def optimizer_status(*, home: Path | None = None) -> dict[str, Any]:
    """Return a strictly read-only, enumerable managed-state snapshot."""
    paths = managed_paths(home)
    result: dict[str, Any] = {
        "artifacts": paths.as_dict(),
        "config": {"exists": paths.config.exists(), "sha256": None, "valid": None, "error": None},
        "lock": _lock_status(paths.lock),
        "preimage": {"exists": paths.preimage.exists(), "valid": None, "error": None},
        "state": "absent",
    }
    if paths.config.exists():
        try:
            current = paths.config.read_bytes()
            result["config"]["sha256"] = sha256_bytes(current)
            plan = validate_plan_wrapper(current)
            result["config"]["valid"] = True
            result["config"]["plan_sha256"] = sha256_bytes(canonical_json_bytes(plan))
            result["config"]["mode"] = plan["mode"]
            result["config"]["profile"] = plan["profile"]
            result["state"] = "managed_without_preimage"
        except CorruptManagedConfigError as exc:
            result["config"]["valid"] = False
            result["config"]["error"] = str(exc)
            result["state"] = "corrupt_config"

    if paths.preimage.exists():
        try:
            receipt = _load_receipt(paths.preimage)
            result["preimage"]["valid"] = True
            result["preimage"]["applied_sha256"] = receipt["applied_sha256"]
            result["preimage"]["preimage_state"] = receipt["preimage"]["state"]
            receipt_state, actual_hash = _classify_receipt_state(paths, receipt)
            result["state"] = receipt_state
            result["config"]["sha256"] = actual_hash
        except CorruptManagedConfigError as exc:
            result["preimage"]["valid"] = False
            result["preimage"]["error"] = str(exc)
            result["state"] = "corrupt_preimage"
    return result


def apply_plan(
    *,
    profile: str = "balanced",
    mode: str = "auto",
    home: Path | None = None,
    facts: HostFacts | None = None,
    expect_hash: str | None = None,
) -> dict[str, Any]:
    """Re-probe, calculate, and atomically apply one managed process plan."""
    paths = managed_paths(home)
    with _exclusive_lock(paths.lock):
        effective_facts = probe_host_facts() if facts is None else facts
        plan = build_plan(effective_facts, profile=profile, mode=mode)
        if not plan.supported:
            raise UnsupportedHostError(plan.support_reason or "host is unsupported")
        wrapper = wrap_plan(plan)
        plan_hash = wrapper["plan_sha256"]
        if expect_hash is not None and expect_hash != plan_hash:
            raise ApplyRefusedError(
                f"recomputed plan hash {plan_hash} does not match --expect-hash {expect_hash}"
            )
        new_bytes = canonical_json_bytes(wrapper) + b"\n"
        if paths.config.exists() and paths.config.read_bytes() == new_bytes:
            return {"changed": False, "plan": wrapper["plan"], "plan_sha256": plan_hash}

        status = optimizer_status(home=paths.home)
        if status["state"] in {"external_drift", "corrupt_config", "corrupt_preimage"}:
            raise ApplyRefusedError(
                f"managed optimizer state is {status['state']}; inspect status before applying"
            )
        before = paths.config.read_bytes() if paths.config.exists() else None
        receipt = _preimage_receipt(before=before, applied=new_bytes)
        _atomic_write_bytes(paths.preimage, canonical_json_bytes(receipt) + b"\n")
        _atomic_write_bytes(paths.config, new_bytes)
        if paths.config.read_bytes() != new_bytes:
            raise OptimizationError("managed config verification failed after atomic apply")
        return {"changed": True, "plan": wrapper["plan"], "plan_sha256": plan_hash}


def rollback_plan(*, home: Path | None = None, force: bool = False) -> dict[str, Any]:
    """Restore the exact one-level preimage and retire its receipt."""
    paths = managed_paths(home)
    with _exclusive_lock(paths.lock):
        if not paths.preimage.exists():
            raise RollbackRefusedError("no optimizer preimage receipt exists")
        receipt = _load_receipt(paths.preimage)
        state, _ = _classify_receipt_state(paths, receipt)
        if state == "external_drift" and not force:
            raise RollbackRefusedError(
                "current optimizer bytes do not match the applied or preimage hash; "
                "rerun with --force only to restore the recorded preimage"
            )
        preimage = receipt["preimage"]
        if state != "interrupted_apply" or force:
            if preimage["state"] == "absent":
                _durable_unlink(paths.config)
            else:
                restored = base64.b64decode(preimage["bytes_base64"], validate=True)
                _atomic_write_bytes(paths.config, restored)
                if sha256_bytes(paths.config.read_bytes()) != preimage["sha256"]:
                    raise OptimizationError("preimage verification failed after rollback")
        _durable_unlink(paths.preimage)
        return {"changed": state != "interrupted_apply", "restored": preimage["state"]}
