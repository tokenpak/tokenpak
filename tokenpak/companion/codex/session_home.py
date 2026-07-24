# SPDX-License-Identifier: Apache-2.0
"""Resolve and provision Codex homes for parallel launcher sessions.

``TOKENPAK_CODEX_SESSION_MODE`` accepts exactly three values:

``shared``
    Use the existing user Codex home.  This preserves the pre-isolation
    behavior and is intentionally single-session when local state is in use.
``workspace``
    Use a deterministic home derived from the resolved project directory.
``isolated``
    Use a fresh, unique home for each launcher invocation.

Provisioning is allowlist-only.  It never walks or copies the source Codex
home.  A new home may receive a private copy of ``config.toml`` and a symlink
to the externally refreshed ``auth.json`` credential; databases, WAL/SHM
sidecars, history, logs, sessions, and every other runtime file stay behind.

The ``codex.pid`` file is a lifecycle lease, not lock-attribution evidence.
Actual SQLite holders are discovered from kernel lock and file-descriptor
state by :mod:`tokenpak.companion.codex.state_lock`.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Callable, Iterator, cast

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 backport
    import tomli as tomllib  # type: ignore[no-redef]

# Launcher-internal implementation.  Keeping the module out of ``import *``
# also keeps these lifecycle helpers out of the released API snapshot.
__all__: list[str] = []

MODE_SHARED = "shared"
MODE_WORKSPACE = "workspace"
MODE_ISOLATED = "isolated"
VALID_MODES = (MODE_SHARED, MODE_WORKSPACE, MODE_ISOLATED)

ENV_SESSION_MODE = "TOKENPAK_CODEX_SESSION_MODE"
ENV_CODEX_HOME = "CODEX_HOME"

PID_SENTINEL_NAME = "codex.pid"
_LEASE_GUARD_NAME = ".tokenpak-codex-home.lock"
_SENTINEL_SCHEMA = "tokenpak.codex.pid.v1"

# Closed seed allowlist.  Adding a filename here is a security-sensitive
# decision; provisioning deliberately has no glob or recursive-copy path.
SAFE_CONFIG_FILES = ("config.toml",)
SAFE_CREDENTIAL_LINKS = ("auth.json",)
_ISOLATION_BREAKING_CONFIG_KEYS = ("sqlite_home", "log_dir")

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_THREAD_LEASE_LOCK = threading.RLock()
_GUARD_LOCK_TIMEOUT_S = 5.0
_MAX_SEED_BYTES = 4 * 1024 * 1024
_MAX_SENTINEL_BYTES = 16 * 1024
_SENTINEL_TEMP_RE = re.compile(
    rf"^\.{re.escape(PID_SENTINEL_NAME)}\.(acquire|transfer)\.([0-9a-f]{{32}})\.tmp$"
)
_PROCESS_LIVE = "live"
_PROCESS_DEAD = "dead-or-reused"
_PROCESS_UNKNOWN = "unknown"
_PROCESS_OBSERVED = "observed"

RETENTION_MAX_HOMES = 5
RETENTION_MAX_AGE_S = 7 * 24 * 60 * 60
RETENTION_MAX_TOTAL_BYTES = 500 * 1024 * 1024
_RETENTION_CREATION_GRACE_S = 60.0
_RETENTION_SCAN_TIMEOUT_S = 30.0
_RETENTION_MAX_ENTRIES = 4096
_RETENTION_MAX_NODES = 200000
_RETENTION_GUARD_NAME = ".tokenpak-retention.lock"
_RETENTION_RECEIPT_NAME = ".tokenpak-retention.jsonl"
_RETENTION_QUARANTINE_PREFIX = ".tokenpak-quarantine."
_RETENTION_QUARANTINE_RE = re.compile(rf"^{re.escape(_RETENTION_QUARANTINE_PREFIX)}[0-9a-f]{{32}}$")
_RETENTION_MAX_RECEIPT_BYTES = 16 * 1024 * 1024


def _is_sentinel_temp_candidate(name: str) -> bool:
    """Recognize the full wildcard family, including unusual filenames."""
    return any(
        name.startswith(f".{PID_SENTINEL_NAME}.{phase}.") and name.endswith(".tmp")
        for phase in ("acquire", "transfer")
    )


class InvalidSessionMode(ValueError):
    """Raised when the session-mode environment value is not supported."""


class HomeInUseError(RuntimeError):
    """Raised when a validated live lease already owns a selected home."""


@dataclass(frozen=True)
class _IsolatedHomeInfo:
    path: Path
    device: int
    inode: int
    mtime: float
    age_s: float
    size_bytes: int
    size_complete: bool
    state: str
    pid: int | None = None

    @property
    def orphaned(self) -> bool:
        return self.state in {"orphan-absent", "orphan-stale"}

    @property
    def protected(self) -> bool:
        return not self.orphaned or not self.size_complete


@dataclass(frozen=True)
class _RetentionReport:
    root: Path
    homes: tuple[_IsolatedHomeInfo, ...]
    total_bytes: int
    inventory_complete: bool
    quarantines: tuple[str, ...] = ()

    @property
    def orphaned(self) -> tuple[_IsolatedHomeInfo, ...]:
        return tuple(home for home in self.homes if home.orphaned)

    @property
    def active(self) -> tuple[_IsolatedHomeInfo, ...]:
        return tuple(home for home in self.homes if home.state == "active")

    @property
    def unsafe(self) -> tuple[_IsolatedHomeInfo, ...]:
        return tuple(home for home in self.homes if home.state in {"unsafe", "creating", "handoff"})

    @property
    def over_count(self) -> bool:
        return len(self.homes) > RETENTION_MAX_HOMES

    @property
    def over_age(self) -> bool:
        return any(home.age_s > RETENTION_MAX_AGE_S for home in self.homes)

    @property
    def over_size(self) -> bool:
        return self.total_bytes > RETENTION_MAX_TOTAL_BYTES


@dataclass(frozen=True)
class _CleanupResult:
    before: _RetentionReport
    after: _RetentionReport
    removed: tuple[Path, ...]
    planned: tuple[Path, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class SessionPaths:
    """Every path selected for one Codex launcher invocation."""

    mode: str
    home: Path
    source_home: Path
    workspace: Path
    config: Path
    auth: Path
    mcp_config: Path
    hooks: Path
    agents: Path
    skills_root: Path
    pid_sentinel: Path

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """Return a child environment pointing Codex at this home."""
        env = dict(os.environ if base is None else base)
        env[ENV_CODEX_HOME] = str(self.home)
        env[ENV_SESSION_MODE] = self.mode
        return env

    def report_rows(self) -> list[tuple[str, str]]:
        """Return stable labels used by launcher and doctor output."""
        return [
            ("session mode", self.mode),
            ("workspace", str(self.workspace)),
            ("CODEX_HOME", str(self.home)),
            ("source home", str(self.source_home)),
            ("config", str(self.config)),
            ("auth", str(self.auth)),
            ("MCP config", str(self.mcp_config)),
            ("hooks", str(self.hooks)),
            ("AGENTS.md", str(self.agents)),
            ("skills", str(self.skills_root)),
            ("PID sentinel", str(self.pid_sentinel)),
        ]


@dataclass(frozen=True)
class ProvisionedHome:
    """Result of allowlist-only home provisioning."""

    paths: SessionPaths
    created: bool
    seeded: tuple[str, ...]
    linked_credentials: tuple[str, ...]


@dataclass(frozen=True)
class PidSentinel:
    """Validated lifecycle lease stored in ``codex.pid``."""

    schema: str
    pid: int
    start_time_ticks: int
    session_id: str
    mode: str
    home: str


@dataclass(frozen=True)
class _SentinelArtifactInspection:
    """Fail-closed retention view of lifecycle publication artifacts."""

    state: str | None = None
    pid: int | None = None
    complete: bool = True


def resolve_mode(raw: str | None = None) -> str:
    """Return an exact advertised mode token, failing closed on bad input."""
    value = raw if raw is not None else os.environ.get(ENV_SESSION_MODE, MODE_SHARED)
    if value not in VALID_MODES:
        allowed = "|".join(VALID_MODES)
        shown = value or "<empty>"
        raise InvalidSessionMode(f"invalid {ENV_SESSION_MODE}={shown!r}; expected {allowed}")
    return value


def canonical_codex_home() -> Path:
    """Return the source-of-truth user Codex home."""
    return Path.home() / ".codex"


def _tokenpak_home() -> Path:
    from tokenpak import _paths

    return _paths.home()


def sessions_root(tokenpak_home: Path | None = None) -> Path:
    """Root containing unique per-session Codex homes."""
    return (tokenpak_home or _tokenpak_home()) / "companion" / "codex" / "sessions"


def workspaces_root(tokenpak_home: Path | None = None) -> Path:
    """Root containing deterministic per-project Codex homes."""
    return (tokenpak_home or _tokenpak_home()) / "companion" / "codex" / "workspaces"


def codex_root(tokenpak_home: Path | None = None) -> Path:
    """Private boundary containing TokenPak-managed Codex homes."""
    return (tokenpak_home or _tokenpak_home()) / "companion" / "codex"


def _generated_tokenpak_root(home: Path) -> Path | None:
    parents = home.parents
    if (
        len(parents) >= 4
        and parents[0].name in {"sessions", "workspaces"}
        and parents[1].name == "codex"
        and parents[2].name == "companion"
    ):
        return parents[3]
    return None


def workspace_hash(workspace_dir: Path | str) -> str:
    """Return a stable short digest for an equivalent resolved directory."""
    resolved = str(Path(workspace_dir).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:20]


def project_root(workspace_dir: Path | str) -> Path:
    """Resolve a stable project root, using the nearest Git boundary."""
    resolved = Path(workspace_dir).expanduser().resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _validated_session_id(session_id: str | None) -> str:
    value = session_id or uuid.uuid4().hex
    if not _SESSION_ID_RE.fullmatch(value):
        raise ValueError("session_id must be a safe filename component")
    return value


def select_paths(
    mode: str | None = None,
    *,
    workspace_dir: Path | str | None = None,
    session_id: str | None = None,
    tokenpak_home: Path | None = None,
    source_home: Path | None = None,
    selected_home: Path | None = None,
) -> SessionPaths:
    """Resolve all paths without creating or modifying the filesystem.

    ``selected_home`` is intended for doctor/uninstall inspection of an
    already-running isolated session.  Normal launcher calls omit it so an
    ``isolated`` mode invocation always receives a new UUID home.
    """
    resolved_mode = resolve_mode(mode)
    workspace = project_root(workspace_dir or Path.cwd())
    source = Path(
        source_home or os.environ.get(ENV_CODEX_HOME) or canonical_codex_home()
    ).expanduser()

    if selected_home is not None:
        home = Path(selected_home).expanduser()
    elif resolved_mode == MODE_SHARED:
        home = Path(os.environ.get(ENV_CODEX_HOME) or source).expanduser()
    elif resolved_mode == MODE_WORKSPACE:
        home = workspaces_root(tokenpak_home) / workspace_hash(workspace)
    else:
        home = sessions_root(tokenpak_home) / _validated_session_id(session_id)

    return SessionPaths(
        mode=resolved_mode,
        home=home,
        source_home=source,
        workspace=workspace,
        config=home / "config.toml",
        auth=home / "auth.json",
        mcp_config=home / "config.toml",
        hooks=home / "hooks.json",
        agents=home / "AGENTS.md",
        # User skills remain at Codex's documented user discovery root.  The
        # selected config records explicit per-skill entries separately.
        skills_root=Path.home() / ".agents" / "skills",
        pid_sentinel=home / PID_SENTINEL_NAME,
    )


def current_paths(
    mode: str | None = None,
    *,
    workspace_dir: Path | str | None = None,
    tokenpak_home: Path | None = None,
    source_home: Path | None = None,
) -> SessionPaths:
    """Resolve the active home for doctor/uninstall without creating one."""
    resolved_mode = resolve_mode(mode)
    selected = os.environ.get(ENV_CODEX_HOME)
    if resolved_mode == MODE_ISOLATED and not selected:
        raise InvalidSessionMode(
            "isolated mode has no selected home outside a launch; set CODEX_HOME "
            "to the session home before running doctor or uninstall"
        )
    if resolved_mode == MODE_ISOLATED and selected:
        selected_path = Path(selected).expanduser().resolve()
        expected_parent = sessions_root(tokenpak_home).resolve()
        if selected_path.parent != expected_parent:
            raise InvalidSessionMode(
                "isolated CODEX_HOME is outside the TokenPak sessions root; "
                "refusing unsafe inspection or cleanup"
            )
    return select_paths(
        resolved_mode,
        workspace_dir=workspace_dir,
        tokenpak_home=tokenpak_home,
        source_home=source_home,
        # A workspace home is a deterministic function of the project.  An
        # inherited CODEX_HOME must not silently redirect it.  Isolated mode,
        # by contrast, can only be inspected when its selected home is passed
        # through the environment by the launcher.
        selected_home=(
            Path(selected) if selected and resolved_mode in {MODE_SHARED, MODE_ISOLATED} else None
        ),
    )


def _entry_stat(name: str, dir_fd: int) -> os.stat_result | None:
    """Return an entry's no-follow stat relative to a validated home."""
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _owned_directory(fd: int, path: Path, *, allowed_modes: set[int]) -> os.stat_result:
    current = os.fstat(fd)
    getuid = getattr(os, "geteuid", None)
    mode = stat.S_IMODE(current.st_mode)
    if (
        not stat.S_ISDIR(current.st_mode)
        or (getuid is not None and current.st_uid != getuid())
        or mode not in allowed_modes
    ):
        expected = "/".join(f"{value:04o}" for value in sorted(allowed_modes))
        raise HomeInUseError(
            f"selected CODEX_HOME ancestor is not an owned {expected} directory: {path}"
        )
    return current


