from __future__ import annotations

"""tes/adapt.py — Claude Code session JSONL adapter.

Self-contained implementation (no scripts/ or src/ import) so the installed wheel
works without repo access. This is a direct port of the frozen adapter logic from
scripts/adapters/claudecode_adapter.py.

Public API:
    adapt_session(session_path: Path) -> dict
"""

import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

from tes._digest import SessionDigest, TurnDigest
from tes.discovery import SUBAGENTS_DIRNAME, is_subagent_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800

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
    """Replace any secret-looking values in text with ``<SECRET_REDACTED>``."""
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
# JSONL parsing helpers
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


def _extract_edit_operations_from_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """XX2.2: pull Edit/Write/MultiEdit/NotebookEdit operations out of this
    assistant message's tool_use blocks -- the ONE place in the adapter that
    reads tool_use.input, since TurnDigest deliberately never carries it
    (compact-representation design, see _digest.py). Computed in the SAME
    single pass over the source JSONL that builds the rest of the record,
    per RR1's lesson: this is the only opportunity to read it before the
    file may later disappear. Returns plain dicts (not EditOperation
    instances) so this module stays free of a tes.impact import cycle risk;
    tes.impact.extract_edit_operations does the actual parsing.
    """
    from tes.impact import extract_edit_operations

    ops: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        tool_input = block.get("input")
        if not name or not isinstance(tool_input, dict):
            continue
        for op in extract_edit_operations(str(name), tool_input):
            ops.append(
                {
                    "path": op.path,
                    "additions": op.additions,
                    "deletions": op.deletions,
                    "tool": op.tool,
                    "prior_content_unknown": op.prior_content_unknown,
                    "untested_tool_shape": op.untested_tool_shape,
                }
            )
    return ops


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


def _parse_server_tool_use(usage: dict[str, Any]) -> dict[str, int] | None:
    """Extract ``usage.server_tool_use`` counts (e.g. ``{"web_search_requests": 2}``)
    from a raw Claude API usage dict, when present and non-empty.

    Added 0.10.2 (S1 fix): before this, these counts were silently dropped —
    ``_parse_usage`` above reads only 4 token-count fields, and nothing in
    this module looked at ``server_tool_use`` at all, so a session with
    server-side tool calls (e.g. web search, billed at $10/1,000 searches on
    top of token costs) was priced as if those calls never happened, with
    zero warning. This function only detects and surfaces the counts —
    tes.cost uses them to emit an explicit warning (never to compute a
    price; see tes/cost.py's _server_tool_warning docstring for why not).

    Returns None when the field is absent, not a dict, or every count in it
    is zero/falsy -- distinguishing "no server-side tool usage" from "usage
    present but nothing billable" isn't needed downstream, so both collapse
    to None (nothing to warn about).
    """
    raw = usage.get("server_tool_use")
    if not isinstance(raw, dict):
        return None
    counts = {str(k): int(v) for k, v in raw.items() if isinstance(v, int | float) and v}
    return counts or None


