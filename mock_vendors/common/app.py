"""FastAPI app factory shared by the five mocks.

Routes:
  GET /health                      liveness + store stats
  GET /v1/meta                     vendor, resource, schema version, cursor bounds
  GET /v1/<resource>?since=&limit= incremental page; ``since`` is the ``next_cursor`` of the previous page
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Response

from .config import load_config
from .store import Store, utcnow


def create_app(
    *,
    service: str,
    vendor: str,
    resource: str,
    store: Store,
    schema_version: str | Callable[[], str],
    description: str = "",
) -> FastAPI:
    cfg = load_config()
    default_limit = int(cfg["page_size_default"])
    max_limit = int(cfg["page_size_max"])

    def current_schema_version() -> str:
        return schema_version() if callable(schema_version) else schema_version

    app = FastAPI(title=f"{vendor} (mock)", description=description, version="0.1.0")

    @app.on_event("startup")
    def _warm() -> None:
        store.refresh()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": service, "vendor": vendor, "time": utcnow().isoformat(), **store.stats()}

    @app.get("/v1/meta")
    def meta() -> dict[str, Any]:
        return {
            "service": service,
            "vendor": vendor,
            "resource": resource,
            "endpoint": f"/v1/{resource}",
            "schema_version": current_schema_version(),
            "cursor": "opaque integer; pass the previous page's next_cursor as ?since=",
            **store.stats(),
        }

    @app.get(f"/v1/{resource}")
    def list_resource(
        response: Response,
        since: str | None = Query(default=None, description="Cursor from the previous page's next_cursor (omit for the beginning)"),
        limit: int = Query(default=default_limit, ge=1, le=max_limit),
    ) -> dict[str, Any]:
        if since in (None, ""):
            cursor = 0
        else:
            try:
                cursor = int(since)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid cursor {since!r}") from exc
            if cursor < 0:
                raise HTTPException(status_code=400, detail="cursor must be >= 0")
        chunk, next_cursor, has_more = store.page(cursor, limit)
        version = current_schema_version()
        response.headers["X-Schema-Version"] = version
        response.headers["X-Next-Cursor"] = str(next_cursor)
        return {
            "data": [r.payload for r in chunk],
            "count": len(chunk),
            "next_cursor": str(next_cursor),
            "has_more": has_more,
            "schema_version": version,
            "served_at": utcnow().isoformat(),
        }

    return app
