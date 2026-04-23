# SPDX-License-Identifier: Apache-2.0
"""Hybrid worker calibration for TokenPak.

- Static calibration: benchmark candidate worker counts and store baseline.
- Dynamic adjustment: nudge worker count within safe bounds based on live load.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

from tokenpak.compression.processors import get_processor
from tokenpak.core.registry import Block, BlockRegistry
from tokenpak.sources.walker import walk_directory
from tokenpak.telemetry.tokens import clear_cache, count_tokens

PROFILE_PATH = Path.home() / ".tokenpak" / "calibration.json"


def _ensure_profile_dir() -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_profile() -> Dict:
    if not PROFILE_PATH.exists():
        return {}
    try:
        return json.loads(PROFILE_PATH.read_text())
    except Exception:
        return {}


def save_profile(profile: Dict) -> None:
    _ensure_profile_dir()
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))


def _host_key() -> str:
    return socket.gethostname()


def _candidate_workers(max_workers: int = 8) -> List[int]:
    cpu = max(1, (os.cpu_count() or 2))
    cap = max(1, min(max_workers, cpu))
    cands = [1, 2, 4, 6, 8]
    cands = sorted({w for w in cands if w <= cap})
    if cap not in cands:
        cands.append(cap)
    return sorted(cands)


def _sample_files(directory: str, max_files: int = 150) -> List[Tuple[str, str, str]]:
    files = list(walk_directory(directory))
    return files[:max_files]  # type: ignore


def _run_index_once(files: List[Tuple[str, str, str]], workers: int) -> float:
    """Return elapsed seconds for indexing sample."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    clear_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        db = f"{tmpdir}/calibrate.db"
        reg = BlockRegistry(db)
        start = time.perf_counter()

        def process_one(path: str, file_type: str):
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return None
            if not content.strip():
                return None
            proc = get_processor(file_type)
            if not proc:
                return None
            compressed = proc.process(content, path)
            return (
                path,
                content,
                Block(
                    path=path,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    version=1,
                    file_type=file_type,
                    raw_tokens=count_tokens(content),
                    compressed_tokens=count_tokens(compressed),
                    compressed_content=compressed,
                    quality_score=1.0,
                    importance=5.0,
                ),
            )

        results = []
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(process_one, p, ft) for p, ft, _ in files]
                for fut in as_completed(futs):
                    r = fut.result()
                    if r:
                        results.append(r)
        else:
            for p, ft, _ in files:
                r = process_one(p, ft)
                if r:
                    results.append(r)

        with reg.batch_transaction() as conn:
            for path, content, block in results:
                if reg.has_changed(path, content):
                    reg.add_block_batch(block, conn)

        elapsed = time.perf_counter() - start
        reg.close()
        return elapsed


def calibrate_workers(directory: str, max_workers: int = 8, rounds: int = 2) -> Dict:
    files = _sample_files(directory)
    if not files:
        return {"error": "No files found for calibration"}

    candidates = _candidate_workers(max_workers=max_workers)
    scores = {}

    for w in candidates:
        runs = []
        for _ in range(max(1, rounds)):
            runs.append(_run_index_once(files, workers=w))
        avg = sum(runs) / len(runs)
        scores[w] = avg

    best_workers = min(scores, key=scores.get)  # type: ignore

    profile = load_profile()
    host = _host_key()
    profile[host] = {
        "best_workers": best_workers,
        "scores_sec": {str(k): round(v, 4) for k, v in scores.items()},
        "updated_at": int(time.time()),
        "sample_files": len(files),
    }
    save_profile(profile)

    return {
        "host": host,
        "best_workers": best_workers,
        "scores_sec": scores,
        "sample_files": len(files),
    }


def get_recommended_workers(default_workers: int = 4, max_workers: int = 8) -> int:
    cpu = max(1, (os.cpu_count() or 2))
    hard_cap = min(max_workers, cpu)

    profile = load_profile()
    host = _host_key()
    baseline = int(profile.get(host, {}).get("best_workers", default_workers))
    baseline = max(1, min(baseline, hard_cap))

    # Dynamic bounded adjustment using load average if available.
    dyn = baseline
    try:
        load1, _, _ = os.getloadavg()
        # Normalize by CPU cores.
        ratio = load1 / max(1, cpu)
        if ratio > 0.85:
            dyn = max(1, baseline - 1)
        elif ratio < 0.35:
            dyn = min(hard_cap, baseline + 1)
    except Exception:
        pass

    # Never move more than +-1 from baseline for stability.
    if dyn > baseline + 1:
        dyn = baseline + 1
    if dyn < baseline - 1:
        dyn = baseline - 1
    return max(1, min(dyn, hard_cap))
