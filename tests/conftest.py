"""Shared pytest fixtures."""

import gc
import os
import sys
import threading
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.models import (
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SearchCriteria,
    SpaceAnalysis,
    TransportMode,
    ValueAnalysis,
)


def pytest_configure(config: pytest.Config) -> None:
    """Force line-buffered stdout when piped (e.g. Claude Code Bash tool)."""
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure") and not sys.stderr.isatty():
        sys.stderr.reconfigure(line_buffering=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply timeout exemptions for slow-marked tests."""
    for item in items:
        if item.get_closest_marker("slow"):
            item.add_marker(pytest.mark.timeout(120))


# Hypothesis settings profiles for different environments
settings.register_profile("fast", max_examples=10)
settings.register_profile(
    "ci",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "mutmut",
    max_examples=10,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.differing_executors],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "fast"))


@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the local .env file from leaking into test Settings instances."""
    monkeypatch.setattr(
        Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )


@pytest.fixture(autouse=True)
def _cleanup_aiosqlite_threads(request: pytest.FixtureRequest):
    """Safety net: detect and stop leaked aiosqlite worker threads.

    aiosqlite v0.22+ creates a non-daemon worker thread per connection that
    blocks on SimpleQueue.get() indefinitely.  If a test leaks a connection
    (doesn't call ``await conn.close()``), the thread prevents clean process exit.
    """
    yield

    # Browser tests run a uvicorn server in a daemon thread that holds its own
    # aiosqlite connection.  Killing it mid-session breaks all subsequent tests.
    if request.node.get_closest_marker("browser"):
        return

    from aiosqlite.core import _STOP_RUNNING_SENTINEL, Connection

    leaked = False

    # Strategy 1: find leaked Connection objects via gc, call stop()
    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, Connection) and obj._connection is not None:
            leaked = True
            obj.stop()

    # Strategy 2: inject sentinel directly for orphaned threads
    for thread in threading.enumerate():
        if "_connection_worker_thread" in (thread.name or "") and thread.is_alive():
            leaked = True
            tx = getattr(thread, "_args", (None,))[0]
            if tx is not None and hasattr(tx, "put_nowait"):
                tx.put_nowait((None, lambda: _STOP_RUNNING_SENTINEL))
                thread.join(timeout=1.0)

    if leaked:
        import warnings

        warnings.warn(
            "Test leaked aiosqlite connection(s) — add 'await storage.close()' to fixture teardown",
            ResourceWarning,
            stacklevel=1,
        )


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_property() -> Property:
    """A valid sample property for testing."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=HttpUrl("https://www.openrent.com/property/12345"),
        title="Spacious 1-bed flat in Hackney",
        price_pcm=1850,
        bedrooms=1,
        address="123 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description="A lovely flat with good transport links.",
        first_seen=datetime(2025, 1, 15, 10, 30),
    )


@pytest.fixture
def sample_property_no_coords() -> Property:
    """A valid sample property without coordinates."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="67890",
        url=HttpUrl("https://www.rightmove.co.uk/properties/67890"),
        title="2-bed apartment in Islington",
        price_pcm=2100,
        bedrooms=2,
        address="45 Upper Street, Islington, London",
        postcode="N1 0NY",
        first_seen=datetime(2025, 1, 16, 14, 0),
    )


@pytest.fixture
def default_search_criteria() -> SearchCriteria:
    """Default search criteria matching the plan requirements."""
    return SearchCriteria(
        min_price=1800,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        destination_postcode="N1 5AA",
        max_commute_minutes=30,
        transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT),
    )


