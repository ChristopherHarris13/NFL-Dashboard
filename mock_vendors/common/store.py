"""In-memory emission stream with cursor pagination."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import cursor as cursor_mod


@dataclass
class Emitted:
    seq: int
    emitted_at: str
    record: dict


@dataclass
class Stream:
    records: list[Emitted] = field(default_factory=list)

    def append(self, emitted_at: str, record: dict) -> None:
        self.records.append(Emitted(len(self.records) + 1, emitted_at, record))

    def page(self, since: str | None, limit: int) -> tuple[list[Emitted], str | None]:
        start_seq = 0
        if since:
            _, start_seq = cursor_mod.decode(since)
        # seq is assigned in emission order, so this is an emission-time scan.
        window = [r for r in self.records if r.seq > start_seq][:limit]
        if not window:
            return [], None
        last = window[-1]
        return window, cursor_mod.encode(last.emitted_at, last.seq)

    def __len__(self) -> int:
        return len(self.records)
