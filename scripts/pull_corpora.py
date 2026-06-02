from __future__ import annotations

"""pull_corpora.py — Merge local + public HuggingFace CC session corpora.

Pulls two public HuggingFace datasets of Claude Code traces, merges with the
user's 152 local CC session files, runs all sessions through the existing
claudecode_adapter, and reports a summary table.

Usage:
    python scripts/pull_corpora.py --dry-run --limit 5
    python scripts/pull_corpora.py --dry-run
    python scripts/pull_corpora.py --output data/corpus_pool/pool_adapted.jsonl
    python scripts/pull_corpora.py --skip-public
"""

import argparse
import hashlib
import json
import sys
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Suppress HF symlink warning on Windows — expected in dev environment.
warnings.filterwarnings("ignore", message=".*symlinks.*", category=UserWarning)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from adapters.claudecode_adapter import adapt_session  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_OUTPUT: Path = ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"

_LOCAL_PROJECTS_ROOT: Path = Path.home() / ".claude" / "projects"
_LOCAL_PROJECT_DIRS: list[str] = [
    "C--Users-gaura-ml-projects",
    "C--Users-gaura-ml-projects-AetherArt",
    "C--Users-gaura-ml-projects-agentgauge",
    "C--Users-gaura-ml-projects-agentic-shopping-assistant",
    "C--Users-gaura-ml-projects-agentic-travel-booking-system",
    "C--Users-gaura-ml-projects-expense-tracker",
    "C--Users-gaura-ml-projects-gold-rate-tracker",
    "C--Users-gaura-ml-projects-loop",
    "C--Users-gaura-ml-projects-multimodal-fashion-recommender",
    "C--Users-gaura-ml-projects-review-iq",
    "C--Users-gaura-ml-projects-shelfsense-m5",
    "C--Users-gaura-ml-projects-token-efficiency-scorer",
    "C--Users-gaura-ml-projects-triage-iq",
]

_HF_REPO_ARMAND: str = "armand0e/kimi-k2.6-claude-code-traces"
_HF_REPO_CFAHLGREN: str = "cfahlgren1/agent-sessions-list"

# Filter thresholds — sessions below either are trivial/empty.
_MIN_TURN_COUNT: int = 3
_MIN_TOTAL_TOKENS: int = 100


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SourceStats:
    """Per-source adaptation counters."""

    label: str
    attempted: int = 0
    adapted: int = 0
    trivial: int = 0
    failed: int = 0
    format_unknown: int = 0


@dataclass
class AdaptResult:
    """Return value from a single-session adaptation attempt."""

    record: dict[str, Any] | None
    trivial: bool = False
    failed: bool = False
    format_unknown: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# CC-format detection
# ---------------------------------------------------------------------------


def _looks_like_cc_jsonl(lines: list[str]) -> bool:
    """Return True if the JSONL lines look like a Claude Code session transcript.

    A file is considered CC-format if at least one line contains both
    ``isSidechain`` and ``type`` keys, which are unique to CC session JSONL.
    Requires at least one parseable line.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "isSidechain" in obj and "type" in obj:
            return True
    return False


def _derive_session_id(hf_repo: str, filename: str) -> str:
    """Derive a stable session_id from repo + filename for dedup purposes.

    Uses the stem of the filename (UUID portion) when the filename is a UUID,
    otherwise hashes repo + filename to produce a 16-char hex id.
    """
    stem = Path(filename).stem
    # UUID stems are exactly 36 chars with hyphens — use as-is.
    parts = stem.split("-")
    if len(parts) == 5 and all(len(p) in (8, 4, 4, 4, 12) for p in parts):
        return stem
    # Non-UUID filename: derive from hash.
    raw = f"{hf_repo}:{filename}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Single-session adaptation
# ---------------------------------------------------------------------------


def _adapt_from_path(session_path: Path, session_id_override: str | None = None) -> AdaptResult:
    """Attempt to adapt one session file via the CC adapter.

    Args:
        session_path: Path to the JSONL file on disk.
        session_id_override: When set, patch the returned record's session_id
            to this value (used for public sessions whose filename stem is
            not a reliable UUID).

    Returns:
        An AdaptResult with the record populated on success, or failure flags set.
    """
    try:
        record = adapt_session(session_path)
    except Exception as exc:
        return AdaptResult(record=None, failed=True, error=str(exc))

    if session_id_override:
        record["session_id"] = session_id_override
        if "digest" in record and isinstance(record["digest"], dict):
            record["digest"]["session_id"] = session_id_override

    turn_count: int = record.get("turn_count", 0)
    total_tokens: int = record.get("total_tokens", 0)
    if turn_count < _MIN_TURN_COUNT or total_tokens < _MIN_TOTAL_TOKENS:
        return AdaptResult(record=record, trivial=True)

    return AdaptResult(record=record)


def _adapt_from_lines(
    lines: list[str], session_id: str, source_label: str
) -> AdaptResult:
    """Write JSONL lines to a temp file and adapt them via the CC adapter.

    Used for public-dataset sessions that are already in CC JSONL format
    but haven't been written to disk yet.

    Args:
        lines: Raw JSONL lines for one session.
        session_id: ID to assign to this session in the output record.
        source_label: Human-readable label for error messages.

    Returns:
        An AdaptResult.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.writelines(lines)
        tmp_path = Path(tmp.name)

    try:
        return _adapt_from_path(tmp_path, session_id_override=session_id)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Local sessions
