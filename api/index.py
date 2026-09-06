"""Vercel serverless entry point for razorpay-agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Add src/ to path for Vercel's Python runtime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from mangum import Mangum

from razorpay_agent.server import build_serverless_app

_byok_store: dict[str, Any] = {}

try:
    _app, _, _ = build_serverless_app(byok_store=_byok_store)
    app = _app
    application = _app
    handler = Mangum(_app, lifespan="off")
except Exception as e:
    print(f"[ERROR] Failed to build app: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Serve static files from web/dist/ if they exist
DIST_DIR = Path(__file__).parent.parent / "web" / "dist"
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the React SPA index.html at root."""
    index_file = DIST_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(), status_code=200)
    return HTMLResponse(
        content="<h1>Common — Razorpay Agent</h1><p>API running.</p>",
        status_code=200,
    )


@app.get("/{full_path:path}")
async def serve_spa(full_path: str, request: Request):
    """Serve static files for all other paths, fallback to index.html for SPA routing."""
    # Don't intercept API routes
    if full_path.startswith("api/"):
        raise HTTPException(404, "API endpoint not found")

    # Try to serve the static file
    file_path = DIST_DIR / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))

    # Fallback to index.html for SPA routing
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

    return {"status": "ok", "provider": normalized, "key_masked": mask_key(api_key)}


@app.get("/api/settings/llm")
async def get_llm_settings() -> dict:
    provider = _byok_store.get(
        "provider", os.environ.get("RAZORPAY_AGENT_LLM_PROVIDER", "lazy")
    )
    api_key = _byok_store.get("api_key", "")
    return {
        "provider": provider,
        "key_masked": mask_key(api_key) if api_key else "stub (no key)",
        "has_key": bool(api_key),
    }


@app.delete("/api/settings/llm")
async def clear_llm_settings() -> dict:
    _byok_store.clear()
    for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "TENCENT_HY3_API_KEY",
    ]:
        os.environ.pop(key, None)
    return {"status": "cleared", "message": "Using stub backend"}