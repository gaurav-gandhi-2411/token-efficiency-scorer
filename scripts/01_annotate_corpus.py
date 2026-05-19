"""
01_annotate_corpus.py

Annotates all normalized traces with ground-truth waste labels using a
hybrid LLM approach:
  - Bulk (all 200 sessions): claude-haiku-4-5-20251001
  - Verification (random 20% = 40 sessions): claude-sonnet-4-6
  - IAA: Cohen's kappa computed on the 40-session overlap

Per-session, one API call annotates all assistant turns at once.
Traces longer than MAX_TURNS are truncated to the first MAX_TURNS turns
(documented as a limitation in report 03).

Outputs:
  data/validation-corpus/annotations/haiku/  — {session_id}.json per trace
  data/validation-corpus/annotations/sonnet/ — {session_id}.json (40 traces)
  data/validation-corpus/annotations/iaa_report.json — Cohen's kappa

Usage:
    python scripts/01_annotate_corpus.py [--dry-run] [--model haiku|sonnet|both]
    python scripts/01_annotate_corpus.py --dry-run      # show first trace prompt, no API calls
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import sys
import time
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
ANNOT_DIR = REPO_ROOT / "data" / "validation-corpus" / "annotations"
HAIKU_DIR = ANNOT_DIR / "haiku"
SONNET_DIR = ANNOT_DIR / "sonnet"
HAIKU_DIR.mkdir(parents=True, exist_ok=True)
SONNET_DIR.mkdir(parents=True, exist_ok=True)

MAX_TURNS = 60          # cap per session for context budget
SEED = 42
SONNET_FRACTION = 0.20  # 20% verification sample

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


ANNOTATION_SYSTEM = """\
You are an expert evaluator for coding-agent sessions. Your job is to label waste signals in agent trajectories. A "waste" is any turn that consumed tokens without contributing to the final solution.

Return ONLY valid JSON. No explanation outside the JSON object.
"""

ANNOTATION_PROMPT_TEMPLATE = """\
Analyze the following coding-agent session and produce ground-truth labels.

SESSION METADATA
  session_id: {session_id}
  scaffold: {scaffold}
  outcome: {outcome}
  total_turns: {total_turns}
  turns_shown: {turns_shown}

TURN TRANSCRIPT (assistant turns only, with preceding tool results)
{turn_text}

TASK
Produce a JSON object with exactly this structure:
{{
  "session_id": "{session_id}",
  "per_turn_labels": [
    {{
      "turn_index": <int>,
      "is_retry": <bool>,
      "is_retry_reason": "<empty string if false, else 1-sentence reason>",
      "is_backtrack": <bool>,
      "is_backtrack_reason": "<empty string if false, else 1-sentence reason>",
      "tool_result_used": <bool>,
      "tool_result_used_confidence": "<high|medium|low>",
      "redundant_read": <bool>,
      "redundant_read_prior_turn": <turn_index of the earlier duplicate read, or null>,
      "wasted_reasoning": <bool>
    }},
    ...
  ],
  "session_summary": {{
    "waste_categories": <list of zero or more from: ["bad_initial_direction", "premature_commitment", "tool_thrashing", "context_bloat", "verbose_reasoning", "dead_exploration"]>,
    "total_waste_pct_estimate": <integer 0-100, your estimate of % of tokens that were wasted>,
    "annotation_confidence": "<high|medium|low>"
  }}
}}

LABEL DEFINITIONS
is_retry: True if this turn calls the same tool with the same arguments that was called (and failed or produced an error) in a prior turn of this session. A retry is a sign the agent did not learn from the prior error.
is_backtrack: True if this turn explicitly reverses or abandons a prior approach (e.g., "let me try a different approach", undoing edits, abandoning a file path that was partially explored).
tool_result_used: True if the result of a tool call in this turn is visibly used in the assistant's reasoning or the next assistant turn. False if the tool was called but the result appears to have been ignored.
redundant_read: True if this turn reads a file/path that was already read in an earlier turn and has not been modified since. Reading the same file again without an intervening edit is wasteful.
wasted_reasoning: True if this turn contains reasoning that the agent itself contradicts in a later turn (e.g., concludes X is the root cause, then later correctly identifies a different root cause).

