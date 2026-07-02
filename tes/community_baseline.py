from __future__ import annotations

"""tes/community_baseline.py — Cross-developer COMMUNITY token baseline (0.9.0).

Two clearly separate responsibilities:

1. Batch computation (offline, maintainer-run, no I/O beyond the pure function):
     compute_community_baseline(rows) -> dict
   Groups pooled content-free contribution rows (tes/contribution.py's
   ALLOWED_FIELDS shape) by task_type and computes percentile statistics over
   real_tokens — the SAME token measure the self/B2-corpus baseline uses (see
   tes/baselines.compute_real_tokens and tes/data/cc_baselines.json's
   "token_measure" field). Emits a JSON-serializable dict that mirrors the
   shape of the bundled cc_baselines.json (generated / token_measure /
   schema_version, then per-task_type stats) — this is the "DATA FILE the
   client fetches" from spec.md, published exactly like cc_baselines.json
   ships today. `main()` is a thin CLI wrapper: reads a JSONL file of rows,
   writes the computed baseline JSON.

2. Client-side scoring (ships in the installed package):
     score_against_community(real_tokens, task_type, community_baseline) -> dict | None
     fetch_community_baseline(url, timeout_s=10.0) -> dict | None
   Used by CLI/web display to show a session's percentile rank against the
   pooled community distribution, ALONGSIDE (never replacing) the self-baseline.

This module is purely additive: it does not import from, modify, or call into
tes/score.py, tes/baselines.py, or tes/_waste_detectors.py. The self-baseline
scoring path is completely unaffected by this module's presence or absence.

Public API:
    MIN_CONTRIBUTORS               — minimum distinct contributors for a
                                      task_type to be reported as scoreable
    compute_community_baseline(rows) -> dict
    score_against_community(real_tokens, task_type, community_baseline) -> dict | None
    fetch_community_baseline(url, timeout_s=10.0) -> dict | None
    main(argv=None) -> None
"""

import argparse
import bisect
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Reuses the SAME token measure as the self-baseline / B2 corpus baseline
# (tes/baselines.compute_real_tokens; tes/data/cc_baselines.json's
# "token_measure" field). The contribution row's real_tokens field is computed
# client-side with that exact formula (tes/contribution.build_contribution_payload),
# so pooling raw real_tokens values across contributors compares like with like.
COMMUNITY_TOKEN_MEASURE: str = (
    "real_tokens = sum_ai_turns(token_count_input - cache_read + token_count_output) "
    "— same measure as the self/corpus baseline (see tes.baselines.compute_real_tokens)"
)

COMMUNITY_SCHEMA_VERSION: str = "1"

# A "community" baseline computed from one prolific contributor's 50 sessions
# is not a community signal — it is that one person's self-baseline wearing a
# community label. MIN_CONTRIBUTORS is a floor on DISTINCT CONTRIBUTORS, not
# on session count, because session count alone can't rule out a single-person
# distribution (a type could have 200 sessions from 2 people). 5 is chosen as
# a deliberately conservative early-corpus floor: below it, one or two
# contributors could single-handedly define "the community," which would make
# the cross-developer framing misleading. Task types below this floor are
# reported with their counts but marked unscoreable (available=False) — no
# percentile is emitted for them.
MIN_CONTRIBUTORS: int = 5

# Percentile points computed and stored for display (in addition to the full
# sorted real_tokens list, which supports exact percentile-rank lookups at
# score time — see _percentile_rank).
_PERCENTILE_POINTS: tuple[float, ...] = (10.0, 25.0, 50.0, 75.0, 90.0)


# ---------------------------------------------------------------------------
# Batch computation (offline, maintainer-run — pure function, no I/O)
# ---------------------------------------------------------------------------


@dataclass
class _TypeAccumulator:
    """Internal accumulator for one task_type while scanning pooled rows."""

    real_tokens: list[int] = field(default_factory=list)
    contributor_ids: set[str] = field(default_factory=set)
    row_count: int = 0


def _percentile_of_sorted(sorted_values: list[int], pct: float) -> int:
    """Return the value at the given percentile (0-100) from a sorted list.

    Nearest-rank method: idx = int(n * pct/100) - 1, floor, clamped to a
    valid index. Matches the convention already used in
    tes.self_baseline._percentile, kept consistent rather than reinventing a
    different (e.g. numpy-interpolated) percentile method in a sibling module.
    """
    n = len(sorted_values)
    idx = max(0, int(n * (pct / 100.0)) - 1)
    idx = min(idx, n - 1)
    return sorted_values[idx]


