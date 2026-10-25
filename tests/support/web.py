"""Build a route test client around the supplied database."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.web.routes import router


def web_client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = Settings(_env_file=None, database_path=storage.db_path)
    app.include_router(router)
    return TestClient(app)
