from __future__ import annotations

from ..common.base_service import VendorApp, create_app
from ..common.settings import Settings
from .generator import SERVICE, WellnessGenerator, schema_version


def build_vendor(settings: Settings | None = None) -> VendorApp:
    settings = settings or Settings.from_env(SERVICE, default_port=8003)
    return VendorApp(settings, WellnessGenerator(settings), schema_version_fn=schema_version)


app = create_app(build_vendor())
