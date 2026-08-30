from __future__ import annotations

import re
import sys
from pathlib import Path

# The brief bar: every money action explainable/bounded/gated, show the audit
# trail, and ONE graceful failure handled end-to-end. The full demo walks all
# of that; this script asserts the generated summary actually shows it.

EXPECTATIONS = {
    "A accept flow": {"completed"},
    "B gate-cap moment": {"completed"},
    "C graceful failure": {"not_ready_for_payment"},
    "D live settlement": {"completed", "D live settlement not available here"},
}


def latest_summary(root: Path = Path("demo/out")) -> Path | None:
    summaries = sorted(root.glob("*_*/summary.md")) + sorted(root.glob("*/summary.md"))
    return summaries[-1] if summaries else None


def parse_summary(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if not m:
            continue
        label, rest = m.group(1), m.group(2)
        if label in EXPECTATIONS:
            status = rest.split("(")[0].strip()
            rows[label] = status
    return rows


def main() -> int:
    path = latest_summary()
    if path is None:
        print("[verify] no demo summary.md found under demo/out/ — run a demo first")
        return 1
    print(f"[verify] checking {path}")
    rows = parse_summary(path)
    failed = False
    for label, allowed in EXPECTATIONS.items():
        got = rows.get(label, "<missing>")
        ok = any(got == a or got.startswith(a) for a in allowed)
        flag = "PASS" if ok else "FAIL"
        if not ok:
            failed = True
        print(f"  [{flag}] {label:<20} -> {got!r} (expected one of {sorted(allowed)})")
    if failed:
        print("[verify] demo did not satisfy the brief bar")
        return 1
    print("[verify] demo satisfies the brief bar (accept / gate-cap / graceful failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
