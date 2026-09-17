from fastapi import FastAPI

from muad_api import install_api_foundation
from muad_logging import configure_logging

from .api.delivery import router as delivery_router
from .api.health import router as health_router

SERVICE_NAME = "muad-im-gateway"

configure_logging(SERVICE_NAME)
app = FastAPI(title="MUAD IM Gateway", version="0.1.0")
catalog = install_api_foundation(app)
app.state.catalog = catalog
app.include_router(health_router)
app.include_router(delivery_router)
