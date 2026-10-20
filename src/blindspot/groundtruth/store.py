"""SQLite storage for answer keys (#18). Lives under data/ (gitignored): it holds Qloo names.

One file, `data/groundtruth.sqlite` by default, with WAL and a busy timeout like the Qloo cache.
Columns added later go in MIGRATIONS and are applied idempotently, as in `qloo/db.py`. All SQL is
parameterized; the only f-strings build table and column names from the constants below.

Tables:
- `scenarios`: the exam as built (#16), for joins and status.
- `ground_truth_runs`: one fetched spec (a local list or a global baseline), keyed by the spec's
  content key, so a spec shared by several scenarios is fetched and stored once.
- `ground_truth_items`: the ranked results of a run.
- `entity_index`: every entity seen, with its normalized name and aliases (for #29's resolver).
- `scenario_status`: one row per scenario: the build checkpoint and the disposition.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
  id TEXT PRIMARY KEY,
  persona_id TEXT NOT NULL,
  city_id TEXT NOT NULL,
  domain_id TEXT NOT NULL,
  probe_type TEXT NOT NULL,
  local_key TEXT NOT NULL,
  global_key TEXT NOT NULL,
  depth INTEGER NOT NULL,
  pool INTEGER NOT NULL,
  pair_id TEXT,
  tags_json TEXT NOT NULL,
  conditions_json TEXT NOT NULL,
  spec_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ground_truth_runs (
  spec_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- local | global
  params_json TEXT NOT NULL,       -- what was sent (after a broader retry, the broader params)
  served_from TEXT NOT NULL,       -- how the client got it: live | cache | snapshot
  outcome TEXT NOT NULL,
  coverage TEXT NOT NULL,          -- rich | thin | sparse | empty | failed
  result_count INTEGER NOT NULL,
  broadened INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  fetched_at REAL
);
CREATE TABLE IF NOT EXISTS ground_truth_items (
  spec_key TEXT NOT NULL,
  rank INTEGER NOT NULL,
  qloo_id TEXT NOT NULL,
  affinity REAL,
  score_present INTEGER NOT NULL,
  PRIMARY KEY (spec_key, rank)
);
CREATE TABLE IF NOT EXISTS entity_index (
  qloo_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  norm_name TEXT NOT NULL,
  type TEXT,
  aliases_json TEXT NOT NULL,
  external_json TEXT,
  lat REAL,
  lon REAL,
  popularity REAL
);
CREATE INDEX IF NOT EXISTS entity_norm ON entity_index(norm_name);
CREATE TABLE IF NOT EXISTS scenario_status (
  scenario_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,            -- pending | done | thin | failed | excluded
  reason TEXT NOT NULL DEFAULT ''
);
"""
MIGRATIONS: dict[str, dict[str, str]] = {}  # table -> {column: declaration}, applied idempotently


class Store:
    """One connection guarded by a lock, like qloo.db.Database."""

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
            for table, cols in MIGRATIONS.items():
                have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def transaction(self, statements: list[tuple[str, tuple]]) -> None:
        """Apply several writes atomically: a run and its items land together or not at all."""
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                for sql, params in statements:
                    self.conn.execute(sql, params)
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ------------------------------------------------------------------ reads
    def run(self, spec_key: str) -> sqlite3.Row | None:
        rows = self.query("SELECT * FROM ground_truth_runs WHERE spec_key = ?", (spec_key,))
        return rows[0] if rows else None

    def items(self, spec_key: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM ground_truth_items WHERE spec_key = ? ORDER BY rank", (spec_key,))

    def status_counts(self) -> dict[str, int]:
        rows = self.query("SELECT status, COUNT(*) n FROM scenario_status GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def table_counts(self) -> dict[str, int]:
        names = ("scenarios", "ground_truth_runs", "ground_truth_items", "entity_index", "scenario_status")
        return {t: self.query(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in names}  # noqa: S608

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """Every table as sorted rows: for comparing two builds."""
        out = {}
        for t, order in (
            ("scenarios", "id"),
            ("ground_truth_runs", "spec_key"),
            ("ground_truth_items", "spec_key, rank"),
            ("entity_index", "qloo_id"),
            ("scenario_status", "scenario_id"),
        ):
            rows = self.query(f"SELECT * FROM {t} ORDER BY {order}")  # noqa: S608
            out[t] = [{k: r[k] for k in r.keys() if k != "fetched_at"} for r in rows]
        return out


def dumps(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
