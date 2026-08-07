"""Behavioral contracts for the A5 fresh-install timing gate."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts import fresh_install_demo


class _RetainedTemporaryDirectory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        self.path.mkdir()
        return str(self.path)

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeEnvironmentBuilder:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def create(self, env_dir: Path) -> None:
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        python = env_dir / scripts_dir / ("python.exe" if os.name == "nt" else "python")
        python.parent.mkdir(parents=True)
        python.touch()


def test_dependencies_are_staged_before_the_offline_install_clock(monkeypatch, tmp_path: Path):
    root = tmp_path / "fresh"
    events: list[str] = []
    clock = iter((10.0, 20.0, 24.0))

    def monotonic() -> float:
        value = next(clock)
        events.append(f"clock:{value}")
        return value

    def run(command: list[str], *, environment: dict[str, str], cwd: Path) -> str:
        assert cwd in {tmp_path, root}
        assert "PYTHONPATH" not in environment
        if command[1:4] == ["-m", "build", "--wheel"]:
            events.append("build")
            (root / "wheelhouse" / "tokenpak-1.18.2-py3-none-any.whl").touch()
        elif command[1:4] == ["-m", "pip", "download"]:
            events.append("download")
        elif command[1:4] == ["-m", "pip", "install"]:
            events.append("install")
            tokenpak = Path(command[0]).with_name("tokenpak.exe" if os.name == "nt" else "tokenpak")
            tokenpak.touch()
        elif command[-1] == "--help":
            events.append("help")
        elif command[-1] == "demo":
            events.append("demo")
            return "\n".join(fresh_install_demo.DEMO_MARKERS)
        else:  # pragma: no cover - exposes unexpected command-shape drift
            raise AssertionError(command)
        return ""

    monkeypatch.setattr(
        fresh_install_demo.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: _RetainedTemporaryDirectory(root),
    )
    monkeypatch.setattr(fresh_install_demo.venv, "EnvBuilder", _FakeEnvironmentBuilder)
    monkeypatch.setattr(fresh_install_demo.time, "monotonic", monotonic)
    monkeypatch.setattr(fresh_install_demo, "_run", run)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    elapsed = fresh_install_demo.run_fresh_install(tmp_path, max_seconds=60.0)

    assert elapsed == 4.0
    assert events == [
        "clock:10.0",
        "build",
        "download",
        "clock:20.0",
        "install",
        "help",
        "demo",
        "clock:24.0",
    ]


def test_timed_install_is_bound_to_the_staged_wheelhouse(monkeypatch, tmp_path: Path):
    root = tmp_path / "fresh"
    commands: list[list[str]] = []
    clock = iter((1.0, 2.0, 3.0))

    def run(command: list[str], *, environment: dict[str, str], cwd: Path) -> str:
        del environment, cwd
        commands.append(command)
        if command[1:4] == ["-m", "build", "--wheel"]:
            (root / "wheelhouse" / "tokenpak-1.18.2-py3-none-any.whl").touch()
        elif command[1:4] == ["-m", "pip", "install"]:
            Path(command[0]).with_name("tokenpak.exe" if os.name == "nt" else "tokenpak").touch()
        elif command[-1] == "demo":
            return "\n".join(fresh_install_demo.DEMO_MARKERS)
        return ""

    monkeypatch.setattr(
        fresh_install_demo.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: _RetainedTemporaryDirectory(root),
    )
    monkeypatch.setattr(fresh_install_demo.venv, "EnvBuilder", _FakeEnvironmentBuilder)
    monkeypatch.setattr(fresh_install_demo.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(fresh_install_demo, "_run", run)

    fresh_install_demo.run_fresh_install(tmp_path, max_seconds=60.0)

    download = next(command for command in commands if "download" in command)
    install = next(command for command in commands if "install" in command)
    candidate = root / "wheelhouse" / "tokenpak-1.18.2-py3-none-any.whl"
    assert download == [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(root / "wheelhouse"),
        str(candidate),
    ]
    assert install[-5:] == [
        "--quiet",
        "--no-index",
        "--find-links",
        str(root / "wheelhouse"),
        str(candidate),
    ]
    assert "--no-index" in install