# ---------------------------------------------------------------------------


def _collect_local_files() -> list[Path]:
    """Return all *.jsonl files from the known local CC project directories."""
    files: list[Path] = []
    for dir_name in _LOCAL_PROJECT_DIRS:
        project_dir = _LOCAL_PROJECTS_ROOT / dir_name
        if project_dir.is_dir():
            files.extend(sorted(project_dir.glob("*.jsonl")))
    return files


def process_local(
    stats: SourceStats, limit: int | None, seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Adapt all (or up to ``limit``) local CC session files.

    Args:
        stats: SourceStats to update in-place.
        limit: Maximum number of sessions to process (None = no limit).
        seen_ids: Set of already-seen session_ids for deduplication (updated in-place).

    Returns:
        List of adapted records (non-trivial, non-failed, non-duplicate).
    """
    files = _collect_local_files()
    if limit is not None:
        files = files[:limit]

    records: list[dict[str, Any]] = []
    for path in files:
        stats.attempted += 1
        result = _adapt_from_path(path)
        if result.format_unknown:
            stats.format_unknown += 1
        elif result.failed:
            stats.failed += 1
            print(f"  [local] FAILED {path.name}: {result.error}", file=sys.stderr)
        elif result.trivial:
            stats.trivial += 1
        else:
            assert result.record is not None
            sid = result.record["session_id"]
            if sid in seen_ids:
                # Duplicate — count as trivial (already represented).
                stats.trivial += 1
            else:
                seen_ids.add(sid)
                stats.adapted += 1
                records.append(result.record)

    return records


# ---------------------------------------------------------------------------
# Public dataset: armand0e/kimi-k2.6-claude-code-traces
# ---------------------------------------------------------------------------


def _list_armand_files() -> list[str]:
    """Return JSONL filenames from the armand0e HF repo (excluding .cache)."""
    from huggingface_hub import list_repo_files

    all_files = list(list_repo_files(_HF_REPO_ARMAND, repo_type="dataset"))
    return [
        f
        for f in all_files
        if f.endswith(".jsonl") and not f.startswith(".cache")
    ]


def process_armand(
    stats: SourceStats, limit: int | None, seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Fetch and adapt sessions from armand0e/kimi-k2.6-claude-code-traces.

    Each file in this repo is a standalone Claude Code session JSONL.
    They are downloaded one by one (huggingface_hub caches on disk).

    Args:
        stats: SourceStats to update in-place.
        limit: Maximum number of sessions to process.
        seen_ids: Deduplication set (updated in-place).

    Returns:
        List of adapted, non-trivial, non-duplicate records.
    """
    from huggingface_hub import hf_hub_download

    try:
        file_list = _list_armand_files()
    except Exception as exc:
        print(f"  [armand0e] Cannot list repo files: {exc}", file=sys.stderr)
        return []

    if limit is not None:
        file_list = file_list[:limit]

    records: list[dict[str, Any]] = []
    for filename in file_list:
        stats.attempted += 1
        session_id = _derive_session_id(_HF_REPO_ARMAND, filename)

        try:
            local_path = Path(
                hf_hub_download(
                    repo_id=_HF_REPO_ARMAND,
                    filename=filename,
                    repo_type="dataset",
                )
            )
        except Exception as exc:
            stats.failed += 1
            print(f"  [armand0e] DOWNLOAD FAILED {filename}: {exc}", file=sys.stderr)
            continue

        # Verify CC format before adapting.
        try:
            raw_lines = local_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            try:
                raw_lines = local_path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
            except Exception as exc:
                stats.failed += 1
                print(f"  [armand0e] READ FAILED {filename}: {exc}", file=sys.stderr)
                continue

        if not _looks_like_cc_jsonl(raw_lines):
            stats.format_unknown += 1
            print(
                f"  [armand0e] format-unknown (not CC JSONL): {filename}", file=sys.stderr
            )
            continue

        result = _adapt_from_path(local_path, session_id_override=session_id)
        if result.failed:
            stats.failed += 1
            print(f"  [armand0e] ADAPT FAILED {filename}: {result.error}", file=sys.stderr)
        elif result.trivial:
            stats.trivial += 1
        else:
            assert result.record is not None
            sid = result.record["session_id"]
            if sid in seen_ids:
                stats.trivial += 1
            else:
                seen_ids.add(sid)
                stats.adapted += 1
                records.append(result.record)

    return records


# ---------------------------------------------------------------------------
# Public dataset: cfahlgren1/agent-sessions-list
# ---------------------------------------------------------------------------


def _list_cfahlgren_files() -> list[str]:
    """Return all JSONL filenames from the cfahlgren1 HF repo."""
    from huggingface_hub import list_repo_files

    all_files = list(list_repo_files(_HF_REPO_CFAHLGREN, repo_type="dataset"))
    return [f for f in all_files if f.endswith(".jsonl")]


def process_cfahlgren(
    stats: SourceStats, limit: int | None, seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Fetch and adapt CC-compatible sessions from cfahlgren1/agent-sessions-list.

    This repo contains mixed-agent sessions (claude, codex, pi subfolders).
    Only files in CC JSONL format are adapted; others are counted as
    format_unknown without crashing.

    Args:
        stats: SourceStats to update in-place.
        limit: Maximum number of sessions to process.
        seen_ids: Deduplication set (updated in-place).

    Returns:
        List of adapted, non-trivial, non-duplicate records.
    """
    from huggingface_hub import hf_hub_download

    try:
        file_list = _list_cfahlgren_files()
    except Exception as exc:
        print(f"  [cfahlgren1] Cannot list repo files: {exc}", file=sys.stderr)
        return []

    if limit is not None:
        file_list = file_list[:limit]

    records: list[dict[str, Any]] = []
    for filename in file_list:
        stats.attempted += 1
        session_id = _derive_session_id(_HF_REPO_CFAHLGREN, filename)

        try:
            local_path = Path(
                hf_hub_download(
                    repo_id=_HF_REPO_CFAHLGREN,
                    filename=filename,
                    repo_type="dataset",
                )
            )
        except Exception as exc:
            stats.failed += 1
            print(f"  [cfahlgren1] DOWNLOAD FAILED {filename}: {exc}", file=sys.stderr)
            continue

        try:
            raw_lines = local_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            try:
                raw_lines = local_path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
            except Exception as exc:
                stats.failed += 1
                print(f"  [cfahlgren1] READ FAILED {filename}: {exc}", file=sys.stderr)
                continue

        if not _looks_like_cc_jsonl(raw_lines):
            # Non-CC format (codex, pi, etc.) — count and skip gracefully.
            stats.format_unknown += 1
            print(
                f"  [cfahlgren1] format-unknown (not CC JSONL): {filename}", file=sys.stderr
            )
            continue

        result = _adapt_from_path(local_path, session_id_override=session_id)
        if result.failed:
            stats.failed += 1
            print(f"  [cfahlgren1] ADAPT FAILED {filename}: {result.error}", file=sys.stderr)
        elif result.trivial:
            stats.trivial += 1
        else:
            assert result.record is not None
            sid = result.record["session_id"]
            if sid in seen_ids:
                stats.trivial += 1
            else:
                seen_ids.add(sid)
                stats.adapted += 1
                records.append(result.record)

    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_summary(sources: list[SourceStats], pool_size: int) -> None:
    """Print a formatted summary table to stdout.

    Args:
        sources: One SourceStats per source.
        pool_size: Total non-trivial adapted sessions across all sources.
    """
    col_label = 38
    col_num = 9
    header = (
        f"{'Source':<{col_label}}"
        f"{'Attempted':>{col_num}}"
        f"{'Adapted':>{col_num}}"
        f"{'Trivial':>{col_num}}"
        f"{'Failed':>{col_num}}"
    )
    separator = "-" * (col_label + col_num * 4)

    print()
    print(header)
    print(separator)
    for s in sources:
        print(
            f"{s.label:<{col_label}}"
            f"{s.attempted:>{col_num}}"
            f"{s.adapted:>{col_num}}"
            f"{s.trivial:>{col_num}}"
            f"{s.failed:>{col_num}}"
        )
    print(separator)

    totals = SourceStats(label="TOTAL")
    for s in sources:
        totals.attempted += s.attempted
        totals.adapted += s.adapted
        totals.trivial += s.trivial
        totals.failed += s.failed

    print(
        f"{'TOTAL':<{col_label}}"
        f"{totals.attempted:>{col_num}}"
        f"{totals.adapted:>{col_num}}"
        f"{totals.trivial:>{col_num}}"
        f"{totals.failed:>{col_num}}"
    )
    print()
    print(f"Pool size (adapted, non-trivial): {pool_size} sessions")
    print()

    # Report format_unknown counts if any.
    for s in sources:
        if s.format_unknown > 0:
            print(
                f"  Note: {s.label} — {s.format_unknown} file(s) were format-unknown "
                f"(not CC JSONL; skipped without error)"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Pull local + public CC corpora, adapt, and write to pool JSONL."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Adapt sessions and report counts but do not write output file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N sessions per source (for quick testing).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Output JSONL path (default: {_DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--skip-public",
        action="store_true",
        help="Only process local sessions (fallback when network is unavailable).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = _parse_args()
    output_path: Path = args.output
    dry_run: bool = args.dry_run
    limit: int | None = args.limit
    skip_public: bool = args.skip_public

    seen_ids: set[str] = set()
    all_records: list[dict[str, Any]] = []
    sources: list[SourceStats] = []

    # ------------------------------------------------------------------
    # 1. Local sessions
    # ------------------------------------------------------------------
    local_stats = SourceStats(label="local (152 files)")
    print("[pull_corpora] Processing local sessions...", file=sys.stderr)
    local_records = process_local(local_stats, limit=limit, seen_ids=seen_ids)
    all_records.extend(local_records)
    sources.append(local_stats)

    # ------------------------------------------------------------------
    # 2. Public datasets
    # ------------------------------------------------------------------
    if not skip_public:
        print(
            f"[pull_corpora] Fetching {_HF_REPO_ARMAND}...", file=sys.stderr
        )
        armand_stats = SourceStats(label="armand0e/kimi-k2.6 (HF)")
        try:
            armand_records = process_armand(
                armand_stats, limit=limit, seen_ids=seen_ids
            )
            all_records.extend(armand_records)
        except Exception as exc:
            print(
                f"[pull_corpora] armand0e fetch error: {exc}", file=sys.stderr
            )
        sources.append(armand_stats)

        print(
            f"[pull_corpora] Fetching {_HF_REPO_CFAHLGREN}...", file=sys.stderr
        )
        cfahlgren_stats = SourceStats(label="cfahlgren1/agent-sessions (HF)")
        try:
            cfahlgren_records = process_cfahlgren(
                cfahlgren_stats, limit=limit, seen_ids=seen_ids
            )
            all_records.extend(cfahlgren_records)
        except Exception as exc:
            print(
                f"[pull_corpora] cfahlgren1 fetch error: {exc}", file=sys.stderr
            )
        sources.append(cfahlgren_stats)

    # ------------------------------------------------------------------
    # 3. Output
    # ------------------------------------------------------------------
    pool_size = len(all_records)

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            for record in all_records:
                fh.write(json.dumps(record) + "\n")
        print(
            f"[pull_corpora] Wrote {pool_size} records to {output_path}",
            file=sys.stderr,
        )
    else:
        print(
            "[pull_corpora] Dry-run mode: no output file written.",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # 4. Summary table
    # ------------------------------------------------------------------
    _print_summary(sources, pool_size)


if __name__ == "__main__":
    main()
