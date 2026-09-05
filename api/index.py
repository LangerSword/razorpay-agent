"""Vercel serverless entry point for razorpay-agent."""

from razorpay_agent.server import build_live_app

_app, _, _ = build_live_app()
app = _app
application = _app
handler = _app