Only label assistant turns (role=assistant or role=ai). Return an entry for each assistant turn in the transcript, in order.
"""


def _format_turns_for_prompt(session: dict[str, Any]) -> tuple[str, list[int]]:
    """Format assistant turns (and preceding tool results) for the annotation prompt."""
    turns = session["turns"]
    # Filter to assistant-role turns with some content, cap at MAX_TURNS
    # Include agent turns even if they have only tool calls and minimal text
    assistant_turns = [
        t for t in turns
        if t["role"] in ("assistant", "ai")
        and (len(t["content_text"]) > 10 or len(t["tool_uses"]) > 0)
    ][:MAX_TURNS]
    shown_indices = [t["turn_index"] for t in assistant_turns]

    # Build a turn_index -> turn dict for fast lookup of preceding observations
    all_turns_by_idx = {t["turn_index"]: t for t in turns}

    lines: list[str] = []
    for t in assistant_turns:
        # Show preceding tool/user observation if it exists (SWE-agent uses user role for obs)
        prev_idx = t["turn_index"] - 1
        if prev_idx in all_turns_by_idx:
            prev_t = all_turns_by_idx[prev_idx]
            if prev_t["role"] in ("tool", "user") and len(prev_t["content_text"]) > 20:
                obs_preview = prev_t["content_text"][:300]
                lines.append(f"\n  [OBS t{prev_idx}]: {obs_preview}")

        lines.append(f"\n--- TURN {t['turn_index']} [{t['role'].upper()}] ---")
        content = t["content_text"][:800]
        lines.append(content)
        if t["tool_uses"]:
            for tu in t["tool_uses"][:3]:  # cap tool uses shown
                lines.append(f"  [TOOL: {tu['tool_name']}]")
                if tu.get("tool_result"):
                    result_preview = str(tu["tool_result"])[:200]
                    lines.append(f"  [RESULT: {result_preview}]")
    return "\n".join(lines), shown_indices


def annotate_session(
    client: Any,
    session: dict[str, Any],
    model: str,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    turn_text, shown_indices = _format_turns_for_prompt(session)
    prompt = ANNOTATION_PROMPT_TEMPLATE.format(
        session_id=session["session_id"],
        scaffold=session["scaffold"],
        outcome=session["outcome"]["result"],
        total_turns=session["turn_count"],
        turns_shown=len(shown_indices),
        turn_text=turn_text,
    )

    if dry_run:
        print("=== DRY RUN — First annotation prompt ===")
        print(prompt[:2000])
        print("...")
        return None

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=ANNOTATION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = msg.content[0].text.strip()
            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            annotation = json.loads(raw_text)
            annotation["_model"] = model
            annotation["_shown_turn_indices"] = shown_indices
            annotation["_input_tokens"] = msg.usage.input_tokens
            annotation["_output_tokens"] = msg.usage.output_tokens
            return annotation
        except json.JSONDecodeError as e:
            print(f"  JSON parse error attempt {attempt+1}: {e}")
            if attempt == 2:
                return {"error": "json_parse_failed", "session_id": session["session_id"]}
        except Exception as e:
            print(f"  API error attempt {attempt+1}: {e}")
            if attempt == 2:
                return {"error": str(e), "session_id": session["session_id"]}
            time.sleep(2 ** attempt)

    return None


def compute_iaa(haiku_dir: pathlib.Path, sonnet_dir: pathlib.Path) -> dict[str, Any]:
    """Compute Cohen's kappa between haiku and sonnet labels on the overlap set."""
    haiku_files = {f.stem: f for f in haiku_dir.glob("*.json")}
    sonnet_files = {f.stem: f for f in sonnet_dir.glob("*.json")}
    overlap = set(haiku_files.keys()) & set(sonnet_files.keys())

    if len(overlap) < 5:
        return {"error": f"Not enough overlap ({len(overlap)} sessions)"}

    # Binary labels for kappa computation
    label_fields = ["is_retry", "is_backtrack", "tool_result_used", "redundant_read", "wasted_reasoning"]
    results: dict[str, dict] = {}

    for field in label_fields:
        h_labels: list[int] = []
        s_labels: list[int] = []
        for sid in overlap:
            h_ann = json.loads(haiku_files[sid].read_text(encoding="utf-8"))
            s_ann = json.loads(sonnet_files[sid].read_text(encoding="utf-8"))
            h_turns = {t["turn_index"]: t for t in h_ann.get("per_turn_labels", [])}
            s_turns = {t["turn_index"]: t for t in s_ann.get("per_turn_labels", [])}
            common_turns = set(h_turns.keys()) & set(s_turns.keys())
            for ti in common_turns:
                h_val = int(bool(h_turns[ti].get(field, False)))
                s_val = int(bool(s_turns[ti].get(field, False)))
                h_labels.append(h_val)
                s_labels.append(s_val)

        if not h_labels:
            results[field] = {"kappa": None, "n_turns": 0}
            continue

        # Cohen's kappa
        n = len(h_labels)
        po = sum(h == s for h, s in zip(h_labels, s_labels)) / n
        h_pos = sum(h_labels) / n
        s_pos = sum(s_labels) / n
        pe = h_pos * s_pos + (1 - h_pos) * (1 - s_pos)
        kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0

        results[field] = {
            "kappa": round(kappa, 3),
            "po": round(po, 3),
            "pe": round(pe, 3),
            "n_turns": n,
            "haiku_positive_rate": round(h_pos, 3),
            "sonnet_positive_rate": round(s_pos, 3),
        }

    return {
        "overlap_sessions": len(overlap),
        "per_label": results,
        "overall_kappa": round(
            sum(v["kappa"] for v in results.values() if v.get("kappa") is not None)
            / max(1, sum(1 for v in results.values() if v.get("kappa") is not None)),
            3,
        ),
    }


