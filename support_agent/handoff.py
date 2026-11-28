"""Durable local support queue; only server-side code may write tickets."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .agent import Reply


class HandoffQueue:
    def __init__(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("Handoff database must not be a symlink")
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("CREATE TABLE IF NOT EXISTS handoffs ("
                       "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                       "reason TEXT NOT NULL, question TEXT NOT NULL, "
                       "order_id TEXT, sources TEXT NOT NULL, state TEXT NOT NULL, "
                       "customer_id TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(handoffs)")}
            if "customer_id" not in columns:
                db.execute("ALTER TABLE handoffs ADD COLUMN customer_id TEXT")
            db.commit()
        os.chmod(self.path, 0o600)

    def enqueue(self, reply: Reply, customer_id: str | None = None) -> str:
        if reply.outcome != "handoff" or not reply.handoff:
            raise ValueError("Only handoff replies can be queued")
        ticket_id = uuid.uuid4().hex
        item = reply.handoff
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("INSERT INTO handoffs "
                       "(id, created_at, reason, question, order_id, sources, state, customer_id) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (ticket_id, datetime.now(timezone.utc).isoformat(), reply.reason,
                        item["question"], item["order_id"],
                        json.dumps(reply.sources), "open", customer_id))
            db.commit()
        return ticket_id

    def list_open(self) -> list[dict]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM handoffs WHERE state = 'open' "
                              "ORDER BY created_at, id").fetchall()
        return [{**dict(row), "sources": json.loads(row["sources"])} for row in rows]

    def close(self, ticket_id: str) -> bool:
        with sqlite3.connect(self.path, timeout=5) as db:
            cursor = db.execute("UPDATE handoffs SET state = 'closed' "
                                "WHERE id = ? AND state = 'open'", (ticket_id,))
            db.commit()
        return cursor.rowcount == 1

    def ready(self) -> bool:
        try:
            with sqlite3.connect(self.path, timeout=2) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("SELECT 1 FROM handoffs LIMIT 1").fetchone()
                db.rollback()
            return True
        except sqlite3.Error:
            return False
