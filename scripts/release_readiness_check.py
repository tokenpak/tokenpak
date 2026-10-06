#!/usr/bin/env python3
"""Offline release-candidate manifest + verification (no network, no keys, no publish).

Exports pinned commits with ``git archive`` into a scratch dir, builds wheels with
the local setuptools backend (no build isolation, no downloads), installs them into
an isolated venv with ``--no-index --no-deps``, then checks metadata, dependency
resolvability, the import/CLI surface and byte-preservation of a sentinel
``license.json``. Every child process runs with a throwaway HOME/TMPDIR and
no proxy/token env; nothing here imports an app factory or touches signing keys.

Usage: release_readiness_check.py --out DIR --worktree-root DIR [--oss REPO@SHA] [--paid REPO@SHA] [--prior-oss REPO@SHA]

Default pins are directory names (relative to --worktree-root) plus a full commit SHA.
The operator must supply --worktree-root; the tool fails closed without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

# Archival pins: "<dir name under --worktree-root>@<full sha>". Explicit absolute
# REPO@SHA values passed on the command line take precedence.
DEFAULTS = {
    "oss": "productization-entitlements-20261003@910923215dc6945cf7382d26c74d80d29d766139",
    "paid": "paid-stored-token-20261003@d5e7ceb62a07443f36db97d7061c45cde1c7ee73",
    "prior_oss": "release-v1.30.1@562fef78baf9fab9becd11bd7a5ec136da2ee02b",
}
SERVER = "license-server-init-safety-20261003@08def751d6b475013db517c3f826e3ba38cc7aba"
SENTINEL = b'{"sentinel":"NOT-A-REAL-LICENSE","bytes":"\\u00ff\\n"}\n\x00\xff'
BUILD_SNIPPET = (
    "import sys;from setuptools import build_meta as b;print(b.build_wheel(sys.argv[1]))"
)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def clean_env(home: Path) -> dict:
    tmp = home / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "*",
        "TOKENPAK_HOME": str(home / ".tokenpak"),
    }


GUARD = """import socket, sys
def _deny(*a, **k):
    raise RuntimeError("release-readiness guard: network blocked")
socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.create_connection = _deny
socket.getaddrinfo = _deny
try:
    from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519
    def _nokey(*a, **k):
        raise RuntimeError("release-readiness guard: key generation blocked")
    rsa.generate_private_key = _nokey
    ec.generate_private_key = _nokey
    ed25519.Ed25519PrivateKey.generate = staticmethod(_nokey)
except ImportError:
    pass
