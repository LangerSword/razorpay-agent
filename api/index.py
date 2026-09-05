from __future__ import annotations

import os
import sys
from typing import Any

# Add src/ to path for Vercel's Python runtime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi import HTTPException

from razorpay_agent.server import build_serverless_app

_byok_store: dict[str, dict] = {}

_app, _, _ = build_serverless_app(byok_store=_byok_store)
app = _app
application = _app
handler = _app


def mask_key(key: str) -> str:
    """Mask an API key for safe display/logging."""
    if not key or len(key) < 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


@app.post("/api/settings/llm")
async def set_llm_settings(request: Any) -> dict:
    from fastapi import Request as _Request
    if not isinstance(request, _Request):
        raise HTTPException(400, "invalid request")

    body = await request.json()
    provider = body.get("provider", "openai")
    api_key = body.get("apiKey", "").strip()
    base_url = body.get("baseUrl", "").strip()
    model = body.get("model", "").strip()

    if not api_key:
        raise HTTPException(400, "apiKey is required")

    provider_map = {
        "openai": "openai",
        "anthropic": "anthropic",
        "nous": "nous",
        "tencent": "tencent",
        "custom": "openai",
    }
    normalized = provider_map.get(provider, "openai")

    _byok_store["provider"] = normalized
    _byok_store["api_key"] = api_key
    _byok_store["base_url"] = base_url
    _byok_store["model"] = model

    os.environ[normalized.upper() + "_API_KEY"] = api_key
    if base_url:
        os.environ[normalized.upper() + "_BASE_URL"] = base_url
    if model:
        os.environ[normalized.upper() + "_MODEL"] = model

    return {
        "status": "ok",
        "provider": normalized,
        "key_masked": mask_key(api_key),
    }


@app.get("/api/settings/llm")
async def get_llm_settings() -> dict:
    provider = _byok_store.get("provider", os.environ.get("RAZORPAY_AGENT_LLM_PROVIDER", "stub"))
    api_key = _byok_store.get("api_key", "")
    return {
        "provider": provider,
        "key_masked": mask_key(api_key) if api_key else "stub (no key)",
        "has_key": bool(api_key),
    }


@app.delete("/api/settings/llm")
async def clear_llm_settings() -> dict:
    _byok_store.clear()
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOUS_PORTAL_API_KEY", "TENCENT_HY3_API_KEY"]:
        os.environ.pop(key, None)
    return {"status": "cleared", "message": "Using stub backend"}
