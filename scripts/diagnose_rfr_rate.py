from __future__ import annotations

"""diagnose_rfr_rate.py — Diagnose why RFR fires 4.7x less on SWE-chat CC vs pool.

Three hypotheses:
  H1: Pool has genuinely more retry-waste (harder/longer tasks, GPU/infra work).
      Evidence: pool sessions significantly longer OR more Bash errors per session.
  H2: SWE-chat sessions are shorter/simpler (task-mix artifact, not comparable).
      Evidence: SWE-chat sessions are much shorter, fewer Bash calls per session.
  H3: Format/adapter differences suppress fires on SWE-chat (version-fragility).
      Evidence: SWE-chat has similar Bash error rate but near-miss pairs where
      snippets differ slightly (not exact match) despite same logical error.

Diagnostics:
  1. Session-length distribution (turn count): pool vs SWE-chat CC
  2. Bash call rate + error rate: pool vs SWE-chat CC
  3. Near-miss RFR analysis: consecutive same-type Bash errors that DIDN'T fire
     because snippets weren't exactly equal — quantifies suppression effect
"""

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import _is_error_result, _is_transient, _is_write_call

POOL_DIGESTS = ROOT / "data" / "layer1_outputs.jsonl"
POOL_SIGNALS = ROOT / "data" / "pool_waste_signals.jsonl"
SWECHAT_CC_ADAPTED = ROOT / "data" / "swechat_cc_adapted.jsonl"
SWECHAT_CC_SIGNALS = ROOT / "data" / "public_waste_signals.jsonl"

# Pool data uses 'assistant' role (old adapter); SWE-chat CC uses 'ai'.
# We treat both as equivalent agent turns for diagnostic purposes.
_AGENT_ROLES: frozenset[str] = frozenset({"ai", "assistant"})
_SHELL_TOOLS: frozenset[str] = frozenset({"Bash", "PowerShell"})
_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _next_tool_pos(turns: list[dict], from_pos: int) -> int | None:
    for k in range(from_pos + 1, len(turns)):
        role = turns[k].get("role")
        if role == "tool":
            return k
        if role == "user":
            return None
    return None


def _is_shell_call_compat(turn: dict) -> bool:
    """Role-agnostic shell call check: handles both 'ai' and 'assistant' roles."""
    return turn.get("role") in _AGENT_ROLES and bool(
        set(turn.get("tool_names", [])) & _SHELL_TOOLS
    )


def _is_write_call_compat(turn: dict) -> bool:
    return turn.get("role") in _AGENT_ROLES and bool(
        set(turn.get("tool_names", [])) & _WRITE_TOOLS
    )


def session_stats(records: list[dict]) -> dict:
    """Compute turn counts, Bash call rates, and error rates per session."""
    turn_counts: list[int] = []
    bash_calls_per_session: list[int] = []
    bash_errors_per_session: list[int] = []
    error_rate_when_bash: list[float] = []

    for row in records:
        turns = row.get("digest", {}).get("turns", row.get("turns", []))
        n = len(turns)
        turn_counts.append(n)

        bash_count = 0
        error_count = 0

        for i, t in enumerate(turns):
            if _is_shell_call_compat(t):
                bash_count += 1
                rp = _next_tool_pos(turns, i)
                if rp is not None:
                    snip = turns[rp].get("content_snippet", "")
                    if _is_error_result(snip) and not _is_transient(snip):
                        error_count += 1

        bash_calls_per_session.append(bash_count)
        bash_errors_per_session.append(error_count)
        if bash_count > 0:
            error_rate_when_bash.append(error_count / bash_count)

    def _pct(lst: list[float | int], p: float) -> float:
        if not lst:
            return 0.0
        s = sorted(lst)
        idx = int(len(s) * p)
        return float(s[min(idx, len(s) - 1)])

    def _mean(lst: list[float | int]) -> float:
        return float(statistics.mean(lst)) if lst else 0.0

    return {
        "n_sessions": len(records),
        "turn_count_median": _pct(turn_counts, 0.5),
        "turn_count_p25": _pct(turn_counts, 0.25),
        "turn_count_p75": _pct(turn_counts, 0.75),
        "turn_count_mean": _mean(turn_counts),
        "turn_count_max": max(turn_counts) if turn_counts else 0,
        "sessions_with_bash": sum(1 for c in bash_calls_per_session if c > 0),
        "bash_calls_mean": _mean(bash_calls_per_session),
        "bash_errors_mean": _mean(bash_errors_per_session),
        "sessions_with_bash_errors": sum(1 for c in bash_errors_per_session if c > 0),
        "error_rate_per_bash_call_mean": _mean(error_rate_when_bash),
    }


