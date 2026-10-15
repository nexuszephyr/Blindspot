"""Live-call budgets at four scopes: lifetime, rolling-daily (with a demo reserve),
per-client hourly, and per-run.

Every live HTTP attempt (retries included) reserves one unit *before* it is sent, inside
one lock, so concurrent callers can never overshoot a cap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from .context import RequestContext
from .db import Database
from .errors import BudgetExceeded

DAY, HOUR = 86400.0, 3600.0
# Local scripts and the CLI (client ids "local:*") skip the per-client and per-run caps,
# which exist to keep anonymous web users apart. Lifetime and daily caps still apply.
LOCAL_PREFIX = "local:"


@dataclass
class Caps:
    lifetime: int
    daily: int
    adhoc_daily: int
    client_hourly: int
    run: int

    @classmethod
    def from_settings(cls, s: Settings) -> Caps:
        return cls(
            lifetime=s.qloo_max_live_calls,
            daily=s.qloo_daily_live_cap,
            adhoc_daily=s.adhoc_daily_cap,
            client_hourly=s.qloo_client_hourly_cap,
            run=s.qloo_run_cap,
        )


class BudgetManager:
    def __init__(self, db: Database, caps: Caps, clock=time.time):
        self.db = db
        self.caps = caps
        self.clock = clock

    def _n(self, where: str = "", params: tuple = ()) -> int:
        # `where` is always an internal constant; values are bound parameters.
        sql = "SELECT COUNT(*) n FROM live_attempts" + (f" WHERE {where}" if where else "")  # noqa: S608
        return int(self.db.query(sql, params)[0]["n"])

    def reserve(self, ctx: RequestContext) -> None:
        """Reserve one live attempt or raise BudgetExceeded naming the scope that is full."""
        now = self.clock()
        c = self.caps
        with self.db.lock:
            if self._n() >= c.lifetime:
                raise BudgetExceeded("lifetime live-call budget is used up", scope="lifetime")
            daily_used = self._n("ts > ?", (now - DAY,))
            if ctx.priority == "demo":
                if daily_used >= c.daily:
                    raise BudgetExceeded("daily live-call budget is used up", scope="daily")
            else:
                adhoc_used = self._n("ts > ? AND priority = 'adhoc'", (now - DAY,))
                # Ad-hoc traffic may use the daily cap minus the demo reserve, and never the
                # reserve itself, even if demo calls have not consumed it yet.
                if adhoc_used >= c.adhoc_daily or daily_used >= c.daily:
                    raise BudgetExceeded(
                        "daily budget for ad-hoc queries is used up (demo reserve kept)",
                        scope="daily",
                    )
            if not ctx.client_id.startswith(LOCAL_PREFIX):
                if self._n("client_id = ? AND ts > ?", (ctx.client_id, now - HOUR)) >= c.client_hourly:
                    raise BudgetExceeded(
                        "you have reached the hourly live-query limit; try again later",
                        scope="client",
                    )
                if self._n("run_id = ?", (ctx.run_id,)) >= c.run:
                    raise BudgetExceeded("this run has reached its live-query limit", scope="run")
            self.db.execute(
                "INSERT INTO live_attempts(ts,client_id,run_id,priority,purpose) VALUES(?,?,?,?,?)",
                (now, ctx.client_id, ctx.run_id, ctx.priority, ctx.purpose),
            )

    def remaining(self, client_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        now = self.clock()
        c = self.caps
        with self.db.lock:
            total = self._n()
            daily = self._n("ts > ?", (now - DAY,))
            adhoc = self._n("ts > ? AND priority = 'adhoc'", (now - DAY,))
            out: dict[str, Any] = {
                "lifetime": {"cap": c.lifetime, "used": total, "remaining": max(0, c.lifetime - total)},
                "daily": {"cap": c.daily, "used": daily, "remaining": max(0, c.daily - daily)},
                "daily_adhoc": {
                    "cap": c.adhoc_daily,
                    "used": adhoc,
                    "remaining": max(0, min(c.adhoc_daily - adhoc, c.daily - daily)),
                },
                "demo_reserve": c.daily - c.adhoc_daily,
                "client_hourly_cap": c.client_hourly,
                "run_cap": c.run,
            }
            if client_id:
                used = self._n("client_id = ? AND ts > ?", (client_id, now - HOUR))
                out["client_hourly"] = {
                    "cap": c.client_hourly,
                    "used": used,
                    "remaining": max(0, c.client_hourly - used),
                }
            if run_id:
                used = self._n("run_id = ?", (run_id,))
                out["run"] = {"cap": c.run, "used": used, "remaining": max(0, c.run - used)}
            return out

    def by_purpose(self) -> dict[str, int]:
        """Live attempts (real upstream requests) broken down by purpose."""
        rows = self.db.query("SELECT COALESCE(purpose,'app') p, COUNT(*) n FROM live_attempts GROUP BY 1")
        return {r["p"]: r["n"] for r in rows}

    def total_used(self) -> int:
        return self._n()
