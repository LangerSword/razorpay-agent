from pathlib import Path

INDEX_HTML_PATH = Path(__file__).resolve().parent / "index.html"

# React build path (used when available)
REACT_BUILD_PATH = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist" / "index.html"