def _mkdir_open_owned_at(
    parent_fd: int,
    name: str,
    path: Path,
    *,
    existing_modes: set[int],
) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise HomeInUseError(f"selected CODEX_HOME ancestor is unsafe: {path}") from exc
    try:
        if created:
            os.fchmod(fd, 0o700)
            allowed = {0o700}
        else:
            allowed = existing_modes
        _owned_directory(fd, path, allowed_modes=allowed)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_selected_home(paths: SessionPaths) -> int | None:
    """Create/open the selected home and pin its directory inode.

    Non-shared homes are TokenPak-owned private runtime directories.  Their
    immediate container and leaf are therefore forced to 0700 before any
    credential link or configuration is installed.  ``O_NOFOLLOW`` closes
    the leaf-symlink race; callers retain the returned descriptor for the
    entire lease so sentinel operations stay bound to this exact inode.
    """
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if paths.mode == MODE_SHARED:
        paths.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "nt":  # Windows CRT cannot open directory descriptors.
            entry = paths.home.lstat()
            if not stat.S_ISDIR(entry.st_mode) or paths.home.is_symlink():
                raise HomeInUseError(f"selected CODEX_HOME is not a directory: {paths.home}")
            return None
        fd = os.open(str(paths.home), directory_flags)
    else:
        # Normal generated homes have this shape:
        # <tokenpak-home>/companion/codex/{sessions,workspaces}/<id>.  Create
        # the TokenPak-owned chain one component at a time and pin every
        # component with O_NOFOLLOW before creating the next.  Tests and
        # explicit internal callers with another shape use the immediate
        # parent as their private root.
        parents = paths.home.parents
        generated = _generated_tokenpak_root(paths.home) is not None
        private_root = parents[3] if generated else paths.home.parent
        root_created = not private_root.exists()
        private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        current_fd = os.open(
            str(private_root),
            directory_flags | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if root_created:
                os.fchmod(current_fd, 0o700)
            _owned_directory(current_fd, private_root, allowed_modes={0o700})

            relative_parent = paths.home.parent.relative_to(private_root)
            for index, component in enumerate(relative_parent.parts):
                component_path = private_root / Path(*relative_parent.parts[: index + 1])
                allowed_modes = (
                    {0o700, 0o775} if generated and component == "companion" else {0o700}
                )
                next_fd = _mkdir_open_owned_at(
                    current_fd,
                    component,
                    component_path,
                    existing_modes=allowed_modes,
                )
                os.close(current_fd)
                current_fd = next_fd
            fd = _mkdir_open_owned_at(
                current_fd,
                paths.home.name,
                paths.home,
                existing_modes={0o700},
            )
        finally:
            os.close(current_fd)
    try:
        pinned = os.fstat(fd)
        current = os.stat(paths.home, follow_symlinks=paths.mode == MODE_SHARED)
        if not stat.S_ISDIR(pinned.st_mode) or (pinned.st_dev, pinned.st_ino) != (
            current.st_dev,
            current.st_ino,
        ):
            raise HomeInUseError(f"selected CODEX_HOME changed during validation: {paths.home}")
        if paths.mode != MODE_SHARED and pinned.st_mode & 0o077:
            raise HomeInUseError(f"selected CODEX_HOME is not private (0700): {paths.home}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_bounded_regular(
    path: Path,
    *,
    dir_fd: int | None = None,
    private: bool = False,
    max_bytes: int = _MAX_SEED_BYTES,
) -> bytes | None:
    """Read a bounded regular file without following its final symlink."""
    if dir_fd is None and path.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path.name if dir_fd is not None else str(path), flags, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        source_stat = os.fstat(fd)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > max_bytes:
            return None
        if private:
            getuid = getattr(os, "geteuid", None)
            if getuid is not None and source_stat.st_uid != getuid():
                return None
            if source_stat.st_mode & 0o077:
                return None
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(1024 * 1024, max_bytes + 1 - total)):
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _sanitized_config(data: bytes) -> bytes | None:
    """Validate one TOML config and remove isolation-breaking root keys."""
    try:
        text = data.decode("utf-8")
        tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None

    lines = text.splitlines()
    top_level_end = next(
        (index for index, line in enumerate(lines) if re.match(r"^\s*\[", line)),
        len(lines),
    )
    key_pattern = re.compile(
        r"^\s*(?:"
        + "|".join(
            rf"(?:{re.escape(key)}|\"{re.escape(key)}\"|'{re.escape(key)}')"
            for key in _ISOLATION_BREAKING_CONFIG_KEYS
        )
        + r")\s*="
    )
    filtered = [
        line
        for index, line in enumerate(lines)
        if not (index < top_level_end and key_pattern.match(line))
    ]
    sanitized = "\n".join(filtered)
    if text.endswith("\n"):
        sanitized += "\n"
    try:
        parsed = tomllib.loads(sanitized)
    except tomllib.TOMLDecodeError:
        return None
    if any(key in parsed for key in _ISOLATION_BREAKING_CONFIG_KEYS):
        return None
    return sanitized.encode("utf-8")


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte, treating a zero-length write as an I/O failure."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while persisting Codex session metadata")
        view = view[written:]


def _replace_private_at(dir_fd: int, name: str, data: bytes) -> None:
    """Atomically replace one regular file inside a pinned directory."""
    tmp_name = f".{name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name, dir_fd=dir_fd)


