"""Shared pytest fixtures."""

import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
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
    SpaceAnalysis,
    ValueAnalysis,
)

pytest_plugins = [
    "tests.plugins.aiosqlite_leaks",
    "tests.plugins.schema_template",
    "tests.support.pytest_profile",
]


def pytest_configure(config: pytest.Config) -> None:
    """Force line-buffered stdout when piped (e.g. Claude Code Bash tool)."""
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure") and not sys.stderr.isatty():
        sys.stderr.reconfigure(line_buffering=True)
    _render_logged_exceptions_plainly()


def _render_logged_exceptions_plainly() -> None:
    """Keep structlog's default console output, but format tracebacks as plain text.

    With ``rich`` installed (a dev dependency of inline-snapshot), structlog's
    default renderer highlights the source and the locals of every frame for
    each logged exception. Tests that log provider failures spent most of their
    time there, rendering output that pytest captures and usually discards.
    """
    renderer = structlog.get_config()["processors"][-1]
    if isinstance(renderer, structlog.dev.ConsoleRenderer):
        renderer.exception_formatter = structlog.dev.plain_traceback


def _is_xdist_active(config: pytest.Config) -> bool:
    """Detect whether xdist is distributing tests (controller or worker)."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return True
    num_workers = config.getoption("numprocesses", default=0)
    return bool(num_workers) and str(num_workers) != "0"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the browser and live-network test rules wherever those tests live.

    Browser tests need real TCP sockets (a server plus Chromium), so they get
    pytest-socket's ``enable_socket``. pytest-timeout's SIGALRM corrupts Playwright's
    connection state and deadlocks teardown, so they rely on Playwright's own
    timeouts instead. Under xdist they share one worker, and so one session server.

    Slow tests check live services, so they get real sockets too (``--disable-socket``
    in addopts would otherwise block the Python transports, e.g. Rightmove's httpx),
    and 120 seconds unless they set their own timeout. A marker added to the item
    takes precedence over class and module markers, so adding it unconditionally
    would cut an explicit longer timeout short.
    """
    xdist_active = _is_xdist_active(config)
    for item in items:
        if item.get_closest_marker("browser"):
            item.add_marker(pytest.mark.enable_socket)
            item.add_marker(pytest.mark.timeout(0))
            if xdist_active:
                item.add_marker(pytest.mark.xdist_group(name="browser"))
        if item.get_closest_marker("slow"):
            item.add_marker(pytest.mark.enable_socket)
            if item.get_closest_marker("timeout") is None:
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
def _isolate_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests opt into configuration; neither a shell export nor .env supplies it."""
    for name in os.environ:
        if name.upper().startswith("HOME_FINDER_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(
        Settings,
        "model_config",
        {**Settings.model_config, "env_file": None},
    )


@pytest.fixture(autouse=True)
def _keep_data_out_of_the_repository(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail a test that reads a ``Settings.data_dir`` inside the repository.

    Settings derives ``data_dir`` (the image cache) from ``database_path`` and ignores
    a ``data_dir=`` keyword, so ``database_path=":memory:"`` resolves to the working
    directory. The test would then share its cache with every other xdist worker and
    with the checkout's real one; a test once flaked that way. Give Settings a
    database path under ``tmp_path``.
    """
    if request.node.get_closest_marker("repository_data_dir"):
        return
    root = request.config.rootpath.resolve()
    derive = Settings.data_dir.fget
    assert derive is not None

    def data_dir(self: Settings) -> str:
        value = derive(self)
        resolved = Path(value).resolve()
        if resolved == root or root in resolved.parents:
            # pytest.fail raises a BaseException, so the application's broad
            # ``except Exception`` handlers cannot swallow it and let the test pass.
            pytest.fail(
                f"Settings.data_dir resolves to {resolved}, inside the repository. Give "
                'Settings a database_path under tmp_path, e.g. str(tmp_path / "home_finder.db").'
            )
        return value

    monkeypatch.setattr(Settings, "data_dir", property(data_dir))


@pytest.fixture(autouse=True)
def _skip_telegram_delivery_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not wait out Telegram flood-control pacing (1 second after each send).

    Pipeline and recovery tests deliver several plans per run. The pacing itself
    is asserted in test_notifiers/test_delivery_recovery.py.
    """
    from home_finder.notifiers import telegram

    monkeypatch.setattr(telegram, "DELIVERY_PAUSE_SECONDS", 0.0)


@pytest.fixture
async def native_analysis_spending(monkeypatch):
    """Explicit test-only budget for suites exercising the actual SDK transport."""
    from home_finder.analysis.analyzer import PropertyQualityAnalyzer
    from home_finder.db import PropertyStorage
    from home_finder.models.spend import SpendPolicy

    policy = SpendPolicy(
        per_call_usd="10",
        daily_usd="1000",
        monthly_usd="1000",
        max_in_flight=50,
        max_attempts_per_operation=3,
    )
    original = PropertyQualityAnalyzer.__init__
    async with PropertyStorage(":memory:") as storage:

        def initialize(self, *args, **kwargs):
            if "spend_repository" not in kwargs and "spend_policy" not in kwargs:
                kwargs.update(spend_repository=storage.spend, spend_policy=policy)
            original(self, *args, **kwargs)

        monkeypatch.setattr(PropertyQualityAnalyzer, "__init__", initialize)
        yield storage, policy


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


@pytest.fixture
def cached_analysis_property(make_merged_property, tmp_path):
    from tests.support.images import populate_image_cache

    merged = make_merged_property()
    merged = merged.model_copy(
        update={
            "images": (
                PropertyImage(
                    url="https://example.com/fixture.jpg",
                    source=merged.canonical.source,
                    image_type="gallery",
                ),
            )
        }
    )
    populate_image_cache(str(tmp_path), merged)
    return merged, str(tmp_path)
