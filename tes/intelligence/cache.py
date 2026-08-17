from __future__ import annotations

"""tes/intelligence/cache.py — Persistent clustering result cache.

Stores the ML results in ~/.tes/intelligence_cache.json so `tes ask` and the
dashboard don't re-run KMeans on every invocation.

Cache invalidation rules (all must pass for cache to be fresh):
  1. tracegauge version matches (version bump = full re-compute)
  2. session_count within ±5 of stored count (small adds don't re-cluster)
  3. Cache file exists and is valid JSON

The cache stores:
  - tracegauge_version, session_count, computed_at
  - silhouette, k, valid, status
  - archetypes: name, size, fraction, centroid feature values, task_type_counts
  - anomaly_count, anomaly_pct
  - domain_of_validity

What the cache does NOT store:
  - Raw session IDs (privacy: the chat doesn't need to name sessions from cache)
  - Full feature matrix (re-extracted from DB on cache miss)
  - Per-session cluster labels (re-computed on cache miss)

The "not enough sessions" path: if content session count < MIN_CONTENT_FOR_CACHE,
the cache stores {"valid": false, "reason": "not_enough_sessions", "n_content": N}
and the chat/dashboard shows an honest "still building your pattern library" message.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIN_CONTENT_FOR_CACHE: int = 30     # below this: don't attempt clustering
INVALIDATION_DELTA: int = 5         # re-compute if session_count changed by more than this


def _cache_path(db_path: Path | str) -> Path:
    """RR2: co-located with, and named after, the resolved TES database --
    NOT a fixed ~/.tes/intelligence_cache.json regardless of which DB is in
    use. Before this fix, every caller shared one global cache file even
    when TES_DB_PATH pointed somewhere else entirely (e.g. an isolated test
    DB), so computing patterns against a scratch/test database silently
    overwrote the real cache the next real `tes ask`/`tes patterns` call
    would read -- confirmed live during RR1 verification, which is what
    surfaced this.

    UU2: `db_path` is REQUIRED, not `| None = None` -- this function used to
    silently resolve a missing db_path to the real default (~/.tes/tes.db)
    itself, which is exactly how a caller that only meant to inspect/verify
    something (no explicit target in mind) ended up writing to the real
    cache file (found twice: RR2's own discovery, then again during 0.11.1's
    own release verification). Resolution now happens exactly once, at each
    top-level entry point (CLI commands, tes.intelligence.chat), via
    tes.store.resolve_db_path -- everything below that boundary, including
    this function, only ever sees a concrete path it was explicitly handed.
    Mirrors tes.store.resolve_db_path's own resolution order (explicit arg
    -> TES_DB_PATH env var -> ~/.tes/tes.db) so a given DB always maps to
    the same cache file, and a different DB always maps to a different one.
    """
    from tes.store import resolve_db_path

    resolved_db = resolve_db_path(db_path)
    return resolved_db.parent / f"{resolved_db.stem}.intelligence_cache.json"


def _tracegauge_version() -> str:
    try:
        from importlib.metadata import version
        return version("tracegauge")
    except Exception:
        return "unknown"


def load_cache(db_path: Path | str) -> dict[str, Any] | None:
    """Return the parsed cache dict for db_path's DB, or None if not found /
    invalid JSON. See _cache_path (RR2) for why this is DB-scoped, and its
    UU2 note for why db_path is required rather than defaulted."""
    p = _cache_path(db_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_cache_fresh(cache: dict[str, Any], current_session_count: int) -> bool:
    """Return True if the cache can be used without re-computing.

    Checks version match and session count delta.
    """
    if cache is None:
        return False
    if cache.get("tracegauge_version") != _tracegauge_version():
        return False
    stored_count = cache.get("session_count", -1)
    if abs(current_session_count - stored_count) > INVALIDATION_DELTA:
        return False
    return True


def save_cache(
    cache_dict: dict[str, Any],
    session_count: int,
    db_path: Path | str,
) -> None:
    """Write cache_dict to disk, stamped with version + session_count + timestamp.

    Written to db_path's own cache file (RR2) -- see _cache_path. UU2:
    db_path is required -- this function writes to disk, and a defaultable
    path on a write is exactly the shape of bug RR2/UU2 both found.
    """
    p = _cache_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        **cache_dict,
        "tracegauge_version": _tracegauge_version(),
        "session_count": session_count,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_cache_from_results(
    result: "ClusteringResult",
    anomalies: list["AnomalyResult"],
) -> dict[str, Any]:
    """Build the cache dict from clustering + anomaly results.

    Serialises only what the chat + dashboard need (no session IDs, no full matrix).
    """
    archetypes_serial = []
    for a in result.archetypes:
        archetypes_serial.append({
            "cluster_id": a.cluster_id,
            "name": a.name,
            "size": a.size,
            "fraction": round(a.fraction, 4),
            "centroid": {
                name: round(float(a.centroid_unscaled[i]), 6)
                for i, name in enumerate(
                    __import__("tes.intelligence.features", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES
                )
            },
            "task_type_counts": a.task_type_counts,
            "dominant_features": [
                {
                    "name": df["name"],
                    "label": df["label"],
                    "value_unscaled": round(df["value_unscaled"], 6),
                    "z_from_global": round(df["z_from_global"], 4),
                }
                for df in a.dominant_features[:3]
            ],
        })

    return {
        "valid": result.valid,
        "k": result.k,
        "silhouette": round(result.silhouette, 6),
        "silhouette_stability_mean": round(result.silhouette_stability_mean, 6),
        "silhouette_stability_cv": round(result.silhouette_stability_cv, 6),
        "stable": result.stable,
        "status": result.status,
        "domain_of_validity": result.domain_of_validity,
        "n_sessions": result.n_sessions,
        "archetypes": archetypes_serial,
        "anomaly_count": len(anomalies),
        "anomaly_pct": round(100.0 * len(anomalies) / max(result.n_sessions, 1), 2),
    }


def get_or_compute_intelligence(
    *,
    db_path: Path | str,
    force_recompute: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Return intelligence cache, computing it if necessary.

    This is the single entry point for both the chat and the dashboard.
    Returns a dict with the clustering + anomaly results (serialised).

    UU2: `db_path` is required, not `| None = None`. This function computes
    AND WRITES (via save_cache) when the cache is stale or missing -- a
    defaultable path on a function with a write side effect is exactly how
    an interactive/diagnostic call that never meant to touch the real DB
    ended up writing to the real ~/.tes/intelligence_cache.json (twice: the
    original RR2 discovery, then again during 0.11.1's own release
    verification). Resolution (explicit arg -> TES_DB_PATH env var ->
    ~/.tes/tes.db default) still happens, but now exactly once, at each
    top-level entry point (tes.cli's command handlers, tes.intelligence.chat's
    build_chat_context) via tes.store.resolve_db_path -- callers below that
    boundary must be handed a path, never left to guess one.

    The returned dict always has:
      "valid": bool        — False if not enough sessions or clustering failed
      "reason": str        — present when valid=False
      "n_sessions": int    — content sessions the model was trained on

    When valid=True, it also has:
      "k", "silhouette", "archetypes", "anomaly_count", "anomaly_pct",
      "status", "domain_of_validity", "computed_at", "session_count",
      "tracegauge_version"
    """
    from tes.store import open_db, list_sessions
    from tes.intelligence.features import build_feature_matrix
    from tes.intelligence.cluster import run_clustering
    from tes.intelligence.anomaly import detect_anomalies

    conn = open_db(db_path)
    rows = list_sessions(conn, limit=5000, offset=0)
    conn.close()

    total_session_count = len(rows)

    # Check cache validity
    if not force_recompute:
        cached = load_cache(db_path)
        if cached and is_cache_fresh(cached, total_session_count):
            if verbose:
                print(f"[intelligence] Using cached results ({cached.get('n_sessions')} sessions, "
                      f"computed {cached.get('computed_at', 'unknown')})")
            return cached

    # Need to re-compute
    if verbose:
        print("[intelligence] Computing ML patterns...")

    features, X, diagnostics = build_feature_matrix(rows, verbose=verbose)
    n_content = len(features)

    if n_content < MIN_CONTENT_FOR_CACHE:
        # RR1.4: name the real cause rather than the generic "not enough
        # sessions" whenever unreachable source files (legacy rows scored
        # before attribution fractions were persisted at score time) are
        # what's actually blocking the count, not a genuinely thin corpus.
        n_no_source = diagnostics["n_no_source"]
        if n_no_source > 0:
            status = (
                f"Not enough content sessions for pattern analysis yet "
                f"({n_content} < {MIN_CONTENT_FOR_CACHE} needed) -- "
                f"{n_no_source} previously-scored session(s) can't count because "
                "their original transcript file no longer exists on disk (scored "
                "before this version started saving what it needs at score time; "
                "those specific sessions can't be recovered -- re-scoring requires "
                "the same file, which is gone). Your pattern corpus rebuilds from "
                "sessions scored from now on; nothing else to do."
            )
        else:
            status = (
                f"Not enough content sessions for pattern analysis yet "
                f"({n_content} < {MIN_CONTENT_FOR_CACHE} needed). "
                "Patterns will be available as your session corpus grows."
            )
        cache_dict: dict[str, Any] = {
            "valid": False,
            "reason": "not_enough_sessions",
            "n_sessions": n_content,
            "n_content_sessions_needed": MIN_CONTENT_FOR_CACHE,
            "status": status,
            "domain_of_validity": "n/a — minimum corpus size not reached",
        }
        save_cache(cache_dict, total_session_count, db_path)
        return load_cache(db_path) or cache_dict  # reload so caller sees stamps too

    result = run_clustering(features, X, verbose=verbose)
    anomalies = detect_anomalies(features, X, result)

    cache_dict = build_cache_from_results(result, anomalies)
    save_cache(cache_dict, total_session_count, db_path)

    if verbose:
        print(f"[intelligence] k={result.k}, silhouette={result.silhouette:.4f}, "
              f"anomalies={len(anomalies)}")

    return load_cache(db_path) or cache_dict  # reload so caller sees stamps too


