"""The dirt engine: seeded randomness plus probability rolls driven by dirt_config.yaml."""
from __future__ import annotations

import random
from datetime import date
from typing import Any, Sequence, TypeVar

from .config import load_config

T = TypeVar("T")


class Dirt:
    """One instance per (service, day). Every roll is reproducible from the global seed."""

    def __init__(self, service: str, day: date, salt: str = ""):
        cfg = load_config()
        self.service = service
        self.cfg: dict[str, Any] = dict(cfg.get(service, {}))
        self.identity_cfg: dict[str, Any] = cfg["identity"]
        self.timestamps_cfg: dict[str, Any] = cfg["timestamps"]
        self.rng = random.Random(f"{cfg['seed']}:{service}:{day.isoformat()}:{salt}")

    # --- probability rolls -------------------------------------------------
    def roll(self, key: str, default: float = 0.0) -> bool:
        """True with the probability named ``key`` in this service's config section."""
        return self.rng.random() < float(self.cfg.get(key, default))

    def roll_p(self, p: float) -> bool:
        return self.rng.random() < p

    # --- convenience wrappers ---------------------------------------------
    def choice(self, seq: Sequence[T]) -> T:
        return self.rng.choice(seq)

    def uniform(self, a: float, b: float) -> float:
        return self.rng.uniform(a, b)

    def gauss(self, mu: float, sigma: float, lo: float | None = None, hi: float | None = None) -> float:
        x = self.rng.gauss(mu, sigma)
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x

    def randint(self, a: int, b: int) -> int:
        return self.rng.randint(a, b)

    def int_id(self, prefix: str = "", width: int = 8) -> str:
        return f"{prefix}{self.rng.randrange(10 ** (width - 1), 10 ** width)}"

    def hex_id(self, n: int = 12) -> str:
        return "".join(self.rng.choice("0123456789abcdef") for _ in range(n))