def main(model_choice: str = "both", dry_run: bool = False) -> None:
    try:
        import anthropic  # type: ignore[import]
    except ImportError:
        print("ERROR: anthropic library not installed", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not dry_run:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key) if not dry_run else None

    sessions = []
    for f in sorted(TRACES_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text(encoding="utf-8")))

    random.seed(SEED)
    random.shuffle(sessions)
    n_sonnet = int(len(sessions) * SONNET_FRACTION)
    sonnet_sessions = sessions[:n_sonnet]

    print(f"Total sessions: {len(sessions)}")
    print(f"Haiku targets: {len(sessions)} | Sonnet targets: {n_sonnet}")

    total_cost_usd = 0.0

    # ---- Haiku bulk annotation ----
    if model_choice in ("haiku", "both"):
        print(f"\nRunning HAIKU annotation ({HAIKU_MODEL})…")
        for i, session in enumerate(sessions):
            sid = session["session_id"]
            out_path = HAIKU_DIR / f"{sid}.json"
            if out_path.exists():
                print(f"  [{i+1}/{len(sessions)}] {sid} — skipped (exists)")
                continue
            print(f"  [{i+1}/{len(sessions)}] {sid} ({session['scaffold']}/{session['outcome']['result']})…", end="", flush=True)
            result = annotate_session(client, session, HAIKU_MODEL, dry_run)
            if dry_run:
                return
            if result:
                out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                in_t = result.get("_input_tokens", 0)
                out_t = result.get("_output_tokens", 0)
                # haiku: $0.80/M input, $4.00/M output
                cost = in_t * 0.80 / 1e6 + out_t * 4.00 / 1e6
                total_cost_usd += cost
                print(f" {in_t}in/{out_t}out ${cost:.4f}")
            else:
                print(" ERROR")

    # ---- Sonnet verification ----
    if model_choice in ("sonnet", "both"):
        print(f"\nRunning SONNET verification ({SONNET_MODEL}) on {n_sonnet} sessions…")
        for i, session in enumerate(sonnet_sessions):
            sid = session["session_id"]
            out_path = SONNET_DIR / f"{sid}.json"
            if out_path.exists():
                print(f"  [{i+1}/{n_sonnet}] {sid} — skipped (exists)")
                continue
            print(f"  [{i+1}/{n_sonnet}] {sid}…", end="", flush=True)
            result = annotate_session(client, session, SONNET_MODEL, dry_run)
            if result:
                out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                in_t = result.get("_input_tokens", 0)
                out_t = result.get("_output_tokens", 0)
                # sonnet: $3.00/M input, $15.00/M output
                cost = in_t * 3.00 / 1e6 + out_t * 15.00 / 1e6
                total_cost_usd += cost
                print(f" {in_t}in/{out_t}out ${cost:.4f}")
            else:
                print(" ERROR")

    # ---- IAA ----
    print("\nComputing inter-annotator agreement…")
    iaa = compute_iaa(HAIKU_DIR, SONNET_DIR)
    iaa_path = ANNOT_DIR / "iaa_report.json"
    iaa_path.write_text(json.dumps(iaa, indent=2), encoding="utf-8")
    print(json.dumps(iaa, indent=2))

    print(f"\nTotal annotation cost: ${total_cost_usd:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", choices=["haiku", "sonnet", "both"], default="both")
    args = parser.parse_args()
    main(model_choice=args.model, dry_run=args.dry_run)