# Convenience: build a text summary of the intelligence results for the chat context
def format_intelligence_summary(cache: dict[str, Any]) -> str:
    """Plain-text summary of ML results for inclusion in chat context."""
    if not cache.get("valid"):
        return f"Pattern analysis: {cache.get('status', 'not available')}"

    lines = [
        f"Pattern analysis over {cache['n_sessions']} content sessions "
        f"(k={cache['k']}, silhouette={cache['silhouette']:.3f} — "
        f"{'meaningful structure' if cache['silhouette'] >= 0.20 else 'weak structure'}).",
        "",
        # Note: all attribution values (context_resend, context_growth, output) are
        # expressed as % of billed tokens.  has_waste is a binary YES/NO flag, not a rate.
        "SESSION ARCHETYPES (measured behavioral patterns, not quality labels):",
    ]
    for a in cache["archetypes"]:
        c = a["centroid"]
        task_str = ", ".join(f"{k}:{v}" for k, v in sorted(a["task_type_counts"].items()))
        has_waste_label = "YES" if c.get("has_waste", 0) >= 0.5 else "NO"
        lines.append(
            f"  [{a['cluster_id']}] {a['name']!r}: "
            f"n={a['size']} sessions ({a['fraction']*100:.1f}% of corpus), "
            f"context_resend={c.get('context_resend_pct', 0):.1%} of billed tokens, "
            f"context_growth={c.get('context_growth_pct', 0):.1%} of billed tokens, "
            f"output={c.get('output_pct', 0):.1%} of billed tokens, "
            f"has_waste: {has_waste_label} "
            f"[task mix: {task_str}]"
        )
    lines += [
        "",
        f"ANOMALIES: {cache['anomaly_count']} of {cache['n_sessions']} sessions "
        f"({cache['anomaly_pct']:.1f}%) are statistical outliers for their cluster "
        f"(Tukey fence on centroid distance).",
        "",
        f"Domain of validity: {cache['domain_of_validity']}",
    ]
    return "\n".join(lines)


__all__ = [
    "MIN_CONTENT_FOR_CACHE",
    "INVALIDATION_DELTA",
    "load_cache",
    "is_cache_fresh",
    "save_cache",
    "build_cache_from_results",
    "get_or_compute_intelligence",
    "format_intelligence_summary",
]
