from __future__ import annotations

"""tes/cli.py — Command-line interface for the Token-Efficiency Scorer.

Frictionless front door (the tool does the work; the user does almost nothing):
    tes                          — bare command launches the dashboard (= tes serve)
    tes score                    — scores your MOST RECENT session (no path needed)
    tes score --pick             — pick from a list of recent sessions
    tes score <path> [options]   — score a specific file/directory (power users)
    tes score --judge            — run the trajectory judge (auto-detects Ollama/API)
    tes serve [options]          — background watcher + localhost dashboard

Session resolution order: explicit PATH > --pick > newest session by mtime.
Sessions are adapted through the frozen CC adapter (secret redaction ON by
default) and scored on three axes. The engine, numbers, and honesty surfacing
are unchanged from 0.5.0 — this is invocation ergonomics only.

API-judge egress is NEVER silent: auto-detecting ANTHROPIC_API_KEY only OFFERS
the API judge; the per-session consent screen still gates every byte that leaves.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from tes._digest import reconstruct_digest
from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.cost import SessionCost, compute_session_cost, load_price_table
from tes.judge import (
    ApiJudgeConfig,
    JudgeConfig,
    JUDGE_SETUP_HINT_FULL,
    build_api_judge_consent_notice,
    detect_env_api_key,
    is_judge_available,
    score_trajectory,
    score_trajectory_api,
)
from tes.watcher import DEFAULT_CC_PATH
from tes.report import format_human, format_json
from tes.score import score_session
from tes.waste import annotate_waste_costs, build_waste_entry, detect_redundant_read, detect_repeated_failed_retry
from tes import __version__

# Load price table once at import time — prices don't change between sessions in a run.
_PRICES: dict = load_price_table()


def _print_contribution_preview(payload: object, out_path: Path) -> None:
    """Print the consent/preview screen for export-contribution.

    Shows: row count, one real sample row (JSON), full field list, explicit
    exclusions, output path, and the non-transmission statement.
    """
    from tes.contribution import ALLOWED_FIELDS, ContributionPayload
    payload = payload  # type: ContributionPayload

    sep = "─" * 72
    print(sep)
    print("CONTRIBUTION EXPORT PREVIEW")
    print(sep)
    print(f"\n  {payload.manifest.row_count} session(s) found in your store.\n")

    print("SAMPLE ROW (real data from your store):\n")
    sample = payload.rows[0]
    print(json.dumps(sample, indent=2, default=str))

    print("\nFIELDS INCLUDED (all content-free):")
    for field_name in sorted(ALLOWED_FIELDS):
        print(f"  {field_name}")

    print("\nNEVER INCLUDED:")
    for excluded in payload.manifest.fields_excluded:
        print(f"  {excluded}")

    print(f"\nOutput: {out_path}")
    print("This writes a local file ONLY.")
    print("NOTHING is transmitted anywhere — tracegauge has no server and sends no data.")
    print("You can open and inspect the file yourself.")
    print(f"\n{sep}")


def _discover_sessions(path: Path) -> list[Path]:
    """Return JSONL paths to score: single file or all *.jsonl in a directory."""
    if path.is_file():
        return [path]
    return sorted(path.glob("*.jsonl"))


def _resolve_cc_path(cc_path_arg: str | None) -> Path:
    """Resolve the Claude Code projects directory (the tool knows where sessions live)."""
    return Path(cc_path_arg).expanduser() if cc_path_arg else DEFAULT_CC_PATH


def _recent_sessions(cc_path: Path, limit: int | None = None) -> list[tuple[Path, float]]:
    """Return (path, mtime) for *.jsonl sessions under cc_path, newest first.

    The tool already scans ~/.claude/projects (this mirrors the watcher's discovery)
    so the user never has to hunt for a session path. limit=None returns all.
    """
    if not cc_path.exists():
        return []
    found: list[tuple[Path, float]] = []
    for p in cc_path.rglob("*.jsonl"):
        try:
            found.append((p, p.stat().st_mtime))
        except OSError:
            continue
    found.sort(key=lambda pm: pm[1], reverse=True)
    return found[:limit] if limit is not None else found


def _newest_session(cc_path: Path) -> Path | None:
    """Return the single most-recently-modified CC session, or None if none exist."""
    recent = _recent_sessions(cc_path, limit=1)
    return recent[0][0] if recent else None


def _fmt_age(mtime: float, _now: float | None = None) -> str:
    """Human-readable 'modified N ago' string from an mtime."""
    now = _now if _now is not None else time.time()
    delta = max(0.0, now - mtime)
    if delta < 90:
        return "just now"
    if delta < 5400:  # < 90 min
        return f"{int(delta // 60)}m ago"
    if delta < 172800:  # < 48 h
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _fmt_size(path: Path) -> str:
    """Human-readable file size."""
    try:
        size = float(path.stat().st_size)
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _project_label(path: Path) -> str:
    """A short, readable label for the project a session belongs to.

    CC encodes the project path as the parent directory name under
    ~/.claude/projects (e.g. 'C--Users-gaura-ml-projects-token-efficiency-scorer').
    Show the tail so the user can recognize it without a wall of path encoding.
    """
    name = path.parent.name
    return name[-40:] if len(name) > 40 else name


def _pick_session(cc_path: Path) -> list[Path]:
    """Interactive picker: show recent sessions, return the chosen one (or [] if aborted)."""
    recent = _recent_sessions(cc_path, limit=10)
    if not recent:
        print(f"[ERROR] No CC sessions found under {cc_path}.", file=sys.stderr)
        return []
    print("Recent Claude Code sessions:\n")
    for i, (p, m) in enumerate(recent, 1):
        print(f"  [{i}]  {_project_label(p):<40}  {p.stem[:8]}…  {_fmt_age(m):>8}  {_fmt_size(p):>8}")
    try:
        raw = input(f"\nPick a session to score [1-{len(recent)}, default 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return []
    if raw == "":
        choice = 1
    else:
        try:
            choice = int(raw)
        except ValueError:
            print("Not a number — aborted.")
            return []
    if not (1 <= choice <= len(recent)):
        print("Out of range — aborted.")
        return []
    return [recent[choice - 1][0]]


def _resolve_score_targets(args: argparse.Namespace) -> list[Path]:
    """Resolve which session(s) to score.

    Resolution order (locked): explicit PATH > --pick (interactive list) > newest.
    The common case needs no path at all — the tool scores the most recent session.
    Returns [] when nothing should be scored (error printed, or pick aborted).
    """
    if args.path is not None:
        target = Path(args.path).expanduser().resolve()
        if not target.exists():
            print(f"[ERROR] Path not found: {target}", file=sys.stderr)
            return []
        paths = _discover_sessions(target)
        if not paths:
            print(f"[ERROR] No .jsonl files found in {target}", file=sys.stderr)
        return paths

    cc_path = _resolve_cc_path(getattr(args, "cc_path", None))

    if getattr(args, "pick", False):
        return _pick_session(cc_path)

    newest = _newest_session(cc_path)
    if newest is None:
        print(
            f"[ERROR] No Claude Code sessions found under {cc_path}.\n"
            "        Run some Claude Code sessions first, or pass an explicit PATH.",
            file=sys.stderr,
        )
        return []
    # First-run / orientation: tell the user exactly what was auto-selected.
    print(
        "No path given — scoring your most recent session "
        "(use `tes score --pick` to choose, or pass a PATH):\n"
        f"  {newest.name}\n"
        f"  {_project_label(newest)} · modified {_fmt_age(newest.stat().st_mtime)}\n",
        file=sys.stderr,
    )
    return [newest]


def score_path(
    path: Path,
    baselines: dict,
    judge_config: JudgeConfig,
    use_judge: bool,
    json_mode: bool,
    store_conn: object = None,
) -> None:
    """Adapt, detect waste, optionally judge, score, and print one session.

    store_conn is an optional open sqlite3.Connection. When provided the
    ThreeAxisResult is written to the TES store after printing. Any store
    write failure is swallowed — it must never break CLI output.
    """
    try:
        record = adapt_session(path)
    except Exception as exc:
        print(f"[ERROR] Failed to adapt {path.name}: {exc}", file=sys.stderr)
        return

    session_id: str = record.get("session_id", path.stem)
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(session_id, turns)

    judge_entry: dict | None = None
    if use_judge:
        judge_entry = score_trajectory(record, judge_config)

    # Cost annotation: compute from measured tokens at per-turn rates.
    session_cost: SessionCost | None = None
    digest_dict = record.get("digest", {})
    if digest_dict and digest_dict.get("turns"):
        try:
            digest = reconstruct_digest(digest_dict)
            session_cost = compute_session_cost(digest, _PRICES)
        except Exception:
            pass  # cost failure must never break CLI output

    # Embed per-event wasted cost (redundant turns only) into waste_events.
    if session_cost is not None:
        per_turn_cost = {tc.turn_index: tc.total_usd for tc in session_cost.turn_costs}
        annotate_waste_costs(waste_entry["waste_events"], per_turn_cost)

    result = score_session(
        record, baselines,
        judge_entry=judge_entry,
        waste_entry=waste_entry,
        session_cost=session_cost,
    )

    # Cost vs baseline framing: look up from the store if available.
    baseline_cost_band: tuple[float, float, float] | None = None
    if store_conn is not None and session_cost is not None:
        try:
            import sqlite3 as _sqlite3  # noqa: PLC0415
            from tes.self_baseline import compute_baseline_cost_band  # noqa: PLC0415
            task_type = result.task_type
            # Derive scope_floor from DB: use p10 of turn_counts as a rough floor (min 20).
            tc_rows = store_conn.execute(  # type: ignore[union-attr]
                "SELECT turn_count FROM sessions "
                "WHERE task_type = ? AND turn_count > 0 ORDER BY turn_count",
                (task_type,),
            ).fetchall()
            if tc_rows:
                counts = [r[0] for r in tc_rows]
                p10_idx = max(0, int(len(counts) * 0.10) - 1)
                scope_floor = max(20, counts[p10_idx])
            else:
                scope_floor = 20
            baseline_cost_band = compute_baseline_cost_band(
                store_conn, task_type, scope_floor  # type: ignore[arg-type]
            )
        except Exception:
            pass  # baseline band lookup failure is non-fatal

    if json_mode:
        print(format_json(result))
    else:
        print(format_human(result, baseline_cost_band=baseline_cost_band))

    if store_conn is not None:
        try:
            from tes.store import file_hash, upsert_session
            source_hash = file_hash(path)
            source_mtime = path.stat().st_mtime
            upsert_session(store_conn, result, str(path), source_mtime, source_hash)
        except Exception:
            pass  # store write failure must never break the CLI output


def _score_path_with_api_judge(
    path: Path,
    baselines: dict,
    judge_config: JudgeConfig,
    use_local_judge: bool,
    json_mode: bool,
    store_conn: object = None,
    api_judge_config: ApiJudgeConfig | None = None,
    api_judge_consent: bool = False,
) -> None:
    """Score a session with optional local or API judge.

    When api_judge_config is provided AND api_judge_consent=True, uses the API
    judge instead of the local judge. Otherwise falls through to use_local_judge.
    """
    try:
        record = adapt_session(path)
    except Exception as exc:
        print(f"[ERROR] Failed to adapt {path.name}: {exc}", file=sys.stderr)
        return

    session_id: str = record.get("session_id", path.stem)
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(session_id, turns)

    judge_entry: dict | None = None
    if api_judge_config is not None and api_judge_consent:
        judge_entry = score_trajectory_api(record, api_judge_config, consent_given=True)
    elif use_local_judge:
        judge_entry = score_trajectory(record, judge_config)

    session_cost: SessionCost | None = None
    digest_dict = record.get("digest", {})
    if digest_dict and digest_dict.get("turns"):
        try:
            digest = reconstruct_digest(digest_dict)
            session_cost = compute_session_cost(digest, _PRICES)
        except Exception:
            pass

    if session_cost is not None:
        per_turn_cost = {tc.turn_index: tc.total_usd for tc in session_cost.turn_costs}
        annotate_waste_costs(waste_entry["waste_events"], per_turn_cost)

    result = score_session(
        record, baselines,
        judge_entry=judge_entry,
        waste_entry=waste_entry,
        session_cost=session_cost,
    )

    baseline_cost_band: tuple[float, float, float] | None = None
    if store_conn is not None and session_cost is not None:
        try:
            import sqlite3 as _sqlite3  # noqa: PLC0415
            from tes.self_baseline import compute_baseline_cost_band  # noqa: PLC0415
            task_type = result.task_type
            tc_rows = store_conn.execute(  # type: ignore[union-attr]
                "SELECT turn_count FROM sessions "
                "WHERE task_type = ? AND turn_count > 0 ORDER BY turn_count",
                (task_type,),
            ).fetchall()
            if tc_rows:
                counts = [r[0] for r in tc_rows]
                p10_idx = max(0, int(len(counts) * 0.10) - 1)
                scope_floor = max(20, counts[p10_idx])
            else:
                scope_floor = 20
            baseline_cost_band = compute_baseline_cost_band(
                store_conn, task_type, scope_floor  # type: ignore[arg-type]
            )
        except Exception:
            pass

    if json_mode:
        print(format_json(result))
    else:
        print(format_human(result, baseline_cost_band=baseline_cost_band))

    if store_conn is not None:
        try:
            from tes.store import file_hash, upsert_session  # noqa: PLC0415
            source_hash = file_hash(path)
            source_mtime = path.stat().st_mtime
            upsert_session(store_conn, result, str(path), source_mtime, source_hash)
        except Exception:
            pass


def _store_session_count(db_path: Path | None) -> int | None:
    """Return the number of sessions already in the store, or None if unavailable.

    Used only for a friendly first-run orientation line — never affects scoring.
    """
    try:
        from tes.store import open_db, resolve_db_path  # noqa: PLC0415
        conn = open_db(resolve_db_path(db_path))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def _run_serve(
    *,
    port: int = 4747,
    scan_interval: int = 120,
    stability_window: int = 300,
    cc_path_arg: str | None = None,
    db_path_arg: str | None = None,
    background_judge: bool = False,
) -> None:
    """Launch the watcher + localhost dashboard. Shared by `tes serve` and bare `tes`.

    Blocks until Ctrl+C. Prints a first-run orientation line so the user is never
    left staring at a blank screen wondering whether anything happened.
    """
    from tes.watcher import WatcherConfig, start_watcher  # noqa: PLC0415
    from tes.web.server import ServerConfig, start_server  # noqa: PLC0415

    db_path = Path(db_path_arg).expanduser() if db_path_arg else None
    cc_path = _resolve_cc_path(cc_path_arg)

    if background_judge:
        print(
            "\nWARNING: --background-judge enabled.\n"
            "This runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new CC session.\n"
            "Ensure Ollama is running before proceeding.\n",
            file=sys.stderr,
        )

    watcher_config = WatcherConfig(
        cc_path=cc_path,
        scan_interval=scan_interval,
        stability_window=stability_window,
        db_path=db_path,
        background_judge=background_judge,
    )
    server_config = ServerConfig(host="127.0.0.1", port=port, db_path=db_path)

    # First-run orientation — the tool tells you what it found and where to look.
    found = len(_recent_sessions(cc_path))
    already_scored = _store_session_count(db_path)
    print("TES service starting...")
    print(f"  Dashboard:        http://127.0.0.1:{port}/")
    print(f"  Watching:         {cc_path}  ({found} session file(s) found)")
    if not already_scored:
        print("  First run:        scoring begins as sessions settle; the dashboard fills in live.")
    print(f"  Scan interval:    {scan_interval}s")
    print(f"  Stability window: {stability_window}s")
    print(f"  Judge:            {'ON (--background-judge)' if background_judge else 'OFF (token+waste only)'}")
    print(f"  Database:         {db_path or '~/.tes/tes.db'}")
    print("  Press Ctrl+C to stop.", flush=True)

    watcher_thread, stop_event = start_watcher(watcher_config)
    try:
        start_server(server_config)  # blocks until Ctrl+C / process exit
    finally:
        stop_event.set()
        watcher_thread.join(timeout=5)


def _run_patterns(
    *,
    db_path: str | None = None,
    force_recompute: bool = False,
) -> None:
    """Show the ML pattern analysis for the session corpus."""
    from tes.intelligence.cache import get_or_compute_intelligence

    print("Computing session patterns...", flush=True)
    cache = get_or_compute_intelligence(
        db_path=db_path,
        force_recompute=force_recompute,
        verbose=True,
    )

    if not cache.get("valid"):
        print(f"\n{cache.get('status', 'Pattern analysis unavailable.')}")
        if cache.get("n_sessions") is not None:
            print(f"Content sessions: {cache['n_sessions']} (need {cache.get('n_content_sessions_needed', 30)}+)")
        return

    sep = "─" * 70
    print(f"\n{sep}")
    print("SESSION PATTERN ANALYSIS")
    print(sep)
    print(f"  {cache['n_sessions']} content sessions  |  k={cache['k']}  |  "
          f"silhouette={cache['silhouette']:.3f}  |  {'stable' if cache['stable'] else 'variable'}")
    print(f"  {cache['status']}")
    print()
    print("ARCHETYPES (measured behavioral patterns — not quality labels):")
    for a in cache["archetypes"]:
        c = a["centroid"]
        task_str = "  ".join(f"{k}:{v}" for k, v in sorted(a["task_type_counts"].items(), key=lambda x: -x[1]))
        print(f"\n  [{a['cluster_id']}] {a['name']}")
        print(f"      {a['size']} sessions ({a['fraction']*100:.1f}%)  "
              f"context_resend={c.get('context_resend_pct', 0):.1%}  "
              f"context_growth={c.get('context_growth_pct', 0):.1%}  "
              f"output={c.get('output_pct', 0):.1%}  "
              f"waste_flag={'yes' if c.get('has_waste', 0) > 0.5 else 'no'}")
        print(f"      task mix: {task_str}")
    print()
    print(f"ANOMALIES: {cache['anomaly_count']} of {cache['n_sessions']} sessions "
          f"({cache['anomaly_pct']:.1f}%) are statistical outliers for their cluster.")
    print()
    print(f"Domain of validity: {cache['domain_of_validity']}")
    print(f"Computed from {cache['session_count']} total sessions in store  "
          f"|  tracegauge {cache['tracegauge_version']}  |  {cache.get('computed_at', '')[:19]}")
    print(sep)
    print("\nTip: 'tes ask \"<question>\"' to ask questions about these patterns in plain language.")


def _run_ask(
    question: str,
    *,
    db_path: str | None = None,
    use_api: bool = False,
    api_model: str = "claude-haiku-4-5-20251001",
    api_key: str | None = None,
    force_recompute: bool = False,
) -> None:
    """Handle `tes ask "<question>"` — the conversational explainer."""
    from tes.intelligence.chat import (
        ChatApiConfig,
        ChatConfig,
        CHAT_EGRESS_NOTICE,
        ask_api,
        ask_local,
    )

    print(f"\nLooking up your session data...", flush=True)

    # --- Try local Ollama first (unless --api is specified) ---
    if not use_api:
        answer = ask_local(
            question,
            db_path=db_path,
            force_recompute=force_recompute,
        )
        if answer:
            print(f"\n{answer}\n")
            print("(answered from measured metrics — local Ollama)")
            return

        # Local unavailable — offer API if key is present
        if not api_key:
            api_key = None
            import os as _os
            api_key = _os.environ.get("ANTHROPIC_API_KEY")

        if api_key:
            print(
                "\nNo local Ollama judge available. "
                "ANTHROPIC_API_KEY is set — the API can answer instead (metrics only, consent required).\n",
            )
            use_api = True
        else:
            print(
                "\nNo LLM available to answer. To enable:\n"
                "  Option 1 — Local (free): install Ollama + pull any 7B+ model\n"
                "  Option 2 — API: export ANTHROPIC_API_KEY=<key> then tes ask --api \"<question>\"\n"
            )
            return

    # --- API path ---
    if not api_key:
        print("[ERROR] --api requires ANTHROPIC_API_KEY env var or --api-key.", file=sys.stderr)
        return

    # Show consent notice
    print(CHAT_EGRESS_NOTICE)
    try:
        consent = input("\nSend metrics to Anthropic to answer this question? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        consent = ""

    if consent != "y":
        print("Aborted — nothing sent.")
        return

    cfg = ChatApiConfig(api_key=api_key, model=api_model)
    answer = ask_api(
        question,
        cfg,
        consent_given=True,
        db_path=db_path,
        force_recompute=force_recompute,
    )
    if answer:
        print(f"\n{answer}\n")
        print(f"(answered from measured metrics — {api_model})")
    else:
        print("[ERROR] API call failed. Check your key and try again.", file=sys.stderr)


def main() -> None:
    """CLI entry point."""
    # Ensure UTF-8 output on Windows (cp1252 console cannot encode ═/─ box-drawing chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="tes",
        description="Token-Efficiency Scorer — three-axis efficiency report for CC sessions.",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    score_p = sub.add_parser(
        "score",
        help="Score CC session log(s).",
        description=(
            "Score one or more Claude Code session JSONL files. "
            "PATH may be a single .jsonl file or a directory of .jsonl files. "
            "Secret redaction is ON by default at ingestion."
        ),
    )
    score_p.add_argument(
        "path",
        metavar="PATH",
        nargs="?",
        default=None,
        help=(
            "Optional path to a CC session JSONL file or directory of JSONL files. "
            "If omitted, the most recent session under ~/.claude/projects is scored. "
            "Use --pick to choose from a list instead."
        ),
    )
    score_p.add_argument(
        "--pick",
        action="store_true",
        help="Choose from a numbered list of your recent sessions instead of scoring the newest.",
    )
    score_p.add_argument(
        "--cc-path",
        default=None,
        dest="cc_path",
        metavar="PATH",
        help="Claude Code projects directory to search (default: ~/.claude/projects).",
    )
    score_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Output full ThreeAxisResult as JSON (includes domain-of-validity strings).",
    )
    score_p.add_argument(
        "--judge",
        action="store_true",
        help=(
            "Run the trajectory-quality judge. Auto-detects a local Ollama judge; if none is "
            "found but an API key is in your environment, offers the API judge (consent "
            "required before any data is sent). With neither, prints the single simplest "
            "setup step — token + waste axes always run regardless."
        ),
    )
    score_p.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip trajectory quality axis even if a judge is available.",
    )
    score_p.add_argument(
        "--judge-model",
        default="qwen3:30b-a3b",
        metavar="MODEL",
        help="Ollama model name for the trajectory judge (default: qwen3:30b-a3b).",
    )
    score_p.add_argument(
        "--judge-endpoint",
        default="http://localhost:11434",
        metavar="URL",
        help="Ollama endpoint URL (default: http://localhost:11434).",
    )
    score_p.add_argument(
        "--api-judge",
        action="store_true",
        dest="api_judge",
        help=(
            "Use the Anthropic API as trajectory judge (opt-in). "
            "Requires ANTHROPIC_API_KEY env var or --api-judge-key. "
            "Shows a consent screen before sending any session data. "
            "Uses the same validated v3 rubric as the local judge. "
            "Cannot be combined with --no-judge."
        ),
    )
    score_p.add_argument(
        "--api-judge-model",
        default="claude-haiku-4-5-20251001",
        metavar="MODEL",
        dest="api_judge_model",
        help="Anthropic model for the API judge (default: claude-haiku-4-5-20251001).",
    )
    score_p.add_argument(
        "--api-judge-key",
        default=None,
        metavar="KEY",
        dest="api_judge_key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var).",
    )

    backfill_p = sub.add_parser(
        "backfill-waste",
        help="Re-run frozen detectors on all stored sessions; fix stale waste counts.",
        description=(
            "Re-run REPEATED-FAILED-RETRY and REDUNDANT-READ detectors on every session "
            "in the store whose source file is accessible, embed per-event wasted_cost_usd "
            "(redundant turns only, P5 cost model), and write correct waste_event_count + "
            "waste_events to the store. Fixes the stale-zeros bug from sessions scored "
            "before waste detection was fully wired. Detectors are frozen (byte-verbatim)."
        ),
    )
    backfill_p.add_argument(
        "--db-path", default=None, dest="db_path",
        metavar="PATH",
        help="Path to TES database (default: ~/.tes/tes.db, or TES_DB_PATH env var).",
    )

    serve_p = sub.add_parser(
        "serve",
        help="Launch background watcher + localhost dashboard (token+waste auto-scoring).",
        description=(
            "Start the TES service: a background scan loop that auto-scores finished CC sessions "
            "(token economy + deterministic waste) and a web dashboard on localhost. "
            "Judge is OFF by default — token+waste run continuously, trajectory requires --background-judge."
        ),
    )
    serve_p.add_argument(
        "--port", type=int, default=4747,
        metavar="PORT",
        help="Dashboard port (default: 4747).",
    )
    serve_p.add_argument(
        "--scan-interval", type=int, default=120, dest="scan_interval",
        metavar="SECONDS",
        help="Seconds between scan cycles (default: 120).",
    )
    serve_p.add_argument(
        "--stability-window", type=int, default=300, dest="stability_window",
        metavar="SECONDS",
        help="Seconds a session file must be unmodified before scoring (default: 300).",
    )
    serve_p.add_argument(
        "--cc-path", default=None, dest="cc_path",
        metavar="PATH",
        help="Path to Claude Code projects directory (default: ~/.claude/projects).",
    )
    serve_p.add_argument(
        "--db-path", default=None, dest="db_path",
        metavar="PATH",
        help="Path to TES database (default: ~/.tes/tes.db, or TES_DB_PATH env var).",
    )
    serve_p.add_argument(
        "--background-judge", action="store_true", dest="background_judge",
        help=(
            "Enable trajectory judge in the background watcher (local Ollama only). "
            "Requires Ollama + qwen3:30b-a3b (~18 GB VRAM). "
            "Setup: install Ollama (https://ollama.ai) then 'ollama pull qwen3:30b-a3b'. "
            "For on-demand judging without a GPU: use 'tes score <path> --api-judge' instead."
        ),
    )

    export_p = sub.add_parser(
        "export-contribution",
        help="Export a redacted, content-free local file for the optional corpus contribution program.",
        description=(
            "Build an allow-listed, content-free summary of your scored sessions and write it "
            "to a local file you can inspect. NOTHING is transmitted — tracegauge has no server. "
            "Shows a preview and requires explicit confirmation before writing."
        ),
    )
    export_p.add_argument(
        "--output", default=None, dest="output",
        metavar="PATH",
        help="Output file path (default: ~/.tes/contribution-<date>.jsonl).",
    )
    export_p.add_argument(
        "--anonymous", action="store_true",
        help="Omit contributor_id from all rows.",
    )
    export_p.add_argument(
        "--preview", action="store_true",
        help="Show the sample row and field list without writing any file.",
    )
    export_p.add_argument(
        "--db-path", default=None, dest="db_path",
        metavar="PATH",
        help="Path to TES database (default: ~/.tes/tes.db, or TES_DB_PATH env var).",
    )

    ask_p = sub.add_parser(
        "ask",
        help="Ask a natural-language question about your sessions (conversational explainer).",
        description=(
            "Ask questions about your session history in plain language. "
            "The LLM answers ONLY from already-measured metrics and ML pattern results — "
            "it never invents analysis, predicts future costs, or judges session quality. "
            "Tries local Ollama first; with ANTHROPIC_API_KEY set, offers the API path "
            "(sends metrics only — no session content — with your consent)."
        ),
    )
    ask_p.add_argument(
        "question",
        metavar="QUESTION",
        help='Question about your sessions, e.g. "What kind of sessions do I run?"',
    )
    ask_p.add_argument(
        "--api",
        action="store_true",
        help=(
            "Use the Anthropic API for answering (opt-in). Requires ANTHROPIC_API_KEY. "
            "Sends corpus metrics only — no session content — with your explicit consent."
        ),
    )
    ask_p.add_argument(
        "--api-model",
        default="claude-haiku-4-5-20251001",
        metavar="MODEL",
        dest="api_model",
        help="Anthropic model for the API chat path (default: claude-haiku-4-5-20251001).",
    )
    ask_p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        dest="api_key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var).",
    )
    ask_p.add_argument(
        "--db-path", default=None, dest="db_path",
        metavar="PATH",
        help="Path to TES database (default: ~/.tes/tes.db, or TES_DB_PATH env var).",
    )
    ask_p.add_argument(
        "--recompute",
        action="store_true",
        help="Force re-computation of ML patterns instead of using cached results.",
    )

    patterns_p = sub.add_parser(
        "patterns",
        help="Show the session archetypes and anomaly summary (ML pattern analysis).",
        description=(
            "Run or display the ML pattern analysis: validated clustering of your session corpus "
            "into behavioral archetypes, plus statistical anomaly detection. "
            "Results are cached to ~/.tes/intelligence_cache.json and re-used by 'tes ask'."
        ),
    )
    patterns_p.add_argument(
        "--db-path", default=None, dest="db_path",
        metavar="PATH",
        help="Path to TES database (default: ~/.tes/tes.db, or TES_DB_PATH env var).",
    )
    patterns_p.add_argument(
        "--recompute",
        action="store_true",
        help="Force re-computation even if a fresh cache exists.",
    )

    args = parser.parse_args()
    if args.command is None:
        # Bare `tes` does the obvious useful thing: launch the dashboard.
        # (`tes --help` still shows help; `tes <unknown>` still errors via argparse.)
        _run_serve()
        sys.exit(0)

    if args.command == "backfill-waste":
        from pathlib import Path as _Path
        from tes.store import backfill_waste

        db_path = _Path(args.db_path).expanduser() if args.db_path else None
        print("Running waste backfill — re-running frozen detectors on all accessible sessions...")
        summary = backfill_waste(db_path=db_path)
        print(f"  Sessions with waste written: {summary['updated']}")
        print(f"  Sessions confirmed 0-waste:  {summary['no_waste']}")
        print(f"  Source files not accessible: {summary['missing_source']}")
        print(f"  Errors (skipped):            {summary['errors']}")
        total_processed = summary['updated'] + summary['no_waste']
        print(f"  Total processed: {total_processed}")
        sys.exit(0)

    if args.command == "serve":
        _run_serve(
            port=args.port,
            scan_interval=args.scan_interval,
            stability_window=args.stability_window,
            cc_path_arg=args.cc_path,
            db_path_arg=args.db_path,
            background_judge=args.background_judge,
        )
        sys.exit(0)

    if args.command == "export-contribution":
        from datetime import date as _date
        from tes.contribution import build_contribution_payload, get_or_create_contributor_id
        from tes.store import open_db as _open_db

        db_path = Path(args.db_path).expanduser() if args.db_path else None

        try:
            conn = _open_db(db_path)
        except Exception as exc:
            print(f"[ERROR] Cannot open TES store: {exc}", file=sys.stderr)
            sys.exit(1)

        contributor_id: str | None = None if args.anonymous else get_or_create_contributor_id()

        try:
            payload = build_contribution_payload(
                conn,
                contributor_id=contributor_id,
                include_source_components=True,
            )
        except Exception as exc:
            print(f"[ERROR] Failed to build contribution payload: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        if payload.manifest.row_count == 0:
            print("No sessions found in store. Run `tes score` or `tes serve` first.")
            conn.close()
            sys.exit(0)

        today_str = _date.today().isoformat()
        out_path = (
            Path(args.output).expanduser()
            if args.output
            else Path.home() / ".tes" / f"contribution-{today_str}.jsonl"
        )

        _print_contribution_preview(payload, out_path)

        if args.preview:
            print("\n[--preview mode: no file written]")
            conn.close()
            sys.exit(0)

        try:
            answer = input("\nContinue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if answer != "y":
            print("Aborted — no file written.")
            conn.close()
            sys.exit(0)

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                for row in payload.rows:
                    fh.write(json.dumps(row) + "\n")
            print(f"\nWritten: {out_path}")
            print(f"  {payload.manifest.row_count} row(s)")
            print("  Open the file to inspect it. Nothing has been transmitted.")
        except Exception as exc:
            print(f"[ERROR] Failed to write file: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        conn.close()
        sys.exit(0)

    if args.command == "patterns":
        _run_patterns(
            db_path=args.db_path,
            force_recompute=args.recompute,
        )
        sys.exit(0)

    if args.command == "ask":
        import os as _os
        _run_ask(
            question=args.question,
            db_path=args.db_path,
            use_api=args.api,
            api_model=args.api_model,
            api_key=getattr(args, "api_key", None) or _os.environ.get("ANTHROPIC_API_KEY"),
            force_recompute=args.recompute,
        )
        sys.exit(0)

    # --- score command ---
    import os as _os  # noqa: PLC0415

    # Contradictory judge flags — fail fast and clearly (never a cryptic argparse error).
    if getattr(args, "no_judge", False) and getattr(args, "judge", False):
        print("[ERROR] --judge and --no-judge are mutually exclusive.", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "api_judge", False) and getattr(args, "no_judge", False):
        print("[ERROR] --api-judge and --no-judge are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    judge_config = JudgeConfig(
        model=args.judge_model,
        endpoint=args.judge_endpoint,
    )

    # ----- Resolve which session(s) to score (explicit PATH > --pick > newest). -----
    session_paths = _resolve_score_targets(args)
    if not session_paths:
        # _resolve_score_targets already printed why (no sessions, bad path, or aborted pick).
        sys.exit(0 if getattr(args, "pick", False) else 1)

    # ----- Resolve the judge plan: auto-detect + guide. Consent stays the egress gate. -----
    # Detecting an API key NEVER sends data: any API-judge call still passes the
    # unconditional per-session consent prompt below.
    use_local_judge = False
    want_api = getattr(args, "api_judge", False)

    if getattr(args, "no_judge", False):
        pass  # judge explicitly skipped — token + waste still run
    elif want_api:
        pass  # explicit API path — handled by want_api below
    elif getattr(args, "judge", False):
        # Explicit --judge: auto-detect the best available judge.
        if is_judge_available(judge_config):
            use_local_judge = True
        elif detect_env_api_key() is not None:
            # An API key is present. OFFER the API judge (still consent-gated below).
            # We do NOT send anything here — we route to the consent screen.
            print(
                "\nNo local judge detected — but ANTHROPIC_API_KEY is set in your environment.\n"
                "The API judge can run instead (your key; sends trajectory data to Anthropic).\n"
                "Review the consent notice below — NOTHING is sent until you confirm.\n",
                file=sys.stderr,
            )
            want_api = True
        else:
            # Neither available: the single simplest next step, never a cryptic fail.
            print(f"\n{JUDGE_SETUP_HINT_FULL}\n", file=sys.stderr)
    else:
        # Default (no judge flag): attempt the local judge if present — behavior preserved.
        use_local_judge = True

    # Build the API judge config when the API path is in play (explicit or offered).
    api_judge_config: ApiJudgeConfig | None = None
    api_judge_consent: bool = False
    api_key_source = ""
    if want_api:
        api_key = getattr(args, "api_judge_key", None) or _os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print(
                "[ERROR] --api-judge requires ANTHROPIC_API_KEY env var or --api-judge-key.",
                file=sys.stderr,
            )
            sys.exit(1)
        api_judge_config = ApiJudgeConfig(
            api_key=api_key,
            model=getattr(args, "api_judge_model", "claude-haiku-4-5-20251001"),
        )
        api_key_source = (
            "--api-judge-key argument"
            if getattr(args, "api_judge_key", None)
            else "ANTHROPIC_API_KEY env var"
        )

    # API judge consent: obtained once before scoring any sessions. UNCONDITIONAL egress gate.
    # Declining does not abort the run — token + waste axes still score (a complete result).
    if api_judge_config is not None:
        notice_session_id = "this session" if len(session_paths) > 1 else session_paths[0].stem
        notice = build_api_judge_consent_notice(
            session_id=notice_session_id,
            task_type="auto-detected",
            model=api_judge_config.model,
            api_key_source=api_key_source,
        )
        print(notice)
        try:
            answer = input("\nContinue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            print("Aborted — no data sent. Scoring token + waste axes only.")
            api_judge_config = None  # no egress
            api_judge_consent = False
        else:
            api_judge_consent = True

    # Print judge on-ramp hint when local judge would be used but is unavailable.
    if use_local_judge and not is_judge_available(judge_config):
        print(f"\n{JUDGE_SETUP_HINT_FULL}\n", file=sys.stderr)

    from tes.store import open_db  # noqa: PLC0415
    store_conn = None
    try:
        store_conn = open_db()
    except Exception:
        pass

    try:
        for sp in session_paths:
            _score_path_with_api_judge(
                sp, baselines, judge_config, use_local_judge, args.json_mode,
                store_conn=store_conn,
                api_judge_config=api_judge_config,
                api_judge_consent=api_judge_consent,
            )
            if not args.json_mode and len(session_paths) > 1:
                print()
    finally:
        if store_conn is not None:
            store_conn.close()
