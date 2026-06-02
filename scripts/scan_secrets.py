from __future__ import annotations

"""scan_secrets.py — Pre-commit gate: scan pool JSONL for unmasked secrets.

Scans ``content_snippet`` and ``task_description`` fields in a pool_adapted.jsonl
(or any compatible JSONL) file for live credentials.  Prints a report and exits
with code 1 if any unmasked secrets are found, making it safe to use as a
pre-commit hook gate.

Usage::

    python scripts/scan_secrets.py
    python scripts/scan_secrets.py --path data/corpus_pool/pool_adapted.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Patterns — mirrors _SECRET_PATTERNS in claudecode_adapter.py
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Provider API keys
    (r"gsk_[A-Za-z0-9]{20,}", "groq_key"),
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "anthropic_key"),
    (r"sk-or-[A-Za-z0-9\-_]{20,}", "openrouter_key"),
    (r"sk-[A-Za-z0-9]{40,}", "openai_style_key"),
    (r"ghp_[A-Za-z0-9]{20,}", "github_pat"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "github_fine_grained_pat"),
    (r"ghs_[A-Za-z0-9]{20,}", "github_actions_token"),
    (r"hf_[A-Za-z0-9]{20,}", "huggingface_token"),
    (r"AIzaSy[A-Za-z0-9\-_]{30,}", "google_api_key"),
    (r"AKIA[A-Z0-9]{16}", "aws_access_key_id"),
    (r"wandb_v1_[A-Za-z0-9_]{20,}", "wandb_api_key"),
    (r"xoxb-[A-Za-z0-9\-]{20,}", "slack_bot_token"),
    (r"xoxp-[A-Za-z0-9\-]{20,}", "slack_user_token"),
    (r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}", "slack_webhook"),
    # Generic assignments: KEY=<long-random-value>
    # Excludes: placeholders, code attribute-access values (settings.key, self.key),
    # and visual separators (strings of = chars).
    (
        r"(?i)(?:API_KEY|SECRET_KEY|PRIVATE_KEY|ACCESS_TOKEN|AUTH_TOKEN|DB_PASSWORD|ANON_KEY|SERVICE_ROLE_KEY)"
        r"\s*=\s*(?!NOT_SET|your_|<|REDACTED|\*{3}|\.{3}|none|null|placeholder|change.me|example"
        r"|=+|[A-Za-z_][A-Za-z0-9_]*\.)"
        r"[A-Za-z0-9\-_+=/.@#$%]{16,}",
        "generic_key_assignment",
    ),
    # Database URLs with embedded credentials
    (r"(?:postgresql|mysql|mongodb)://[^:]+:[^@\s'\"\\]{6,}@[^\s'\"\\]+", "database_url"),
    # JWT session tokens (only in value position after "token":)
    (r'"token"\s*:\s*"eyJ[A-Za-z0-9+/=._\-]{20,}"', "jwt_session_token"),
    # Private key blocks
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private_key_header"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in _SECRET_PATTERNS
]

_DEFAULT_PATH = Path("data/corpus_pool/pool_adapted.jsonl")

# Substrings that indicate an already-masked or placeholder value — not a live secret.
_SAFE_SUBSTRINGS: tuple[str, ...] = (
    "<SECRET_REDACTED>",
    "_REDACTED",
    "<REDACTED>",
    "REDACTED",
    "NOT_SET",
    "your_key",
    "placeholder",
    "***",
    "...",
    "none",
    "null",
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Hit(NamedTuple):
    """A single secret match found during scanning."""

    line_num: int        # 1-based line number in the JSONL file
    session_id: str
    pattern_type: str
    match_display: str   # first-8[...]last-4 safe display string


def _make_display(value: str) -> str:
    """Return a display-safe truncation: first 8 chars + [...] + last 4 chars."""
    if len(value) <= 12:
        return value[:4] + "[...]"
    return value[:8] + "[...]" + value[-4:]


def _is_safe(value: str) -> bool:
    """Return True if *value* looks like a placeholder, not a live secret."""
    lower = value.lower()
    for sub in _SAFE_SUBSTRINGS:
        if sub.lower() in lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_text(text: str, line_num: int, session_id: str) -> list[Hit]:
    """Scan *text* for unmasked secrets.  Return a list of Hit objects."""
    hits: list[Hit] = []
    for compiled, label in _COMPILED:
        for m in compiled.finditer(text):
            matched = m.group(0)
            if _is_safe(matched):
                continue
            hits.append(
                Hit(
                    line_num=line_num,
                    session_id=session_id,
                    pattern_type=label,
                    match_display=_make_display(matched),
                )
            )
    return hits


def scan_file(path: Path) -> tuple[list[Hit], int]:
    """Scan all records in a pool_adapted.jsonl file.

    Args:
        path: Path to a JSONL file whose records contain ``digest.task_description``
              and ``digest.turns[*].content_snippet`` fields.

    Returns:
        A tuple of ``(hits, total_records_scanned)``.
    """
    hits: list[Hit] = []
    total = 0
    with path.open(encoding="utf-8") as fh:
        for line_num, raw_line in enumerate(fh, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            total += 1
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(f"WARN: JSON decode error on line {line_num}: {exc}", file=sys.stderr)
                continue

            sid: str = str(rec.get("session_id", "unknown"))
            digest = rec.get("digest", {})
            if not isinstance(digest, dict):
                continue

            task_description: str = str(digest.get("task_description", ""))
            if task_description:
                hits.extend(_scan_text(task_description, line_num, sid))

            for turn in digest.get("turns", []):
                snippet: str = str(turn.get("content_snippet", ""))
                if snippet:
                    hits.extend(_scan_text(snippet, line_num, sid))

    return hits, total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Scan pool JSONL for unmasked secrets. Exit 1 if any found."
    )
    parser.add_argument(
        "--path",
        metavar="FILE_OR_DIR",
        default=str(_DEFAULT_PATH),
        help=f"File or directory to scan (default: {_DEFAULT_PATH}).",
    )
    return parser.parse_args()


def _collect_paths(target: Path) -> list[Path]:
    """Expand *target* to a flat list of JSONL files to scan."""
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.glob("**/*.jsonl"))
    # Glob pattern — let caller handle missing
    return [target]


def main() -> None:
    """Entry point.  Exits with code 1 if unmasked secrets are found."""
    args = _parse_args()
    target = Path(args.path)

    if not target.exists():
        print(f"ERROR: path does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    paths = _collect_paths(target)
    if not paths:
        print(f"ERROR: no JSONL files found under {target}", file=sys.stderr)
        sys.exit(1)

    all_hits: list[Hit] = []
    total_records = 0

    for p in paths:
        hits, n = scan_file(p)
        all_hits.extend(hits)
        total_records += n

    if not all_hits:
        print(f"Scan complete: 0 unmasked secrets found. ({total_records} records scanned)")
        sys.exit(0)

    # Print report before failing
    print(f"FAIL: {len(all_hits)} unmasked secret(s) found in {total_records} records.\n")
    for h in all_hits:
        sid_short = h.session_id[:8] if len(h.session_id) >= 8 else h.session_id
        print(
            f"LINE {h.line_num:<5}  SID={sid_short}  "
            f"TYPE={h.pattern_type:<30}  MATCH={h.match_display}"
        )
    sys.exit(1)


if __name__ == "__main__":
    main()
