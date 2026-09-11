from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "dirt_config.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = Path(os.environ.get("MOCK_DIRT_CONFIG", DEFAULT_CONFIG_PATH))
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("seed", 0)
    cfg.setdefault("history_days", 60)
    cfg.setdefault("page_size_default", 100)
    cfg.setdefault("page_size_max", 500)
    cfg.setdefault("identity", {})
    cfg.setdefault("timestamps", {})
    return cfg


def service_config(name: str) -> dict[str, Any]:
    cfg = load_config()
    return dict(cfg.get(name, {}))