@pytest.fixture
def enriched_merged_property() -> MergedProperty:
    """MergedProperty with gallery, floorplan, descriptions, multi-source."""
    openrent_url = HttpUrl("https://www.openrent.com/property/12345")
    zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/99999")

    canonical = Property(
        source=PropertySource.OPENRENT,
        source_id="12345",
        url=openrent_url,
        title="Spacious 2-bed flat in Hackney",
        price_pcm=1800,
        bedrooms=2,
        address="123 Mare Street, Hackney, London",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        description="A lovely 2-bed flat with good transport links.",
        first_seen=datetime(2025, 1, 15, 10, 30),
    )

    return MergedProperty(
        canonical=canonical,
        sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
        source_urls={
            PropertySource.OPENRENT: openrent_url,
            PropertySource.ZOOPLA: zoopla_url,
        },
        images=(
            PropertyImage(
                url=HttpUrl("https://example.com/img1.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img2.jpg"),
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
            PropertyImage(
                url=HttpUrl("https://example.com/img3.jpg"),
                source=PropertySource.ZOOPLA,
                image_type="gallery",
            ),
        ),
        floorplan=PropertyImage(
            url=HttpUrl("https://example.com/floorplan.jpg"),
            source=PropertySource.ZOOPLA,
            image_type="floorplan",
        ),
        min_price=1800,
        max_price=1850,
        descriptions={
            PropertySource.OPENRENT: "A lovely 2-bed flat with good transport links.",
            PropertySource.ZOOPLA: "Spacious two bedroom apartment near Mare Street.",
        },
    )


@pytest.fixture
def make_property() -> Callable[..., Property]:
    """Factory for Property instances with sensible defaults and auto-incrementing IDs."""
    _counter = 0

    def _make(
        source: PropertySource = PropertySource.OPENRENT,
        price_pcm: int = 1850,
        bedrooms: int = 1,
        postcode: str = "E8 3RH",
        latitude: float | None = 51.5465,
        longitude: float | None = -0.0553,
        address: str = "123 Mare Street, Hackney, London",
        **overrides: Any,
    ) -> Property:
        nonlocal _counter
        _counter += 1
        source_id = overrides.pop("source_id", f"test-{_counter}")
        url = overrides.pop("url", HttpUrl(f"https://example.com/{source.value}/{source_id}"))
        title = overrides.pop("title", f"Test Property {_counter}")
        defaults: dict[str, Any] = {
            "source": source,
            "source_id": source_id,
            "url": url,
            "title": title,
            "price_pcm": price_pcm,
            "bedrooms": bedrooms,
            "address": address,
            "postcode": postcode,
            "latitude": latitude,
            "longitude": longitude,
        }
        defaults.update(overrides)
        return Property(**defaults)

    return _make


@pytest.fixture
def make_merged_property(
    make_property: Callable[..., Property],
) -> Callable[..., MergedProperty]:
    """Factory for MergedProperty instances."""

    def _make(
        sources: tuple[PropertySource, ...] = (PropertySource.OPENRENT,),
        price_pcm: int = 1850,
        images: tuple[PropertyImage, ...] = (),
        floorplan: PropertyImage | None = None,
        descriptions: dict[PropertySource, str] | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        floor_area_sqm: float | None = None,
        floor_area_source: str | None = None,
        **property_overrides: Any,
    ) -> MergedProperty:
        canonical = make_property(source=sources[0], price_pcm=price_pcm, **property_overrides)
        source_urls = {sources[0]: canonical.url}
        for extra_source in sources[1:]:
            source_urls[extra_source] = HttpUrl(
                f"https://example.com/{extra_source.value}/{canonical.source_id}"
            )
        return MergedProperty(
            canonical=canonical,
            sources=sources,
            source_urls=source_urls,
            images=images,
            floorplan=floorplan,
            min_price=min_price if min_price is not None else price_pcm,
            max_price=max_price if max_price is not None else price_pcm,
            descriptions=descriptions or {},
            floor_area_sqm=floor_area_sqm,
            floor_area_source=floor_area_source,
        )

    return _make


@pytest.fixture
def make_quality_analysis() -> Callable[..., PropertyQualityAnalysis]:
    """Factory for PropertyQualityAnalysis instances with sensible defaults."""

    def _make(
        rating: int = 4,
        summary: str = "A nice flat.",
        **overrides: Any,
    ) -> PropertyQualityAnalysis:
        defaults: dict[str, Any] = {
            "kitchen": KitchenAnalysis(overall_quality="modern"),
            "condition": ConditionAnalysis(overall_condition="good"),
            "light_space": LightSpaceAnalysis(natural_light="good"),
            "space": SpaceAnalysis(living_room_sqm=20.0),
            "overall_rating": rating,
            "summary": summary,
        }
        defaults.update(overrides)
        return PropertyQualityAnalysis(**defaults)

    return _make


@pytest.fixture
def sample_quality_analysis() -> PropertyQualityAnalysis:
    """Complete PropertyQualityAnalysis for notification/DB tests."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(
            overall_quality="modern",
            hob_type="gas",
            has_dishwasher="yes",
            has_washing_machine="yes",
            notes="Modern integrated kitchen with gas hob",
        ),
        condition=ConditionAnalysis(
            overall_condition="good",
            has_visible_damp="no",
            has_visible_mold="no",
            has_worn_fixtures="no",
            maintenance_concerns=[],
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(
            natural_light="good",
            window_sizes="medium",
            feels_spacious=True,
            ceiling_height="standard",
            notes="Good natural light throughout",
        ),
        space=SpaceAnalysis(
            living_room_sqm=22.0,
            is_spacious_enough=True,
            hosting_layout="good",
        ),
        bedroom=BedroomAnalysis(
            primary_is_double="yes",
            office_separation="dedicated_room",
            can_fit_desk="yes",
        ),
        flooring_noise=FlooringNoiseAnalysis(
            hosting_noise_risk="low",
            has_double_glazing="yes",
        ),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2350,
            difference=-550,
            rating="excellent",
            note="£550 below E8 average",
            quality_adjusted_rating="excellent",
            quality_adjusted_note="Good condition at well below market rate",
        ),
        overall_rating=4,
        summary=(
            "Bright, well-maintained flat with modern kitchen. Good for home office and hosting."
        ),
    )


# ---------------------------------------------------------------------------
# Crawlee state isolation (used by integration + scraper location leakage tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_crawlee_state() -> Generator[None, None, None]:
    """Reset Crawlee's global state between tests.

    Crawlee caches service locators and storage clients that are bound to
    specific event loops. Without resetting this state, tests running with
    different event loops will fail with "attached to a different event loop".

    Not autouse — only tests that exercise real Crawlee scrapers need this.
    Use via @pytest.mark.usefixtures("reset_crawlee_state").
    """
    _clear_crawlee_caches()
    yield
    _clear_crawlee_caches()


def _clear_crawlee_caches() -> None:
    """Clear Crawlee's internal caches and state."""
    try:
        from crawlee._service_locator import service_locator

        service_locator._configuration = None
        service_locator._event_manager = None
        service_locator._storage_client = None
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.storages import KeyValueStore

        if hasattr(KeyValueStore, "_cache"):
            KeyValueStore._cache.clear()
        if hasattr(KeyValueStore, "_cache_by_id"):
            KeyValueStore._cache_by_id.clear()
        if hasattr(KeyValueStore, "_cache_by_name"):
            KeyValueStore._cache_by_name.clear()
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.statistics import Statistics

        if hasattr(Statistics, "_instance"):
            Statistics._instance = None
    except (ImportError, AttributeError):
        pass

    try:
        from crawlee.crawlers import BasicCrawler

        if hasattr(BasicCrawler, "_running_crawlers"):
            BasicCrawler._running_crawlers.clear()
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def set_crawlee_storage_dir(tmp_path: Path) -> Generator[None, None, None]:
    """Use a temporary directory for Crawlee storage during tests.

    Not autouse — only tests that exercise real Crawlee scrapers need this.
    Use via @pytest.mark.usefixtures("set_crawlee_storage_dir").
    """
    old_value = os.environ.get("CRAWLEE_STORAGE_DIR")
    os.environ["CRAWLEE_STORAGE_DIR"] = str(tmp_path / "crawlee_storage")
    yield
    if old_value is not None:
        os.environ["CRAWLEE_STORAGE_DIR"] = old_value
    else:
        os.environ.pop("CRAWLEE_STORAGE_DIR", None)
