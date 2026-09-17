from fastapi import FastAPI

from muad_api import install_api_foundation
from muad_logging import configure_logging

from .api.router import router

SERVICE_NAME = "muad-console-platform"

configure_logging(SERVICE_NAME)
app = FastAPI(title="MUAD Console Platform", version="0.1.0")
catalog = install_api_foundation(app)
app.state.catalog = catalog
app.include_router(router)