def _provision_config(src: Path, dir_fd: int, name: str) -> bool:
    """Seed or revalidate the selected home's private config."""
    existing = _entry_stat(name, dir_fd)
    if existing is None:
        source = _read_bounded_regular(src)
        if source is None:
            return False
        sanitized = _sanitized_config(source)
        if sanitized is None:
            return False
        _replace_private_at(dir_fd, name, sanitized)
        return True

    getuid = getattr(os, "geteuid", None)
    if (
        not stat.S_ISREG(existing.st_mode)
        or existing.st_nlink != 1
        or (getuid is not None and existing.st_uid != getuid())
    ):
        raise RuntimeError(f"selected config is not a regular file: {name}")
    current = _read_bounded_regular(Path(name), dir_fd=dir_fd)
    if current is None:
        raise RuntimeError(f"selected config is unreadable or oversized: {name}")
    sanitized = _sanitized_config(current)
    if sanitized is None:
        raise RuntimeError(f"selected config is not safe UTF-8 TOML: {name}")
    if sanitized != current or existing.st_mode & 0o777 != 0o600:
        _replace_private_at(dir_fd, name, sanitized)
    return False


def _safe_credential_source(src: Path) -> Path | None:
    """Validate an externally refreshed credential without copying it."""
    raw = _read_bounded_regular(src, private=True)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return Path(os.path.abspath(src))


def _link_credential_once(src: Path, dir_fd: int, name: str) -> bool:
    """Install or validate one exact allowlisted credential symlink."""
    safe_source = _safe_credential_source(src)
    existing = _entry_stat(name, dir_fd)
    if existing is not None:
        if not stat.S_ISLNK(existing.st_mode):
            raise RuntimeError(f"selected credential is not a managed symlink: {name}")
        try:
            target = os.readlink(name, dir_fd=dir_fd)
        except OSError as exc:
            raise RuntimeError(f"selected credential link is unreadable: {name}") from exc
        if safe_source is None or target != str(safe_source):
            raise RuntimeError(f"selected credential link has an unsafe target: {name}")
        return False
    if safe_source is None:
        return False
    os.symlink(str(safe_source), name, dir_fd=dir_fd)
    return True


def _validate_selected_entries(dir_fd: int) -> None:
    """Reject redirectable managed files and aliased runtime databases."""
    for name in ("hooks.json", "AGENTS.md"):
        entry = _entry_stat(name, dir_fd)
        if entry is None:
            continue
        getuid = getattr(os, "geteuid", None)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or entry.st_size > _MAX_SEED_BYTES
            or (getuid is not None and entry.st_uid != getuid())
        ):
            raise RuntimeError(f"selected managed file is unsafe: {name}")

    runtime_pattern = re.compile(r"^(?:state|logs)_[^/]+\.sqlite(?:-(?:wal|wal2|shm|journal))?$")
    for name in os.listdir(dir_fd):
        if not runtime_pattern.fullmatch(name):
            continue
        entry = _entry_stat(name, dir_fd)
        if entry is None:
            continue
        getuid = getattr(os, "geteuid", None)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or (getuid is not None and entry.st_uid != getuid())
        ):
            raise RuntimeError(f"selected runtime database is aliased or unsafe: {name}")


def provision(paths: SessionPaths, *, home_fd: int | None = None) -> ProvisionedHome:
    """Create ``paths.home`` and seed only the closed safe-file allowlist."""
    if paths.mode == MODE_SHARED:
        return ProvisionedHome(paths, False, (), ())
    created = not paths.home.exists()
    owned_fd = home_fd is None
    fd = _open_selected_home(paths) if home_fd is None else home_fd
    if fd is None:
        raise RuntimeError("non-shared selected home requires directory-descriptor support")

    seeded: list[str] = []
    linked: list[str] = []
    try:
        _validate_selected_entries(fd)
        for name in SAFE_CONFIG_FILES:
            if _provision_config(paths.source_home / name, fd, name):
                seeded.append(name)
        for name in SAFE_CREDENTIAL_LINKS:
            if _link_credential_once(paths.source_home / name, fd, name):
                linked.append(name)
        return ProvisionedHome(paths, created, tuple(seeded), tuple(linked))
    finally:
        if owned_fd:
            os.close(fd)


