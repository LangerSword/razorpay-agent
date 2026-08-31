"""Thin presentational storefront for the demo merchant.

The storefront is a visual layer over the existing ACP ``/products`` feed. It is
served by the same FastAPI app that exposes the agent surface, so it introduces no
second system, no separate credentials, and no external dependency beyond a CDN for
the React runtime (used only to render the page in a browser).
"""

from pathlib import Path

INDEX_HTML_PATH = Path(__file__).resolve().parent / "index.html"
