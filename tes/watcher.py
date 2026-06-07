from __future__ import annotations

"""tes/watcher.py — Background scan loop for auto-scoring finished CC sessions.

Trigger: scheduled-scan + file-stability (sole P2 mechanism).
- Scans cc_path recursively for *.jsonl files.
- Skips files modified within the stability window (not yet "finished").
- Skips sessions already scored with the same file hash (incremental ledger).
- Scores token + waste axes via the unchanged P1 pipeline.
- Judge is OFF by default; opt-in via background_judge=True (--background-judge flag).
- Writes results to the shared SQLite store.

SessionEnd hook: deferred to a future `tes install-hook` command (opt-in, explicit).
Rationale: configuring ~/.claude/settings.json without user consent violates the
no-surprises/moat posture. The hook IS a real fast-path (exists in CC) but must
be user-initiated, not automatic.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.score import ThreeAxisResult, score_session
from tes.store import file_hash, needs_scoring, open_db, upsert_session
from tes.waste import build_waste_entry

logger = logging.getLogger(__name__)

DEFAULT_CC_PATH: Path = Path.home() / ".claude" / "projects"
DEFAULT_SCAN_INTERVAL: int = 120    # seconds between scans
DEFAULT_STABILITY_WINDOW: int = 300  # seconds a file must be unmodified to be "finished"


@dataclass
class WatcherConfig:
    cc_path: Path = field(default_factory=lambda: DEFAULT_CC_PATH)
    scan_interval: int = DEFAULT_SCAN_INTERVAL
    stability_window: int = DEFAULT_STABILITY_WINDOW
    db_path: Path | None = None
    background_judge: bool = False  # OFF by default — opt-in only, see spec discipline 3


def score_session_file(
    path: Path,
    baselines: dict,
    *,
    use_judge: bool = False,
) -> ThreeAxisResult | None:
    """Score a single CC session JSONL file. Returns None on any scoring error.

    Calling convention is identical to the P1 CLI (behavior-preservation guarantee):
      adapt → build_waste_entry → score_session(judge_entry=None unless use_judge).
    Same session → same ThreeAxisResult whether called from the watcher or `tes score`.

    Any exception raised by adapt, build_waste_entry, score_session, or the judge
    step is caught here, logged, and converted to a None return so the caller can
    continue processing remaining sessions.
    """
    try:
        record = adapt_session(path)

        session_id: str = record.get("session_id", path.stem)
        turns: list[dict] = record.get("digest", {}).get("turns", [])
        waste_entry = build_waste_entry(session_id, turns)

        judge_entry: dict | None = None
        if use_judge:
            # background_judge opt-in path. Lazy import so the judge module
            # is never loaded (or its GPU checks triggered) unless opted in.
            from tes.judge import JudgeConfig, score_trajectory  # noqa: PLC0415
            judge_entry = score_trajectory(record, JudgeConfig())

        return score_session(record, baselines, judge_entry=judge_entry, waste_entry=waste_entry)
    except Exception:
        logger.exception("Failed to score %s — skipping", path.name)
        return None


def _scan_once(
    config: WatcherConfig,
    conn: Any,
    baselines: dict,
    *,
    _now: float | None = None,
) -> int:
    """One scan cycle. Returns the count of sessions scored this cycle.

    _now is injectable for testing (avoids sleeping in tests).
    """
    now = _now if _now is not None else time.time()
    scored = 0

    if not config.cc_path.exists():
        logger.debug("cc_path %s does not exist — skipping scan", config.cc_path)
        return 0

    for jsonl_path in config.cc_path.rglob("*.jsonl"):
        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            continue

        # File-stability: skip if modified too recently (session may still be active)
        if (now - mtime) < config.stability_window:
            continue

        session_id = jsonl_path.stem
        try:
            current_hash = file_hash(jsonl_path)
        except OSError:
            continue

        if not needs_scoring(conn, session_id, current_hash):
            continue

        try:
            result = score_session_file(
                jsonl_path,
                baselines,
                use_judge=config.background_judge,
            )
            if result is None:
                continue

            upsert_session(conn, result, str(jsonl_path), mtime, current_hash)
            scored += 1
            logger.info(
                "Scored %s  band=%s  waste=%d",
                session_id,
                result.band_verdict,
                result.waste_event_count,
            )
        except Exception:
            logger.exception("Failed to score/store %s — skipping", jsonl_path.name)
            continue

    return scored


def run_watcher(
    config: WatcherConfig,
    stop_event: threading.Event | None = None,
) -> None:
    """Blocking scan loop. Intended to run in a daemon thread via start_watcher().

    Stops cleanly when stop_event is set (or runs forever if None).
    """
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    conn = open_db(config.db_path)
    logger.info(
        "Watcher started: cc_path=%s  interval=%ds  stability=%ds  judge=%s",
        config.cc_path,
        config.scan_interval,
        config.stability_window,
        "ON" if config.background_judge else "OFF",
    )

    while True:
        try:
            count = _scan_once(config, conn, baselines)
            if count:
                logger.info("Scan complete: %d session(s) scored", count)
        except Exception:
            logger.exception("Scan cycle error — continuing")

        if stop_event is not None:
            if stop_event.wait(timeout=config.scan_interval):
                break
        else:
            time.sleep(config.scan_interval)

    logger.info("Watcher stopped")


def start_watcher(
    config: WatcherConfig,
) -> tuple[threading.Thread, threading.Event]:
    """Start the watcher in a daemon thread.

    Returns (thread, stop_event). Call stop_event.set() to stop the watcher;
    then join the thread if you need to wait for it to finish.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_watcher,
        args=(config, stop_event),
        daemon=True,
        name="tes-watcher",
    )
    thread.start()
    return thread, stop_event


__all__ = [
    "WatcherConfig",
    "DEFAULT_CC_PATH",
    "DEFAULT_SCAN_INTERVAL",
    "DEFAULT_STABILITY_WINDOW",
    "score_session_file",
    "run_watcher",
    "start_watcher",
]