def _portable_process_identity(pid: int) -> tuple[str, int] | None:
    """Best-effort process incarnation identity when Linux procfs is absent."""
    if os.name == "posix":
        try:
            result = subprocess.run(
                ["ps", "-o", "stat=,lstart=", "-p", str(pid)],
                capture_output=True,
                env={**os.environ, "LC_ALL": "C"},
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        row = result.stdout.strip()
        if result.returncode != 0 or not row:
            return None
        fields = row.split(maxsplit=1)
        if len(fields) != 2:
            return None
        fingerprint = int.from_bytes(hashlib.sha256(fields[1].encode("utf-8")).digest()[:8], "big")
        return fields[0][0], fingerprint or 1

    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

        win_dll = cast(Callable[..., ctypes.CDLL], getattr(ctypes, "WinDLL"))
        kernel32 = win_dll("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        )
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            exit_code = wintypes.DWORD()
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return "R", (created.high << 32) | created.low
        finally:
            kernel32.CloseHandle(handle)
    return None


def _proc_identity(pid: int, proc_root: Path = Path("/proc")) -> tuple[str, int] | None:
    """Return ``(state, start-time ticks)`` for one process incarnation."""
    if pid <= 0:
        return None
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        if proc_root == Path("/proc") and not sys.platform.startswith("linux"):
            return _portable_process_identity(pid)
        return None
    return _parse_proc_stat_identity(raw)


def _parse_proc_stat_identity(raw: str) -> tuple[str, int] | None:
    """Parse one Linux proc stat row without performing fallback I/O."""
    rparen = raw.rfind(")")
    if rparen < 0:
        return None
    fields = raw[rparen + 1 :].split()
    if len(fields) <= 19:
        return None
    try:
        return fields[0], int(fields[19])
    except ValueError:
        return None


def _sentinel_from_data(data: object) -> PidSentinel | None:
    if not isinstance(data, dict):
        return None
    try:
        sentinel = PidSentinel(
            schema=str(data["schema"]),
            pid=int(data["pid"]),
            start_time_ticks=int(data["start_time_ticks"]),
            session_id=str(data["session_id"]),
            mode=str(data["mode"]),
            home=str(data["home"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        sentinel.schema != _SENTINEL_SCHEMA
        or sentinel.pid <= 0
        or sentinel.start_time_ticks <= 0
        or sentinel.mode not in VALID_MODES
        or not _SESSION_ID_RE.fullmatch(sentinel.session_id)
    ):
        return None
    return sentinel


def read_pid_sentinel(path: Path, *, dir_fd: int | None = None) -> PidSentinel | None:
    """Parse a bounded private regular sentinel without following links."""
    raw = _read_bounded_regular(
        path,
        dir_fd=dir_fd,
        private=True,
        max_bytes=_MAX_SENTINEL_BYTES,
    )
    if raw is None:
        return None
    try:
        return _sentinel_from_data(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _process_identity_evidence(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[str, tuple[str, int] | None]:
    """Separate proven absence from incomplete process inspection."""
    if pid <= 0:
        return _PROCESS_DEAD, None
    use_proc = proc_root != Path("/proc") or sys.platform.startswith("linux")
    if use_proc:
        try:
            root_info = proc_root.stat()
        except OSError:
            return _PROCESS_UNKNOWN, None
        if not stat.S_ISDIR(root_info.st_mode):
            return _PROCESS_UNKNOWN, None
        stat_path = proc_root / str(pid) / "stat"
        try:
            stat_path.stat()
        except FileNotFoundError:
            # Distinguish a proven-absent PID from procfs disappearing or
            # becoming unreadable between the root and PID observations.
            try:
                if not stat.S_ISDIR(proc_root.stat().st_mode):
                    return _PROCESS_UNKNOWN, None
            except OSError:
                return _PROCESS_UNKNOWN, None
            return _PROCESS_DEAD, None
        except OSError:
            return _PROCESS_UNKNOWN, None
        try:
            raw = stat_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _PROCESS_UNKNOWN, None
        except OSError:
            return _PROCESS_UNKNOWN, None
        identity = _parse_proc_stat_identity(raw)
    else:
        identity = _proc_identity(pid, proc_root)
    if identity is None:
        return _PROCESS_UNKNOWN, None
    return _PROCESS_OBSERVED, identity


def _sentinel_liveness(
    sentinel: PidSentinel,
    *,
    expected_home: Path | None = None,
    proc_root: Path = Path("/proc"),
) -> str:
    """Return live, proven-dead/reused, or unknown incarnation evidence."""
    if expected_home is not None:
        try:
            if Path(sentinel.home).resolve() != expected_home.resolve():
                return _PROCESS_UNKNOWN
        except OSError:
            return _PROCESS_UNKNOWN
    evidence, identity = _process_identity_evidence(sentinel.pid, proc_root=proc_root)
    if evidence != _PROCESS_OBSERVED or identity is None:
        return evidence
    state, start_ticks = identity
    if state in {"Z", "X", "x"} or start_ticks != sentinel.start_time_ticks:
        return _PROCESS_DEAD
    return _PROCESS_LIVE


def sentinel_is_live(
    sentinel: PidSentinel,
    *,
    expected_home: Path | None = None,
    proc_root: Path = Path("/proc"),
) -> bool:
    """Validate process incarnation and selected-home binding."""
    return (
        _sentinel_liveness(
            sentinel,
            expected_home=expected_home,
            proc_root=proc_root,
        )
        == _PROCESS_LIVE
    )


def _open_managed_sessions_root(
    tokenpak_home: Path | None = None,
    *,
    create: bool = False,
) -> tuple[Path, int] | None:
    """Open the sessions root through its pinned, no-follow chain.

    Inspection callers leave ``create`` false so doctor remains read-only.
    Isolated-session publication sets it true to create only the managed
    ancestors; the leaf is created later while holding the retention guard.
    """
    tokenpak_root = tokenpak_home or _tokenpak_home()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        root_created = not tokenpak_root.exists()
        tokenpak_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    else:
        root_created = False
    try:
        current_fd = os.open(str(tokenpak_root), flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise HomeInUseError(f"unsafe TokenPak home: {tokenpak_root}") from exc
    current_path = tokenpak_root
    try:
        if root_created:
            os.fchmod(current_fd, 0o700)
        _owned_directory(current_fd, current_path, allowed_modes={0o700})
        for component, modes in (
            ("companion", {0o700, 0o775}),
            ("codex", {0o700}),
            ("sessions", {0o700}),
        ):
            current_path = current_path / component
            if create:
                next_fd = _mkdir_open_owned_at(
                    current_fd,
                    component,
                    current_path,
                    existing_modes=modes,
                )
            else:
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    os.close(current_fd)
                    return None
                except OSError as exc:
                    raise HomeInUseError(f"unsafe managed Codex path: {current_path}") from exc
            os.close(current_fd)
            current_fd = next_fd
            if not create:
                _owned_directory(current_fd, current_path, allowed_modes=modes)
        pinned = os.fstat(current_fd)
        named = os.stat(current_path, follow_symlinks=False)
        if (pinned.st_dev, pinned.st_ino) != (named.st_dev, named.st_ino):
            raise HomeInUseError("isolated homes root changed during inspection")
        return current_path, current_fd
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(current_fd)
        raise


def _open_isolated_leaf_at(paths: SessionPaths, root_fd: int) -> int:
    """Create/open and pin one isolated leaf beneath a guarded sessions root."""
    fd = _mkdir_open_owned_at(
        root_fd,
        paths.home.name,
        paths.home,
        existing_modes={0o700},
    )
    try:
        pinned = os.fstat(fd)
        named = os.stat(paths.home.name, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(pinned.st_mode) or (pinned.st_dev, pinned.st_ino) != (
            named.st_dev,
            named.st_ino,
        ):
            raise HomeInUseError(f"selected CODEX_HOME changed during validation: {paths.home}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _tree_allocated_size(
    directory_fd: int,
    *,
    root_device: int,
    owner_uid: int | None,
    deadline: float,
) -> tuple[int, bool]:
    """Count allocated bytes without following links, mounts, or duplicate inodes."""
    seen: set[tuple[int, int]] = set()
    nodes = 0

    def walk(fd: int) -> tuple[int, bool]:
        nonlocal nodes
        total = 0
        complete = True
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        return total, False
                    nodes += 1
                    if nodes > _RETENTION_MAX_NODES:
                        return total, False
                    try:
                        info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        complete = False
                        continue
                    if info.st_dev != root_device:
                        complete = False
                        continue
                    if owner_uid is not None and info.st_uid != owner_uid:
                        complete = False
                    key = (info.st_dev, info.st_ino)
                    if key not in seen:
                        seen.add(key)
                        total += max(0, getattr(info, "st_blocks", 0) * 512)
                    if stat.S_ISDIR(info.st_mode):
                        try:
                            child_fd = os.open(
                                entry.name,
                                os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=fd,
                            )
                        except OSError:
                            complete = False
                            continue
                        try:
                            pinned = os.fstat(child_fd)
                            if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino):
                                complete = False
                                continue
                            child_total, child_complete = walk(child_fd)
                            total += child_total
                            complete = complete and child_complete
                        finally:
                            os.close(child_fd)
                    elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                        complete = False
        except OSError:
            return total, False
        return total, complete

    return walk(directory_fd)


def _inspect_sentinel_temp_artifacts(
    home_fd: int,
    path: Path,
    *,
    deadline: float,
    proc_root: Path,
) -> _SentinelArtifactInspection:
    """Inspect every acquire/transfer temp; ambiguity always protects."""
    getuid = getattr(os, "geteuid", None)
    artifacts = 0
    live_pid: int | None = None
    handoff = False
    unsafe = False
    try:
        with os.scandir(home_fd) as entries:
            for entry_count, entry in enumerate(entries, start=1):
                if time.monotonic() >= deadline or entry_count > _RETENTION_MAX_ENTRIES:
                    return _SentinelArtifactInspection("unsafe", None, False)
                name = entry.name
                if not _is_sentinel_temp_candidate(name):
                    continue
                artifacts += 1
                match = _SENTINEL_TEMP_RE.fullmatch(name)
                if match is None:
                    unsafe = True
                    continue
                try:
                    info = os.stat(name, dir_fd=home_fd, follow_symlinks=False)
                except OSError:
                    unsafe = True
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (getuid is not None and info.st_uid != getuid())
                ):
                    unsafe = True
                    continue
                candidate = read_pid_sentinel(Path(name), dir_fd=home_fd)
                if candidate is None or candidate.mode != MODE_ISOLATED:
                    unsafe = True
                    continue
                liveness = _sentinel_liveness(
                    candidate,
                    expected_home=path,
                    proc_root=proc_root,
                )
                if liveness == _PROCESS_UNKNOWN:
                    unsafe = True
                elif liveness == _PROCESS_LIVE:
                    if live_pid is not None and live_pid != candidate.pid:
                        unsafe = True
                    live_pid = candidate.pid
                elif match.group(1) == "transfer":
                    # A complete but interrupted transfer is still a handoff,
                    # not proof that no child inherited this home.
                    handoff = True
    except OSError:
        return _SentinelArtifactInspection("unsafe", None, False)

    if artifacts > 1:
        unsafe = True
    if unsafe:
        return _SentinelArtifactInspection("unsafe", live_pid, False)
    if live_pid is not None:
        return _SentinelArtifactInspection("active", live_pid, True)
    if handoff:
        return _SentinelArtifactInspection("handoff", None, True)
    return _SentinelArtifactInspection()


def _inspect_isolated_home_at(
    root_fd: int,
    root: Path,
    name: str,
    *,
    now: float,
    deadline: float,
    proc_root: Path = Path("/proc"),
) -> _IsolatedHomeInfo:
    getuid = getattr(os, "geteuid", None)
    owner_uid = getuid() if getuid is not None else None
    path = root / name
    try:
        entry = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        return _IsolatedHomeInfo(path, -1, -1, now, 0.0, 0, False, "unsafe")
    age = max(0.0, now - entry.st_mtime)
    if (
        not stat.S_ISDIR(entry.st_mode)
        or stat.S_IMODE(entry.st_mode) != 0o700
        or (owner_uid is not None and entry.st_uid != owner_uid)
        or entry.st_dev != os.fstat(root_fd).st_dev
    ):
        return _IsolatedHomeInfo(
            path, entry.st_dev, entry.st_ino, entry.st_mtime, age, 0, False, "unsafe"
        )
    try:
        home_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
    except OSError:
        return _IsolatedHomeInfo(
            path, entry.st_dev, entry.st_ino, entry.st_mtime, age, 0, False, "unsafe"
        )
    try:
        pinned = os.fstat(home_fd)
        if (pinned.st_dev, pinned.st_ino) != (entry.st_dev, entry.st_ino):
            return _IsolatedHomeInfo(
                path, entry.st_dev, entry.st_ino, entry.st_mtime, age, 0, False, "unsafe"
            )
        size, size_complete = _tree_allocated_size(
            home_fd,
            root_device=entry.st_dev,
            owner_uid=owner_uid,
            deadline=deadline,
        )
        artifacts = _inspect_sentinel_temp_artifacts(
            home_fd,
            path,
            deadline=deadline,
            proc_root=proc_root,
        )
        if artifacts.state is not None:
            return _IsolatedHomeInfo(
                path,
                entry.st_dev,
                entry.st_ino,
                entry.st_mtime,
                age,
                size,
                size_complete and artifacts.complete,
                artifacts.state,
                artifacts.pid,
            )
        sentinel_entry = _entry_stat(PID_SENTINEL_NAME, home_fd)
        if sentinel_entry is None:
            state = "creating" if age < _RETENTION_CREATION_GRACE_S else "orphan-absent"
            return _IsolatedHomeInfo(
                path,
                entry.st_dev,
                entry.st_ino,
                entry.st_mtime,
                age,
                size,
                size_complete,
                state,
            )
        sentinel = read_pid_sentinel(Path(PID_SENTINEL_NAME), dir_fd=home_fd)
        if sentinel is None:
            state = "unsafe"
            pid = None
        else:
            liveness = _sentinel_liveness(sentinel, expected_home=path, proc_root=proc_root)
            if liveness == _PROCESS_LIVE:
                state = "active"
            elif liveness == _PROCESS_DEAD:
                state = "orphan-stale"
            else:
                state = "unsafe"
            pid = sentinel.pid
        return _IsolatedHomeInfo(
            path,
            entry.st_dev,
            entry.st_ino,
            entry.st_mtime,
            age,
            size,
            size_complete,
            state,
            pid,
        )
    finally:
        os.close(home_fd)


def inspect_isolated_homes(
    tokenpak_home: Path | None = None,
    *,
    now: float | None = None,
    deadline: float | None = None,
    proc_root: Path = Path("/proc"),
) -> _RetentionReport:
    """Read-only isolated-home inventory for doctor and retention planning."""
    root = sessions_root(tokenpak_home)
    opened = _open_managed_sessions_root(tokenpak_home)
    if opened is None:
        return _RetentionReport(root, (), 0, True)
    root, root_fd = opened
    now = time.time() if now is None else now
    deadline = time.monotonic() + _RETENTION_SCAN_TIMEOUT_S if deadline is None else deadline
    homes: list[_IsolatedHomeInfo] = []
    quarantines: list[str] = []
    total = 0
    complete = True
    try:
        try:
            with os.scandir(root_fd) as entries:
                for entry_count, entry in enumerate(entries, start=1):
                    if time.monotonic() >= deadline or entry_count > _RETENTION_MAX_ENTRIES:
                        complete = False
                        break
                    name = entry.name
                    if name in {
                        _RETENTION_GUARD_NAME,
                        _RETENTION_RECEIPT_NAME,
                    }:
                        continue
                    if name.startswith(_RETENTION_QUARANTINE_PREFIX):
                        quarantines.append(name)
                        quarantine_info = _inspect_isolated_home_at(
                            root_fd,
                            root,
                            name,
                            now=now,
                            deadline=deadline,
                            proc_root=proc_root,
                        )
                        total += quarantine_info.size_bytes
                        complete = False
                        continue
                    info = _inspect_isolated_home_at(
                        root_fd,
                        root,
                        name,
                        now=now,
                        deadline=deadline,
                        proc_root=proc_root,
                    )
                    homes.append(info)
                    total += info.size_bytes
                    complete = (
                        complete and info.size_complete and info.state not in {"unsafe", "handoff"}
                    )
        except OSError:
            complete = False
    finally:
        os.close(root_fd)
    homes.sort(key=lambda item: (item.mtime, item.path.name))
    return _RetentionReport(
        root=root,
        homes=tuple(homes),
        total_bytes=total,
        inventory_complete=complete,
        quarantines=tuple(sorted(quarantines)),
    )


@contextlib.contextmanager
def _interprocess_file_lock(fd: int, *, deadline: float) -> Iterator[None]:
    """Hold one process-shared lock on POSIX or Windows."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - exercised on Windows CI
        import msvcrt

        locking = cast(Callable[[int, int, int], None], getattr(msvcrt, "locking"))
        lock_nonblocking = cast(int, getattr(msvcrt, "LK_NBLCK"))
        unlock = cast(int, getattr(msvcrt, "LK_UNLCK"))
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                locking(fd, lock_nonblocking, 1)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN} or time.monotonic() >= deadline:
                    raise HomeInUseError("timed out acquiring selected-home lease guard") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            with contextlib.suppress(OSError):
                locking(fd, unlock, 1)
        return

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN} or time.monotonic() >= deadline:
                raise HomeInUseError("timed out acquiring Codex lifecycle guard") from exc
            time.sleep(0.01)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _bounded_guard_lock(fd: int) -> Iterator[None]:
    """Bound both in-process and kernel guard acquisition by one deadline."""
    deadline = time.monotonic() + _GUARD_LOCK_TIMEOUT_S
    acquired = _THREAD_LEASE_LOCK.acquire(timeout=max(0.0, deadline - time.monotonic()))
    if not acquired:
        raise HomeInUseError("timed out acquiring Codex lifecycle thread guard")
    try:
        with _interprocess_file_lock(fd, deadline=deadline):
            yield
    finally:
        _THREAD_LEASE_LOCK.release()


@contextlib.contextmanager
def _lease_guard(home: Path, *, home_fd: int | None = None) -> Iterator[None]:
    """Serialize lease mutation without deleting or signalling processes."""
    guard = home / _LEASE_GUARD_NAME
    guard_stat = _entry_stat(_LEASE_GUARD_NAME, home_fd) if home_fd is not None else None
    if home_fd is None and guard.is_symlink():
        raise HomeInUseError(f"invalid lease guard: {guard}")
    if guard_stat is not None and not stat.S_ISREG(guard_stat.st_mode):
        raise HomeInUseError(f"invalid lease guard: {guard}")
    fd = os.open(
        _LEASE_GUARD_NAME if home_fd is not None else str(guard),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=home_fd,
    )
    guard_info = os.fstat(fd)
    getuid = getattr(os, "geteuid", None)
    if guard_stat is None:
        os.fchmod(fd, 0o600)
        guard_info = os.fstat(fd)
    if (
        not stat.S_ISREG(guard_info.st_mode)
        or guard_info.st_nlink != 1
        or stat.S_IMODE(guard_info.st_mode) != 0o600
        or (getuid is not None and guard_info.st_uid != getuid())
    ):
        os.close(fd)
        raise HomeInUseError(f"invalid lease guard: {guard}")
    if guard_stat is not None and (guard_stat.st_dev, guard_stat.st_ino) != (
        guard_info.st_dev,
        guard_info.st_ino,
    ):
        os.close(fd)
        raise HomeInUseError(f"lease guard changed during validation: {guard}")
    if home_fd is None:
        named = guard.lstat()
        if stat.S_ISLNK(named.st_mode) or (named.st_dev, named.st_ino) != (
            guard_info.st_dev,
            guard_info.st_ino,
        ):
            os.close(fd)
            raise HomeInUseError(f"invalid lease guard: {guard}")
    if guard_info.st_size == 0:
        _write_all(fd, b"\0")
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
    try:
        with _bounded_guard_lock(fd):
            yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _existing_lease_guard(home: Path, *, home_fd: int) -> Iterator[None]:
    """Join an existing home lease without creating state in a bare orphan."""
    if _entry_stat(_LEASE_GUARD_NAME, home_fd) is None:
        yield
        return
    with _lease_guard(home, home_fd=home_fd):
        yield


@contextlib.contextmanager
def _retention_guard(root: Path, root_fd: int) -> Iterator[None]:
    """Serialize isolated-home publication and cleanup across processes."""
    entry = _entry_stat(_RETENTION_GUARD_NAME, root_fd)
    if entry is not None and (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or stat.S_IMODE(entry.st_mode) != 0o600
    ):
        raise HomeInUseError(f"unsafe retention guard: {root / _RETENTION_GUARD_NAME}")
    fd = os.open(
        _RETENTION_GUARD_NAME,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=root_fd,
    )
    try:
        info = os.fstat(fd)
        getuid = getattr(os, "geteuid", None)
        if entry is None:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or (getuid is not None and info.st_uid != getuid())
        ):
            raise HomeInUseError(f"unsafe retention guard: {root / _RETENTION_GUARD_NAME}")
        if entry is not None and (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino):
            raise HomeInUseError(
                f"retention guard changed during validation: {root / _RETENTION_GUARD_NAME}"
            )
        if info.st_size == 0:
            _write_all(fd, b"\0")
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        with _bounded_guard_lock(fd):
            yield
    finally:
        os.close(fd)


def _append_retention_receipt(root_fd: int, payload: dict[str, object]) -> None:
    getuid = getattr(os, "geteuid", None)
    entry = _entry_stat(_RETENTION_RECEIPT_NAME, root_fd)
    if entry is not None and (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or stat.S_IMODE(entry.st_mode) != 0o600
        or (getuid is not None and entry.st_uid != getuid())
    ):
        raise HomeInUseError("unsafe retention receipt file")
    fd = os.open(
        _RETENTION_RECEIPT_NAME,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=root_fd,
    )
    try:
        info = os.fstat(fd)
        if entry is None:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or (getuid is not None and info.st_uid != getuid())
        ):
            raise HomeInUseError("unsafe retention receipt file")
        if entry is not None and (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino):
            raise HomeInUseError("retention receipt changed during validation")
        _write_all(fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(root_fd)


def _read_retention_receipts(root_fd: int) -> tuple[dict[str, object], ...]:
    """Read and validate the bounded append-only quarantine ledger."""
    entry = _entry_stat(_RETENTION_RECEIPT_NAME, root_fd)
    if entry is None:
        return ()
    getuid = getattr(os, "geteuid", None)
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or stat.S_IMODE(entry.st_mode) != 0o600
        or entry.st_size > _RETENTION_MAX_RECEIPT_BYTES
        or (getuid is not None and entry.st_uid != getuid())
    ):
        raise HomeInUseError("unsafe retention receipt file")
    raw = _read_bounded_regular(
        Path(_RETENTION_RECEIPT_NAME),
        dir_fd=root_fd,
        private=True,
        max_bytes=_RETENTION_MAX_RECEIPT_BYTES,
    )
    if raw is None:
        raise HomeInUseError("unreadable retention receipt file")

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HomeInUseError(f"invalid retention receipt at line {line_number}") from exc
        if not isinstance(record, dict):
            raise HomeInUseError(f"invalid retention receipt at line {line_number}")
        quarantine = record.get("quarantine")
        home = record.get("home")
        device = record.get("device")
        inode = record.get("inode")
        size_bytes = record.get("size_bytes")
        timestamp_ns = record.get("timestamp_ns")
        reason = record.get("reason")
        if (
            record.get("schema") != "tokenpak.codex.retention.v1"
            or record.get("action") not in {"planned", "completed"}
            or not isinstance(quarantine, str)
            or _RETENTION_QUARANTINE_RE.fullmatch(quarantine) is None
            or not isinstance(home, str)
            or _SESSION_ID_RE.fullmatch(home) is None
            or not isinstance(device, int)
            or isinstance(device, bool)
            or device < 0
            or not isinstance(inode, int)
            or isinstance(inode, bool)
            or inode <= 0
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
            or not isinstance(timestamp_ns, int)
            or isinstance(timestamp_ns, bool)
            or timestamp_ns <= 0
            or not isinstance(reason, str)
            or _SESSION_ID_RE.fullmatch(reason) is None
        ):
            raise HomeInUseError(f"invalid retention receipt at line {line_number}")
        records.append(record)
    return tuple(records)


def _remove_tree_contents_at(
    directory_fd: int,
    *,
    root_device: int,
    deadline: float,
    counter: list[int],
) -> None:
    """Delete a quarantined tree through pinned descriptors only."""
    try:
        names: list[str] = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    raise HomeInUseError("quarantined isolated-home cleanup timed out")
                if len(names) + counter[0] >= _RETENTION_MAX_NODES:
                    raise HomeInUseError("quarantined isolated home exceeds cleanup node limit")
                names.append(entry.name)
    except OSError as exc:
        raise HomeInUseError("cannot enumerate quarantined isolated home") from exc
    for name in names:
        if time.monotonic() >= deadline:
            raise HomeInUseError("quarantined isolated-home cleanup timed out")
        counter[0] += 1
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if info.st_dev != root_device:
            raise HomeInUseError("refusing to cross a mount during isolated-home cleanup")
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                pinned = os.fstat(child_fd)
                if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino):
                    raise HomeInUseError("directory changed during isolated-home cleanup")
                _remove_tree_contents_at(
                    child_fd,
                    root_device=root_device,
                    deadline=deadline,
                    counter=counter,
                )
            finally:
                os.close(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise HomeInUseError("directory changed before isolated-home removal")
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _purge_quarantine_at(
    root_fd: int,
    quarantine: str,
    *,
    expected_device: int,
    expected_inode: int,
    deadline: float,
) -> None:
    """Delete one receipt-proven quarantine through pinned descriptors."""
    if _RETENTION_QUARANTINE_RE.fullmatch(quarantine) is None:
        raise HomeInUseError("invalid isolated-home quarantine name")
    entry = _entry_stat(quarantine, root_fd)
    getuid = getattr(os, "geteuid", None)
    if (
        entry is None
        or not stat.S_ISDIR(entry.st_mode)
        or stat.S_IMODE(entry.st_mode) != 0o700
        or (getuid is not None and entry.st_uid != getuid())
        or (entry.st_dev, entry.st_ino) != (expected_device, expected_inode)
    ):
        raise HomeInUseError("quarantine does not match its retention receipt")
    quarantine_fd = os.open(
        quarantine,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    try:
        pinned = os.fstat(quarantine_fd)
        if (
            not stat.S_ISDIR(pinned.st_mode)
            or stat.S_IMODE(pinned.st_mode) != 0o700
            or (getuid is not None and pinned.st_uid != getuid())
            or (pinned.st_dev, pinned.st_ino) != (expected_device, expected_inode)
        ):
            raise HomeInUseError("quarantine inode does not match its retention receipt")
        _remove_tree_contents_at(
            quarantine_fd,
            root_device=expected_device,
            deadline=deadline,
            counter=[0],
        )
    finally:
        os.close(quarantine_fd)
    current = os.stat(quarantine, dir_fd=root_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (expected_device, expected_inode):
        raise HomeInUseError("quarantine changed before final removal")
    os.rmdir(quarantine, dir_fd=root_fd)
    _fsync_directory(root_fd)


def _recover_quarantines_at(
    root: Path,
    root_fd: int,
    *,
    deadline: float,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Resume receipt-proven quarantines; preserve and report all others."""
    names: list[str] = []
    try:
        with os.scandir(root_fd) as entries:
            for entry_count, entry in enumerate(entries, start=1):
                if time.monotonic() >= deadline:
                    return (), ("quarantine recovery wall-time limit reached",)
                if entry_count > _RETENTION_MAX_ENTRIES:
                    return (), ("quarantine inventory exceeds retention entry limit",)
                if entry.name.startswith(_RETENTION_QUARANTINE_PREFIX):
                    names.append(entry.name)
    except OSError as exc:
        return (), (f"cannot inspect quarantine inventory: {exc}",)
    names.sort()
    if not names:
        return (), ()
    if len(names) > _RETENTION_MAX_ENTRIES:
        return (), ("quarantine inventory exceeds retention entry limit",)
    try:
        records = _read_retention_receipts(root_fd)
    except (OSError, RuntimeError) as exc:
        return (), (str(exc),)

    planned: dict[str, dict[str, object]] = {}
    completed: set[str] = set()
    conflicted: set[str] = set()
    errors: list[str] = []
    for record in records:
        quarantine = str(record["quarantine"])
        if record["action"] == "completed":
            completed.add(quarantine)
            continue
        prior = planned.get(quarantine)
        if prior is not None and any(
            prior[field] != record[field]
            for field in ("home", "device", "inode", "size_bytes", "reason")
        ):
            errors.append(f"{quarantine}: conflicting planned retention receipts")
            conflicted.add(quarantine)
            planned.pop(quarantine, None)
            continue
        if quarantine in conflicted:
            continue
        planned[quarantine] = record

    removed: list[Path] = []
    for quarantine in names:
        if time.monotonic() >= deadline:
            errors.append("quarantine recovery wall-time limit reached")
            break
        if quarantine in conflicted:
            continue
        receipt = planned.get(quarantine)
        if receipt is None:
            errors.append(f"{quarantine}: no planned retention receipt")
            continue
        if quarantine in completed:
            errors.append(f"{quarantine}: completed receipt still has a quarantine")
            continue
        try:
            _purge_quarantine_at(
                root_fd,
                quarantine,
                expected_device=cast(int, receipt["device"]),
                expected_inode=cast(int, receipt["inode"]),
                deadline=deadline,
            )
            _append_retention_receipt(
                root_fd,
                {
                    **receipt,
                    "action": "completed",
                    "timestamp_ns": time.time_ns(),
                },
            )
            removed.append(root / str(receipt["home"]))
        except (OSError, RuntimeError) as exc:
            errors.append(f"{quarantine}: {exc}")
    return tuple(removed), tuple(errors)


def _retire_isolated_home_at(
    root: Path,
    root_fd: int,
    expected: _IsolatedHomeInfo,
    *,
    reason: str,
    deadline: float,
    proc_root: Path = Path("/proc"),
) -> Path:
    """Revalidate, receipt, quarantine, and delete exactly one orphan."""
    home_fd = os.open(
        expected.path.name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    try:
        pinned = os.fstat(home_fd)
        if (pinned.st_dev, pinned.st_ino) != (expected.device, expected.inode):
            raise HomeInUseError(f"isolated home changed before cleanup: {expected.path}")
        # cleanup already holds the root retention guard.  Joining any
        # pre-existing home guard closes races with older launchers that did
        # not yet coordinate transfer publication through the root guard.
        with _existing_lease_guard(expected.path, home_fd=home_fd):
            current = _inspect_isolated_home_at(
                root_fd,
                root,
                expected.path.name,
                now=time.time(),
                deadline=deadline,
                proc_root=proc_root,
            )
            if (
                not current.orphaned
                or not current.size_complete
                or (current.device, current.inode) != (expected.device, expected.inode)
            ):
                raise HomeInUseError(f"isolated home changed before cleanup: {expected.path}")

            from . import state_lock

            status = state_lock.probe(
                current.path,
                deadline=deadline,
            )
            if status.locked or not status.diagnostics_complete:
                raise HomeInUseError(
                    f"isolated home has live or incomplete holder evidence: {current.path}"
                )
            final = _inspect_isolated_home_at(
                root_fd,
                root,
                expected.path.name,
                now=time.time(),
                deadline=deadline,
                proc_root=proc_root,
            )
            stable_fields = (
                "device",
                "inode",
                "mtime",
                "size_bytes",
                "size_complete",
                "state",
                "pid",
            )
            if not final.orphaned or any(
                getattr(final, field) != getattr(current, field) for field in stable_fields
            ):
                raise HomeInUseError(
                    f"isolated home changed after holder inspection: {expected.path}"
                )
            quarantine = f"{_RETENTION_QUARANTINE_PREFIX}{uuid.uuid4().hex}"
            receipt = {
                "schema": "tokenpak.codex.retention.v1",
                "action": "planned",
                "home": current.path.name,
                "device": current.device,
                "inode": current.inode,
                "size_bytes": current.size_bytes,
                "reason": reason,
                "quarantine": quarantine,
                "timestamp_ns": time.time_ns(),
            }
            _append_retention_receipt(root_fd, receipt)
            os.rename(current.path.name, quarantine, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            _fsync_directory(root_fd)
    finally:
        os.close(home_fd)
    _purge_quarantine_at(
        root_fd,
        quarantine,
        expected_device=current.device,
        expected_inode=current.inode,
        deadline=deadline,
    )
    _append_retention_receipt(
        root_fd,
        {
            **receipt,
            "action": "completed",
            "timestamp_ns": time.time_ns(),
        },
    )
    return current.path


def _retention_plan(
    report: _RetentionReport,
    *,
    preserve_home: Path | None,
    remove_all_orphans: bool,
    remove_all_reason: str = "explicit-orphan-cleanup",
) -> list[tuple[_IsolatedHomeInfo, str]]:
    preserve = str(preserve_home.resolve()) if preserve_home is not None else None
    eligible = [
        home
        for home in report.homes
        if home.orphaned
        and home.size_complete
        and (preserve is None or str(home.path.resolve()) != preserve)
    ]
    eligible.sort(key=lambda home: (home.mtime, home.path.name))
    if remove_all_orphans:
        return [(home, remove_all_reason) for home in eligible]

    planned: list[tuple[_IsolatedHomeInfo, str]] = []
    selected: set[Path] = set()
    for home in eligible:
        if home.age_s > RETENTION_MAX_AGE_S:
            planned.append((home, "age"))
            selected.add(home.path)

    remaining_count = len(report.homes) - len(selected)
    for home in eligible:
        if remaining_count <= RETENTION_MAX_HOMES:
            break
        if home.path in selected:
            continue
        planned.append((home, "count"))
        selected.add(home.path)
        remaining_count -= 1

    remaining_bytes = report.total_bytes - sum(
        home.size_bytes for home in eligible if home.path in selected
    )
    for home in eligible:
        if remaining_bytes <= RETENTION_MAX_TOTAL_BYTES:
            break
        if home.path in selected:
            continue
        planned.append((home, "size"))
        selected.add(home.path)
        remaining_bytes -= home.size_bytes
    return planned


def cleanup_isolated_homes(
    tokenpak_home: Path | None = None,
    *,
    preserve_home: Path | None = None,
    remove_all_orphans: bool = False,
    dry_run: bool = False,
    orphan_cleanup_reason: str = "explicit-orphan-cleanup",
    proc_root: Path = Path("/proc"),
) -> _CleanupResult:
    """Apply the isolated-home policy without touching live/unknown homes."""
    if tokenpak_home is None and preserve_home is not None:
        tokenpak_home = _generated_tokenpak_root(preserve_home)
    opened = _open_managed_sessions_root(tokenpak_home)
    if opened is None:
        empty = inspect_isolated_homes(tokenpak_home, proc_root=proc_root)
        return _CleanupResult(empty, empty, (), (), ())
    root, root_fd = opened
    removed: list[Path] = []
    errors: list[str] = []
    deadline = time.monotonic() + _RETENTION_SCAN_TIMEOUT_S
    try:
        with _retention_guard(root, root_fd):
            if not dry_run:
                recovered, recovery_errors = _recover_quarantines_at(
                    root,
                    root_fd,
                    deadline=deadline,
                )
                removed.extend(recovered)
                errors.extend(recovery_errors)
            before = inspect_isolated_homes(
                tokenpak_home,
                deadline=deadline,
                proc_root=proc_root,
            )
            if not before.inventory_complete:
                errors.append("isolated-home inventory incomplete; preserving all ordinary homes")
                plan: list[tuple[_IsolatedHomeInfo, str]] = []
            else:
                plan = _retention_plan(
                    before,
                    preserve_home=preserve_home,
                    remove_all_orphans=remove_all_orphans,
                    remove_all_reason=orphan_cleanup_reason,
                )
            if not dry_run:
                for home, reason in plan:
                    if time.monotonic() >= deadline:
                        errors.append("retention cleanup wall-time limit reached")
                        break
                    try:
                        removed.append(
                            _retire_isolated_home_at(
                                root,
                                root_fd,
                                home,
                                reason=reason,
                                deadline=deadline,
                                proc_root=proc_root,
                            )
                        )
                    except (OSError, RuntimeError) as exc:
                        errors.append(f"{home.path.name}: {exc}")
            after = (
                before
                if dry_run
                else inspect_isolated_homes(
                    tokenpak_home,
                    deadline=deadline,
                    proc_root=proc_root,
                )
            )
            return _CleanupResult(
                before=before,
                after=after,
                removed=tuple(removed),
                planned=tuple(home.path for home, _reason in plan),
                errors=tuple(errors),
            )
    finally:
        os.close(root_fd)


def _fsync_directory(dir_fd: int) -> None:
    """Persist directory-entry publication on platforms that support it."""
    try:
        os.fsync(dir_fd)
    except OSError as exc:  # pragma: no cover - Windows directory handles
        if exc.errno not in {errno.EINVAL, errno.EBADF}:
            raise


def _sentinel_payload(sentinel: PidSentinel) -> bytes:
    return (json.dumps(asdict(sentinel), sort_keys=True) + "\n").encode("utf-8")


def _create_sentinel_temp(
    path: Path,
    sentinel: PidSentinel,
    *,
    dir_fd: int | None,
    phase: str,
) -> tuple[str, tuple[int, int]]:
    """Durably create and verify one private lifecycle temp artifact."""
    if phase not in {"acquire", "transfer"}:
        raise ValueError("invalid sentinel publication phase")
    payload = _sentinel_payload(sentinel)
    tmp_name = f".{PID_SENTINEL_NAME}.{phase}.{uuid.uuid4().hex}.tmp"
    tmp_target = tmp_name if dir_fd is not None else str(path.parent / tmp_name)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp_target, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, payload)
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        persisted = os.read(fd, _MAX_SENTINEL_BYTES + 1)
        try:
            parsed = _sentinel_from_data(json.loads(persisted))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        info = os.fstat(fd)
        if parsed != sentinel:
            raise OSError("sentinel verification failed before publication")
        identity = (info.st_dev, info.st_ino)
        os.close(fd)
        fd = -1
        if dir_fd is not None:
            _fsync_directory(dir_fd)
        return tmp_name, identity
    except BaseException:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            if dir_fd is None:
                os.unlink(path.parent / tmp_name)
            else:
                os.unlink(tmp_name, dir_fd=dir_fd)
                with contextlib.suppress(OSError):
                    _fsync_directory(dir_fd)
        raise


def _write_sentinel_atomic(
    path: Path,
    sentinel: PidSentinel,
    *,
    dir_fd: int | None,
    phase: str,
    replace_existing: bool,
) -> None:
    """Publish a complete private sentinel with fsync + atomic rename."""
    tmp_name, _identity = _create_sentinel_temp(
        path,
        sentinel,
        dir_fd=dir_fd,
        phase=phase,
    )
    published = False
    try:
        existing = (
            _entry_stat(PID_SENTINEL_NAME, dir_fd)
            if dir_fd is not None
            else path.lstat()
            if path.exists() or path.is_symlink()
            else None
        )
        if not replace_existing and existing is not None:
            raise FileExistsError(PID_SENTINEL_NAME)
        if dir_fd is None:
            os.replace(path.parent / tmp_name, path)
            published = True
        else:
            os.replace(
                tmp_name,
                PID_SENTINEL_NAME,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
            published = True
            _fsync_directory(dir_fd)
    except BaseException:
        # Initial publication has no predecessor to restore.  If durability
        # confirmation fails after rename, remove only the exact record just
        # written so a bounded ENOSPC/EDQUOT retry cannot collide with itself.
        if published and not replace_existing:
            current = read_pid_sentinel(path, dir_fd=dir_fd)
            if current == sentinel:
                with contextlib.suppress(OSError):
                    _unlink_sentinel(path, dir_fd)
        raise
    finally:
        with contextlib.suppress(OSError):
            if dir_fd is None:
                os.unlink(path.parent / tmp_name)
            else:
                os.unlink(tmp_name, dir_fd=dir_fd)


def _unlink_owned_sentinel_temp(
    path: Path,
    *,
    name: str,
    expected_identity: tuple[int, int],
    expected_sentinel: PidSentinel,
    dir_fd: int | None,
) -> None:
    """Remove only the exact durable handoff marker this lease created."""
    entry = (
        _entry_stat(name, dir_fd)
        if dir_fd is not None
        else (path.parent / name).lstat()
        if (path.parent / name).exists() or (path.parent / name).is_symlink()
        else None
    )
    if entry is None:
        return
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or stat.S_IMODE(entry.st_mode) != 0o600
        or (entry.st_dev, entry.st_ino) != expected_identity
    ):
        raise HomeInUseError(f"handoff marker changed during launch: {path.parent / name}")
    current = read_pid_sentinel(
        Path(name) if dir_fd is not None else path.parent / name,
        dir_fd=dir_fd,
    )
    if current != expected_sentinel:
        raise HomeInUseError(f"handoff marker content changed during launch: {path.parent / name}")
    if dir_fd is None:
        os.unlink(path.parent / name)
    else:
        os.unlink(name, dir_fd=dir_fd)
        _fsync_directory(dir_fd)


def _recover_sentinel_temps(
    path: Path,
    *,
    dir_fd: int | None,
    proc_root: Path,
) -> None:
    """Recover only positively identified crash artifacts under the guard."""
    getuid = getattr(os, "geteuid", None)
    location = dir_fd if dir_fd is not None else path.parent
    try:
        entries = os.scandir(location)
    except OSError as exc:
        raise HomeInUseError(f"cannot inspect sentinel recovery artifacts: {path.parent}") from exc
    with entries:
        names: list[str] = []
        for entry_count, candidate_entry in enumerate(entries, start=1):
            if entry_count > _RETENTION_MAX_ENTRIES:
                raise HomeInUseError("sentinel recovery artifact inventory exceeds limit")
            if _is_sentinel_temp_candidate(candidate_entry.name):
                names.append(candidate_entry.name)
    for name in names:
        match = _SENTINEL_TEMP_RE.fullmatch(name)
        if match is None:
            raise HomeInUseError(f"unsafe sentinel recovery artifact: {path.parent / name}")
        if dir_fd is not None:
            entry_stat = _entry_stat(name, dir_fd)
        else:
            try:
                entry_stat = (path.parent / name).lstat()
            except FileNotFoundError:
                entry_stat = None
        if entry_stat is None:
            continue
        if (
            not stat.S_ISREG(entry_stat.st_mode)
            or entry_stat.st_nlink != 1
            or stat.S_IMODE(entry_stat.st_mode) != 0o600
            or (getuid is not None and entry_stat.st_uid != getuid())
        ):
            raise HomeInUseError(f"unsafe sentinel recovery artifact: {path.parent / name}")
        candidate = read_pid_sentinel(
            Path(name) if dir_fd is not None else path.parent / name,
            dir_fd=dir_fd,
        )
        phase = match.group(1)
        if candidate is None:
            if phase == "transfer":
                raise HomeInUseError(
                    f"partial transfer sentinel requires manual inspection: {path.parent / name}"
                )
            os.unlink(name if dir_fd is not None else path.parent / name, dir_fd=dir_fd)
            if dir_fd is not None:
                _fsync_directory(dir_fd)
            continue
        liveness = _sentinel_liveness(
            candidate,
            expected_home=path.parent,
            proc_root=proc_root,
        )
        if liveness == _PROCESS_LIVE:
            raise HomeInUseError(
                f"live sentinel recovery artifact claims PID {candidate.pid}: {path.parent / name}"
            )
        if liveness == _PROCESS_UNKNOWN:
            raise HomeInUseError(
                f"incomplete sentinel recovery evidence for PID {candidate.pid}: "
                f"{path.parent / name}"
            )
        if phase == "transfer":
            raise HomeInUseError(
                f"interrupted transfer sentinel requires manual inspection: {path.parent / name}"
            )
        os.unlink(name if dir_fd is not None else path.parent / name, dir_fd=dir_fd)
        if dir_fd is not None:
            _fsync_directory(dir_fd)


def _write_sentinel_exclusive(
    path: Path, sentinel: PidSentinel, *, dir_fd: int | None = None
) -> None:
    """Compatibility wrapper for guarded initial atomic publication."""
    _write_sentinel_atomic(
        path,
        sentinel,
        dir_fd=dir_fd,
        phase="acquire",
        replace_existing=False,
    )


def _sentinel_stat(path: Path, home_fd: int | None) -> os.stat_result | None:
    if home_fd is not None:
        return _entry_stat(PID_SENTINEL_NAME, home_fd)
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _unlink_sentinel(path: Path, home_fd: int | None) -> None:
    if home_fd is None:
        path.unlink()
    else:
        os.unlink(PID_SENTINEL_NAME, dir_fd=home_fd)
        _fsync_directory(home_fd)


class SessionLease:
    """Owner-checked ``codex.pid`` lifecycle lease."""

    def __init__(
        self,
        paths: SessionPaths,
        sentinel: PidSentinel,
        *,
        proc_root: Path = Path("/proc"),
        home_fd: int | None,
    ) -> None:
        self.paths = paths
        self.sentinel = sentinel
        self.proc_root = proc_root
        self.home_fd = home_fd
        self._released = False
        self._handoff_temp: tuple[str, tuple[int, int], PidSentinel] | None = None

    def assert_home_binding(self) -> None:
        """Fail if the selected pathname no longer names the pinned home."""
        if self.paths.mode == MODE_SHARED:
            return
        if self.home_fd is None:
            raise HomeInUseError("selected CODEX_HOME is missing its pinned directory descriptor")
        try:
            current = os.stat(self.paths.home, follow_symlinks=False)
            pinned = os.fstat(self.home_fd)
        except OSError as exc:
            raise HomeInUseError("selected CODEX_HOME disappeared during launch") from exc
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            pinned.st_dev,
            pinned.st_ino,
        ):
            raise HomeInUseError("selected CODEX_HOME changed during launch")

    @contextlib.contextmanager
    def _mutation_guard(self) -> Iterator[None]:
        """Serialize isolated lifecycle mutation in retention→home order."""
        managed_root = _generated_tokenpak_root(self.paths.home)
        if self.paths.mode != MODE_ISOLATED or managed_root is None:
            with _lease_guard(self.paths.home, home_fd=self.home_fd):
                yield
            return
        opened = _open_managed_sessions_root(managed_root)
        if opened is None:
            raise HomeInUseError("managed sessions root disappeared during launch")
        root, root_fd = opened
        try:
            with _retention_guard(root, root_fd):
                self.assert_home_binding()
                with _lease_guard(self.paths.home, home_fd=self.home_fd):
                    yield
        finally:
            os.close(root_fd)

    @classmethod
    def acquire(
        cls,
        paths: SessionPaths,
        *,
        pid: int | None = None,
        session_id: str | None = None,
        proc_root: Path = Path("/proc"),
    ) -> "SessionLease":
        home_fd: int | None = None
        owner_pid = pid if pid is not None else os.getpid()
        identity = _proc_identity(owner_pid, proc_root)
        if identity is None:
            raise RuntimeError(f"cannot validate launcher PID {owner_pid}")
        state, start_ticks = identity
        if state in {"Z", "X", "x"}:
            raise RuntimeError(f"launcher PID {owner_pid} is not running")
        validated_session_id = _validated_session_id(session_id)

        def claim(sentinel: PidSentinel) -> None:
            with _lease_guard(paths.home, home_fd=home_fd):
                _recover_sentinel_temps(
                    paths.pid_sentinel,
                    dir_fd=home_fd,
                    proc_root=proc_root,
                )
                if _sentinel_stat(paths.pid_sentinel, home_fd) is not None:
                    existing = read_pid_sentinel(paths.pid_sentinel, dir_fd=home_fd)
                    if existing is None:
                        raise HomeInUseError(
                            f"invalid {paths.pid_sentinel}; refusing unsafe replacement"
                        )
                    process_liveness = _sentinel_liveness(
                        existing,
                        proc_root=proc_root,
                    )
                    liveness = _sentinel_liveness(
                        existing,
                        expected_home=paths.home,
                        proc_root=proc_root,
                    )
                    if process_liveness == _PROCESS_LIVE and liveness != _PROCESS_LIVE:
                        raise HomeInUseError(
                            f"live {paths.pid_sentinel} is bound to another home; "
                            "refusing unsafe replacement"
                        )
                    if liveness == _PROCESS_LIVE:
                        raise HomeInUseError(
                            f"{paths.home} is already claimed by PID {existing.pid}"
                        )
                    if liveness == _PROCESS_UNKNOWN:
                        raise HomeInUseError(
                            f"incomplete {paths.pid_sentinel} process evidence; "
                            "refusing unsafe replacement"
                        )
                    _unlink_sentinel(paths.pid_sentinel, home_fd)
                _write_sentinel_exclusive(paths.pid_sentinel, sentinel, dir_fd=home_fd)

        try:
            managed_root = _generated_tokenpak_root(paths.home)
            if paths.mode == MODE_ISOLATED and managed_root is not None:
                root_opened = _open_managed_sessions_root(managed_root, create=True)
                assert root_opened is not None
                root_path, root_fd = root_opened
                try:
                    with _retention_guard(root_path, root_fd):
                        # Publication ordering is deliberate: the isolated leaf
                        # does not become visible until this process owns the
                        # same coordinator used by retention cleanup, and the
                        # guard remains held through the durable sentinel rename.
                        home_fd = _open_isolated_leaf_at(paths, root_fd)
                        sentinel = PidSentinel(
                            schema=_SENTINEL_SCHEMA,
                            pid=owner_pid,
                            start_time_ticks=start_ticks,
                            session_id=validated_session_id,
                            mode=paths.mode,
                            home=str(paths.home.resolve()),
                        )
                        claim(sentinel)
                finally:
                    os.close(root_fd)
            else:
                home_fd = _open_selected_home(paths)
                sentinel = PidSentinel(
                    schema=_SENTINEL_SCHEMA,
                    pid=owner_pid,
                    start_time_ticks=start_ticks,
                    session_id=validated_session_id,
                    mode=paths.mode,
                    home=str(paths.home.resolve()),
                )
                claim(sentinel)
            return cls(paths, sentinel, proc_root=proc_root, home_fd=home_fd)
        except BaseException:
            if home_fd is not None:
                os.close(home_fd)
            raise

    def begin_transfer(self) -> None:
        """Publish a durable handoff marker before spawning the child."""
        if self._handoff_temp is not None:
            raise HomeInUseError("PID sentinel handoff is already in progress")
        with self._mutation_guard():
            current = read_pid_sentinel(self.paths.pid_sentinel, dir_fd=self.home_fd)
            if current != self.sentinel:
                raise HomeInUseError("PID sentinel ownership changed before child launch")
            name, identity = _create_sentinel_temp(
                self.paths.pid_sentinel,
                self.sentinel,
                dir_fd=self.home_fd,
                phase="transfer",
            )
            self._handoff_temp = (name, identity, self.sentinel)

    def transfer_to(self, pid: int) -> None:
        """Transfer the lease to the spawned child process incarnation."""
        if self._handoff_temp is None:
            # Compatibility for direct callers; the launcher calls
            # begin_transfer() before Popen to close the spawn window.
            self.begin_transfer()
        identity = _proc_identity(pid, self.proc_root)
        if identity is None:
            raise RuntimeError(f"cannot validate Codex child PID {pid}")
        state, start_ticks = identity
        if state in {"Z", "X", "x"}:
            raise RuntimeError(f"Codex child PID {pid} is not running")
        replacement = PidSentinel(
            schema=_SENTINEL_SCHEMA,
            pid=pid,
            start_time_ticks=start_ticks,
            session_id=self.sentinel.session_id,
            mode=self.sentinel.mode,
            home=self.sentinel.home,
        )
        published = False
        try:
            with self._mutation_guard():
                current = read_pid_sentinel(self.paths.pid_sentinel, dir_fd=self.home_fd)
                if current != self.sentinel:
                    raise HomeInUseError("PID sentinel ownership changed during launch")
                revalidated = _proc_identity(pid, self.proc_root)
                if (
                    revalidated != identity
                    or revalidated is None
                    or revalidated[0]
                    in {
                        "Z",
                        "X",
                        "x",
                    }
                ):
                    raise HomeInUseError("Codex child PID changed during handoff")
                try:
                    _write_sentinel_atomic(
                        self.paths.pid_sentinel,
                        replacement,
                        dir_fd=self.home_fd,
                        phase="transfer",
                        replace_existing=True,
                    )
                except BaseException:
                    # Rename may have succeeded before directory durability
                    # reported ENOSPC/EDQUOT.  Bind this lease to the exact
                    # replacement so supervised closeout can remove it later.
                    if (
                        read_pid_sentinel(self.paths.pid_sentinel, dir_fd=self.home_fd)
                        == replacement
                    ):
                        published = True
                        self.sentinel = replacement
                    raise
                published = True
                self.sentinel = replacement
                assert self._handoff_temp is not None
                name, marker_identity, marker_sentinel = self._handoff_temp
                _unlink_owned_sentinel_temp(
                    self.paths.pid_sentinel,
                    name=name,
                    expected_identity=marker_identity,
                    expected_sentinel=marker_sentinel,
                    dir_fd=self.home_fd,
                )
                self._handoff_temp = None
        finally:
            if published:
                self.sentinel = replacement

    def release(self) -> bool:
        """Remove only the still-matching sentinel owned by this session."""
        if self._released:
            return False
        removed = False
        try:
            with self._mutation_guard():
                if self._handoff_temp is not None:
                    name, marker_identity, marker_sentinel = self._handoff_temp
                    _unlink_owned_sentinel_temp(
                        self.paths.pid_sentinel,
                        name=name,
                        expected_identity=marker_identity,
                        expected_sentinel=marker_sentinel,
                        dir_fd=self.home_fd,
                    )
                    self._handoff_temp = None
                current = read_pid_sentinel(self.paths.pid_sentinel, dir_fd=self.home_fd)
                if current == self.sentinel:
                    with contextlib.suppress(FileNotFoundError):
                        _unlink_sentinel(self.paths.pid_sentinel, self.home_fd)
                    removed = True
        finally:
            self._released = True
            if self.home_fd is not None:
                os.close(self.home_fd)
        return removed

    def __enter__(self) -> "SessionLease":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.release()
