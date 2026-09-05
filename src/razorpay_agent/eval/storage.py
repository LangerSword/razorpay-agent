from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    seed INTEGER NOT NULL,
    n_sessions INTEGER NOT NULL,
    uplift_over_baseline REAL NOT NULL,
    gate_compliance_rate REAL NOT NULL,
    cumulative_regret REAL NOT NULL,
    honesty_note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_steps (
    run_id INTEGER NOT NULL REFERENCES eval_runs(id),
    step_index INTEGER NOT NULL,
    policy TEXT NOT NULL,
    session_category TEXT NOT NULL,
    cart_value_rupees REAL NOT NULL,
    arm_id TEXT,
    allowed INTEGER NOT NULL,
    reward REAL NOT NULL,
    chosen_expected_reward REAL NOT NULL,
    best_expected_reward REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_steps_run ON eval_steps(run_id, step_index);
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    item_category TEXT NOT NULL,
    cart_value_rupees REAL NOT NULL,
    buyer_allowance_rupees REAL NOT NULL,
    target_sku TEXT NOT NULL,
    arm_id TEXT,
    action_type TEXT,
    discount_percent REAL,
    bundle_price_rupees REAL,
    allowed_unmodified INTEGER,
    reward REAL
);
CREATE INDEX IF NOT EXISTS idx_decision_log_id ON decision_log(id);
CREATE TABLE IF NOT EXISTS buyer_accuracy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    discount_accuracy REAL NOT NULL,
    bundle_accuracy REAL NOT NULL,
    failures_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS merchant_reasoning_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    arm_identification_rate REAL NOT NULL,
    gate_awareness_rate REAL NOT NULL,
    limits_accuracy_rate REAL NOT NULL,
    verdict_accuracy_rate REAL NOT NULL,
    failures_json TEXT NOT NULL
);
"""


class EvalStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_run(
        self,
        seed: int,
        n_sessions: int,
        uplift_over_baseline: float,
        gate_compliance_rate: float,
        cumulative_regret: float,
        honesty_note: str,
        steps: list[dict],
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO eval_runs
                    (created_at, seed, n_sessions, uplift_over_baseline,
                     gate_compliance_rate, cumulative_regret, honesty_note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    seed,
                    n_sessions,
                    uplift_over_baseline,
                    gate_compliance_rate,
                    cumulative_regret,
                    honesty_note,
                ),
            )
            run_id = int(cursor.lastrowid)
            self._connection.executemany(
                """
                INSERT INTO eval_steps
                    (run_id, step_index, policy, session_category, cart_value_rupees,
                     arm_id, allowed, reward, chosen_expected_reward, best_expected_reward)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        step["step_index"],
                        step["policy"],
                        step["session_category"],
                        step["cart_value_rupees"],
                        step["arm_id"],
                        1 if step["allowed"] else 0,
                        step["reward"],
                        step["chosen_expected"],
                        step["best_expected"],
                    )
                    for step in steps
                ],
            )
            return run_id

    def latest_run_summary(self) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        summary = dict(row)
        summary["steps"] = self._steps(int(row["id"]))
        return summary

    def _steps(self, run_id: int) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM eval_steps WHERE run_id = ? ORDER BY step_index",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_decision(
        self,
        session_id: str,
        item_category: str,
        cart_value_rupees: float,
        buyer_allowance_rupees: float,
        target_sku: str,
        arm_id: str | None,
        action_type: str | None,
        discount_percent: float | None,
        bundle_price_rupees: float | None,
        allowed_unmodified: bool | None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO decision_log
                    (created_at, session_id, item_category, cart_value_rupees,
                     buyer_allowance_rupees, target_sku, arm_id, action_type,
                     discount_percent, bundle_price_rupees, allowed_unmodified, reward)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    session_id,
                    item_category,
                    cart_value_rupees,
                    buyer_allowance_rupees,
                    target_sku,
                    arm_id,
                    action_type,
                    discount_percent,
                    bundle_price_rupees,
                    None if allowed_unmodified is None else int(allowed_unmodified),
                    0.0 if arm_id is None else None,
                ),
            )
            return int(cursor.lastrowid)

    def resolve_decision_reward(self, session_id: str, reward: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE decision_log SET reward = ? "
                "WHERE session_id = ? AND reward IS NULL AND arm_id IS NOT NULL",
                (float(reward), session_id),
            )

    def logged_decisions(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM decision_log WHERE reward IS NOT NULL ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def logged_decision_count(self) -> int:
        with self._lock:
            (value,) = self._connection.execute(
                "SELECT COUNT(*) FROM decision_log WHERE reward IS NOT NULL"
            ).fetchone()
        return int(value)

    def record_buyer_accuracy_run(
        self,
        summary: "BuyerAccuracySummary",
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO buyer_accuracy_runs
                    (created_at, total, correct, accuracy,
                     discount_accuracy, bundle_accuracy, failures_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    summary.total,
                    summary.correct,
                    summary.accuracy,
                    summary.by_offer_type.get("discount", {}).get("accuracy", 0.0),
                    summary.by_offer_type.get("bundle", {}).get("accuracy", 0.0),
                    json.dumps([g.detail for g in summary.failures]),
                ),
            )
            return int(cursor.lastrowid)

    def latest_buyer_accuracy(self) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM buyer_accuracy_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def record_merchant_reasoning_run(
        self,
        summary: "MerchantReasoningSummary",
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO merchant_reasoning_runs
                    (created_at, total, correct, accuracy,
                     arm_identification_rate, gate_awareness_rate,
                     limits_accuracy_rate, verdict_accuracy_rate, failures_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    summary.total,
                    summary.correct,
                    summary.accuracy,
                    summary.arm_identification_rate,
                    summary.gate_awareness_rate,
                    summary.limits_accuracy_rate,
                    summary.verdict_accuracy_rate,
                    json.dumps([g.detail for g in summary.failures]),
                ),
            )
            return int(cursor.lastrowid)

    def latest_merchant_reasoning(self) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM merchant_reasoning_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(row)
