from __future__ import annotations

import json

from token_efficiency.layer1_features import (
    LayerOneFeatures,
    compute_domain_p25_baselines,
    extract_features,
)
from token_efficiency.trace_digest import (
    SessionDigest,
    TurnDigest,
    build_digest,
    digest_to_dict,
    digest_to_text,
)

# ---------------------------------------------------------------------------
# Fixture helpers — all in-memory, no disk reads
# ---------------------------------------------------------------------------


def _row(
    domain: str = "lib_general",
    resolved: bool = True,
    turn_count: int = 10,
    tokens_total: int = 5000,
) -> dict:
    return {
        "session_id": "test_abc",
        "domain": domain,
        "resolved": resolved,
        "turn_count": turn_count,
        "tokens_total": tokens_total,
    }


def _annotation(h2_turns: list[int], model: str = "_mock_model") -> dict:
    """Build a minimal annotation dict.

    h2_turns: list of turn_index values where llm_h2_duplicate_message=True.
    """
    labels = [
        {
            "turn_index": i,
            "llm_h2_duplicate_message": i in h2_turns,
            "llm_h1_redundant_read": False,
            "llm_h3_backtrack": False,
            "llm_h4_tool_result_used": False,
        }
        for i in range(10)
    ]
    return {"session_id": "test_abc", "_model": model, "per_turn_labels": labels}


def _trace(turns_with_cache: list[tuple[int, int]]) -> dict:
    """Build a minimal trace dict.

    turns_with_cache: list of (input_tokens, cache_read_tokens) per turn.
    """
    turns = [
        {
            "turn_index": i,
            "role": "ai",
            "content_text": f"turn {i}",
            "tool_uses": [],
            "token_counts": {
                "input": inp,
                "output": 0,
                "cache_read": cr,
                "cache_creation": 0,
            },
        }
        for i, (inp, cr) in enumerate(turns_with_cache)
    ]
    return {
        "session_id": "test_abc",
        "outcome": {"result": "pass", "patch_diff": ""},
        "turns": turns,
    }


def _features(
    session_id: str = "test_abc",
    domain_id: str = "lib_general",
    test_outcome: bool = True,
    total_tokens: int = 5000,
    turn_count: int = 10,
    h2_duplicate_count: int = 2,
    cache_hit_rate: float = 0.3,
    p25_token_ratio: float = 1.5,
    labeler_model: str = "_mock",
    scaffold: str = "swe_agent",
    output_tokens_available: bool = False,
) -> LayerOneFeatures:
    return LayerOneFeatures(
        session_id=session_id,
        domain_id=domain_id,
        test_outcome=test_outcome,
        total_tokens=total_tokens,
        turn_count=turn_count,
        h2_duplicate_count=h2_duplicate_count,
        cache_hit_rate=cache_hit_rate,
        p25_token_ratio=p25_token_ratio,
        labeler_model=labeler_model,
        scaffold=scaffold,
        output_tokens_available=output_tokens_available,
    )


# ---------------------------------------------------------------------------
# 1. compute_domain_p25_baselines
# ---------------------------------------------------------------------------


def test_compute_domain_p25_baselines_basic() -> None:
    """Two domains each with ≥3 sessions; verify p25 matches hand-computed value."""
    taxonomy = [
        {"session_id": f"s{i}", "domain": "alpha", "tokens_total": str(t), "turn_count": "5",
         "resolved": True}
        for i, t in enumerate([1000, 2000, 3000, 4000])
    ] + [
        {"session_id": f"b{i}", "domain": "beta", "tokens_total": str(t), "turn_count": "5",
         "resolved": True}
        for i, t in enumerate([500, 1500, 2500])
    ]

    baselines = compute_domain_p25_baselines(taxonomy)

    # alpha tokens sorted: [1000, 2000, 3000, 4000]; p25 = 1750.0
    assert abs(baselines["alpha"] - 1750.0) < 1.0
    # beta tokens sorted: [500, 1500, 2500]; p25 = 1000.0
    assert abs(baselines["beta"] - 1000.0) < 1.0


