from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reasoning_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    role TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reasoning_log_session ON reasoning_log(session_id);
"""


class ReasoningStore:
    """SQLite store for LLM reasoning traces (the ``reasoning_log`` side table).

    Kept separate from the core audit trail: LLM reasoning goes here only, never
    into the frozen ``ProposedAction`` / ``GateDecision`` / ``AuditEntry`` contract.
    """

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def append(
        self,
        session_id: str,
        step: int,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO reasoning_log
                    (timestamp, session_id, step, role, provider, model, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                    step,
                    role,
                    provider,
                    model,
                    content,
                ),
            )
            return int(cursor.lastrowid)

    def for_session(self, session_id: str) -> list[dict]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT step, role, provider, model, content FROM reasoning_log "
                "WHERE session_id = ? ORDER BY step ASC",
                (session_id,),
            ).fetchall()
        return [{k: row[k] for k in row.keys()} for row in rows]
