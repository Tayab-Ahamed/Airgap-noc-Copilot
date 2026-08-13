"""SQLite persistence (stdlib only) for telemetry and alert history.

Replaces the in-memory buffer with durable storage so risk history and copilot
explanations survive restarts \u2014 needed for the '/api/alerts/history' endpoint and
for post-incident review. For higher write rates, swap for TimescaleDB; the
interface here is intentionally small.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with _LOCK, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    node TEXT NOT NULL,
                    predicted_issue TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    time_to_impact_s REAL,
                    explanation TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_node ON alerts(node);
                CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
                """
            )

    def record_alert(self, node: str, predicted_issue: str, confidence: float,
                     time_to_impact_s: float | None, explanation: str = "") -> None:
        with _LOCK, self._conn() as c:
            c.execute(
                "INSERT INTO alerts(ts,node,predicted_issue,confidence,time_to_impact_s,explanation)"
                " VALUES(?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), node, predicted_issue,
                 float(confidence), time_to_impact_s, explanation),
            )

    def history(self, limit: int = 50, node: str | None = None) -> list[dict]:
        q = "SELECT * FROM alerts"
        params: list = []
        if node:
            q += " WHERE node = ?"
            params.append(node)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with _LOCK, self._conn() as c:
            return [dict(r) for r in c.execute(q, params).fetchall()]