# ---------------------------------------------------------------------------
# Near-miss RFR analysis (Hypothesis 3)
# ---------------------------------------------------------------------------

def _error_prefix(snip: str, n: int = 60) -> str:
    return snip[:n].lower()


def _same_exit_code(s1: str, s2: str) -> bool:
    m1 = re.search(r"exit code (\d+)", s1, re.IGNORECASE)
    m2 = re.search(r"exit code (\d+)", s2, re.IGNORECASE)
    return bool(m1 and m2 and m1.group(1) == m2.group(1))


def find_near_miss_rfr(records: list[dict]) -> dict:
    """Find consecutive Bash error pairs: exact matches (fires), near-misses (didn't fire).

    Near-miss severity split:
      - 'same_prefix': same first-60-char error prefix → likely same error, format diffed → H3 evidence
      - 'same_exit_code': same exit code but different body → possible same tool, different output
      - 'different_error': genuinely different errors → H3 NOT applicable

    Returns counts + samples for manual inspection.
    """
    near_miss_sessions = 0
    exact_match_sessions = 0
    near_miss_same_prefix = 0
    near_miss_same_exit_code = 0
    near_miss_different_error = 0
    samples_same_prefix: list[dict] = []
    samples_different: list[dict] = []

    for row in records:
        turns = row.get("digest", {}).get("turns", row.get("turns", []))
        session_id = row.get("session_id", "?")
        n = len(turns)
        has_near_miss = False
        has_exact = False

        i = 0
        while i < n:
            if not _is_shell_call_compat(turns[i]):
                i += 1
                continue
            rp = _next_tool_pos(turns, i)
            if rp is None:
                i += 1
                continue
            snip1 = turns[rp].get("content_snippet", "")
            if not _is_error_result(snip1) or _is_transient(snip1):
                i = rp + 1
                continue

            k = rp + 1
            while k < n:
                t2 = turns[k]
                role2 = t2.get("role")
                if role2 == "user":
                    break
                if role2 in _AGENT_ROLES:
                    if _is_write_call_compat(t2):
                        break
                    if _is_shell_call_compat(t2):
                        rp2 = _next_tool_pos(turns, k)
                        if rp2 is not None:
                            snip2 = turns[rp2].get("content_snippet", "")
                            if _is_error_result(snip2) and not _is_transient(snip2):
                                if snip1 == snip2:
                                    has_exact = True
                                else:
                                    has_near_miss = True
                                    pfx = _error_prefix(snip1) == _error_prefix(snip2)
                                    same_exit = _same_exit_code(snip1, snip2)
                                    if pfx:
                                        near_miss_same_prefix += 1
                                        if len(samples_same_prefix) < 5:
                                            samples_same_prefix.append({
                                                "session_id": session_id[:12],
                                                "snip1": snip1[:150],
                                                "snip2": snip2[:150],
                                            })
                                    elif same_exit:
                                        near_miss_same_exit_code += 1
                                    else:
                                        near_miss_different_error += 1
                                        if len(samples_different) < 3:
                                            samples_different.append({
                                                "session_id": session_id[:12],
                                                "snip1": snip1[:100],
                                                "snip2": snip2[:100],
                                            })
                        break
                    k += 1
                    continue
                k += 1

            if has_near_miss:
                near_miss_sessions += 1
                has_near_miss = False
            if has_exact:
                exact_match_sessions += 1
                has_exact = False
            i = rp + 1

    return {
        "sessions_with_near_miss": near_miss_sessions,
        "sessions_with_exact_match": exact_match_sessions,
        "near_miss_same_prefix": near_miss_same_prefix,
        "near_miss_same_exit_code": near_miss_same_exit_code,
        "near_miss_different_error": near_miss_different_error,
        "samples_same_prefix": samples_same_prefix,
        "samples_different": samples_different,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading pool digests...", end="", flush=True)
    pool_records = load_jsonl(POOL_DIGESTS)
    print(f" {len(pool_records)} sessions")

    print("Loading SWE-chat CC adapted...", end="", flush=True)
    swechat_records = load_jsonl(SWECHAT_CC_ADAPTED)
    print(f" {len(swechat_records)} sessions")

    # --- RFR fire rates (from signals files) ---
    pool_signals = load_jsonl(POOL_SIGNALS)

    def _pool_has_rfr(r: dict) -> bool:
        if "rfr_fired" in r:
            return bool(r["rfr_fired"])
        return any(
            e.get("detector") == "REPEATED-FAILED-RETRY"
            for e in r.get("waste_events", [])
        )

    pool_rfr = sum(1 for r in pool_signals if _pool_has_rfr(r))
    swechat_signals = [r for r in load_jsonl(SWECHAT_CC_SIGNALS) if r.get("source") == "swechat_cc"]
    swechat_rfr = sum(1 for r in swechat_signals if r.get("rfr_fired"))

    pool_n = len(pool_signals)  # 181 (confirmed)
    swechat_n = len(swechat_records)

    pool_rfr_pct = pool_rfr / pool_n * 100 if pool_n else 0
    swechat_rfr_pct = swechat_rfr / swechat_n * 100 if swechat_n else 0

    print(f"\n=== RFR BASELINE ===")
    print(f"Pool:       {pool_rfr}/{pool_n} = {pool_rfr_pct:.1f}%")
    print(f"SWE-chat:   {swechat_rfr}/{swechat_n} = {swechat_rfr_pct:.1f}%")
    if swechat_rfr_pct > 0:
        print(f"Ratio:      {pool_rfr_pct/swechat_rfr_pct:.1f}x higher in pool")

    # --- Diagnostic 1 & 2: Session length + Bash error rate ---
    print("\nComputing pool stats...")
    pool_stats = session_stats(pool_records)
    print("Computing SWE-chat CC stats...")
    swechat_stats = session_stats(swechat_records)

    print("\n=== DIAGNOSTIC 1: SESSION LENGTH (turns) ===")
    print(f"{'Metric':<30} {'Pool (181)':<22} {'SWE-chat CC (1053)':<20}")
    print("-" * 72)
    for key, label in [
        ("turn_count_median", "Median"),
        ("turn_count_p25", "P25"),
        ("turn_count_p75", "P75"),
        ("turn_count_mean", "Mean"),
        ("turn_count_max", "Max"),
    ]:
        print(f"  {label:<28} {pool_stats[key]:<22.1f} {swechat_stats[key]:<20.1f}")
    ratio_median = pool_stats["turn_count_median"] / swechat_stats["turn_count_median"] if swechat_stats["turn_count_median"] else 0
    print(f"\n  Pool median is {ratio_median:.2f}x the SWE-chat CC median")

    print("\n=== DIAGNOSTIC 2: BASH CALL + ERROR RATE ===")
    print(f"{'Metric':<40} {'Pool (181)':<22} {'SWE-chat CC (1053)':<20}")
    print("-" * 82)
    for key, label in [
        ("sessions_with_bash", "Sessions with any Bash call"),
        ("bash_calls_mean", "Mean Bash calls/session"),
        ("sessions_with_bash_errors", "Sessions with Bash errors"),
        ("bash_errors_mean", "Mean Bash errors/session"),
        ("error_rate_per_bash_call_mean", "Error rate per Bash call"),
    ]:
        pv = pool_stats[key]
        sv = swechat_stats[key]
        if isinstance(pv, float):
            print(f"  {label:<38} {pv:<22.3f} {sv:<20.3f}")
        else:
            print(f"  {label:<38} {pv:<22} {sv:<20}")

    pool_err_rate = pool_stats["error_rate_per_bash_call_mean"]
    sw_err_rate = swechat_stats["error_rate_per_bash_call_mean"]
    pool_err_per_session = pool_stats["bash_errors_mean"]
    sw_err_per_session = swechat_stats["bash_errors_mean"]

    if sw_err_rate > 0:
        print(f"\n  Bash error rate ratio: {pool_err_rate/sw_err_rate:.2f}x (pool vs SWE-chat)")
    if sw_err_per_session > 0:
        print(f"  Bash errors/session ratio: {pool_err_per_session/sw_err_per_session:.2f}x (pool vs SWE-chat)")

    # --- Diagnostic 3: Near-miss format analysis ---
    print("\nRunning near-miss RFR analysis on SWE-chat CC...")
    nm = find_near_miss_rfr(swechat_records)

    print(f"\n=== DIAGNOSTIC 3: FORMAT SPOT-CHECK (H3 — suppression) ===")
    print(f"Sessions with exact snippet match (detector fires):          {nm['sessions_with_exact_match']}")
    print(f"Sessions with near-miss pairs (detector doesn't fire):       {nm['sessions_with_near_miss']}")
    print(f"  Near-miss breakdown:")
    print(f"    Same 60-char prefix (H3 strongest — same error, format diff): {nm['near_miss_same_prefix']}")
    print(f"    Same exit code only (moderate — same tool, different output):  {nm['near_miss_same_exit_code']}")
    print(f"    Different error entirely (H3 inapplicable):                     {nm['near_miss_different_error']}")

    if nm["samples_same_prefix"]:
        print("\nSame-prefix near-miss samples (H3 candidates):")
        for i, s in enumerate(nm["samples_same_prefix"], 1):
            print(f"\n  [{i}] Session {s['session_id']}")
            print(f"       Snip1: {repr(s['snip1'])}")
            print(f"       Snip2: {repr(s['snip2'])}")
    else:
        print("\nNo same-prefix near-miss pairs found — H3 has no direct evidence.")

    # --- Summary verdict ---
    print("\n=== SUMMARY VERDICT ===")
    h3_score = nm["near_miss_same_prefix"]
    h1h2_evidence = []

    if pool_err_per_session > sw_err_per_session * 1.5:
        h1h2_evidence.append(f"pool has {pool_err_per_session/sw_err_per_session:.1f}x more errors/session")
    if pool_stats["turn_count_median"] > swechat_stats["turn_count_median"] * 1.5:
        h1h2_evidence.append(f"pool sessions are {ratio_median:.1f}x longer")
    elif swechat_stats["turn_count_median"] > pool_stats["turn_count_median"] * 1.5:
        h1h2_evidence.append(
            f"SWE-chat sessions are {1/ratio_median:.1f}x LONGER than pool"
            " — OPPOSITE of H2 prediction"
        )

    print(f"\n  H1/H2 evidence: {'; '.join(h1h2_evidence) if h1h2_evidence else 'weak — stats similar'}")
    print(f"  H3 evidence: {h3_score} same-prefix near-miss sessions "
          f"({'significant' if h3_score > 20 else 'low'} — "
          f"{'investigate further' if h3_score > 20 else 'unlikely to explain gap'})")

    if not h1h2_evidence and h3_score <= 20:
        print("\n  FINDING: The 4.7x gap has no single strong explanation from these diagnostics.")
        print("  Likely mixed causes: SWE-chat developers are more adaptive (different approaches")
        print("  on failure), AND SWE-chat session task mix differs from this pool's GPU/infra work.")


if __name__ == "__main__":
    main()