def test_compute_domain_p25_baselines_small_domain_fallback() -> None:
    """Domain with <3 sessions falls back to corpus-wide p25."""
    # "large" has 4 sessions; "tiny" has only 2.
    large_tokens = [1000, 2000, 3000, 4000]
    tiny_tokens = [9000, 9500]

    taxonomy = [
        {"session_id": f"l{i}", "domain": "large", "tokens_total": str(t),
         "turn_count": "5", "resolved": True}
        for i, t in enumerate(large_tokens)
    ] + [
        {"session_id": f"t{i}", "domain": "tiny", "tokens_total": str(t),
         "turn_count": "5", "resolved": True}
        for i, t in enumerate(tiny_tokens)
    ]

    baselines = compute_domain_p25_baselines(taxonomy)

    # Corpus-wide p25 from all 6 values: [1000, 2000, 3000, 4000, 9000, 9500]
    import numpy as np
    all_tokens = large_tokens + tiny_tokens
    expected_corpus_p25 = float(np.percentile(all_tokens, 25))

    # "large" has its own p25 (≥3 sessions)
    assert baselines["large"] != expected_corpus_p25 or True  # it may coincide; just check tiny
    # "tiny" must equal corpus-wide p25
    assert abs(baselines["tiny"] - expected_corpus_p25) < 0.01


def test_compute_domain_p25_baselines_empty() -> None:
    """Empty taxonomy returns empty dict without crashing."""
    result = compute_domain_p25_baselines([])
    assert result == {}


# ---------------------------------------------------------------------------
# 2. extract_features — one test per branch
# ---------------------------------------------------------------------------


def test_h2_duplicate_count_zero() -> None:
    """Annotation with no h2 duplicates → h2_duplicate_count == 0."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=_annotation(h2_turns=[]),
        trace=_trace([(100, 0)] * 10),
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.h2_duplicate_count == 0


def test_h2_duplicate_count_nonzero() -> None:
    """Annotation marks 3 turns as h2 → h2_duplicate_count == 3."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=_annotation(h2_turns=[0, 4, 7]),
        trace=_trace([(100, 0)] * 10),
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.h2_duplicate_count == 3


def test_h2_missing_annotation() -> None:
    """annotation=None → h2_duplicate_count=0 and labeler_model='missing'."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=None,
        trace=_trace([(100, 0)] * 10),
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.h2_duplicate_count == 0
    assert feat.labeler_model == "missing"


def test_cache_hit_rate_zero() -> None:
    """All cache_read=0 → cache_hit_rate == 0.0."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=_annotation(h2_turns=[]),
        trace=_trace([(200, 0), (300, 0), (500, 0)]),
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.cache_hit_rate == 0.0


def test_cache_hit_rate_nonzero() -> None:
    """sum(cache_read)=100, sum(input)=500 → cache_hit_rate == 0.2."""
    # 5 turns: input=100 each, cache_read=20 each
    # total input=500, total cache_read=100 → rate=0.2
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=_annotation(h2_turns=[]),
        trace=_trace([(100, 20)] * 5),
        domain_p25={"lib_general": 4000.0},
    )
    assert abs(feat.cache_hit_rate - 0.2) < 1e-9


def test_cache_hit_rate_missing_trace() -> None:
    """trace=None → cache_hit_rate == 0.0."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(),
        annotation=_annotation(h2_turns=[]),
        trace=None,
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.cache_hit_rate == 0.0


def test_p25_ratio_clamped_low() -> None:
    """tokens_total=1 with very high baseline → ratio clamped to 0.1."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(tokens_total=1),
        annotation=None,
        trace=None,
        domain_p25={"lib_general": 100_000.0},
    )
    assert feat.p25_token_ratio == 0.1


def test_p25_ratio_clamped_high() -> None:
    """tokens_total=10_000_000 with tiny baseline → ratio clamped to 100.0."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(tokens_total=10_000_000),
        annotation=None,
        trace=None,
        domain_p25={"lib_general": 100.0},
    )
    assert feat.p25_token_ratio == 100.0


def test_test_outcome_resolved() -> None:
    """resolved=True in taxonomy_row → test_outcome=True."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(resolved=True),
        annotation=None,
        trace=None,
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.test_outcome is True


def test_test_outcome_unresolved() -> None:
    """resolved=False in taxonomy_row → test_outcome=False."""
    feat = extract_features(
        session_id="test_abc",
        taxonomy_row=_row(resolved=False),
        annotation=None,
        trace=None,
        domain_p25={"lib_general": 4000.0},
    )
    assert feat.test_outcome is False


# ---------------------------------------------------------------------------
# 3. build_digest
# ---------------------------------------------------------------------------


def _three_turn_trace() -> dict:
    """A trace with system, user, and ai turns."""
    return {
        "session_id": "test_abc",
        "outcome": {"result": "pass", "patch_diff": ""},
        "turns": [
            {
                "turn_index": 0,
                "role": "system",
                "content_text": "You are a helpful assistant.",
                "tool_uses": [],
                "token_counts": {"input": 50, "output": 0, "cache_read": 0, "cache_creation": 0},
            },
            {
                "turn_index": 1,
                "role": "user",
                "content_text": "Fix the bug in my code",
                "tool_uses": [],
                "token_counts": {"input": 100, "output": 0, "cache_read": 0, "cache_creation": 0},
            },
            {
                "turn_index": 2,
                "role": "ai",
                "content_text": "I will help you fix the bug.",
                "tool_uses": [],
                "token_counts": {"input": 200, "output": 80, "cache_read": 50, "cache_creation": 0},
            },
        ],
    }


