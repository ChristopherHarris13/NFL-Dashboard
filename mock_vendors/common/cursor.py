"""Opaque pagination cursor encoding (emitted_at, record_seq).

The stream is ordered by EMISSION time, not event time: a record emitted
late lands at the end of the stream even though its event timestamp is old.
"""

from __future__ import annotations

import base64
import json


def encode(emitted_at: str, seq: int) -> str:
    payload = json.dumps({"e": emitted_at, "s": seq}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode(cursor: str) -> tuple[str, int]:
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    return payload["e"], int(payload["s"])
