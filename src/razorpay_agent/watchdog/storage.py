from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    component TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class SystemEventStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record(self, component: str, event_type: str, detail: dict | str) -> int:
        payload = detail if isinstance(detail, str) else json.dumps(detail)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO system_events (timestamp, component, event_type, detail) "
                "VALUES (?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    component,
                    event_type,
                    payload,
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 20, component: str | None = None) -> list[dict]:
        query = "SELECT * FROM system_events"
        parameters: tuple = ()
        if component is not None:
            query += " WHERE component = ?"
            parameters = (component,)
        query += " ORDER BY id DESC LIMIT ?"
        parameters = parameters + (limit,)
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]
