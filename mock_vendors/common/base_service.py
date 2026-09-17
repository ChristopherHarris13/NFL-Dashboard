"""Shared FastAPI scaffolding for the five mock vendors.

Each service supplies a DayGenerator that turns one simulated day into
records. The base handles: backfill on boot, the background clock loop,
late-arrival release (records generated on day D but emitted on day D+k),
cursor pagination, and /health.

Generation is deterministic per (GLOBAL_SEED, day), and generate_day is
always called sequentially from day 0, so a restarted container rebuilds an
identical stream.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Callable, Protocol

from fastapi import FastAPI, HTTPException, Query

from .cursor import decode as decode_cursor
from .settings import Settings
from .sim_clock import SimClock
from .store import Stream


@dataclass(order=True)
class Pending:
    emit_day: int
    sort_key: tuple = field(compare=False)
    resource: str = field(compare=False)
    record: dict = field(compare=False)


class DayGenerator(Protocol):
    resources: list[str]

    def generate_day(self, day_index: int, d: date) -> list[Pending]: ...


class VendorApp:
    def __init__(self, settings: Settings, generator: DayGenerator,
                 schema_version_fn: Callable[["VendorApp"], str] | None = None):
        self.settings = settings
        self.generator = generator
        self.clock = SimClock(settings)
        self.streams: dict[str, Stream] = {r: Stream() for r in generator.resources}
        self.pending: list[Pending] = []
        self.generated_through = -1
        self.schema_version_fn = schema_version_fn or (lambda app: "v1")

    # ------------------------------------------------------------- sim time

    def current_sim_date(self) -> date:
        return self.clock.date_for(max(self.generated_through, 0))

    def advance_to(self, day_index: int) -> None:
        for d in range(self.generated_through + 1, day_index + 1):
            sim_date = self.clock.date_for(d)
            self.pending.extend(self.generator.generate_day(d, sim_date))
            due = [p for p in self.pending if p.emit_day <= d]
            self.pending = [p for p in self.pending if p.emit_day > d]
            for resource in self.streams:
                batch = sorted(
                    (p for p in due if p.resource == resource),
                    key=lambda p: p.sort_key,
                )
                for i, p in enumerate(batch):
                    emitted_at = datetime.combine(
                        sim_date, time(8, 0, 0), tzinfo=timezone.utc
                    ).replace(second=0)
                    emitted_at = emitted_at.timestamp() + i
                    stamp = datetime.fromtimestamp(emitted_at, tz=timezone.utc)
                    self.streams[resource].append(
                        stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), p.record
                    )
            self.generated_through = d

    def catch_up(self) -> None:
        self.advance_to(self.clock.current_day_index())

    @property
    def total_records(self) -> int:
        return sum(len(s) for s in self.streams.values())


def create_app(vendor: VendorApp) -> FastAPI:
    settings = vendor.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vendor.catch_up()
        task = None
        if settings.run_background_loop:
            task = asyncio.create_task(_clock_loop(vendor))
        yield
        if task:
            task.cancel()

    app = FastAPI(title=f"GridironOps {settings.service_name}", lifespan=lifespan)
    app.state.vendor = vendor

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": settings.service_name,
            "records": vendor.total_records,
        }

    for resource in vendor.generator.resources:
        _add_resource_route(app, vendor, resource)

    return app


def _add_resource_route(app: FastAPI, vendor: VendorApp, resource: str) -> None:
    @app.get(f"/v1/{resource}")
    def read_resource(
        since: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        if since is not None:
            try:
                decode_cursor(since)
            except Exception:
                raise HTTPException(status_code=400, detail="invalid cursor")
        records, next_cursor = vendor.streams[resource].page(since, limit)
        return {
            "data": [r.record for r in records],
            "next_cursor": next_cursor,
            "schema_version": vendor.schema_version_fn(vendor),
        }


async def _clock_loop(vendor: VendorApp) -> None:
    poll = max(1.0, min(10.0, vendor.settings.gen_interval_seconds / 6))
    while True:
        await asyncio.sleep(poll)
        target = vendor.clock.current_day_index()
        if target > vendor.generated_through:
            vendor.advance_to(target)
