#!/usr/bin/env python3
"""Fail-closed gate for the tokenpak + tokenpak-paid paired upgrade.

pip's resolver does NOT prevent mixed pairs (new OSS + old paid, or old OSS +
new paid): public version constraints cannot tell two builds with the same
name/version apart. The only supported upgrade is therefore:

  1. ``check-artifacts``: the wheel directory holds exactly the two wheels
     pinned by an owner-supplied manifest (name, version, sha256).
  2. Install both exact wheels in ONE pip invocation (see docs).
  3. ``check-installed``: every file of each wheel is present in the
     installed environment with identical bytes, so a same-version pip no-op
     (stale install) cannot pass as an upgrade.

The gate only reads. It never installs, stops processes, or touches the
license/state; a failed run leaves the previous install and license bytes as
they were and writes a ``ready: false`` receipt (exit 1). ``ready: true`` is
only ever written by ``check-installed`` after both members verify.
Callers must require BOTH a success exit code and a current valid receipt
(``ready: true``, expected manifest sha256); the receipt is invalidated at
the start of every run, and if it cannot be written the run exits 1. This
cannot guarantee a read-only output path is overwritable: that case fails.
The guard checks release identity only; it is not full release approval.
Receipts (schema 2) bind the checked target: interpreter, prefix, sys.path,
per-role wheel sha256 and installed dist root. The target is the interpreter
running the gate, so run it with the target venv's own python; a distribution
outside that prefix (another env via --site-packages) is never ready, and
callers must also match ``prefix`` to the venv they will launch. Each wheel's
top-level imports are resolved with find_spec (no application code executed)
and must land inside the verified dist root, so a PYTHONPATH/cwd source
checkout, .pth or editable path shadowing the install is rejected. Launch
only from the verified venv with no PYTHONPATH, no source-checkout cwd and no
unreviewed editable/.pth entries; later out-of-band edits (including stale
extra files) are not detected, so stage freshly and do not write by hand.
The gate lives in the OSS source checkout, not in either wheel: pin the
reviewed gate commit or file sha256 externally.
The manifest is the trust root: it must come from the release owner out of
band; its sha256 is recorded in the receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from importlib import metadata, util
from pathlib import Path

ROLES = ("oss", "paid")
# Files pip rewrites at install time; not part of the wheel's identity.
_PIP_OWNED = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(path: Path) -> tuple[dict, str, list[str]]:
    raw = path.read_bytes()
    errs: list[str] = []
    try:
        man = json.loads(raw)
    except ValueError as exc:
        return {}, _sha(raw), [f"manifest unreadable: {exc}"]
    for role in ROLES:
        ent = man.get(role) if isinstance(man, dict) else None
        if not isinstance(ent, dict) or not all(
            isinstance(ent.get(k), str) and ent[k] for k in ("name", "version", "wheel_sha256")
        ):
            errs.append(f"manifest missing/invalid member: {role}")
    if not errs and man["oss"]["name"] == man["paid"]["name"]:
        errs.append("manifest members must be distinct packages")
    return man, _sha(raw), errs


def _norm(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def check_artifacts(man: dict, wheel_dir: Path) -> tuple[dict, list[str]]:
    errs: list[str] = []
    found: dict[str, Path] = {}
    wheels = sorted(wheel_dir.glob("*.whl")) if wheel_dir.is_dir() else []
    if not wheel_dir.is_dir():
        errs.append("wheel directory missing")
    for w in wheels:
        for role in ROLES:
            ent = man[role]
            if w.name.startswith(f"{_norm(ent['name'])}-{ent['version']}-"):
                found.setdefault(role, w)
                break
        else:
            errs.append(f"unknown wheel not in manifest: {w.name}")
    if len(wheels) > len(found):
        errs.append("extra or duplicate wheels present")
    for role in ROLES:
        ent = man[role]
        w = found.get(role)
        if w is None:
            errs.append(f"missing member: {role} {ent['name']}=={ent['version']}")
        elif _sha(w.read_bytes()) != ent["wheel_sha256"]:
            errs.append(f"sha256 mismatch (same-version different bytes or tampered): {w.name}")
    return {r: str(p) for r, p in found.items()}, errs


def _wheel_files(wheel: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            if parts[0].endswith(".dist-info") and parts[-1] in _PIP_OWNED:
                continue
            out[info.filename] = zf.read(info)
    return out


def _distributions(site: list[str] | None):
    # distributions(path=None) raises TypeError on Python 3.10; omit it for the default.
    return metadata.distributions(path=site) if site else metadata.distributions()


def _under(path: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(root))
        return True
    except ValueError:
        return False


def _top_level(files) -> list[str]:
    tops = set()
    for name in files:
        head = name.split("/")[0]
        if head.endswith((".dist-info", ".data")):
            continue
        tops.add(head[:-3] if "/" not in name and head.endswith(".py") else head)
    return sorted(tops)


def _import_errors(role: str, tops: list[str], root: Path) -> list[str]:
    """Where each top-level name would import from, found without importing it."""
    errs = []
    for top in tops:
        try:
            spec = util.find_spec(top)
        except Exception as exc:
            errs.append(f"{role}: cannot resolve import {top}: {type(exc).__name__}: {exc}")
            continue
        locs = [spec.origin] if spec and spec.origin not in (None, "namespace") else []
        if spec and spec.submodule_search_locations:
            locs += list(spec.submodule_search_locations)
        if not locs or not all(_under(Path(loc), root) for loc in locs):
            errs.append(f"{role}: import {top} resolves to {locs or 'nothing'}, not the verified install {root}")
    return errs


def check_installed(man: dict, wheel_dir: Path, site: list[str] | None, info: dict | None = None) -> list[str]:
    errs: list[str] = []
    info = info if info is not None else {}
    _, aerrs = check_artifacts(man, wheel_dir)
    if aerrs:
        return [f"artifacts: {e}" for e in aerrs]
    for role in ROLES:
        ent = man[role]
        wheel = next(
            p for p in sorted(wheel_dir.glob("*.whl"))
            if p.name.startswith(f"{_norm(ent['name'])}-{ent['version']}-")
        )
        dists = [
            d for d in _distributions(site) if _norm(d.metadata["Name"] or "") == _norm(ent["name"])
        ]
        if len(dists) != 1:
            errs.append(f"{role}: {len(dists)} installed distributions named {ent['name']} (need 1)")
            continue
        dist = dists[0]
        if dist.version != ent["version"]:
            errs.append(f"{role}: installed version {dist.version} != {ent['version']}")
            continue
        root = Path(str(dist.locate_file("")))
        files = _wheel_files(wheel)
        info.setdefault("roles", {})[role] = {
            "wheel_sha256": man[role]["wheel_sha256"], "dist_root": str(root),
        }
        # The checked target is the interpreter running this gate: a distribution
        # outside its prefix (e.g. another env passed via --site-packages) is not ready.
        if not _under(root, Path(sys.prefix)):
            errs.append(f"{role}: installed at {root}, outside this interpreter's prefix {sys.prefix}")
            continue
        errs += _import_errors(role, _top_level(files), root)
        for rel, data in files.items():
            f = root / rel
            if not f.is_file():
                errs.append(f"{role}: installed file missing: {rel}")
            elif _sha(f.read_bytes()) != _sha(data):
                errs.append(f"{role}: installed bytes differ from pinned wheel: {rel}")
    return errs


def _write_receipt(path: Path | None, receipt: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    with os.fdopen(fd, "w") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _receipt(command: str, ready: bool, man_sha: str, errs: list[str], info: dict | None = None) -> dict:
    out = {"schema": 2, "phase": command, "ready": ready, "manifest_sha256": man_sha, "errors": errs}
    if info is not None:
        # Environment binding: callers must match these to the target venv.
        out.update(info, executable=sys.executable, prefix=sys.prefix,
                   site_packages=[str(p) for p in sys.path if p])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("check-artifacts", "check-installed"))
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--wheel-dir", type=Path, required=True)
    ap.add_argument("--receipt", type=Path)
    ap.add_argument("--site-packages", action="append", help="default: this interpreter's sys.path")
    a = ap.parse_args(argv)

    # Invalidate any earlier receipt first: if this cannot be written we fail
    # explicitly rather than let a stale ready:true stand in for this run.
    man_sha = ""
    info: dict = {}
    try:
        _write_receipt(a.receipt, _receipt(a.command, False, man_sha, ["run did not complete"], info))
    except OSError as exc:
        print(f"FAIL: receipt not writable, stale receipt must not be trusted: {exc}", file=sys.stderr)
        return 1
    try:
        man, man_sha, errs = _load_manifest(a.manifest)
        if not errs:
            if a.command == "check-artifacts":
                _, errs = check_artifacts(man, a.wheel_dir)
            else:
                errs = check_installed(man, a.wheel_dir, a.site_packages, info)
    except Exception as exc:  # missing manifest, IO error, bad wheel, ...
        errs = [f"{type(exc).__name__}: {exc}"]
    # Artifact checks alone never mark the environment ready.
    ready = not errs and a.command == "check-installed"
    receipt = _receipt(a.command, ready, man_sha, errs, info)
    try:
        _write_receipt(a.receipt, receipt)
    except OSError as exc:
        print(f"FAIL: receipt not writable, stale receipt must not be trusted: {exc}", file=sys.stderr)
        return 1
    for e in errs:
        print(f"FAIL: {e}", file=sys.stderr)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
