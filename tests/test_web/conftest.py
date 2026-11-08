"""Fixtures for the web dashboard tests.

The route fixtures mount only the dashboard router on an in-memory database. The
Playwright fixtures and collection hook live in ``browser/conftest.py``, which pytest
loads only when it collects that directory.
"""

import logging
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
)
from home_finder.web import app as web_app
from home_finder.web.routes import router


@pytest.fixture(autouse=True)
def _isolate_app_logging(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """App creation must not retain a captured stream or change later test logging."""
    previous = structlog.get_config().copy()
    configure = web_app.configure_logging

    def configure_for_test(*, json_output: bool = False, level: int = logging.INFO) -> None:
        configure(json_output=json_output, level=level)
        # Keep real app setup, but do not cache loggers bound to pytest's
        # temporary stderr; restoring global configuration cannot unbind them.
        structlog.configure(cache_logger_on_first_use=False)

    monkeypatch.setattr(web_app, "configure_logging", configure_for_test)
    try:
        yield
    finally:
        structlog.configure(**previous)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Routes read storage from app state; database_path only places data_dir (the image cache)."""
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        search_areas="e8,e3,n16",
        database_path=str(tmp_path / "properties.db"),
    )


@pytest.fixture
async def storage() -> AsyncGenerator[PropertyStorage]:
    async with PropertyStorage(":memory:") as storage:
        yield storage


@pytest.fixture
def app(storage: PropertyStorage, settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = settings
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def prop_a() -> Property:
    """A one-bed in E8 at £1,900, matched to the Hackney Central micro-area by address."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="100",
        url=HttpUrl("https://openrent.com/100"),
        title="1 bed in E8",
        price_pcm=1900,
        bedrooms=1,
        address="10 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def merged_a(prop_a: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_a,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
        descriptions={PropertySource.OPENRENT: "A nice flat."},
    )


@pytest.fixture
def prop_b() -> Property:
    """A two-bed in N16 at £2,500."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="200",
        url=HttpUrl("https://rightmove.co.uk/200"),
        title="2 bed in N16",
        price_pcm=2500,
        bedrooms=2,
        address="5 Church Street",
        postcode="N16 0AP",
        latitude=51.5615,
        longitude=-0.0750,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def merged_b(prop_b: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_b,
        sources=(PropertySource.RIGHTMOVE,),
        source_urls={PropertySource.RIGHTMOVE: prop_b.url},
        min_price=2500,
        max_price=2500,
        descriptions={PropertySource.RIGHTMOVE: "Spacious flat."},
    )


@pytest.fixture
def studio_prop() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="300",
        url=HttpUrl("https://openrent.com/300"),
        title="Studio in E8",
        price_pcm=1500,
        bedrooms=0,
        address="15 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def merged_studio(studio_prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=studio_prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: studio_prop.url},
        min_price=1500,
        max_price=1500,
    )


@pytest.fixture
def base_analysis() -> PropertyQualityAnalysis:
    """Reusable quality analysis — tests override fields via model_copy(update={...})."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True),
        overall_rating=4,
        summary="Nice place.",
    )
