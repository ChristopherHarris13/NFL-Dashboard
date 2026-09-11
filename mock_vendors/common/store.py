"""In-memory record store with arrival-ordered ``since`` cursors.

Every record has an *event* time (when the thing happened) and an *available*
time (when the vendor's API would first return it). Records become visible
in ``available_at`` order and get a monotonically increasing ``seq`` at that
moment. ``GET /v1/<resource>?since=<seq>`` returns everything that became
visible after that cursor, so a late-arriving session with an old event time
still shows up on the next incremental pull -- exactly the case the
pipeline's event-time watermark has to handle.

The store back-fills ``history_days`` on first use and then generates each
new day lazily, so the mocks keep producing data for as long as they run.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .config import load_config


@dataclass
class Record:
    payload: dict[str, Any]
    event_ts: datetime      # tz-aware UTC
    available_at: datetime  # tz-aware UTC, >= event_ts
    seq: int = field(default=0)


DayGenerator = Callable[[date], list[Record]]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, service: str, generate_day: DayGenerator, history_days: int | None = None):
        cfg = load_config()
        self.service = service
        self._generate_day = generate_day
        self._history_days = int(history_days if history_days is not None else cfg["history_days"])
        self._lock = threading.Lock()
        self._visible: list[Record] = []
        self._pending: list[Record] = []
        self._generated_through: date | None = None
        self._next_seq = 1

    # --- lifecycle -----------------------------------------------------------
    def refresh(self, now: datetime | None = None) -> None:
        """Generate any days not yet generated and release records whose time has come."""
        now = now or utcnow()
        today = now.date()
        with self._lock:
            if self._generated_through is None:
                start = today - timedelta(days=self._history_days)
                day = start
            else:
                day = self._generated_through + timedelta(days=1)
            while day <= today:
                self._pending.extend(self._generate_day(day))
                self._generated_through = day
                day += timedelta(days=1)
            self._release(now)

    def _release(self, now: datetime) -> None:
        ready = [r for r in self._pending if r.available_at <= now]
        if not ready:
            return
        self._pending = [r for r in self._pending if r.available_at > now]
        ready.sort(key=lambda r: (r.available_at, r.event_ts))
        for r in ready:
            r.seq = self._next_seq
            self._next_seq += 1
            self._visible.append(r)

    # --- reads ----------------------------------------------------------------
    def page(self, since: int, limit: int) -> tuple[list[Record], int, bool]:
        self.refresh()
        with self._lock:
            # _visible is sorted by seq; find the first record with seq > since
            lo, hi = 0, len(self._visible)
            while lo < hi:
                mid = (lo + hi) // 2
                if self._visible[mid].seq <= since:
                    lo = mid + 1
                else:
                    hi = mid
            chunk = self._visible[lo : lo + limit]
            has_more = lo + limit < len(self._visible)
            next_cursor = chunk[-1].seq if chunk else since
            return chunk, next_cursor, has_more

    def stats(self) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            return {
                "records_visible": len(self._visible),
                "records_pending": len(self._pending),
                "latest_cursor": self._next_seq - 1,
                "generated_through": self._generated_through.isoformat() if self._generated_through else None,
            }
