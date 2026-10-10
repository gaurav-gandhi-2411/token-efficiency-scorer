from __future__ import annotations

"""Rebuild tes/data/cc_baselines.json on the adapter_version-2 scale (usage counted once).

The old builder (scripts/build_baselines.py) used the frozen per-record research pool, whose raw
sessions are gone, so it cannot be re-run. Subcommands:

  freeze    scan main (non-subagent) sessions with the CURRENT adapt_session; write
            eval/manifest.json (id, task_type, turns, real_tokens, size/mtime/sha256; no text/paths)
  build     derive the baseline from the manifest ALONE (reproducible after transcripts expire;
            deterministic: order statistics, bootstrap with random.Random(42))
  evaluate  score Phase 0 set B with disjoint and leave-one-out bands, Wilson 95% intervals
  verify    re-hash the transcripts that still exist against the manifest

The population is single-developer and NOT quality-gated; see POPULATION_LIMITATIONS.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # datetime.UTC is 3.11+; tracegauge supports 3.10 and tests import this script

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tes.adapt import ADAPTER_VERSION, adapt_session  # noqa: E402
from tes.baselines import compute_real_tokens  # noqa: E402
from tes.classify import TASK_TYPES, classify_session  # noqa: E402
from tes.discovery import iter_session_files  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "eval" / "manifest.json"
BASELINES_PATH = REPO_ROOT / "tes" / "data" / "cc_baselines.json"

SEED = 42  # house rule: every stochastic step is seeded
MIN_N = 10  # a cell is active only with at least this many sessions (same as the old builder)
BOOTSTRAP_RESAMPLES = 2000
QUIESCENT_HOURS = 2.0  # a transcript written to within this window may still be growing
MANIFEST_VERSION = 1

POPULATION_DESCRIPTION = (
    "main (non-subagent) Claude Code sessions of a single developer found under "
    "~/.claude/projects at freeze time, scored with tes.adapt.adapt_session "
    f"(adapter_version {ADAPTER_VERSION}: usage counted once per API response)"
)
POPULATION_LIMITATIONS = (
    "single developer; ungated (every quiescent session with usage, no quality verdict, so the "
    "bands describe typical not exemplary runs); heavy on infrastructure and agent-orchestration "
    "work; small n per cell; sessions are not independent (one repo's sessions share context). "
    "Not comparable with bands published before 0.15.0, which were built on per-record usage."
)

TOKEN_MEASURE = "real_tokens = sum_ai_turns(token_count_input - cache_read + token_count_output)"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def percentile_at_index(sorted_values: list[int], fraction: float) -> int:
    """Return the element at floor(n * fraction) of an ascending list (the old builder's rule)."""
    n = len(sorted_values)
    idx = max(0, min(int(n * fraction), n - 1))
    return sorted_values[idx]


def bootstrap_ci(
    values: list[int],
    fraction: float,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = SEED,
    level: float = 0.95,
) -> tuple[int, int]:
    """Percentile-bootstrap interval for ``percentile_at_index(values, fraction)``.

    Deterministic: a fresh ``random.Random(seed)`` per call. With n around 10 the interval is wide
    on purpose; it is the honest statement of how little a one-developer cell pins down.
    """
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not cryptography
    n = len(values)
    stats: list[int] = []
    for _ in range(resamples):
        sample = sorted(values[rng.randrange(n)] for _ in range(n))
        stats.append(percentile_at_index(sample, fraction))
    stats.sort()
    tail = (1.0 - level) / 2.0
    lo = stats[max(0, int(len(stats) * tail))]
    hi = stats[min(len(stats) - 1, int(len(stats) * (1.0 - tail)))]
    return lo, hi


def wilson_interval(successes: int, total: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (default 95%)."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


def build_bands(entries: Iterable[dict[str, Any]], *, with_ci: bool = True) -> dict[str, Any]:
    """Per-task-type bands and scope gates from manifest entries (included sessions only).

    Cells with fewer than MIN_N sessions are inactive: ``{"available": False, "n": n}`` and
    ``p10_turns`` None, exactly as the old builder did (the score code then reports no baseline).
    """
    tokens: dict[str, list[int]] = {t: [] for t in TASK_TYPES}
    turns: dict[str, list[int]] = {t: [] for t in TASK_TYPES}
    for e in entries:
        tokens[e["task_type"]].append(int(e["real_tokens"]))
        turns[e["task_type"]].append(int(e["turn_count"]))
    types: dict[str, dict[str, Any]] = {}
    gates: dict[str, dict[str, Any]] = {}
    for t in TASK_TYPES:
        vals = sorted(tokens[t])
        n = len(vals)
        if n < MIN_N:
            types[t] = {"available": False, "n": n}
            gates[t] = {"p10_turns": None}
            continue
        cell: dict[str, Any] = {
            "available": True,
            "n": n,
            "median": percentile_at_index(vals, 0.5),
            "p25": percentile_at_index(vals, 0.25),
            "p75": percentile_at_index(vals, 0.75),
        }
        if with_ci:
            cell["ci95"] = {
                name: list(bootstrap_ci(vals, frac))
                for name, frac in (("p25", 0.25), ("median", 0.5), ("p75", 0.75))
            }
        types[t] = cell
        gates[t] = {"p10_turns": percentile_at_index(sorted(turns[t]), 0.10)}
    return {"types": types, "scope_gates": gates}


def band_verdict(task_type: str, turn_count: int, real_tokens: int, bands: dict[str, Any]) -> str:
    """Token-axis verdict, mirroring ``tes.score`` for the corpus baseline.

    'unavailable' covers both out-of-scope (turns below the p10 gate) and an inactive cell.
    """
    p10 = bands["scope_gates"].get(task_type, {}).get("p10_turns")
    info = bands["types"].get(task_type, {})
    if (p10 is not None and turn_count < p10) or not info.get("available", False):
        return "unavailable"
    if real_tokens > info["p75"]:
        return "above_p75"
    if real_tokens < info["p25"]:
        return "below_p25"
    return "within_band"


VERDICTS = ("above_p75", "within_band", "below_p25", "unavailable")


def verdict_distribution(verdicts: Iterable[str]) -> dict[str, Any]:
    """Counts per verdict plus Wilson 95% intervals on each proportion."""
    vs = list(verdicts)
    counts = Counter(vs)
    total = len(vs)
    out: dict[str, Any] = {"total": total}
    for v in VERDICTS:
        lo, hi = wilson_interval(counts.get(v, 0), total)
        out[v] = {"n": counts.get(v, 0), "wilson95": [round(lo, 4), round(hi, 4)]}
    return out


def included(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Manifest entries that make up the baseline population."""
    return [e for e in manifest["sessions"] if e["included"]]


def evaluate_set(
    manifest: dict[str, Any], eval_ids: set[str], old_bands: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Verdicts for the evaluation sessions under disjoint, leave-one-out and old bands."""
    pop = included(manifest)
    targets = [e for e in manifest["sessions"] if e["session_id"] in eval_ids]
    disjoint_bands = build_bands((e for e in pop if e["session_id"] not in eval_ids), with_ci=False)
    full_bands = build_bands(pop, with_ci=False)
    loo: list[str] = []
    disjoint: list[str] = []
    in_sample: list[str] = []
    old: list[str] = []
    for e in targets:
        args = (e["task_type"], e["turn_count"], e["real_tokens"])
        rest = [x for x in pop if x["session_id"] != e["session_id"]]
        loo.append(band_verdict(*args, build_bands(rest, with_ci=False)))
        disjoint.append(band_verdict(*args, disjoint_bands))
        in_sample.append(band_verdict(*args, full_bands))
        if old_bands is not None:
            old.append(band_verdict(*args, old_bands))
    result = {
        "n_eval": len(targets),
        "n_eval_in_population": sum(1 for e in targets if e["included"]),
        "disjoint_cell_n": {t: disjoint_bands["types"][t]["n"] for t in TASK_TYPES},
        "disjoint_cells_active": [t for t in TASK_TYPES if disjoint_bands["types"][t]["available"]],
        "leave_one_out": verdict_distribution(loo),
        "disjoint_only": verdict_distribution(disjoint),
        "in_sample_full_bands": verdict_distribution(in_sample),
    }
    if old_bands is not None:
        result["old_bands_new_tokens"] = verdict_distribution(old)
    return result


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """Hash of the canonical JSON form (sorted keys, compact separators)."""
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """sha256 of a file's bytes, streamed."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_manifest(
    projects_root: Path,
    *,
    now: float,
    frozen_at: str,
    eval_ids: set[str],
    adapt: Callable[[Path], dict[str, Any]] = adapt_session,
) -> dict[str, Any]:
    """Scan ``projects_root`` and describe every main session; nothing here is transcript text."""
    sessions: list[dict[str, Any]] = []
    for path in sorted(iter_session_files(projects_root), key=lambda p: p.stem):
        stat = path.stat()
        active = (now - stat.st_mtime) < QUIESCENT_HOURS * 3600
        record = adapt(path)
        rt = compute_real_tokens(record)
        reason = "active_within_quiescent_window" if active else ("no_usage" if rt <= 0 else None)
        sessions.append(
            {
                "session_id": path.stem,
                "task_type": classify_session(record),
                "turn_count": int(record["turn_count"]),
                "real_tokens": rt,
                "size_bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                # a file still being written has no stable hash worth pinning
                "sha256": None if active else file_sha256(path),
                "in_phase0_set_b": path.stem in eval_ids,
                "included": reason is None,
                "exclusion_reason": reason,
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "frozen_at": frozen_at,
        "adapter_version": ADAPTER_VERSION,
        "seed": SEED,
        "population": POPULATION_DESCRIPTION,
        "quiescent_hours": QUIESCENT_HOURS,
        "locate": "transcripts are found by session_id under ~/.claude/projects; paths are not stored",
        "sessions": sessions,
    }


def verify_sources(manifest: dict[str, Any], projects_root: Path) -> dict[str, Any]:
    """Compare still-existing transcripts with the manifest (size and sha256)."""
    by_id = {p.stem: p for p in iter_session_files(projects_root)}
    found = missing = mismatch = 0
    for e in manifest["sessions"]:
        if e["sha256"] is None:
            continue
        path = by_id.get(e["session_id"])
        if path is None:
            missing += 1
        elif file_sha256(path) == e["sha256"]:
            found += 1
        else:
            mismatch += 1
    return {"hash_match": found, "missing": missing, "hash_mismatch": mismatch}


def build_baselines(manifest: dict[str, Any]) -> dict[str, Any]:
    """The cc_baselines.json payload, a pure function of the manifest."""
    pop = included(manifest)
    bands = build_bands(pop)
    excluded = Counter(e["exclusion_reason"] for e in manifest["sessions"] if not e["included"])
    return {
        "generated": manifest["frozen_at"],
        "token_measure": TOKEN_MEASURE,
        "strict_gate": "none: ungated population, no judge verdict used",
        "baseline_population": POPULATION_DESCRIPTION,
        "total_baseline_sessions": len(pop),
        "circularity": {
            "available": False,
            "reason": "no judge scores: the population is not quality-gated",
        },
        "provenance": {
            "adapter_version": manifest["adapter_version"],
            "build_date": manifest["frozen_at"],
            "seed": SEED,
            "manifest": "eval/manifest.json",
            "manifest_sha256": manifest_sha256(manifest),
            "builder": "scripts/rebuild_baselines.py build",
            "single_developer": True,
            "quality_gated": False,
            "min_n_to_activate": MIN_N,
            "percentile_rule": "element at floor(n*q) of the ascending real_tokens",
            "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "level": 0.95, "seed": SEED},
            "candidates": len(manifest["sessions"]),
            "excluded": dict(sorted(excluded.items())),
            "n_per_task_type": {t: bands["types"][t]["n"] for t in TASK_TYPES},
            "limitations": POPULATION_LIMITATIONS,
        },
        "scope_gates": bands["scope_gates"],
        "types": bands["types"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_eval_ids(selection_path: Path, key: str) -> set[str]:
    sel = json.loads(selection_path.read_text(encoding="utf-8"))["sel"][key]
    return {Path(p).stem for p in sel}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="scan transcripts and write the manifest")
    f.add_argument("--projects-root", type=Path, default=Path.home() / ".claude" / "projects")
    f.add_argument("--eval-selection", type=Path, help="c2-selection.json (key sel[B])")
    f.add_argument("--eval-key", default="B")
    f.add_argument("--frozen-at", default=date.today().isoformat())
    sub.add_parser("build", help="derive cc_baselines.json from the manifest")
    e = sub.add_parser("evaluate", help="score the evaluation set (disjoint and leave-one-out)")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--old-baselines", type=Path, help="previous cc_baselines.json (git show)")
    v = sub.add_parser("verify", help="re-hash surviving transcripts")
    v.add_argument("--projects-root", type=Path, default=Path.home() / ".claude" / "projects")
    args = ap.parse_args(argv)

    if args.cmd == "freeze":
        ids = _read_eval_ids(args.eval_selection, args.eval_key) if args.eval_selection else set()
        manifest = freeze_manifest(
            args.projects_root, now=time.time(), frozen_at=args.frozen_at, eval_ids=ids
        )
        _write_json(MANIFEST_PATH, manifest)
        print(f"wrote {MANIFEST_PATH} ({len(manifest['sessions'])} sessions)")
        return 0
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if args.cmd == "build":
        _write_json(BASELINES_PATH, build_baselines(manifest))
        print(f"wrote {BASELINES_PATH}")
    elif args.cmd == "evaluate":
        ids = {x["session_id"] for x in manifest["sessions"] if x["in_phase0_set_b"]}
        old = json.loads(args.old_baselines.read_text("utf-8")) if args.old_baselines else None
        result = evaluate_set(manifest, ids, old)
        _write_json(args.out, result)
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(verify_sources(manifest, args.projects_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
