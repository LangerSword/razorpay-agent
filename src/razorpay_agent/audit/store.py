from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

from razorpay_agent.core.audit import AuditEntry, AuditOutcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    proposal_source TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entries_session ON audit_entries(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_entries_timestamp ON audit_entries(timestamp);
"""


class AuditStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> AuditStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def append(self, entry: AuditEntry) -> int:
        record = entry.to_dict()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO audit_entries
                    (timestamp, session_id, action_type, outcome_status,
                     proposal_source, allowed, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["timestamp"],
                    entry.session_id,
                    entry.proposed_action.action_type,
                    entry.outcome.status,
                    entry.proposed_action.source,
                    1 if entry.gate_decision.allowed else 0,
                    json.dumps(record),
                ),
            )
            return int(cursor.lastrowid)

    async def aappend(self, entry: AuditEntry) -> int:
        return await asyncio.to_thread(self.append, entry)

    def update_outcome(self, entry_id: int, status: str, detail: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload FROM audit_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no audit entry with id {entry_id}")
            record = json.loads(row["payload"])
            record["outcome"] = AuditOutcome(status, detail).to_dict()
            self._connection.execute(
                "UPDATE audit_entries SET outcome_status = ?, payload = ? WHERE id = ?",
                (status, json.dumps(record), entry_id),
            )

    def _select_payloads(self, query: str, parameters: tuple) -> list[AuditEntry]:
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [AuditEntry.from_dict(json.loads(row["payload"])) for row in rows]

    def get_by_session(self, session_id: str) -> list[AuditEntry]:
        return self._select_payloads(
            "SELECT payload FROM audit_entries WHERE session_id = ? ORDER BY id",
            (session_id,),
        )

    def recent(self, limit: int = 100) -> list[AuditEntry]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        entries = self._select_payloads(
            "SELECT payload FROM audit_entries ORDER BY id DESC LIMIT ?", (limit,)
        )
        return list(reversed(entries))

    def iter_all(self) -> Iterator[AuditEntry]:
        yield from self._select_payloads("SELECT payload FROM audit_entries ORDER BY id", ())

    def count(self) -> int:
        with self._lock:
            (value,) = self._connection.execute("SELECT COUNT(*) FROM audit_entries").fetchone()
        return int(value)
