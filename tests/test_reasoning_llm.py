from __future__ import annotations

import os

from razorpay_agent.reasoning.llm import (
    NousBackend,
    StubBackend,
    TencentBackend,
    get_provider,
    load_dotenv_into_env,
    resolve_provider,
)


def test_providers_registered():
    assert get_provider("tencent") is TencentBackend
    assert get_provider("nous") is NousBackend


def test_unknown_provider_raises():
    import pytest

    with pytest.raises(KeyError):
        get_provider("does-not-exist")


def test_resolve_falls_back_to_stub_on_unknown():
    assert isinstance(resolve_provider("does-not-exist"), StubBackend)


def test_load_dotenv_into_env_picks_up_keys(monkeypatch, tmp_path):
    p = tmp_path / ".env"
    p.write_text("TENCENT_HY3_API_KEY=secret123\n# comment\nOPENAI_BASE_URL=https://x/v1\n")
    monkeypatch.delenv("TENCENT_HY3_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    load_dotenv_into_env(p)
    assert os.environ.get("TENCENT_HY3_API_KEY") == "secret123"
    assert os.environ.get("OPENAI_BASE_URL") == "https://x/v1"


def test_tencent_backend_constructs_and_names(monkeypatch):
    import razorpay_agent.reasoning.llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_dotenv_into_env", lambda *a, **k: None)
    monkeypatch.setenv("TENCENT_HY3_API_KEY", "dummy")
    monkeypatch.setenv("TENCENT_HY3_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("TENCENT_HY3_MODEL", raising=False)
    b = resolve_provider("tencent")
    assert b.name == "tencent"
    assert b.model == "hy3"


def test_nous_backend_constructs_and_names(monkeypatch):
    import razorpay_agent.reasoning.llm as llm_mod

    monkeypatch.setattr(llm_mod, "load_dotenv_into_env", lambda *a, **k: None)
    monkeypatch.setenv("NOUS_PORTAL_API_KEY", "dummy")
    monkeypatch.setenv("NOUS_PORTAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("NOUS_PORTAL_MODEL", raising=False)
    monkeypatch.delenv("NOUS_PORTAL_TAGS", raising=False)
    b = resolve_provider("nous")
    assert b.name == "nous"
    assert b.model == "stepfun/step-3.7-flash:free"


def test_provider_selected_via_env_var(monkeypatch):
    monkeypatch.setenv("RAZORPAY_AGENT_LLM_PROVIDER", "tencent")
    monkeypatch.setenv("TENCENT_HY3_API_KEY", "dummy")
    monkeypatch.setenv("TENCENT_HY3_BASE_URL", "https://example.invalid/v1")
    assert resolve_provider().name == "tencent"
