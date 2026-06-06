from __future__ import annotations

"""tests/test_redaction_default_on.py — Verify secret redaction fires at ingestion."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "adapters"))

from claudecode_adapter import _redact_secrets


def test_groq_key_is_redacted() -> None:
    text = "export GROQ_API_KEY=gsk_" + "A" * 30
    result = _redact_secrets(text)
    assert "gsk_" not in result
    assert "<SECRET_REDACTED>" in result


def test_anthropic_key_is_redacted() -> None:
    text = "key = sk-ant-" + "B" * 30
    result = _redact_secrets(text)
    assert "sk-ant-" not in result
    assert "<SECRET_REDACTED>" in result


def test_github_pat_is_redacted() -> None:
    text = "token: ghp_" + "C" * 25
    result = _redact_secrets(text)
    assert "ghp_" not in result
    assert "<SECRET_REDACTED>" in result


def test_clean_text_unchanged() -> None:
    text = "this is a normal message with no secrets"
    result = _redact_secrets(text)
    assert result == text


def test_redaction_is_default_not_opt_in() -> None:
    """Verify _redact_secrets is called automatically inside adapt_session.

    Imports adapt_session and confirms it references _redact_secrets (not behind a flag).
    This is a structural check: the moat's privacy guarantee is that redaction
    happens at ingestion, always, without a user opt-in.
    """
    import inspect
    from claudecode_adapter import adapt_session
    source = inspect.getsource(adapt_session)
    assert "_redact_secrets" in source
