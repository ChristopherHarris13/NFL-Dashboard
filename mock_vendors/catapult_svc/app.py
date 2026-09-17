from __future__ import annotations

from ..common.base_service import VendorApp, create_app
from ..common.settings import Settings
from .generator import SERVICE, CatapultGenerator


def build_vendor(settings: Settings | None = None) -> VendorApp:
    settings = settings or Settings.from_env(SERVICE, default_port=8001)
    return VendorApp(settings, CatapultGenerator(settings))


app = create_app(build_vendor())
