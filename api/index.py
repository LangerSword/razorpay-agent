"""Vercel serverless entry point for razorpay-agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from razorpay_agent.server import build_serverless_app

_byok_store: dict[str, Any] = {}

# Provider name -> env prefix for API keys
PROVIDER_ENV_PREFIX = {
    "openai": "OPENAI",
    "anthropic": "ANTHROPIC",
    "nous": "NOUS_PORTAL",
    "tencent": "TENCENT_HY3",
    "custom": "OPENAI",
}

_app, _, _ = build_serverless_app(byok_store=_byok_store)
app = _app
application = _app

DIST_DIR = Path(__file__).parent.parent / "web" / "dist"
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(), status_code=200)
    return HTMLResponse(content="<h1>Common</h1><p>API running.</p>", status_code=200)


@app.get("/{full_path:path}")
async def serve_spa(full_path: str, request: Request):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    file_path = DIST_DIR / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(), status_code=200)
    raise HTTPException(404, "Not found")


def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


@app.post("/api/settings/llm")
async def set_llm_settings(request: Request) -> dict:
    body = await request.json()
    provider = body.get("provider", "openai")
    api_key = body.get("apiKey", "").strip()
    if not api_key:
        raise HTTPException(400, "apiKey required")
    provider_map = {"openai": "openai", "anthropic": "anthropic", "nous": "nous", "tencent": "tencent", "custom": "openai"}
    normalized = provider_map.get(provider, "openai")
    env_prefix = PROVIDER_ENV_PREFIX.get(normalized, normalized.upper())
    _byok_store["provider"] = normalized
    _byok_store["api_key"] = api_key
    _byok_store["base_url"] = body.get("baseUrl", "").strip()
    _byok_store["model"] = body.get("model", "").strip()
    os.environ[env_prefix + "_API_KEY"] = api_key
    os.environ["RAZORPAY_AGENT_LLM_PROVIDER"] = normalized
    if _byok_store["base_url"]:
        os.environ[env_prefix + "_BASE_URL"] = _byok_store["base_url"]
    if _byok_store["model"]:
        os.environ[env_prefix + "_MODEL"] = _byok_store["model"]
    return {"status": "ok", "provider": normalized, "key_masked": mask_key(api_key)}


@app.get("/api/settings/llm")
async def get_llm_settings() -> dict:
    provider = _byok_store.get("provider", os.environ.get("RAZORPAY_AGENT_LLM_PROVIDER", "lazy"))
    api_key = _byok_store.get("api_key", "")
    return {"provider": provider, "key_masked": mask_key(api_key) if api_key else "stub", "has_key": bool(api_key)}


@app.delete("/api/settings/llm")
async def clear_llm_settings() -> dict:
    _byok_store.clear()
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOUS_PORTAL_API_KEY", "TENCENT_HY3_API_KEY"]:
        os.environ.pop(key, None)
    return {"status": "cleared"}