"""Call log: one row per resolved Qloo request, whatever served it. Powers "Truth Receipts"."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from .cache import normalize_params
from .context import RequestContext
from .db import Database
from .redact import redact


class CallLog:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        ctx: RequestContext,
        endpoint: str,
        params: Mapping[str, Any] | None,
        served_from: str,
        *,
        status: int | None = None,
        latency_ms: float | None = None,
        result_count: int | None = None,
        error: str | None = None,
        cache_key: str | None = None,
        outcome: str | None = None,
        coalesced: bool = False,
        unverified_params: list[str] | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO calls(run_id,client_id,ts,endpoint,params_json,served_from,status,"
            "latency_ms,result_count,error,cache_key,outcome,purpose,coalesced,unverified_params)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ctx.run_id,
                ctx.client_id,
                time.time(),
                endpoint,
                redact(json.dumps(normalize_params(params))),
                served_from,
                status,
                None if latency_ms is None else round(latency_ms, 1),
                result_count,
                None if error is None else redact(error)[:500],
                cache_key,
                outcome,
                ctx.purpose,
                int(coalesced),
                ",".join(unverified_params) if unverified_params else None,
            ),
        )
        row_id = int(cur.lastrowid or 0)
        if row_id % 1000 == 0:  # long-lived hosts (keepalive) never restart to prune (#78)
            self.db.prune_calls()
        return row_id

    @staticmethod
    def _rows(rows) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d.pop("params_json"))
            out.append(d)
        return out

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self._rows(self.db.query("SELECT * FROM calls WHERE run_id=? ORDER BY id", (run_id,)))

    def last(self, n: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM calls ORDER BY id DESC LIMIT ?", (max(1, n),))
        return self._rows(rows)

    def count(self, served_from: str | None = None) -> int:
        if served_from:
            rows = self.db.query("SELECT COUNT(*) n FROM calls WHERE served_from=?", (served_from,))
        else:
            rows = self.db.query("SELECT COUNT(*) n FROM calls")
        return int(rows[0]["n"])