def compute_community_baseline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute cross-developer percentile baselines per task_type from pooled rows.

    Pure function — no network, no database access. Run OFFLINE by the
    maintainer against pooled corpus rows (the tes/contribution.py 14-field
    allow-list shape); never run by end users as part of normal tracegauge
    usage.

    Groups rows by task_type and computes, for each type with at least
    MIN_CONTRIBUTORS distinct contributors:
      - p10/p25/p50/p75/p90 percentile points over real_tokens
      - the full sorted real_tokens list (supports exact percentile-rank
        lookups in score_against_community, and is itself content-free —
        just numbers, no session identity)
      - contributor_count (distinct contributor_id, excluding rows with no
        contributor_id — e.g. anonymous contributions) and session_count
        (rows with a usable real_tokens value)

    Task types below MIN_CONTRIBUTORS are still included in the output (with
    their counts, for transparency) but marked available=False and carry no
    percentile stats — see MIN_CONTRIBUTORS docstring for why.

    Parameters
    ----------
    rows:
        List of content-free contribution row dicts, same shape as
        tes.contribution.ALLOWED_FIELDS (task_type, real_tokens,
        contributor_id, ... — see tes/contribution.py for exact field
        semantics). Rows with a missing/falsy task_type are skipped.

    Returns
    -------
    dict
        JSON-serializable, mirroring the shape of the bundled
        tes/data/cc_baselines.json: generated / token_measure /
        schema_version / min_contributors / types (per-task_type stats).
    """
    by_type: dict[str, _TypeAccumulator] = {}
    for row in rows:
        task_type = row.get("task_type")
        if not task_type:
            continue
        acc = by_type.setdefault(task_type, _TypeAccumulator())
        acc.row_count += 1

        real_tokens = row.get("real_tokens")
        if isinstance(real_tokens, int) and not isinstance(real_tokens, bool) and real_tokens > 0:
            acc.real_tokens.append(real_tokens)

        contributor_id = row.get("contributor_id")
        if contributor_id:
            acc.contributor_ids.add(contributor_id)

    types_output: dict[str, dict[str, Any]] = {}
    for task_type, acc in by_type.items():
        contributor_count = len(acc.contributor_ids)
        session_count = len(acc.real_tokens)

        if contributor_count < MIN_CONTRIBUTORS or session_count == 0:
            types_output[task_type] = {
                "available": False,
                "contributor_count": contributor_count,
                "session_count": session_count,
                "row_count": acc.row_count,
            }
            continue

        sorted_tokens = sorted(acc.real_tokens)
        percentiles = {
            f"p{int(pct)}": _percentile_of_sorted(sorted_tokens, pct) for pct in _PERCENTILE_POINTS
        }
        types_output[task_type] = {
            "available": True,
            "contributor_count": contributor_count,
            "session_count": session_count,
            "row_count": acc.row_count,
            **percentiles,
            "sorted_real_tokens": sorted_tokens,
        }

    return {
        "generated": datetime.now(tz=UTC).date().isoformat(),
        "token_measure": COMMUNITY_TOKEN_MEASURE,
        "schema_version": COMMUNITY_SCHEMA_VERSION,
        "min_contributors": MIN_CONTRIBUTORS,
        "min_contributors_rationale": (
            "A 'community' baseline drawn from a single contributor's many sessions is not "
            "a community signal. Task types with fewer than min_contributors distinct "
            "contributors are included here (for transparency) but not reported as scoreable."
        ),
        "types": types_output,
    }


def _load_jsonl_rows(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL file of pooled contribution rows into a list of dicts."""
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: read pooled rows from a JSONL file, write the computed baseline JSON.

    Usage: python -m tes.community_baseline <rows.jsonl> <output.json>

    This is the maintainer-run OFFLINE batch step (spec.md: "publish them as
    a DATA FILE the client fetches — exactly like cc_baselines.json ships
    today"). No network access, no database — reads a local file, writes a
    local file. Never invoked by end users during normal tracegauge usage.
    """
    parser = argparse.ArgumentParser(
        description="Compute the community token-efficiency baseline from pooled rows."
    )
    parser.add_argument(
        "rows_path", type=Path, help="Path to a JSONL file of pooled contribution rows"
    )
    parser.add_argument(
        "output_path", type=Path, help="Path to write the computed community baseline JSON"
    )
    args = parser.parse_args(argv)

    rows = _load_jsonl_rows(args.rows_path)
    baseline = compute_community_baseline(rows)
    args.output_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")

    scoreable = sum(1 for t in baseline["types"].values() if t.get("available"))
    print(f"Written: {args.output_path}")
    print(f"Task types seen: {len(baseline['types'])}")
    print(f"Scoreable (>= {MIN_CONTRIBUTORS} contributors): {scoreable}")


# ---------------------------------------------------------------------------
# Client-side scoring (ships in the installed package)
# ---------------------------------------------------------------------------


def _percentile_rank(sorted_values: list[int], value: int) -> float:
    """Return `value`'s percentile rank (0-100) within a sorted list.

    Uses the 'mean' method: the average of the fraction of values strictly
    below `value` and the fraction of values at-or-below `value`. Implemented
    with bisect rather than scipy.stats.percentileofscore since scipy is not
    a declared dependency of this package (numpy/scikit-learn are, but this
    is simple enough not to need either).
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    below = bisect.bisect_left(sorted_values, value)
    at_or_below = bisect.bisect_right(sorted_values, value)
    return ((below + at_or_below) / 2.0) / n * 100.0


def _ordinal(n: int) -> str:
    """Return the ordinal string for a non-negative integer, e.g. 62 -> '62nd'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _community_domain_of_validity(task_type: str, contributor_count: int, session_count: int) -> str:
    """Build the DOV string carried on every score_against_community() result.

    Non-negotiable per spec.md: every returned result carries (a) N
    contributors for the task_type, (b) self-selection bias, (c)
    content-free coarseness. Same honest, unhedged tone as
    TOKEN_DOMAIN_OF_VALIDITY / TRAJECTORY_DOMAIN_OF_VALIDITY /
    WASTE_DOMAIN_OF_VALIDITY in tes/score.py — plainly stating the
    limitation, not marketing-speak.
    """
    return (
        f"Community baseline for {task_type}: {contributor_count} contributing developers, "
        f"{session_count} pooled sessions. "
        "Self-selection: only developers who opted in are represented — not a random sample "
        "of developers, and their usage patterns (the reasons they chose to opt in) may "
        "differ systematically from the general population. "
        "Content-free coarseness: this compares raw real_tokens counts only — it does NOT "
        "account for task complexity, model choice, or session goals the way the "
        "self-baseline's same-user-same-habits comparison implicitly does. "
        "Alongside, never instead of, your self-baseline."
    )


def score_against_community(
    real_tokens: int,
    task_type: str,
    community_baseline: dict[str, Any],
) -> dict[str, Any] | None:
    """Score a session's real_tokens against the pooled community distribution.

    Parameters
    ----------
    real_tokens:
        The session's real_tokens (tes.baselines.compute_real_tokens — same
        measure the community baseline was computed over).
    task_type:
        The session's classified task type.
    community_baseline:
        A loaded community baseline dict — the output of
        compute_community_baseline() (typically fetched via
        fetch_community_baseline() and cached locally).

    Returns
    -------
    dict or None
        None when task_type is absent from community_baseline, or is below
        the minimum-contributor floor (available=False) — a percentile
        computed from too few contributors would be misleadingly precise, so
        this returns "not available" rather than a number.

        Otherwise a dict with the percentile rank plus reference points
        (p25/median/p75), contributor_count, session_count, and a
        domain_of_validity field. domain_of_validity is ALWAYS populated on
        every non-None result — there is no "just show the number" path.
    """
    types = community_baseline.get("types", {})
    type_info = types.get(task_type)
    if type_info is None or not type_info.get("available", False):
        return None

    sorted_tokens: list[int] = type_info.get("sorted_real_tokens", [])
    if not sorted_tokens:
        return None

    contributor_count = type_info.get("contributor_count", 0)
    session_count = type_info.get("session_count", len(sorted_tokens))

    percentile = round(_percentile_rank(sorted_tokens, real_tokens))
    percentile = max(0, min(100, percentile))

    return {
        "task_type": task_type,
        "real_tokens": real_tokens,
        "percentile": percentile,
        "percentile_label": f"{_ordinal(percentile)} percentile",
        "contributor_count": contributor_count,
        "session_count": session_count,
        "p25": type_info.get("p25"),
        "median": type_info.get("p50"),
        "p75": type_info.get("p75"),
        "domain_of_validity": _community_domain_of_validity(
            task_type, contributor_count, session_count
        ),
    }


def fetch_community_baseline(url: str, timeout_s: float = 10.0) -> dict[str, Any] | None:
    """GET the published community baseline data file, or None on ANY failure.

    Mirrors the graceful-degrade style of tes.judge._probe_ollama_tags /
    _call_judge_api: the community baseline is an enhancement, never a hard
    dependency. Timeout, non-200 status, malformed JSON, network error, or
    any other failure degrades silently to None ("community baseline not
    available") — this function NEVER raises. The self-baseline path is
    completely unaffected either way.

    Parameters
    ----------
    url:
        URL of the published community baseline JSON (the "data file the
        client fetches" from spec.md — the output of compute_community_baseline(),
        published exactly like cc_baselines.json ships today).
    timeout_s:
        Request timeout in seconds.

    Returns
    -------
    dict or None
        The parsed baseline dict, or None on any failure.
    """
    try:
        resp = httpx.get(url, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


if __name__ == "__main__":
    main()


__all__ = [
    "MIN_CONTRIBUTORS",
    "COMMUNITY_TOKEN_MEASURE",
    "COMMUNITY_SCHEMA_VERSION",
    "compute_community_baseline",
    "score_against_community",
    "fetch_community_baseline",
    "main",
]
