from __future__ import annotations

"""claudecode_adapter.py — Convert Claude Code session JSONL transcripts to layer1_outputs format.

Reads raw Claude Code session JSONL files (from ~/.claude/projects/<project>/<uuid>.jsonl)
and emits records in the same schema that layer2_judge.py consumes from layer1_outputs.jsonl.
"""

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.trace_digest import SessionDigest, TurnDigest  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800
_DEFAULT_OUTPUT: Path = ROOT / "data" / "cc_session_digests.jsonl"

# ---------------------------------------------------------------------------
# Secret redaction
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

# Compile once at module load time for efficiency.
_COMPILED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in _SECRET_PATTERNS
]

# Module-level counter so callers can observe how many redactions occurred.
redaction_count: int = 0


def _redact_secrets(text: str) -> str:
    """Replace any secret-looking values in *text* with ``<SECRET_REDACTED>``.

    Uses the module-level ``_COMPILED_PATTERNS`` list. Increments the module-level
    ``redaction_count`` for each substitution made. Logs a warning to stderr when
    at least one redaction occurs so operators can see that live secrets were present.

    Args:
        text: Raw text that may contain credentials, API keys, or other secrets.

    Returns:
        The sanitised text with all matched patterns replaced by ``<SECRET_REDACTED>``.
    """
    global redaction_count
    result = text
    for compiled, label in _COMPILED_PATTERNS:
        new_result, n = compiled.subn("<SECRET_REDACTED>", result)
        if n > 0:
            redaction_count += n
            print(
                f"[adapter] WARNING: redacted {n} occurrence(s) of pattern '{label}'",
                file=sys.stderr,
            )
            result = new_result
    return result


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------


def _is_main_chain(msg: dict[str, Any]) -> bool:
    """Return True if the message belongs to the main (non-sidechain) conversation."""
    return not msg.get("isSidechain", False)


def _extract_tool_names_from_content(content: list[dict[str, Any]]) -> list[str]:
    """Return the names of all tool_use blocks in an assistant content list."""
    return [
        str(block["name"])
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name")
    ]


def _extract_text_snippet(content: list[dict[str, Any]]) -> str:
    """Concatenate all text blocks (skipping thinking) and truncate to snippet max."""
    parts: list[str] = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)[:_SNIPPET_MAX_CHARS]


def _extract_tool_result_snippet(content: list[dict[str, Any]]) -> str:
    """Concatenate content fields from all tool_result blocks and truncate."""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        sub = block.get("content", "")
        if isinstance(sub, str):
            parts.append(sub)
        elif isinstance(sub, list):
            # tool_result.content can itself be a list of content blocks
            for item in sub:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
    return "".join(parts)[:_SNIPPET_MAX_CHARS]


