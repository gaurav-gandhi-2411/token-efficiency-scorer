from __future__ import annotations

"""tes/live_monitor.py — Score the ACTIVE (in-progress) CC session, incrementally.

Reuses the frozen engine (adapt_session, compute_attribution, compute_session_cost)
against whatever the active session file currently contains. Never mutates the
store — this is a read-only, best-effort snapshot of a session that is still
being written. All fields are estimates and MUST be labeled as such by callers
(see LiveSessionState.domain_of_validity).

Public API:
    find_active_session(cc_path, stability_window) -> Path | None
    score_live_session(path, prices=None)           -> LiveSessionState | None
"""

import time
from dataclasses import dataclass
from pathlib import Path

from tes._digest import reconstruct_digest
from tes.adapt import adapt_session
from tes.attribution import compute_attribution
from tes.classify import classify_session
from tes.cost import compute_session_cost, load_price_table
from tes.waste import build_waste_entry

LIVE_ESTIMATE_DOV: str = (
    "Live estimate of an IN-PROGRESS session — cost and context size are "
    "provisional and will change as the session continues. Computed with the "
    "same frozen attribution/cost math used on completed sessions, applied to "
    "the partial transcript seen so far. Never a final or billed figure."
)


@dataclass
class LiveSessionState:
    """A read-only, best-effort snapshot of the session currently being written."""

    session_id: str
    task_type: str
    source_path: str
    live_cost_usd: float
    live_context_tokens: int  # cumulative real_tokens (input - cache_read + output) so far
    live_resend_tokens: int  # cumulative context re-send (cache read) tokens so far
    live_resend_ratio: (
        float  # context_resend_tokens / total_billed_tokens (0.0 when no billed tokens yet)
    )
    context_resend_dominant: (
        bool  # context_resend > output + fresh_input — is /compact actually relevant?
    )
    ai_turn_count: int
    domain_of_validity: str


def find_active_session(
    cc_path: Path,
    stability_window: int,
    _now: float | None = None,
) -> Path | None:
    """Return the most-recently-modified session file that is currently ACTIVE.

    "Active" = modified within stability_window — the inverse of the watcher's
    "finished" filter (tes.watcher._scan_once skips files newer than the
    stability window because they may still be in progress; the live monitor
    is interested in exactly those files). Returns None if none are active.
    """
    now = _now if _now is not None else time.time()
    if not cc_path.exists():
        return None

    candidates: list[tuple[Path, float]] = []
    for p in cc_path.rglob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if (now - mtime) < stability_window:
            candidates.append((p, mtime))

    if not candidates:
        return None
    candidates.sort(key=lambda pm: pm[1], reverse=True)
    return candidates[0][0]


def score_live_session(
    path: Path,
    prices: dict | None = None,
) -> LiveSessionState | None:
    """Adapt + attribute + cost the in-progress session file as it currently stands.

    Returns None on any adaptation error (a file mid-write can be momentarily
    unparseable, or may not yet contain any AI turns) — the live monitor
    tolerates this by simply skipping that tick, never raising.
    """
    if prices is None:
        prices = load_price_table()

    try:
        record = adapt_session(path)
        session_id: str = record.get("session_id", path.stem)
        task_type: str = classify_session(record)
        digest_dict: dict = record.get("digest", {})
        turns: list[dict] = digest_dict.get("turns", [])
        if not turns:
            return None

        waste_entry = build_waste_entry(session_id, turns)
        digest = reconstruct_digest(digest_dict)
        session_cost = compute_session_cost(digest, prices)
        attribution = compute_attribution(digest, waste_entry, prices)
    except Exception:
        return None

    resend = attribution.context_resend_tokens
    output = attribution.output_tokens
    fresh = attribution.fresh_input_tokens
    total_billed = attribution.total_billed_tokens
    resend_ratio = (resend / total_billed) if total_billed else 0.0

    return LiveSessionState(
        session_id=session_id,
        task_type=task_type,
        source_path=str(path),
        live_cost_usd=session_cost.total_usd,
        live_context_tokens=attribution.real_tokens,
        live_resend_tokens=resend,
        live_resend_ratio=resend_ratio,
        context_resend_dominant=resend > (output + fresh),
        ai_turn_count=session_cost.ai_turn_count,
        domain_of_validity=LIVE_ESTIMATE_DOV,
    )


__all__ = [
    "LIVE_ESTIMATE_DOV",
    "LiveSessionState",
    "find_active_session",
    "score_live_session",
]