def _message_dedup_key(msg: dict[str, Any]) -> str:
    """Key identifying the API response an assistant record belongs to.

    Claude Code writes one assistant record per content block (thinking / text /
    tool_use), each repeating the response's ``usage``; streaming partials of one response
    can also differ in ``output_tokens``. Records of one response share ``message.id``.
    Falls back to ``requestId`` then ``uuid`` (unique, so never merged) when absent.
    """
    message = msg.get("message")
    mid = message.get("id") if isinstance(message, dict) else None
    return str(mid or msg.get("requestId") or msg.get("uuid") or id(msg))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping blank/malformed lines (a live file may end mid-write)."""
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def collect_subagent_usage(session_path: Path) -> dict[str, Any] | None:
    """Roll up the usage of a session's subagent transcripts, or None if it has none.

    Layout (verified on real Claude Code data): the parent ``<project>/<sid>.jsonl`` has a
    sibling directory ``<project>/<sid>/subagents/agent-<agentId>.jsonl``; the parent's own
    transcript records the launch (``toolUseResult.agentId``) but NOT the subagent's token
    usage, so adding the subagent files to the parent does not double count.

    Every record in a subagent file has ``isSidechain: true``, which is why the main-chain
    adapter reads them as empty. Here each API response is counted once (records sharing a
    ``message.id`` are merged by taking the per-field maximum, see ``_message_dedup_key``).

    Returns ``{"file_count", "message_count", "real_tokens", "billed_tokens", "turns"}`` where
    ``turns`` are one TurnDigest-shaped dict per (subagent file, model) -- cost and
    attribution are linear in tokens, so aggregating per model loses nothing.
    """
    if is_subagent_path(session_path):
        return None
    sub_dir = session_path.with_suffix("") / SUBAGENTS_DIRNAME
    if not sub_dir.is_dir():
        return None

    turns: list[dict[str, Any]] = []
    file_count = 0
    message_count = 0
    for agent_file in sorted(sub_dir.glob("agent-*.jsonl")):
        try:
            records = _read_jsonl(agent_file)
        except OSError:
            continue
        # message key -> (model, [input, cache_creation, cache_read, output], server_tool_use)
        per_message: dict[str, tuple[str, list[int], dict[str, int]]] = {}
        for rec in records:
            if rec.get("type") != "assistant":
                continue
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            counts = list(_parse_usage(usage))
            stu = _parse_server_tool_use(usage) or {}
            key = _message_dedup_key(rec)
            if key in per_message:
                model, prev, prev_stu = per_message[key]
                counts = [max(a, b) for a, b in zip(prev, counts, strict=True)]
                stu = {k: max(prev_stu.get(k, 0), stu.get(k, 0)) for k in {*prev_stu, *stu}}
                per_message[key] = (model or str(message.get("model", "")), counts, stu)
            else:
                per_message[key] = (str(message.get("model", "")), counts, stu)

        by_model: dict[str, list[int]] = {}
        stu_by_model: dict[str, dict[str, int]] = {}
        requests_by_model: dict[str, int] = {}
        for model, counts, stu in per_message.values():
            if not any(counts):
                continue  # zero-usage records (e.g. synthetic client-side messages)
            acc = by_model.setdefault(model, [0, 0, 0, 0])
            for i, c in enumerate(counts):
                acc[i] += c
            message_count += 1
            requests_by_model[model] = requests_by_model.get(model, 0) + 1
            tot = stu_by_model.setdefault(model, {})
            for k, v in stu.items():
                tot[k] = tot.get(k, 0) + v
        if not by_model:
            continue
        file_count += 1
        for model, (inp, cache_cr, cache_rd, out) in sorted(by_model.items()):
            turns.append(
                {
                    "role": "ai",
                    "tool_names": [],
                    "content_snippet": "",
                    "token_count_input": inp + cache_cr + cache_rd,
                    "token_count_output": out,
                    "cache_read": cache_rd,
                    "h2_duplicate": False,
                    "cache_creation": cache_cr,
                    "model": model,
                    "server_tool_use": stu_by_model.get(model) or None,
                    "request_count": requests_by_model[model],
                }
            )

    if not turns:
        return None
    billed = sum(t["token_count_input"] + t["token_count_output"] for t in turns)
    real = sum(t["token_count_input"] - t["cache_read"] + t["token_count_output"] for t in turns)
    return {
        "file_count": file_count,
        "message_count": message_count,
        "real_tokens": real,
        "billed_tokens": billed,
        "turns": turns,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def adapt_session(session_path: Path) -> dict[str, Any]:
    """Convert a single Claude Code session JSONL file to a layer1_outputs record.

    This is a direct port of scripts/adapters/claudecode_adapter.adapt_session.
    Secret redaction is ON by default.

    Parameters
    ----------
    session_path:
        Path to the Claude Code session JSONL file.

    Returns
    -------
    dict
        A record matching the layer1_outputs.jsonl schema, with a populated
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
            print(
                f"[adapter] WARNING: skipping malformed JSON line in {session_path.name}",
                file=sys.stderr,
            )

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

    # XX2.2: edit operations, accumulated across the whole session -- a
    # sibling to `digest`, not a TurnDigest field (see
    # _extract_edit_operations_from_content's docstring for why).
    edit_operations: list[dict[str, Any]] = []

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
            server_tool_use = _parse_server_tool_use(usage)
            model_str: str = message.get("model", "")

            sum_input += inp
            sum_cache_creation += cache_cr
            sum_cache_read += cache_rd
            sum_output += out

            tool_names: list[str] = _extract_tool_names_from_content(content)
            snippet: str = _redact_secrets(_extract_text_snippet(content))
            edit_operations.extend(_extract_edit_operations_from_content(content))

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
                    cache_creation=cache_cr,
                    model=model_str,
                    server_tool_use=server_tool_use,
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
                if not stripped:
                    continue
                # Skip system-reminder injections at the very start before any AI turn
                if not task_description_set and stripped.startswith("<system-reminder>"):
                    continue

                snippet = _redact_secrets(stripped[:_SNIPPET_MAX_CHARS])

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
                has_tool_result: bool = any(
                    isinstance(item, dict) and item.get("type") == "tool_result" for item in content
                )
                if not has_tool_result:
                    continue

                snippet = _redact_secrets(_extract_tool_result_snippet(content))
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

    # Subagent spend: kept OUT of `turns` (waste detectors, judge text and the verdict's
    # real_tokens stay main-chain only) and carried on the digest for cost/attribution.
    subagent = collect_subagent_usage(session_path)
    subagent_turns: list[TurnDigest] = []
    if subagent is not None:
        for i, t in enumerate(subagent["turns"]):
            subagent_turns.append(TurnDigest(turn_index=turn_count + i, **t))

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
        subagent_turns=subagent_turns,
        subagent_count=subagent["file_count"] if subagent else 0,
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
        "edit_operations": edit_operations,
        "subagent_usage": (
            {k: v for k, v in subagent.items() if k != "turns"}
            if subagent
            else {"file_count": 0, "message_count": 0, "real_tokens": 0, "billed_tokens": 0}
        ),
    }


__all__ = ["adapt_session", "collect_subagent_usage"]
