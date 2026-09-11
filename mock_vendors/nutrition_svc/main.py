from common.app import create_app
from common.store import Store

from .generator import SERVICE, VENDOR, generate_day

store = Store(SERVICE, generate_day)
app = create_app(
    service=SERVICE,
    vendor=VENDOR,
    resource="measurements",
    store=store,
    schema_version="notemeal.bodycomp.v1",
    description="Mock nutrition / body composition export. Synthetic data with deliberate defects; see mock_vendors/dirt_config.yaml.",
)