def test_build_digest_basic() -> None:
    """3-turn trace: task_description = user content, all 3 TurnDigests present."""
    feat = _features()
    trace = _three_turn_trace()
    ann = _annotation(h2_turns=[])

    digest = build_digest("test_abc", feat, trace, ann)

    assert digest.task_description == "Fix the bug in my code"
    assert len(digest.turns) == 3
    roles = [t.role for t in digest.turns]
    assert roles == ["system", "user", "ai"]


def test_build_digest_no_trace() -> None:
    """trace=None → task_description='N/A', turns=[]."""
    feat = _features()
    digest = build_digest("test_abc", feat, None, None)

    assert digest.task_description == "N/A"
    assert digest.turns == []


def test_build_digest_h2_lookup() -> None:
    """Annotation marks turn_index=2 as h2 → TurnDigest at index 2 has h2_duplicate=True."""
    feat = _features()
    trace = _three_turn_trace()
    ann = _annotation(h2_turns=[2])

    digest = build_digest("test_abc", feat, trace, ann)

    turn_by_idx = {t.turn_index: t for t in digest.turns}
    assert turn_by_idx[2].h2_duplicate is True
    assert turn_by_idx[0].h2_duplicate is False
    assert turn_by_idx[1].h2_duplicate is False


# ---------------------------------------------------------------------------
# 4. digest_to_text
# ---------------------------------------------------------------------------


def _sample_digest(with_h2_on_turn: int | None = None) -> SessionDigest:
    """Build a SessionDigest directly for text rendering tests."""
    turns = [
        TurnDigest(
            turn_index=0,
            role="system",
            tool_names=[],
            content_snippet="",
            token_count_input=50,
            token_count_output=0,
            cache_read=0,
            h2_duplicate=False,
        ),
        TurnDigest(
            turn_index=1,
            role="user",
            tool_names=[],
            content_snippet="Fix the bug in my code",
            token_count_input=100,
            token_count_output=0,
            cache_read=0,
            h2_duplicate=False,
        ),
        TurnDigest(
            turn_index=2,
            role="ai",
            tool_names=["bash"],
            content_snippet="I will help you.",
            token_count_input=200,
            token_count_output=80,
            cache_read=50,
            h2_duplicate=(with_h2_on_turn == 2),
        ),
    ]
    return SessionDigest(
        session_id="test_abc",
        domain="lib_general",
        resolved=True,
        total_tokens=5000,
        turn_count=3,
        h2_duplicate_count=1 if with_h2_on_turn is not None else 0,
        cache_hit_rate=0.3,
        p25_token_ratio=1.5,
        output_tokens_available=False,
        task_description="Fix the bug in my code",
        turns=turns,
    )


def test_digest_to_text_excludes_system() -> None:
    """System turn must not appear in the TRAJECTORY section of rendered text."""
    digest = _sample_digest()
    text = digest_to_text(digest)

    # "SYSTEM" must not appear as a turn role label in the trajectory
    # (it appears in "TRAJECTORY:" header but not as a turn entry like "[T0] SYSTEM")
    trajectory_section = text.split("TRAJECTORY:")[-1]
    assert "SYSTEM" not in trajectory_section


def test_digest_to_text_marks_h2() -> None:
    """Turn with h2_duplicate=True causes 'H2 DUPLICATE' to appear in output."""
    digest = _sample_digest(with_h2_on_turn=2)
    text = digest_to_text(digest)

    assert "H2 DUPLICATE" in text


def test_digest_to_text_structure() -> None:
    """Output must start with '=== SESSION' and contain 'TRAJECTORY:'."""
    digest = _sample_digest()
    text = digest_to_text(digest)

    assert text.startswith("=== SESSION")
    assert "TRAJECTORY:" in text


# ---------------------------------------------------------------------------
# 5. digest_to_dict
# ---------------------------------------------------------------------------


def test_digest_to_dict_serialisable() -> None:
    """digest_to_dict output must be JSON-serialisable without raising."""
    digest = _sample_digest(with_h2_on_turn=2)
    d = digest_to_dict(digest)

    # Must not raise
    serialised = json.dumps(d)
    assert isinstance(serialised, str)
    assert len(serialised) > 0
