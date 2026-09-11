from common.app import create_app
from common.store import Store

from .generator import SERVICE, VENDOR, generate_day

store = Store(SERVICE, generate_day)
app = create_app(
    service=SERVICE,
    vendor=VENDOR,
    resource="tests",
    store=store,
    schema_version="forcedecks.tests.v1",
    description="Mock VALD force plate export. Synthetic data with deliberate defects; see mock_vendors/dirt_config.yaml.",
)
