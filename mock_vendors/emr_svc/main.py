from common.app import create_app
from common.store import Store

from .generator import SERVICE, VENDOR, generate_day

store = Store(SERVICE, generate_day)
app = create_app(
    service=SERVICE,
    vendor=VENDOR,
    resource="injuries",
    store=store,
    schema_version="emr.injuries.v1",
    description="Mock team EMR injury feed shaped like the NFL injury report. Synthetic data with deliberate defects; see mock_vendors/dirt_config.yaml.",
)
