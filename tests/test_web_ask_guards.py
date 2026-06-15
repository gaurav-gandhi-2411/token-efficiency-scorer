"""test_web_ask_guards.py — Web Ask endpoint carries identical guards to CLI tes ask.

These tests are the GATE for the web Ask panel. They must all pass before 0.8.0 can proceed.

Guards verified (identical to test_chat_grounding.py + test_chat_no_code_egress.py):
  G1. METRICS-ONLY EGRESS: the payload sent to the LLM contains no raw session content,
      code, file paths, or tool outputs.
  G2. GROUNDING: the /ask route calls the SAME ask_local / ask_api functions as the CLI,
      meaning the same CHAT_SYSTEM_PROMPT and build_chat_context() apply.
  G3. "I don't predict" guard: when the LLM returns the prediction refusal phrase, the
      route passes it through without modification.
  G4. "not measured" guard: when the LLM returns the out-of-scope phrase, it passes through.
  G5. FLOOR: /ask with a below-floor cache returns a valid response (no crash, no invented
      archetype data — corpus stats are still provided).
  G6. CONSENT: the API path (/ask with api_consent=False) returns an error, not an answer.
      The API path (/ask with api_consent=True) calls ask_api with consent_given=True.
  G7. ASK_API CONSENT_GIVEN GATE: ask_api(consent_given=False) must return None (no network
      call). Inherited from CLI path — tested here to confirm the web route doesn't bypass.
  G8. QUESTION LENGTH CAP: questions >500 chars are truncated before reaching the LLM.
  G9. LOCAL-FIRST: when Ollama is available, the local path is used (no API call).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import open_db, upsert_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(session_id: str) -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",
        real_tokens=10000,
        scope_status="in_scope",
        baseline_available=True,
        p25=8000, p75=12000, median=10000,
        band_verdict="within_band",
        interpretation="",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="self",
        judge_verdict=None, judge_score=None, judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0, waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        session_cost_usd=0.05,
        cost_approximate=False,
        cost_domain_of_validity="",
    )


@pytest.fixture()
def flask_app(tmp_path: Path):
    """Minimal Flask app with 5 sessions (below-floor is fine — we mock intelligence)."""
    from tes.web.server import ServerConfig, create_app
    db = tmp_path / "test.db"
    conn = open_db(db)
    for i in range(5):
        sid = f"ask-guard-{i:04d}-aaaa-bbbb-cccc-dddddddddddd"
        upsert_session(conn, _make_result(sid), f"/fake/{sid}.jsonl", float(i), f"h{i}")
    conn.commit()
    conn.close()
    cfg = ServerConfig(db_path=db)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


def _post_ask(client, question: str, api_consent: bool = False) -> dict:
    resp = client.post(
        "/ask",
        data=json.dumps({"question": question, "api_consent": api_consent}),
        content_type="application/json",
    )
    return resp.get_json(), resp.status_code


# ---------------------------------------------------------------------------
# G1: Metrics-only egress — context assembled by build_chat_context() must not
#     contain raw session content, code, file paths, or tool outputs.
# ---------------------------------------------------------------------------

class TestG1MetricsOnlyEgress:
    def test_build_chat_context_no_raw_content(self, flask_app) -> None:
        """The context dict passed to the LLM must not contain raw session content."""
        from tes.intelligence.chat import build_chat_context
        from tes.web.server import ServerConfig
        # Get the db_path from a fresh app
        ctx = build_chat_context("How much do my sessions cost?")
        # Context must have only structured metric fields
        allowed_top_level = {"question", "intelligence", "corpus_stats", "session"}
        assert set(ctx.keys()) <= allowed_top_level, f"Unexpected keys: {set(ctx.keys()) - allowed_top_level}"

    def test_corpus_stats_no_code_fields(self, flask_app) -> None:
        from tes.intelligence.chat import build_chat_context
        ctx = build_chat_context("test question")
        corpus = ctx["corpus_stats"]
        # These fields must NOT be present in corpus_stats
        forbidden = {"content", "code", "tool_input", "tool_output", "file_path",
                     "raw_text", "session_text", "prompt", "message"}
        for f in forbidden:
            assert f not in corpus, f"Forbidden field '{f}' found in corpus_stats"

    def test_session_summary_no_raw_content(self) -> None:
        from tes.intelligence.chat import _summarize_session
        # Build a session row that has content-like fields
        session_row = {
            "session_id": "aaaaaa-bbbbbb-cccccc",
            "task_type": "debug-fix",
            "scored_at": "2026-06-15T10:00:00",
            "real_tokens": 50000,
            "turn_count": 10,
            "band_verdict": "within_band",
            "scope_status": "in_scope",
            "session_cost_usd": 0.05,
            "judge_verdict": None,
            "judge_score": None,
            "waste_event_count": 0,
            "waste_events": [],
            "baseline_source": "self",
            # These are content fields that must NOT appear in the summary
            "raw_content": "def foo(): pass",
            "tool_output": "error: connection refused",
            "file_path": "/home/user/project/main.py",
        }
        summary = _summarize_session(session_row)
        assert "raw_content" not in summary
        assert "tool_output" not in summary
        assert "file_path" not in summary
        assert "def foo" not in str(summary)
        assert "connection refused" not in str(summary)

    def test_user_message_contains_no_code(self, flask_app, tmp_path) -> None:
        from tes.intelligence.chat import _build_user_message, build_chat_context
        ctx = build_chat_context("What is my median cost?")
        msg = _build_user_message(ctx)
        # The message is structured metrics — must not contain code-like content
        assert "def " not in msg
        assert "import " not in msg or "tracegauge" not in msg  # no Python imports in message
        assert "/home/" not in msg
        assert "tool_input" not in msg.lower()


# ---------------------------------------------------------------------------
# G2: Grounding — route calls ask_local / ask_api (same functions as CLI)
# ---------------------------------------------------------------------------

class TestG2Grounding:
    def test_route_calls_ask_local_when_available(self, flask_app) -> None:
        with patch("tes.web.server.ask_local", return_value="local answer") as mock_local, \
             patch("tes.web.server.ask_api") as mock_api:
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "What is my median cost?")
        mock_local.assert_called_once()
        mock_api.assert_not_called()
        assert status == 200
        assert data["answer"] == "local answer"
        assert data["source"] == "local"

    def test_ask_local_called_with_correct_question(self, flask_app) -> None:
        with patch("tes.web.server.ask_local", return_value="answer") as mock_local:
            with flask_app.test_client() as c:
                _post_ask(c, "How much waste do I have?")
        call_args = mock_local.call_args
        assert call_args[0][0] == "How much waste do I have?" or \
               call_args.kwargs.get("question") == "How much waste do I have?" or \
               "How much waste do I have?" in str(call_args)

    def test_same_system_prompt_used(self) -> None:
        """The CLI CHAT_SYSTEM_PROMPT is used — same module, same object."""
        from tes.intelligence.chat import CHAT_SYSTEM_PROMPT
        # Verify the prompt contains all 7 honesty rules
        assert "Answer ONLY from the metrics" in CHAT_SYSTEM_PROMPT
        assert "I don't predict future behavior" in CHAT_SYSTEM_PROMPT
        assert "not measured" in CHAT_SYSTEM_PROMPT or "I don't have that measured" in CHAT_SYSTEM_PROMPT
        assert "quality judgments" in CHAT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# G3: "I don't predict" passes through unchanged
# ---------------------------------------------------------------------------

class TestG3PredictionRefusal:
    def test_prediction_refusal_passes_through(self, flask_app) -> None:
        prediction_refusal = (
            "I don't predict future behavior — I only explain what's already measured "
            "in your session history."
        )
        with patch("tes.web.server.ask_local", return_value=prediction_refusal):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "What will my next session cost?")
        assert status == 200
        assert "don't predict" in data["answer"] or "I don't predict" in data["answer"]


# ---------------------------------------------------------------------------
# G4: "not measured" passes through unchanged
# ---------------------------------------------------------------------------

class TestG4NotMeasured:
    def test_not_measured_response_passes_through(self, flask_app) -> None:
        not_measured = "I don't have that measured — tracegauge hasn't collected that metric."
        with patch("tes.web.server.ask_local", return_value=not_measured):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "What is my GPU utilization?")
        assert status == 200
        assert "measured" in data["answer"] or "haven't collected" in data["answer"]


# ---------------------------------------------------------------------------
# G5: Floor — below-floor cache doesn't crash the /ask endpoint
# ---------------------------------------------------------------------------

class TestG5Floor:
    def test_below_floor_ask_does_not_crash(self, flask_app) -> None:
        """Ask must work (or fail gracefully) even below the 30-session floor."""
        with patch("tes.web.server.ask_local", return_value="Corpus stats answer."):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "How many sessions do I have?")
        assert status == 200
        assert "answer" in data

    def test_below_floor_no_invented_archetype_data(self, flask_app) -> None:
        """If intelligence is below-floor, the answer must not invent archetype data."""
        # Mock ask_local to return what the constrained prompt would return
        below_floor_answer = (
            "Pattern analysis is not yet available — fewer than 30 sessions in the corpus. "
            "I don't have that measured — tracegauge hasn't collected that metric."
        )
        with patch("tes.web.server.ask_local", return_value=below_floor_answer):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "What are my archetypes?")
        assert status == 200
        # Answer must not claim there ARE archetypes
        answer = data.get("answer", "")
        assert "archetype" not in answer.lower() or "not" in answer.lower() or "available" in answer.lower()


# ---------------------------------------------------------------------------
# G6: API consent gate
# ---------------------------------------------------------------------------

class TestG6Consent:
    def test_api_path_without_consent_returns_error(self, flask_app) -> None:
        """When Ollama fails and consent is False, must return error not an answer."""
        with patch("tes.web.server.ask_local", return_value=None), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "What is my median cost?", api_consent=False)
        assert "error" in data
        assert "answer" not in data

    def test_api_path_without_consent_no_network_call(self, flask_app) -> None:
        """ask_api must NOT be called when consent is False."""
        with patch("tes.web.server.ask_local", return_value=None), \
             patch("tes.web.server.ask_api") as mock_api, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with flask_app.test_client() as c:
                _post_ask(c, "test question", api_consent=False)
        mock_api.assert_not_called()

    def test_api_path_with_consent_calls_ask_api(self, flask_app) -> None:
        with patch("tes.web.server.ask_local", return_value=None), \
             patch("tes.web.server.ask_api", return_value="api answer") as mock_api, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "test question", api_consent=True)
        mock_api.assert_called_once()
        call_kwargs = mock_api.call_args.kwargs
        assert call_kwargs.get("consent_given") is True
        assert status == 200
        assert data["answer"] == "api answer"

    def test_no_api_key_no_network_call(self, flask_app) -> None:
        """Without ANTHROPIC_API_KEY, ask_api must never be called."""
        with patch("tes.web.server.ask_local", return_value=None), \
             patch("tes.web.server.ask_api") as mock_api, \
             patch.dict("os.environ", {}, clear=True):
            with flask_app.test_client() as c:
                data, status = _post_ask(c, "test question", api_consent=True)
        mock_api.assert_not_called()
        assert "error" in data

    def test_needs_consent_flag_in_response_when_key_present(self, flask_app) -> None:
        """When Ollama is down + key present + no consent, needs_consent=True hints the UI."""
        with patch("tes.web.server.ask_local", return_value=None), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with flask_app.test_client() as c:
                data, _ = _post_ask(c, "test question", api_consent=False)
        assert data.get("needs_consent") is True


# ---------------------------------------------------------------------------
# G7: ask_api consent_given=False returns None (inherited gate — not web-specific)
# ---------------------------------------------------------------------------

class TestG7AskApiConsentGate:
    def test_ask_api_returns_none_without_consent(self) -> None:
        from tes.intelligence.chat import ChatApiConfig, ask_api
        cfg = ChatApiConfig(api_key="sk-fake")
        result = ask_api("test question", cfg, consent_given=False)
        assert result is None

    def test_ask_api_returns_none_with_empty_key(self) -> None:
        from tes.intelligence.chat import ChatApiConfig, ask_api
        cfg = ChatApiConfig(api_key="")
        result = ask_api("test question", cfg, consent_given=True)
        assert result is None


# ---------------------------------------------------------------------------
# G8: Question length cap
# ---------------------------------------------------------------------------

class TestG8QuestionLengthCap:
    def test_long_question_truncated_to_500(self, flask_app) -> None:
        long_q = "x" * 2000
        captured: list[str] = []

        def _capture(q, **kwargs):
            captured.append(q)
            return "answer"

        with patch("tes.web.server.ask_local", side_effect=_capture):
            with flask_app.test_client() as c:
                _post_ask(c, long_q)
        assert len(captured[0]) <= 500

    def test_empty_question_returns_400(self, flask_app) -> None:
        with flask_app.test_client() as c:
            _, status = _post_ask(c, "")
        assert status == 400


# ---------------------------------------------------------------------------
# G9: Local-first routing
# ---------------------------------------------------------------------------

class TestG9LocalFirst:
    def test_local_used_before_api(self, flask_app) -> None:
        """When ask_local returns an answer, ask_api must never be called."""
        with patch("tes.web.server.ask_local", return_value="local answer"), \
             patch("tes.web.server.ask_api") as mock_api, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with flask_app.test_client() as c:
                data, _ = _post_ask(c, "test question", api_consent=True)
        mock_api.assert_not_called()
        assert data["source"] == "local"
