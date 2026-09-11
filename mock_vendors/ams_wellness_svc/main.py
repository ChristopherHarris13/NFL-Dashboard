from common.app import create_app
from common.store import Store

from .generator import SERVICE, VENDOR, current_form_version, generate_day

store = Store(SERVICE, generate_day)
app = create_app(
    service=SERVICE,
    vendor=VENDOR,
    resource="surveys",
    store=store,
    schema_version=lambda: f"ams.wellness.{current_form_version()}",
    description="Mock athlete-management wellness survey feed. Synthetic data with deliberate defects; see mock_vendors/dirt_config.yaml.",
)
