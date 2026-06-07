from __future__ import annotations

"""tes/cli.py — Command-line interface for the Token-Efficiency Scorer.

Entry point: tes score <path> [options]

The PATH may be a single CC session JSONL file or a directory containing
*.jsonl files. Sessions are adapted through the frozen CC adapter (secret
redaction ON by default) and scored on three axes.
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
) -> None:
    """Adapt, detect waste, optionally judge, score, and print one session."""
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

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

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

    for sp in session_paths:
        score_path(sp, baselines, judge_config, use_judge, args.json_mode)
        if not args.json_mode and len(session_paths) > 1:
            print()  # blank line separator between sessions in human mode
