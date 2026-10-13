"""Shared SQLite connection for cache, call log and budget counters."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  params_json TEXT NOT NULL,
  status INTEGER NOT NULL,
  body TEXT NOT NULL,
  fetched_at REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'live'
);
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  client_id TEXT,
  ts REAL NOT NULL,
  endpoint TEXT NOT NULL,
  params_json TEXT NOT NULL,
  served_from TEXT NOT NULL,
  status INTEGER,
  latency_ms REAL,
  result_count INTEGER,
  error TEXT,
  cache_key TEXT
);
CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE TABLE IF NOT EXISTS live_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  client_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  priority TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_ts ON live_attempts(ts);
"""


class Database:
    """One connection guarded by a lock. Every operation is small, so contention is negligible."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self.lock:
            self.conn.close()
