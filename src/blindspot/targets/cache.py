"""`target_responses_cache` (#27): a repeated prompt costs nothing.

Keyed by (target id, model id, prompt hash, temperature, max_tokens). The prompt hash covers the
system prompt and the user prompt, so `k` (which the system prompt states) is part of it. Rows hold
the prompt hash and the parsed response, never a key or a header. Lives in its own file under
`data/` (gitignored), WAL and a busy timeout as in `qloo/db.py`; parameterized SQL only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import TargetResponse

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_responses_cache (
  target_id TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  temperature REAL NOT NULL,
  max_tokens INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  response_json TEXT NOT NULL,
  scenario_id TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY (target_id, model, prompt_hash, temperature, max_tokens)
);
"""
# Outcomes worth replaying: a timeout or an HTTP error says nothing about the model, so it is retried.
CACHED_OUTCOMES = frozenset({"ok", "empty", "malformed", "refused"})


def prompt_hash(messages: list[dict[str, str]]) -> str:
    canonical = json.dumps(messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "ph_" + hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    """One connection guarded by a lock; every operation is small."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(SCHEMA)

    def get(
        self, target_id: str, model: str, phash: str, temperature: float, max_tokens: int
    ) -> TargetResponse | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT response_json FROM target_responses_cache WHERE target_id = ? AND model = ? "
                "AND prompt_hash = ? AND temperature = ? AND max_tokens = ?",
                (target_id, model, phash, temperature, max_tokens),
            ).fetchone()
        return TargetResponse.model_validate_json(row[0]) if row else None

    def put(
        self,
        resp: TargetResponse,
        model: str,
        temperature: float,
        max_tokens: int,
        scenario_id: str | None = None,
    ) -> bool:
        """Store a replayable answer. Returns False (and stores nothing) for timeouts and errors."""
        if resp.outcome not in CACHED_OUTCOMES:
            return False
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO target_responses_cache (target_id, model, prompt_hash, "
                "temperature, max_tokens, outcome, response_json, scenario_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resp.target_id,
                    model,
                    resp.prompt_hash,
                    temperature,
                    max_tokens,
                    resp.outcome,
                    resp.model_dump_json(),
                    scenario_id,
                    time.time(),
                ),
            )
        return True

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM target_responses_cache").fetchone()[0]

    def dump(self) -> list[str]:
        """Every stored row as text (tests check that no key is in it)."""
        with self.lock:
            rows = self.conn.execute("SELECT * FROM target_responses_cache").fetchall()
        return [json.dumps(list(r), ensure_ascii=False) for r in rows]

    def close(self) -> None:
        with self.lock:
            self.conn.close()


__all__ = ["CACHED_OUTCOMES", "ResponseCache", "prompt_hash"]
