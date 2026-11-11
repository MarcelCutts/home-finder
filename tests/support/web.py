"""Build a route test client around the supplied database."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.web.routes import router


def web_client(storage, data_dir: Path | None = None):
    """Serve ``storage``; routes look for cached images under ``data_dir``.

    An in-memory storage has no directory of its own, so pass ``tmp_path``.
    """
    database_path = storage.db_path if data_dir is None else str(data_dir / "home_finder.db")
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = Settings(_env_file=None, database_path=database_path)
    app.include_router(router)
    return TestClient(app)
