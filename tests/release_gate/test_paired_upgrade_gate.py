"""Paired-upgrade gate: synthetic wheels only, no pip/network/real homes."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "paired_upgrade_gate",
    Path(__file__).resolve().parents[2] / "scripts" / "release" / "paired_upgrade_gate.py",
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

NAMES = {"oss": "synth_oss", "paid": "synth_paid"}


def _wheel(dirp: Path, role: str, version: str, payload: str) -> Path:
    name = NAMES[role]
    w = dirp / f"{name}-{version}-py3-none-any.whl"
    di = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(w, "w") as zf:
        zf.writestr(f"{name}/__init__.py", payload)
        zf.writestr(f"{di}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        zf.writestr(f"{di}/RECORD", "")
    return w


def _manifest(tmp: Path, wheels: dict[str, Path], versions: dict[str, str]) -> Path:
    m = {
        r: {
            "name": NAMES[r],
            "version": versions[r],
            "wheel_sha256": hashlib.sha256(wheels[r].read_bytes()).hexdigest(),
        }
        for r in wheels
    }
    p = tmp / "manifest.json"
    p.write_text(json.dumps(m))
    return p


def _install(site: Path, wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)


@pytest.fixture
def env(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir(), bad.mkdir()
    v = {"oss": "9.1", "paid": "9.2"}
    new = {r: _wheel(good, r, v[r], f"# new {r}") for r in v}
    old = {r: _wheel(bad, r, v[r], f"# OLD {r}") for r in v}  # same name/version, different bytes
    man = _manifest(tmp_path, new, v)
    sentinel = tmp_path / "license.bytes"
    sentinel.write_bytes(b"SENTINEL-LICENSE\x00\x01")
    return tmp_path, good, bad, new, old, man, sentinel


@pytest.fixture(autouse=True)
def _target_env(tmp_path, monkeypatch):
    """The fixture env is the target venv: prefix is tmp, its site is importable."""
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    for name in NAMES.values():
        sys.modules.pop(name, None)


def _run(cmd, man, wdir, site=None, receipt=None):
    importlib.invalidate_caches()
    args = [cmd, "--manifest", str(man), "--wheel-dir", str(wdir)]
    if site is not None:
        args += ["--site-packages", str(site)]
    if receipt is not None:
        args += ["--receipt", str(receipt)]
    return gate.main(args)


def test_exact_pair_ready(env):
    tmp, good, _, new, _, man, sentinel = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    rc = _run("check-installed", man, good, site, tmp / "r.json")
    r = json.loads((tmp / "r.json").read_text())
    assert rc == 0 and r["ready"] is True and r["errors"] == []
    assert sentinel.read_bytes() == b"SENTINEL-LICENSE\x00\x01"


def test_artifacts_alone_never_ready(env):
    tmp, good, _, _, _, man, _ = env
    assert _run("check-artifacts", man, good, receipt=tmp / "r.json") == 0
    assert json.loads((tmp / "r.json").read_text())["ready"] is False


@pytest.mark.parametrize("stale", ["oss", "paid"])
def test_known_bad_mixes_fail_closed(env, stale):
    """new OSS + old paid and old OSS + new paid: same names/versions, wrong bytes."""
    tmp, good, _, new, old, man, sentinel = env
    site = tmp / "site"
    site.mkdir()
    for r in new:
        _install(site, old[r] if r == stale else new[r])
    rc = _run("check-installed", man, good, site, tmp / "r.json")
    r = json.loads((tmp / "r.json").read_text())
    assert rc == 1 and r["ready"] is False
    assert any(stale in e and "differ" in e for e in r["errors"])
    assert sentinel.read_bytes() == b"SENTINEL-LICENSE\x00\x01"


def test_mixed_artifacts_rejected_before_install(env):
    tmp, good, bad, new, old, man, _ = env
    mixed = tmp / "mixed"
    mixed.mkdir()
    (mixed / new["oss"].name).write_bytes(new["oss"].read_bytes())
    (mixed / old["paid"].name).write_bytes(old["paid"].read_bytes())
    assert _run("check-artifacts", man, mixed) == 1
    assert _run("check-artifacts", man, bad) == 1


def test_missing_single_unknown_extra(env):
    tmp, good, _, new, _, man, _ = env
    single = tmp / "single"
    single.mkdir()
    (single / new["oss"].name).write_bytes(new["oss"].read_bytes())
    assert _run("check-artifacts", man, single) == 1
    assert _run("check-artifacts", man, tmp / "nope") == 1
    unknown = tmp / "unknown"
    unknown.mkdir()
    for w in new.values():
        (unknown / w.name).write_bytes(w.read_bytes())
    _wheel(unknown, "oss", "9.9", "# other")
    assert _run("check-artifacts", man, unknown) == 1


def test_tampered_wheel_and_bad_manifest(env):
    tmp, good, _, new, _, man, _ = env
    new["paid"].write_bytes(new["paid"].read_bytes() + b"x")
    assert _run("check-artifacts", man, good) == 1
    bad = tmp / "bad.json"
    bad.write_text("{not json")
    assert _run("check-artifacts", bad, good) == 1
    bad.write_text(json.dumps({"oss": {"name": "a"}}))
    assert _run("check-artifacts", bad, good) == 1


def test_same_version_noop_and_partial_install_fail(env):
    tmp, good, _, new, old, man, sentinel = env
    site = tmp / "site"
    site.mkdir()
    _install(site, old["oss"])  # stale install; pip would no-op on same version
    rc = _run("check-installed", man, good, site, tmp / "r.json")
    assert rc == 1  # paid absent, oss stale
    _install(site, new["oss"])
    assert _run("check-installed", man, good, site) == 1  # paid still absent
    # partial: a file the pinned wheel ships is missing
    _install(site, new["paid"])
    (site / NAMES["paid"] / "__init__.py").unlink()
    assert _run("check-installed", man, good, site) == 1
    assert sentinel.read_bytes() == b"SENTINEL-LICENSE\x00\x01"


def test_failed_run_overwrites_stale_ready_receipt_and_recovery(env):
    tmp, good, _, new, old, man, sentinel = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    rec = tmp / "r.json"
    assert _run("check-installed", man, good, site, rec) == 0
    # recovery: remove paid -> not ready, receipt flips to failed; OSS bytes untouched
    for p in (site / NAMES["paid"]).iterdir():
        p.unlink()
    (site / NAMES["paid"]).rmdir()
    assert _run("check-installed", man, good, site, rec) == 1
    assert json.loads(rec.read_text())["ready"] is False
    assert (site / NAMES["oss"] / "__init__.py").read_text() == "# new oss"
    assert sentinel.read_bytes() == b"SENTINEL-LICENSE\x00\x01"


def test_default_lookup_without_site_packages(env):
    tmp, good, _, new, _, man, _ = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    importlib.invalidate_caches()
    assert _run("check-installed", man, good) == 0  # omitted option: no TypeError
    for p in (site / f"{NAMES['paid']}-9.2.dist-info").iterdir():
        p.unlink()
    (site / f"{NAMES['paid']}-9.2.dist-info").rmdir()
    importlib.invalidate_caches()
    assert _run("check-installed", man, good) == 1


@pytest.mark.parametrize("failure", ["missing_manifest", "bad_wheel"])
def test_input_failure_cannot_leave_stale_ready_receipt(env, failure):
    tmp, good, _, new, _, man, _ = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    rec = tmp / "r.json"
    assert _run("check-installed", man, good, site, rec) == 0
    assert json.loads(rec.read_text())["ready"] is True
    if failure == "missing_manifest":
        man.unlink()
    else:  # pinned hash matches but the zip is corrupt
        w = next(good.glob("synth_oss-*.whl"))
        w.write_bytes(b"not a zip")
        m = json.loads(man.read_text())
        m["oss"]["wheel_sha256"] = hashlib.sha256(b"not a zip").hexdigest()
        man.write_text(json.dumps(m))
    assert _run("check-installed", man, good, site, rec) == 1
    r = json.loads(rec.read_text())
    assert r["ready"] is False and r["errors"]


def test_unwritable_receipt_fails_explicitly(env):
    tmp, good, _, new, _, man, _ = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    rec = tmp / "r.json"
    rec.mkdir()  # a directory cannot be replaced by the receipt file
    assert _run("check-installed", man, good, site, rec) == 1


def _ready_env(env):
    tmp, good, _, new, _, man, _ = env
    site = tmp / "site"
    site.mkdir()
    for w in new.values():
        _install(site, w)
    return tmp, good, man, site


def test_ready_receipt_is_bound_to_target(env):
    tmp, good, man, site = _ready_env(env)
    assert _run("check-installed", man, good, site, tmp / "r.json") == 0
    r = json.loads((tmp / "r.json").read_text())
    assert r["prefix"] == str(tmp) and r["executable"] == sys.executable
    assert r["roles"]["oss"]["dist_root"] == str(site)
    assert r["roles"]["paid"]["wheel_sha256"] == json.loads(man.read_text())["paid"]["wheel_sha256"]


def test_external_site_packages_not_ready_for_this_prefix(env, monkeypatch):
    tmp, good, man, site = _ready_env(env)
    monkeypatch.setattr(sys, "prefix", str(tmp / "other-venv"))
    assert _run("check-installed", man, good, site, tmp / "r.json") == 1
    r = json.loads((tmp / "r.json").read_text())
    assert r["ready"] is False and any(
        "outside this interpreter's prefix" in e for e in r["errors"]
    )


def test_old_source_shadow_without_dist_info_rejected(env, monkeypatch):
    tmp, good, man, site = _ready_env(env)
    shadow = tmp / "shadow"
    (shadow / NAMES["oss"]).mkdir(parents=True)
    marker = shadow / "executed"
    (shadow / NAMES["oss"] / "__init__.py").write_text(f"open({str(marker)!r}, 'w')")
    monkeypatch.syspath_prepend(str(shadow))  # stands in for PYTHONPATH / source cwd
    assert _run("check-installed", man, good, site, tmp / "r.json") == 1
    r = json.loads((tmp / "r.json").read_text())
    assert r["ready"] is False and any(
        "oss" in e and "not the verified install" in e for e in r["errors"]
    )
    assert not marker.exists()  # resolved without executing application code
