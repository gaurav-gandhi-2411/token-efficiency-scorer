from __future__ import annotations

"""tes/cli.py — Command-line interface for the Token-Efficiency Scorer.

Entry points:
    tes score <path> [options]   — score one or more CC session JSONL files
    tes serve [options]          — background watcher + localhost dashboard

The PATH for `score` may be a single CC session JSONL file or a directory
containing *.jsonl files. Sessions are adapted through the frozen CC adapter
(secret redaction ON by default) and scored on three axes.
"""

import argparse
import json
import sys
from pathlib import Path

from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.judge import JudgeConfig, score_trajectory
from tes.report import format_human, format_json
from tes.score import score_session
from tes.waste import build_waste_entry, detect_redundant_read, detect_repeated_failed_retry


def _discover_sessions(path: Path) -> list[Path]:
    """Return JSONL paths to score: single file or all *.jsonl in a directory."""
    if path.is_file():
        return [path]
    return sorted(path.glob("*.jsonl"))


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

    result = score_session(
        record, baselines, judge_entry=judge_entry, waste_entry=waste_entry
    )

    if json_mode:
        print(format_json(result))
    else:
        print(format_human(result))

    if store_conn is not None:
        try:
            from tes.store import file_hash, upsert_session
            source_hash = file_hash(path)
            source_mtime = path.stat().st_mtime
            upsert_session(store_conn, result, str(path), source_mtime, source_hash)
        except Exception:
            pass  # store write failure must never break the CLI output


def main() -> None:
    """CLI entry point."""
    # Ensure UTF-8 output on Windows (cp1252 console cannot encode ═/─ box-drawing chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="tes",
        description="Token-Efficiency Scorer — three-axis efficiency report for CC sessions.",
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
        help="Path to a CC session JSONL file or directory of JSONL files.",
    )
    score_p.add_argument(
        "--json",
        action="store_true",
        dest="json_mode",
        help="Output full ThreeAxisResult as JSON (includes domain-of-validity strings).",
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
            "Enable trajectory judge in the background watcher. "
            "WARNING: runs a 30B model on your GPU for every new session continuously."
        ),
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "serve":
        from pathlib import Path as _Path
        from tes.watcher import WatcherConfig, start_watcher, DEFAULT_CC_PATH
        from tes.web.server import ServerConfig, start_server

        db_path = _Path(args.db_path).expanduser() if args.db_path else None
        cc_path = _Path(args.cc_path).expanduser() if args.cc_path else DEFAULT_CC_PATH

        if args.background_judge:
            print(
                "\nWARNING: --background-judge enabled.\n"
                "This runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new CC session.\n"
                "Ensure Ollama is running before proceeding.\n",
                file=sys.stderr,
            )

        watcher_config = WatcherConfig(
            cc_path=cc_path,
            scan_interval=args.scan_interval,
            stability_window=args.stability_window,
            db_path=db_path,
            background_judge=args.background_judge,
        )
        server_config = ServerConfig(
            host="127.0.0.1",
            port=args.port,
            db_path=db_path,
        )

        print(f"TES service starting...")
        print(f"  Dashboard:        http://127.0.0.1:{args.port}/")
        print(f"  Watching:         {cc_path}")
        print(f"  Scan interval:    {args.scan_interval}s")
        print(f"  Stability window: {args.stability_window}s")
        print(f"  Judge:            {'ON (--background-judge)' if args.background_judge else 'OFF (token+waste only)'}")
        print(f"  Database:         {db_path or '~/.tes/tes.db'}")
        print(f"  Press Ctrl+C to stop.", flush=True)

        watcher_thread, stop_event = start_watcher(watcher_config)
        try:
            start_server(server_config)  # blocks until Ctrl+C / process exit
        finally:
            stop_event.set()
            watcher_thread.join(timeout=5)
        sys.exit(0)

    # --- score command ---
    # Load bundled baselines
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    judge_config = JudgeConfig(
        model=args.judge_model,
        endpoint=args.judge_endpoint,
    )
    use_judge = not args.no_judge

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f"[ERROR] Path not found: {target}", file=sys.stderr)
        sys.exit(1)

    session_paths = _discover_sessions(target)
    if not session_paths:
        print(f"[ERROR] No .jsonl files found in {target}", file=sys.stderr)
        sys.exit(1)

    from tes.store import open_db
    store_conn = None
    try:
        store_conn = open_db()
    except Exception:
        pass  # store unavailable is non-fatal for `tes score`

    try:
        for sp in session_paths:
            score_path(sp, baselines, judge_config, use_judge, args.json_mode, store_conn=store_conn)
            if not args.json_mode and len(session_paths) > 1:
                print()  # blank line separator between sessions in human mode
    finally:
        if store_conn is not None:
            store_conn.close()
