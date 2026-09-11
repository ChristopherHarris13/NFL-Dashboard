from common.app import create_app
from common.store import Store

from .generator import SERVICE, VENDOR, generate_day

store = Store(SERVICE, generate_day)
app = create_app(
    service=SERVICE,
    vendor=VENDOR,
    resource="sessions",
    store=store,
    schema_version="catapult.sessions.v1",
    description="Mock Catapult GPS load feed. Synthetic data with deliberate defects; see mock_vendors/dirt_config.yaml.",
)