def _parse_usage(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return (input_tokens, cache_creation, cache_read, output_tokens) from a usage dict."""
    return (
        int(usage.get("input_tokens", 0)),
        int(usage.get("cache_creation_input_tokens", 0)),
        int(usage.get("cache_read_input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
    )


def adapt_session(session_path: Path) -> dict[str, Any]:
    """Convert a single Claude Code session JSONL file to a layer1_outputs record.

    Args:
        session_path: Path to the Claude Code session JSONL file.

    Returns:
        A dict matching the layer1_outputs.jsonl record schema, with a populated
        ``digest`` field (SessionDigest serialised via dataclasses.asdict).
    """
    session_id: str = session_path.stem

    raw_lines: list[str] = session_path.read_text(encoding="utf-8").splitlines()
    messages: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"[adapter] WARNING: skipping malformed JSON line in {session_path.name}",
                  file=sys.stderr)

    # --- Session-level accumulation ------------------------------------------
    turns: list[TurnDigest] = []
    turn_index: int = 0
    task_description: str = "N/A"
    task_description_set: bool = False

    # Running sums over assistant messages for session-level stats
    sum_input: int = 0
    sum_cache_creation: int = 0
    sum_cache_read: int = 0
    sum_output: int = 0

    for msg in messages:
        if not _is_main_chain(msg):
            continue

        msg_type: str = msg.get("type", "")

        if msg_type == "assistant":
            message: dict[str, Any] = msg.get("message", {})
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            usage: dict[str, Any] = message.get("usage", {})
            inp, cache_cr, cache_rd, out = _parse_usage(usage)

            # Accumulate session totals from all assistant messages
            sum_input += inp
            sum_cache_creation += cache_cr
            sum_cache_read += cache_rd
            sum_output += out

            tool_names: list[str] = _extract_tool_names_from_content(content)
            snippet: str = _redact_secrets(_extract_text_snippet(content))

            # input_tokens in TurnDigest = all tokens billed for this call
            turn_input: int = inp + cache_cr + cache_rd
            turn_output: int = out

            turns.append(
                TurnDigest(
                    turn_index=turn_index,
                    role="ai",
                    tool_names=tool_names,
                    content_snippet=snippet,
                    token_count_input=turn_input,
                    token_count_output=turn_output,
                    cache_read=cache_rd,
                    h2_duplicate=False,
                )
            )
            turn_index += 1

        elif msg_type == "user":
            message = msg.get("message", {})
            if message.get("role") != "user":
                continue

            content = message.get("content", "")

            if isinstance(content, str):
                stripped: str = content.strip()
                # Skip entirely empty or whitespace-only string turns
                if not stripped:
                    continue
                # Skip system-reminder injections at the very start before any AI turn
                if not task_description_set and stripped.startswith("<system-reminder>"):
                    continue

                snippet = _redact_secrets(stripped[:_SNIPPET_MAX_CHARS])

                # Capture the first real human-text turn as the task description
                if not task_description_set:
                    task_description = _redact_secrets(stripped[:_TASK_DESC_MAX_CHARS])
                    task_description_set = True

                turns.append(
                    TurnDigest(
                        turn_index=turn_index,
                        role="user",
                        tool_names=[],
                        content_snippet=snippet,
                        token_count_input=0,
                        token_count_output=0,
                        cache_read=0,
                        h2_duplicate=False,
                    )
                )
                turn_index += 1

            elif isinstance(content, list):
                # Check if it contains tool_result items
                has_tool_result: bool = any(
                    isinstance(item, dict) and item.get("type") == "tool_result"
                    for item in content
                )
                if not has_tool_result:
                    # No meaningful content — skip
                    continue

                snippet = _redact_secrets(_extract_tool_result_snippet(content))
                # Skip if the resulting snippet is blank
                if not snippet.strip():
                    continue

                turns.append(
                    TurnDigest(
                        turn_index=turn_index,
                        role="tool",
                        tool_names=[],
                        content_snippet=snippet,
                        token_count_input=0,
                        token_count_output=0,
                        cache_read=0,
                        h2_duplicate=False,
                    )
                )
                turn_index += 1

    # --- Session-level derived metrics ---------------------------------------
    total_billed: int = sum_input + sum_cache_creation + sum_cache_read
    total_tokens: int = total_billed + sum_output
    cache_hit_rate: float = sum_cache_read / max(1, total_billed)
    turn_count: int = len(turns)

    digest = SessionDigest(
        session_id=session_id,
        domain="unknown",
        resolved=False,
        total_tokens=total_tokens,
        turn_count=turn_count,
        h2_duplicate_count=0,
        cache_hit_rate=cache_hit_rate,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description=task_description,
        turns=turns,
    )

    return {
        "session_id": session_id,
        "domain_id": "unknown",
        "test_outcome": False,
        "total_tokens": total_tokens,
        "turn_count": turn_count,
        "h2_duplicate_count": 0,
        "cache_hit_rate": cache_hit_rate,
        "p25_token_ratio": 1.0,
        "labeler_model": "not_applicable",
        "scaffold": "claude_code",
        "output_tokens_available": True,
        "digest": dataclasses.asdict(digest),
        "token_economy_available": False,
        "domain_inferred": "fallback_unknown",
    }


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------


def _load_existing_ids(output_path: Path) -> set[str]:
    """Return the set of session_ids already present in the output file."""
    if not output_path.exists():
        return set()
    existing: set[str] = set()
    for line in output_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            existing.add(json.loads(line)["session_id"])
        except (json.JSONDecodeError, KeyError):
            pass
    return existing


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Convert Claude Code session JSONL transcripts to layer1_outputs format."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--session-path",
        metavar="PATH",
        help="Single session JSONL file to adapt.",
    )
    group.add_argument(
        "--project-dir",
        metavar="DIR",
        help="Directory containing session JSONL files (all *.jsonl files).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output JSONL file (default: {_DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output; otherwise append (skip already-present session_ids).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = _parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.session_path:
        session_files: list[Path] = [Path(args.session_path)]
    else:
        project_dir = Path(args.project_dir)
        if not project_dir.is_dir():
            print(f"ERROR: {project_dir} is not a directory.", file=sys.stderr)
            sys.exit(1)
        session_files = sorted(project_dir.glob("*.jsonl"))
        if not session_files:
            print(f"WARNING: no *.jsonl files found in {project_dir}", file=sys.stderr)
            return

    existing_ids: set[str] = set() if args.overwrite else _load_existing_ids(output_path)

    written: int = 0
    skipped: int = 0

    with output_path.open("a", encoding="utf-8") as fh:
        for sf in session_files:
            if not sf.exists():
                print(f"ERROR: {sf} does not exist.", file=sys.stderr)
                sys.exit(1)
            session_id = sf.stem
            if session_id in existing_ids:
                print(f"[adapter] SKIP {session_id} (already present)", file=sys.stderr)
                skipped += 1
                continue
            print(f"[adapter] processing {sf.name} ...", file=sys.stderr)
            record = adapt_session(sf)
            fh.write(json.dumps(record) + "\n")
            written += 1
            n_turns = record["turn_count"]
            total_tok = record["total_tokens"]
            cache_pct = record["cache_hit_rate"] * 100
            print(
                f"[adapter]   -> turns={n_turns}, tokens={total_tok}, cache_hit={cache_pct:.1f}%",
                file=sys.stderr,
            )

    print(
        f"[adapter] done: {written} written, {skipped} skipped -> {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
