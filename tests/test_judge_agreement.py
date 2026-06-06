"""
Unit tests for scripts/judge_agreement.py.

Uses hardcoded synthetic records — no live data files required.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load module without relying on package install
_MODULE_PATH = Path(__file__).parent.parent / "scripts" / "judge_agreement.py"
_spec = importlib.util.spec_from_file_location("judge_agreement", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

compute_agreement = _mod.compute_agreement
inner_join = _mod.inner_join
kappa_interpretation = _mod.kappa_interpretation
VERDICT_ORDER = _mod.VERDICT_ORDER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(session_id: str, verdict: str, judge_score: float, waste: list[str] | None = None) -> dict:
    return {
        "session_id": session_id,
        "verdict": verdict,
        "judge_score": judge_score,
        "waste_categories": waste or [],
        "reasoning": f"reason for {session_id}",
    }


# ---------------------------------------------------------------------------
# kappa_interpretation
# ---------------------------------------------------------------------------

def test_kappa_interpretation_slight() -> None:
    assert kappa_interpretation(0.1) == "slight"


def test_kappa_interpretation_fair() -> None:
    assert kappa_interpretation(0.3) == "fair"


def test_kappa_interpretation_moderate() -> None:
    assert kappa_interpretation(0.5) == "moderate"


def test_kappa_interpretation_substantial() -> None:
    assert kappa_interpretation(0.7) == "substantial"


def test_kappa_interpretation_near_perfect() -> None:
    assert kappa_interpretation(0.9) == "near-perfect"


def test_kappa_interpretation_exactly_0_2() -> None:
    # boundary: < 0.2 → slight, so 0.2 itself → fair
    assert kappa_interpretation(0.2) == "fair"


# ---------------------------------------------------------------------------
# inner_join
# ---------------------------------------------------------------------------

def test_inner_join_all_match() -> None:
    q = [_rec("s1", "BETTER", 0.75), _rec("s2", "SIMILAR", 0.5)]
    g = [_rec("s1", "MUCH_BETTER", 1.0), _rec("s2", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    assert len(pairs) == 2


def test_inner_join_partial() -> None:
    q = [_rec("s1", "BETTER", 0.75), _rec("s2", "SIMILAR", 0.5)]
    g = [_rec("s1", "MUCH_BETTER", 1.0)]
    pairs = inner_join(q, g)
    assert len(pairs) == 1
    assert pairs[0][0]["session_id"] == "s1"


def test_inner_join_no_overlap() -> None:
    q = [_rec("s1", "BETTER", 0.75)]
    g = [_rec("s2", "SIMILAR", 0.5)]
    pairs = inner_join(q, g)
    assert len(pairs) == 0


# ---------------------------------------------------------------------------
# compute_agreement — exact match
# ---------------------------------------------------------------------------

def test_exact_match_perfect() -> None:
    """All verdicts identical → exact_match_pct = 1.0."""
    q = [_rec("s1", "BETTER", 0.75), _rec("s2", "SIMILAR", 0.5), _rec("s3", "WORSE", 0.25)]
    g = [_rec("s1", "BETTER", 0.75), _rec("s2", "SIMILAR", 0.5), _rec("s3", "WORSE", 0.25)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, len(q), len(g))
    assert result["exact_match_pct"] == 1.0
    assert result["adjacent_match_pct"] == 1.0
    assert result["directional"]["n_disagreements"] == 0


def test_exact_match_none() -> None:
    """No verdicts match → exact_match_pct = 0.0."""
    q = [_rec("s1", "MUCH_BETTER", 1.0), _rec("s2", "BETTER", 0.75)]
    g = [_rec("s1", "MUCH_WORSE", 0.0), _rec("s2", "WORSE", 0.25)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, len(q), len(g))
    assert result["exact_match_pct"] == 0.0


# ---------------------------------------------------------------------------
# compute_agreement — directional
# ---------------------------------------------------------------------------

def test_gemma_lenient_direction() -> None:
    """Gemma rates UP on one pair → mean_direction positive."""
    # Qwen: WORSE(1), Gemma: BETTER(3) → direction = 3-1 = +2
    q = [_rec("s1", "WORSE", 0.25)]
    g = [_rec("s1", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 1, 1)
    d = result["directional"]
    assert d["n_disagreements"] == 1
    assert d["mean_direction"] > 0
    assert d["pct_gemma_lenient"] == 1.0
    assert d["pct_gemma_harsher"] == 0.0


def test_qwen_negative_slice_tracked() -> None:
    """Sessions where Qwen is WORSE/MUCH_WORSE appear in qwen_negative_slice."""
    q = [
        _rec("s1", "WORSE", 0.25),
        _rec("s2", "MUCH_WORSE", 0.0),
        _rec("s3", "BETTER", 0.75),
    ]
    g = [
        _rec("s1", "SIMILAR", 0.5),
        _rec("s2", "BETTER", 0.75),
        _rec("s3", "BETTER", 0.75),
    ]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 3, 3)
    qneg = result["directional"]["qwen_negative_slice"]
    assert qneg["n"] == 2
    assert qneg["gemma_lenient_n"] == 2
    assert qneg["gemma_also_negative_n"] == 0


# ---------------------------------------------------------------------------
# compute_agreement — waste disagreements
# ---------------------------------------------------------------------------

def test_waste_disagreement_detected() -> None:
    """
    Qwen rates WORSE with flagged waste, Gemma rates BETTER → detected as waste_disagreement.
    """
    q = [_rec("s1", "WORSE", 0.25, waste=["failed_retry"])]
    g = [_rec("s1", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 1, 1)
    assert result["n_waste_disagreements"] == 1
    wd = result["waste_disagreements"][0]
    assert wd["session_id"] == "s1"
    assert wd["qwen_verdict"] == "WORSE"
    assert wd["gemma_verdict"] == "BETTER"
    assert "failed_retry" in wd["qwen_waste_categories"]


def test_waste_disagreement_not_triggered_when_qwen_good() -> None:
    """Qwen rates BETTER with waste flag — should NOT appear in waste_disagreements."""
    q = [_rec("s1", "BETTER", 0.75, waste=["redundant_read"])]
    g = [_rec("s1", "WORSE", 0.25)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 1, 1)
    assert result["n_waste_disagreements"] == 0


def test_waste_disagreement_non_flagged_category_ignored() -> None:
    """Waste category 'trajectory_drift' not in flagged set → not a waste_disagreement."""
    q = [_rec("s1", "WORSE", 0.25, waste=["trajectory_drift"])]
    g = [_rec("s1", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 1, 1)
    assert result["n_waste_disagreements"] == 0


# ---------------------------------------------------------------------------
# compute_agreement — gate overlap
# ---------------------------------------------------------------------------

def test_gate_overlap_strict() -> None:
    """Both judges rate MUCH_BETTER → strict overlap."""
    q = [_rec("s1", "MUCH_BETTER", 1.0), _rec("s2", "MUCH_BETTER", 1.0)]
    g = [_rec("s1", "MUCH_BETTER", 1.0), _rec("s2", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 2, 2)
    go = result["gate_overlap"]
    assert go["n_qwen_much_better"] == 2
    assert go["n_gemma_strict_overlap"] == 1
    assert go["n_gemma_top2_overlap"] == 2
    assert go["pct_strict"] == pytest.approx(0.5)
    assert go["pct_top2"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_agreement — reverse gate
# ---------------------------------------------------------------------------

def test_reverse_gate() -> None:
    """Qwen rates 2 sessions bad; Gemma agrees on 1, lenient on 1."""
    q = [_rec("s1", "WORSE", 0.25), _rec("s2", "MUCH_WORSE", 0.0)]
    g = [_rec("s1", "WORSE", 0.25), _rec("s2", "SIMILAR", 0.5)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 2, 2)
    rg = result["reverse_gate"]
    assert rg["n_qwen_bad"] == 2
    assert rg["n_gemma_also_bad"] == 1
    assert rg["n_gemma_lenient_on_bad"] == 1
    assert rg["pct_gemma_agrees_bad"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compute_agreement — verdict distributions
# ---------------------------------------------------------------------------

def test_verdict_distributions_populated() -> None:
    """Verdict distribution dicts reflect the matched sessions."""
    q = [_rec("s1", "BETTER", 0.75), _rec("s2", "MUCH_BETTER", 1.0)]
    g = [_rec("s1", "SIMILAR", 0.5), _rec("s2", "MUCH_BETTER", 1.0)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 2, 2)
    assert result["verdict_dist_qwen"]["BETTER"] == 1
    assert result["verdict_dist_qwen"]["MUCH_BETTER"] == 1
    assert result["verdict_dist_gemma"]["SIMILAR"] == 1
    assert result["verdict_dist_gemma"]["MUCH_BETTER"] == 1


# ---------------------------------------------------------------------------
# compute_agreement — per_level breakdown
# ---------------------------------------------------------------------------

def test_per_level_breakdown() -> None:
    """per_level tracks Gemma distribution for each Qwen verdict."""
    q = [_rec("s1", "WORSE", 0.25), _rec("s2", "WORSE", 0.25)]
    g = [_rec("s1", "SIMILAR", 0.5), _rec("s2", "BETTER", 0.75)]
    pairs = inner_join(q, g)
    result = compute_agreement(pairs, 2, 2)
    assert result["per_level"]["WORSE"]["n"] == 2
    assert result["per_level"]["WORSE"]["gemma_dist"]["SIMILAR"] == 1
    assert result["per_level"]["WORSE"]["gemma_dist"]["BETTER"] == 1


# ---------------------------------------------------------------------------
# Edge: zero matched sessions
# ---------------------------------------------------------------------------

def test_zero_matched_sessions() -> None:
    """No matched sessions → all metrics are zeroed/default, no crash."""
    result = compute_agreement([], 10, 0)
    assert result["n_matched"] == 0
    assert result["exact_match_pct"] == 0.0
    assert result["n_waste_disagreements"] == 0
    assert result["directional"]["n_disagreements"] == 0