"""


def guarded(env: dict, d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    (d / "sitecustomize.py").write_text(GUARD)
    return dict(env, PYTHONPATH=str(d))


def installed_matches_wheel(py, wheel: Path, env, scratch) -> bool:
    import zipfile

    with zipfile.ZipFile(wheel) as z:
        want = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.endswith(".py")}
    code = (
        "import sys,json,hashlib,site,pathlib;sp=pathlib.Path(site.getsitepackages()[0]);w=json.loads(sys.argv[1]);"
        "print(all((sp/n).is_file() and hashlib.sha256((sp/n).read_bytes()).hexdigest()==h for n,h in w.items()))"
    )
    r = run([py, "-c", code, json.dumps(want)], scratch, env)
    return r["out"].strip() == "True"


def run(argv, cwd, env, timeout=70):
    t = time.time()
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        rc, out = p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        rc, out = 124, "timeout"
    return {
        "argv": [str(a) for a in argv],
        "rc": rc,
        "sec": round(time.time() - t, 2),
        "out": out[-1500:],
    }


def export(spec: str, dest: Path) -> dict:
    repo, sha = spec.rsplit("@", 1)
    head = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
    tar = dest.parent / f"{dest.name}.tar"
    subprocess.check_call(["git", "-C", repo, "archive", "--format=tar", "-o", str(tar), sha])
    dest.mkdir(parents=True)
    with tarfile.open(tar) as t:
        t.extractall(dest, filter="data")
    return {
        "repo": repo,
        "commit": sha,
        "repo_head_matches_pin": head == sha,
        "archive_sha256": sha256(tar),
    }


def resolve_spec(spec: str, root: Path | None) -> str:
    """Resolve a ``REPO@SHA`` pin; relative repo names need an operator-provided root."""
    repo, sha = spec.rsplit("@", 1)
    if Path(repo).is_absolute():
        return spec
    if root is None:
        raise SystemExit(f"error: --worktree-root is required to resolve pin {repo!r}")
    return f"{root / repo}@{sha}"


def build(src: Path, outdir: Path, env) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    # setuptools usually lives in the user site, which the throwaway HOME hides;
    # expose only that one package (read-only symlink) to the build step.
    import setuptools

    tools = outdir.parent / "buildtools"
    tools.mkdir(exist_ok=True)
    site = Path(setuptools.__file__).parent.parent
    for pkg in ("setuptools", "_distutils_hack", "pkg_resources"):
        if (site / pkg).is_dir() and not (tools / pkg).exists():
            (tools / pkg).symlink_to(site / pkg)
    # setuptools registers egg_info/bdist_wheel through its dist-info entry points.
    for di in site.glob("setuptools-*.dist-info"):
        if not (tools / di.name).exists():
            (tools / di.name).symlink_to(di)
    benv = dict(env, PYTHONPATH=str(tools))
    r = run([sys.executable, "-c", BUILD_SNIPPET, str(outdir)], src, benv)
    r["setuptools"] = setuptools.__version__
    whl = sorted(outdir.glob("*.whl"))
    r["wheel"] = whl[-1].name if whl else None
    r["wheel_sha256"] = sha256(whl[-1]) if whl else None
    return r


def wheel_meta(whl: Path) -> dict:
    import email
    import zipfile

    with zipfile.ZipFile(whl) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        m = email.message_from_string(z.read(name).decode())
    return {
        "name": m["Name"],
        "version": m["Version"],
        "requires_python": m["Requires-Python"],
        "requires_dist_core": [
            r for r in (m.get_all("Requires-Dist") or []) if "extra ==" not in r
        ],
    }


def release_semantics(g: dict) -> dict:
    """Exit 0 means the packaging check completed, never that a release is authorized."""
    blockers = sorted(
        k for k in ("dependencies_resolvable_offline", "pip_check_clean", "cli_ok") if not g.get(k)
    )
    blockers += sorted(
        k
        for k in (
            "candidate_oss_version_collides_with_prior_release",
            "client_and_server_need_separate_environments",
        )
        if g.get(k)
    )
    blockers.append(
        "deployment_gates_unverified"
    )  # Worker revision/trust/signer: never checkable here
    return {"packaging_check_completed": True, "release_ready": False, "release_blockers": blockers}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--worktree-root", default=None, help="directory holding the pinned worktrees")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v)
    a = ap.parse_args()
    root = Path(a.worktree_root) if a.worktree_root else None
    if root is not None and not root.is_dir():
        raise SystemExit(f"error: --worktree-root is not a directory: {root}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(
        tempfile.mkdtemp(prefix="tpk-rr-", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
    )
    env = clean_env(scratch / "home")
    res: dict = {"python": sys.version.split()[0], "inputs": {}, "builds": {}, "gates": {}}
    wheels: dict = {}
    for key in ("oss", "paid", "prior_oss"):
        spec = resolve_spec(getattr(a, key), root)
        res["inputs"][key] = export(spec, scratch / f"src-{key}")
        b = build(scratch / f"src-{key}", scratch / f"dist-{key}", env)
        res["builds"][key] = b
        if b["wheel"]:
            w = scratch / f"dist-{key}" / b["wheel"]
            wheels[key] = w
            res["builds"][key]["meta"] = wheel_meta(w)
            (out / key).mkdir(exist_ok=True)
            shutil.copy2(w, out / key / b["wheel"])
            assert sha256(out / key / b["wheel"]) == b["wheel_sha256"], key
    g = res["gates"]
    g["wheels_built"] = set(wheels) == {"oss", "paid", "prior_oss"}
    if not g["wheels_built"]:
        res["verdict"] = "STOP: wheel build failed"
        (out / "candidate-manifest.json").write_text(json.dumps(res, indent=2))
        return 1

    # Declared pairing: paid's tokenpak requirement must admit the candidate OSS version.
    try:
        from packaging.requirements import Requirement
    except Exception:  # pragma: no cover
        Requirement = None
    pm = res["builds"]["paid"]["meta"]
    om = res["builds"]["oss"]["meta"]
    if Requirement:
        reqs = [Requirement(r) for r in pm["requires_dist_core"]]
        tp = [r for r in reqs if r.name == "tokenpak"]
        g["paid_pin_admits_oss_candidate"] = bool(tp) and all(
            r.specifier.contains(om["version"]) for r in tp
        )
        res["paid_tokenpak_requirement"] = [str(r) for r in tp]

    # Server is a separate deployment (no wheel, never imported/instantiated here: init may
    # create keys). Only its pinned requirements are compared with the client metadata.
    res["inputs"]["server"] = export(resolve_spec(SERVER, root), scratch / "src-server")
    from packaging.requirements import Requirement as R

    srv = {}
    for line in (scratch / "src-server/requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            r = R(line)
            srv[r.name.lower()] = str(r.specifier)
    conflicts = []
    for line in om["requires_dist_core"]:
        r = R(line)
        sp = srv.get(r.name.lower())
        if sp and sp.startswith("==") and not r.specifier.contains(sp[2:]):
            conflicts.append({"pkg": r.name, "client": str(r.specifier), "server_pin": sp})
    res["client_server_dependency_conflicts"] = conflicts
    g["client_and_server_need_separate_environments"] = bool(conflicts)  # informational

    # Same version string, different bytes: pip cannot tell candidate from release.
    g["candidate_oss_version_collides_with_prior_release"] = (
        om["version"] == res["builds"]["prior_oss"]["meta"]["version"]
        and res["builds"]["oss"]["wheel_sha256"] != res["builds"]["prior_oss"]["wheel_sha256"]
    )

    # Isolated venv; sentinel license bytes must survive every transition untouched.
    venv = scratch / "venv"
    r = run([sys.executable, "-m", "venv", str(venv)], scratch, env)
    py = str(venv / "bin/python")
    genv = guarded(env, scratch / "guard")
    lic = scratch / "home/.tokenpak/license.json"
    lic.parent.mkdir(parents=True, exist_ok=True)
    lic.write_bytes(SENTINEL)
    want = hashlib.sha256(SENTINEL).hexdigest()
    steps = []

    def pip(label, *args, expect=None):
        r = run([py, "-m", "pip", "install", "--no-index", "--no-deps", *args], scratch, env)
        r["label"] = label
        if expect:
            r["installed_bytes_match_wheel"] = installed_matches_wheel(
                py, wheels[expect], genv, scratch
            )
        r["license_bytes_intact"] = lic.exists() and sha256(lic) == want
        steps.append(r)

    pip("install prior OSS 1.30.1 (562fef78)", str(wheels["prior_oss"]), expect="prior_oss")
    # Candidate OSS and prior OSS share a version string: pip treats it as satisfied,
    # so a real transition needs --force-reinstall. Record the collision, then force.
    pip("install candidate OSS without force (collision probe)", str(wheels["oss"]))
    pip("force candidate OSS over prior", "--force-reinstall", str(wheels["oss"]), expect="oss")
    pip("add paid candidate 0.5.2", str(wheels["paid"]))
    r = run([py, "-m", "pip", "uninstall", "-y", "tokenpak-paid"], scratch, env)
    r["label"] = "downgrade/OSS-fallback: remove paid"
    r["license_bytes_intact"] = sha256(lic) == want
    steps.append(r)
    pip("re-add paid", str(wheels["paid"]))
    pip(
        "rollback OSS to prior (force)",
        "--force-reinstall",
        str(wheels["prior_oss"]),
        expect="prior_oss",
    )
    pip("final: candidate OSS", "--force-reinstall", str(wheels["oss"]), expect="oss")
    res["transitions"] = steps
    g["license_bytes_intact_through_all_steps"] = all(s["license_bytes_intact"] for s in steps)
    g["all_pip_steps_rc0"] = all(s["rc"] == 0 for s in steps)
    g["installed_bytes_match_wheel_after_force_steps"] = all(
        s["installed_bytes_match_wheel"] for s in steps if "installed_bytes_match_wheel" in s
    )
    sur = {}
    sur["pip_check_no_deps_install"] = run([py, "-m", "pip", "check"], scratch, env)
    sur["dependency_resolution_offline"] = run(
        [
            py,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--no-index",
            str(wheels["oss"]),
            str(wheels["paid"]),
        ],
        scratch,
        env,
    )
    sur["import"] = run(
        [
            py,
            "-c",
            "import tokenpak,tokenpak_paid;print(tokenpak.__version__, tokenpak_paid.__file__)",
        ],
        scratch,
        genv,
    )
    sur["cli_version"] = run([str(venv / "bin/tokenpak"), "--version"], scratch, genv)
    sur["license_refusal_no_license"] = run(
        [str(venv / "bin/tokenpak"), "license", "status"], scratch, genv
    )
    import re
    import zipfile

    bad = {}
    for k in ("oss", "paid", "prior_oss"):
        with zipfile.ZipFile(wheels[k]) as z:
            names = z.namelist()
            bad[k] = {
                "members": len(names),
                "key_like_names": [
                    n for n in names if re.search(r"(private|\.pem$|\.key$|id_rsa)", n, re.I)
                ],
            }
    sur["wheel_member_name_scan"] = {"scope": "member file names only; contents not scanned", **bad}
    g["no_key_like_member_names"] = not any(v["key_like_names"] for v in bad.values())
    res["surfaces"] = sur
    g["dependencies_resolvable_offline"] = sur["dependency_resolution_offline"]["rc"] == 0
    g["pip_check_clean"] = sur["pip_check_no_deps_install"]["rc"] == 0
    g["import_ok"] = sur["import"]["rc"] == 0
    g["cli_ok"] = sur["cli_version"]["rc"] == 0
    res["scratch"] = str(scratch)
    (out / "candidate-manifest.json").write_text(json.dumps(res, indent=2))
    findings = {
        "client_and_server_need_separate_environments",
        "candidate_oss_version_collides_with_prior_release",
        "dependencies_resolvable_offline",
        "pip_check_clean",
        "cli_ok",
    }
    res["findings_not_blocking_this_script"] = sorted(k for k in findings if k in g)
    res.update(release_semantics(g))
    (out / "candidate-manifest.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(g, indent=2))
    return 0 if all(v for k, v in g.items() if k not in findings) else 2


if __name__ == "__main__":
    sys.exit(main())
