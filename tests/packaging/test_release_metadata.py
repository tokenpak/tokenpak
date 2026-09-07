# SPDX-License-Identifier: Apache-2.0
"""Source and built-artifact coverage for installed release metadata."""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import pytest

import tokenpak
from tokenpak import release_metadata

ROOT = Path(__file__).resolve().parents[2]
VERSION_PARTS = tuple(int(part) for part in tokenpak.__version__.split("."))


def _metadata(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tokenpak_version": tokenpak.__version__,
        "asserted_tip_version": "TIP-1.0",
        "pinned_registry_schema_tag": "schema-2026-07-18",
        "pinned_docs_revision": "v1.18.5-docs@abc123",
    }
    payload.update(overrides)
    return payload


def test_packaged_metadata_matches_canonical_source_and_module_version() -> None:
    canonical = ROOT / "version.json"
    packaged = ROOT / "tokenpak" / "version.json"
    assert packaged.read_bytes() == canonical.read_bytes()
    payload = json.loads(packaged.read_text(encoding="utf-8"))
    assert payload["tokenpak_version"] == tokenpak.__version__

    loaded = release_metadata.load_release_metadata()
    assert loaded.tokenpak_version == VERSION_PARTS
    assert loaded.asserted_tip_version == (1, 0)


def test_version_resource_is_declared_as_package_data() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python <3.11
        import tomli as tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["tokenpak"]
    assert "version.json" in package_data


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"tokenpak_version":"1.24.0"}',
        b"[" * 1_100,
        b'{"n":' + (b"9" * 5_000) + b"}",
    ],
)
def test_malformed_metadata_fails_closed(tmp_path, monkeypatch, raw):
    (tmp_path / "version.json").write_bytes(raw)
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    with pytest.raises(release_metadata.ReleaseMetadataError) as excinfo:
        release_metadata.load_release_metadata()
    assert excinfo.value.reason == "oss_metadata_malformed"


def test_packaged_version_disagreement_is_malformed(tmp_path, monkeypatch):
    (tmp_path / "version.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    next_patch = ".".join(map(str, (*VERSION_PARTS[:2], VERSION_PARTS[2] + 1)))
    monkeypatch.setattr(release_metadata, "__version__", next_patch)
    with pytest.raises(release_metadata.ReleaseMetadataError) as excinfo:
        release_metadata.load_release_metadata()
    assert excinfo.value.reason == "oss_metadata_malformed"


def test_missing_metadata_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    with pytest.raises(release_metadata.ReleaseMetadataError) as excinfo:
        release_metadata.load_release_metadata()
    assert excinfo.value.reason == "oss_metadata_missing"


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _metadata().items() if key != "pinned_docs_revision"},
        _metadata(unexpected="value"),
        _metadata(pinned_registry_schema_tag=""),
        _metadata(pinned_docs_revision=7),
        _metadata(pinned_docs_revision="x" * 513),
        _metadata(asserted_tip_version="TIP-1.x"),
        _metadata(tokenpak_version="1." + ("9" * 5_000) + ".0"),
    ],
)
def test_metadata_requires_exact_bounded_canonical_fields(tmp_path, monkeypatch, payload):
    (tmp_path / "version.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    with pytest.raises(release_metadata.ReleaseMetadataError) as excinfo:
        release_metadata.load_release_metadata()
    assert excinfo.value.reason == "oss_metadata_malformed"


def test_metadata_preserves_opaque_exact_pin_syntax(tmp_path, monkeypatch):
    payload = _metadata(
        pinned_registry_schema_tag="refs/tags/schema:2026-09-07",
        pinned_docs_revision="release/docs@abc123",
    )
    (tmp_path / "version.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    assert release_metadata.load_release_metadata() == (VERSION_PARTS, (1, 0))


def test_duplicate_metadata_key_is_malformed(tmp_path, monkeypatch):
    payload = json.loads((ROOT / "version.json").read_bytes())
    duplicate = '"tokenpak_version": ' + json.dumps(payload["tokenpak_version"]) + ", "
    raw = ("{" + duplicate + json.dumps(payload)[1:]).encode("utf-8")
    assert raw.count(b'"tokenpak_version":') == 2
    (tmp_path / "version.json").write_bytes(raw)
    monkeypatch.setattr(release_metadata.resources, "files", lambda _package: tmp_path)
    with pytest.raises(release_metadata.ReleaseMetadataError) as excinfo:
        release_metadata.load_release_metadata()
    assert excinfo.value.reason == "oss_metadata_malformed"


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory):
    output = tmp_path_factory.mktemp("release-metadata-dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(output.glob("*.whl"))
    sdist = next(output.glob("*.tar.gz"))
    return wheel, sdist


def _isolated_import(path: Path, cwd: Path) -> str:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(path)!r}); "
        "from tokenpak.release_metadata import load_release_metadata; "
        "m=load_release_metadata(); "
        "print('.'.join(map(str,m.tokenpak_version))+'|'+'.'.join(map(str,m.asserted_tip_version)))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_built_wheel_contains_and_imports_metadata(built_distributions, tmp_path):
    wheel, _sdist = built_distributions
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("tokenpak/version.json") == (ROOT / "version.json").read_bytes()
    assert _isolated_import(wheel, tmp_path) == f"{tokenpak.__version__}|1.0"


def test_built_sdist_contains_and_source_imports_metadata(built_distributions, tmp_path):
    _wheel, sdist = built_distributions
    with tarfile.open(sdist, "r:gz") as archive:
        member = next(
            name for name in archive.getnames() if name.endswith("/tokenpak/version.json")
        )
        extracted = archive.extractfile(member)
        assert extracted is not None
        assert extracted.read() == (ROOT / "version.json").read_bytes()
        for entry in archive.getmembers():
            relative = PurePosixPath(entry.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            destination = tmp_path.joinpath(*relative.parts)
            if entry.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                source = archive.extractfile(entry)
                assert source is not None
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read())

    source_root = next(path for path in tmp_path.iterdir() if path.is_dir())
    assert _isolated_import(source_root, tmp_path) == f"{tokenpak.__version__}|1.0"
