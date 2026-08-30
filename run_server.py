import uvicorn

from razorpay_agent.server import build_live_app


def main() -> None:
    app, _, is_live = build_live_app()
    mode = "LIVE Razorpay test-mode settlement" if is_live else "SCRIPTED payment provider"
    print(f"[razorpay-agent] serving on http://127.0.0.1:8000 ({mode})")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
