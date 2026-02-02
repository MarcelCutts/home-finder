# Floorplan Analysis Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Filter properties by floorplan analysis - reject properties without floorplans, auto-pass 2+ beds, run LLM analysis on 1-beds to check living room size.

**Architecture:** Two-phase approach. After existing filters reduce to ~10-30 properties, fetch detail pages to extract floorplan URLs, then run Claude Sonnet vision analysis on 1-bed floorplans to determine if living room is spacious enough.

**Tech Stack:** Python 3.11+, anthropic SDK, httpx for detail fetching, pydantic for models, pytest for tests.

---

## Task 1: Add Anthropic Dependency

**Files:**
- Modify: `pyproject.toml:7-17`

**Step 1: Add anthropic to dependencies**

Add `anthropic` to the dependencies list in pyproject.toml:

```toml
dependencies = [
    "crawlee[beautifulsoup]>=0.5",
    "traveltimepy>=4.0",
    "aiogram>=3.4",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "aiosqlite>=0.20",
    "structlog>=24.1",
    "curl-cffi>=0.7",
    "httpx>=0.27",
    "anthropic>=0.40.0",
]
```

**Step 2: Sync dependencies**

Run: `uv sync --all-extras`
Expected: anthropic package installed

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add anthropic dependency for floorplan analysis"
```

---

## Task 2: Create FloorplanAnalysis Model

**Files:**
- Test: `tests/test_filters/test_floorplan.py`
- Create: `src/home_finder/filters/floorplan.py`

**Step 1: Write the failing test**

Create `tests/test_filters/test_floorplan.py`:

```python
"""Tests for floorplan analysis filter."""

import pytest
from pydantic import ValidationError

from home_finder.filters.floorplan import FloorplanAnalysis


class TestFloorplanAnalysis:
    """Tests for FloorplanAnalysis model."""

    def test_valid_analysis(self):
        """Should create valid analysis with all fields."""
        analysis = FloorplanAnalysis(
            living_room_sqm=25.5,
            is_spacious_enough=True,
            confidence="high",
            reasoning="Living room is 25.5 sqm, suitable for office and hosting",
        )
        assert analysis.living_room_sqm == 25.5
        assert analysis.is_spacious_enough is True
        assert analysis.confidence == "high"

    def test_minimal_analysis(self):
        """Should create analysis with only required fields."""
        analysis = FloorplanAnalysis(
            is_spacious_enough=False,
            confidence="low",
            reasoning="Cannot determine room sizes from floorplan",
        )
        assert analysis.living_room_sqm is None
        assert analysis.is_spacious_enough is False

    def test_invalid_confidence(self):
        """Should reject invalid confidence values."""
        with pytest.raises(ValidationError):
            FloorplanAnalysis(
                is_spacious_enough=True,
                confidence="very high",  # Invalid
                reasoning="Test",
            )

    def test_model_is_frozen(self):
        """Should be immutable."""
        analysis = FloorplanAnalysis(
            is_spacious_enough=True,
            confidence="high",
            reasoning="Test",
        )
        with pytest.raises(ValidationError):
            analysis.is_spacious_enough = False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_filters/test_floorplan.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'home_finder.filters.floorplan'"

**Step 3: Write minimal implementation**

Create `src/home_finder/filters/floorplan.py`:

```python
"""Floorplan analysis filter using Claude vision."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FloorplanAnalysis(BaseModel):
    """Result of LLM floorplan analysis."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_filters/test_floorplan.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add tests/test_filters/test_floorplan.py src/home_finder/filters/floorplan.py
git commit -m "feat: add FloorplanAnalysis model"
```

---

## Task 3: Create DetailFetcher - Rightmove

**Files:**
- Test: `tests/test_filters/test_floorplan.py` (add to existing)
- Modify: `src/home_finder/filters/floorplan.py`
- Create: `tests/fixtures/rightmove_detail_with_floorplan.html`
- Create: `tests/fixtures/rightmove_detail_no_floorplan.html`

**Step 1: Capture test fixtures**

First, manually save real Rightmove detail pages. Find a property with floorplan and one without:

```bash
# With floorplan - find a real listing and save it
curl -s "https://www.rightmove.co.uk/properties/PROPERTY_ID" -o tests/fixtures/rightmove_detail_with_floorplan.html

# Without floorplan - find one without
curl -s "https://www.rightmove.co.uk/properties/PROPERTY_ID2" -o tests/fixtures/rightmove_detail_no_floorplan.html
```

For now, create minimal mock fixtures. Create `tests/fixtures/rightmove_detail_with_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<head><title>Property</title></head>
<body>
<script>
window.PAGE_MODEL = {
    "propertyData": {
        "floorplans": [
            {"url": "https://media.rightmove.co.uk/floor/123_FLP_00.jpg"}
        ]
    }
};
</script>
</body>
</html>
```

Create `tests/fixtures/rightmove_detail_no_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<head><title>Property</title></head>
<body>
<script>
window.PAGE_MODEL = {
    "propertyData": {
        "floorplans": []
    }
};
</script>
</body>
</html>
```

**Step 2: Write the failing test**

Add to `tests/test_filters/test_floorplan.py`:

```python
from pathlib import Path

import pytest

from home_finder.filters.floorplan import DetailFetcher, FloorplanAnalysis
from home_finder.models import Property, PropertySource


@pytest.fixture
def rightmove_property() -> Property:
    """Sample Rightmove property."""
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="123456789",
        url="https://www.rightmove.co.uk/properties/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def fixtures_path() -> Path:
    """Path to test fixtures."""
    return Path(__file__).parent.parent / "fixtures"


class TestDetailFetcherRightmove:
    """Tests for Rightmove detail page parsing."""

    async def test_extracts_floorplan_url(
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from Rightmove detail page."""
        html = (fixtures_path / "rightmove_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            html=html,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url == "https://media.rightmove.co.uk/floor/123_FLP_00.jpg"

    async def test_returns_none_when_no_floorplan(
        self, rightmove_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "rightmove_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            html=html,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url is None

    async def test_returns_none_on_http_error(
        self, rightmove_property: Property, httpx_mock
    ):
        """Should return None when HTTP request fails."""
        httpx_mock.add_response(
            url="https://www.rightmove.co.uk/properties/123456789",
            status_code=404,
        )

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(rightmove_property)

        assert url is None
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_filters/test_floorplan.py::TestDetailFetcherRightmove -v`
Expected: FAIL with "cannot import name 'DetailFetcher'"

**Step 4: Write minimal implementation**

Add to `src/home_finder/filters/floorplan.py`:

```python
"""Floorplan analysis filter using Claude vision."""

import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from home_finder.logging import get_logger
from home_finder.models import Property, PropertySource

logger = get_logger(__name__)


class FloorplanAnalysis(BaseModel):
    """Result of LLM floorplan analysis."""

    model_config = ConfigDict(frozen=True)

    living_room_sqm: float | None = None
    is_spacious_enough: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class DetailFetcher:
    """Fetches property detail pages and extracts floorplan URLs."""

    def __init__(self) -> None:
        """Initialize the detail fetcher."""
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
        return self._client

    async def fetch_floorplan_url(self, prop: Property) -> str | None:
        """Fetch detail page and extract floorplan URL.

        Args:
            prop: Property to fetch floorplan for.

        Returns:
            Floorplan URL or None if not found.
        """
        match prop.source:
            case PropertySource.RIGHTMOVE:
                return await self._fetch_rightmove(prop)
            case _:
                logger.warning("unsupported_source", source=prop.source.value)
                return None

    async def _fetch_rightmove(self, prop: Property) -> str | None:
        """Extract floorplan URL from Rightmove detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find PAGE_MODEL JSON in script tag
            match = re.search(r"window\.PAGE_MODEL\s*=\s*({.*?});", html, re.DOTALL)
            if not match:
                logger.debug("no_page_model", property_id=prop.unique_id)
                return None

            data = json.loads(match.group(1))
            floorplans = data.get("propertyData", {}).get("floorplans", [])

            if floorplans and floorplans[0].get("url"):
                return floorplans[0]["url"]

            return None

        except Exception as e:
            logger.warning(
                "rightmove_fetch_failed",
                property_id=prop.unique_id,
                error=str(e),
            )
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_filters/test_floorplan.py -v`
Expected: PASS (7 tests)

**Step 6: Commit**

```bash
git add tests/test_filters/test_floorplan.py src/home_finder/filters/floorplan.py tests/fixtures/rightmove_detail_*.html
git commit -m "feat: add DetailFetcher for Rightmove floorplan extraction"
```

---

## Task 4: Add DetailFetcher Support for Zoopla, OpenRent, OnTheMarket

**Files:**
- Test: `tests/test_filters/test_floorplan.py` (add to existing)
- Modify: `src/home_finder/filters/floorplan.py`
- Create: `tests/fixtures/zoopla_detail_with_floorplan.html`
- Create: `tests/fixtures/zoopla_detail_no_floorplan.html`
- Create: `tests/fixtures/openrent_detail_with_floorplan.html`
- Create: `tests/fixtures/openrent_detail_no_floorplan.html`
- Create: `tests/fixtures/onthemarket_detail_with_floorplan.html`
- Create: `tests/fixtures/onthemarket_detail_no_floorplan.html`

**Step 1: Create test fixtures**

Create `tests/fixtures/zoopla_detail_with_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"listing":{"propertyMedia":[{"type":"floorplan","original":"https://lid.zoocdn.com/u/floor/123.jpg"}]}}}}
</script>
</body>
</html>
```

Create `tests/fixtures/zoopla_detail_no_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"listing":{"propertyMedia":[{"type":"image","original":"https://lid.zoocdn.com/photo.jpg"}]}}}}
</script>
</body>
</html>
```

Create `tests/fixtures/openrent_detail_with_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<div class="property-gallery">
    <img class="floorplan-image" src="https://www.openrent.com/floorplan/123.jpg" />
</div>
</body>
</html>
```

Create `tests/fixtures/openrent_detail_no_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<div class="property-gallery">
    <img class="property-image" src="https://www.openrent.com/photo.jpg" />
</div>
</body>
</html>
```

Create `tests/fixtures/onthemarket_detail_with_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<script type="application/json" data-testid="property-details">
{"floorplans":[{"src":"https://media.onthemarket.com/floor/123.jpg"}]}
</script>
</body>
</html>
```

Create `tests/fixtures/onthemarket_detail_no_floorplan.html`:

```html
<!DOCTYPE html>
<html>
<body>
<script type="application/json" data-testid="property-details">
{"floorplans":[]}
</script>
</body>
</html>
```

**Step 2: Write the failing tests**

Add to `tests/test_filters/test_floorplan.py`:

```python
@pytest.fixture
def zoopla_property() -> Property:
    """Sample Zoopla property."""
    return Property(
        source=PropertySource.ZOOPLA,
        source_id="123456789",
        url="https://www.zoopla.co.uk/to-rent/details/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def openrent_property() -> Property:
    """Sample OpenRent property."""
    return Property(
        source=PropertySource.OPENRENT,
        source_id="123456789",
        url="https://www.openrent.com/property/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


@pytest.fixture
def onthemarket_property() -> Property:
    """Sample OnTheMarket property."""
    return Property(
        source=PropertySource.ONTHEMARKET,
        source_id="123456789",
        url="https://www.onthemarket.com/details/123456789",
        title="2 bed flat",
        price_pcm=2000,
        bedrooms=2,
        address="123 Test Street, London",
    )


class TestDetailFetcherZoopla:
    """Tests for Zoopla detail page parsing."""

    async def test_extracts_floorplan_url(
        self, zoopla_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from Zoopla detail page."""
        html = (fixtures_path / "zoopla_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url == "https://lid.zoocdn.com/u/floor/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, zoopla_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "zoopla_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(zoopla_property)

        assert url is None


class TestDetailFetcherOpenRent:
    """Tests for OpenRent detail page parsing."""

    async def test_extracts_floorplan_url(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from OpenRent detail page."""
        html = (fixtures_path / "openrent_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url == "https://www.openrent.com/floorplan/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, openrent_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "openrent_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(openrent_property)

        assert url is None


class TestDetailFetcherOnTheMarket:
    """Tests for OnTheMarket detail page parsing."""

    async def test_extracts_floorplan_url(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should extract floorplan URL from OnTheMarket detail page."""
        html = (fixtures_path / "onthemarket_detail_with_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url == "https://media.onthemarket.com/floor/123.jpg"

    async def test_returns_none_when_no_floorplan(
        self, onthemarket_property: Property, fixtures_path: Path, httpx_mock
    ):
        """Should return None when property has no floorplan."""
        html = (fixtures_path / "onthemarket_detail_no_floorplan.html").read_text()
        httpx_mock.add_response(html=html)

        fetcher = DetailFetcher()
        url = await fetcher.fetch_floorplan_url(onthemarket_property)

        assert url is None
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_filters/test_floorplan.py::TestDetailFetcherZoopla -v`
Expected: FAIL (unsupported source)

**Step 4: Add Zoopla, OpenRent, OnTheMarket support**

Add methods to `DetailFetcher` class in `src/home_finder/filters/floorplan.py`:

```python
    async def fetch_floorplan_url(self, prop: Property) -> str | None:
        """Fetch detail page and extract floorplan URL."""
        match prop.source:
            case PropertySource.RIGHTMOVE:
                return await self._fetch_rightmove(prop)
            case PropertySource.ZOOPLA:
                return await self._fetch_zoopla(prop)
            case PropertySource.OPENRENT:
                return await self._fetch_openrent(prop)
            case PropertySource.ONTHEMARKET:
                return await self._fetch_onthemarket(prop)

    async def _fetch_zoopla(self, prop: Property) -> str | None:
        """Extract floorplan URL from Zoopla detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find __NEXT_DATA__ JSON
            match = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            media = (
                data.get("props", {})
                .get("pageProps", {})
                .get("listing", {})
                .get("propertyMedia", [])
            )

            for item in media:
                if item.get("type") == "floorplan":
                    return item.get("original")

            return None

        except Exception as e:
            logger.warning("zoopla_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_openrent(self, prop: Property) -> str | None:
        """Extract floorplan URL from OpenRent detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Look for floorplan image
            match = re.search(
                r'<img[^>]*class="[^"]*floorplan[^"]*"[^>]*src="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)

            return None

        except Exception as e:
            logger.warning("openrent_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_onthemarket(self, prop: Property) -> str | None:
        """Extract floorplan URL from OnTheMarket detail page."""
        try:
            client = await self._get_client()
            response = await client.get(str(prop.url))
            response.raise_for_status()
            html = response.text

            # Find property-details JSON
            match = re.search(
                r'<script[^>]*data-testid="property-details"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            floorplans = data.get("floorplans", [])

            if floorplans and floorplans[0].get("src"):
                return floorplans[0]["src"]

            return None

        except Exception as e:
            logger.warning("onthemarket_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_filters/test_floorplan.py -v`
Expected: PASS (13 tests)

**Step 6: Commit**

```bash
git add tests/test_filters/test_floorplan.py src/home_finder/filters/floorplan.py tests/fixtures/*_detail_*.html
git commit -m "feat: add DetailFetcher support for Zoopla, OpenRent, OnTheMarket"
```

---

## Task 5: Create FloorplanFilter with LLM Analysis

**Files:**
- Test: `tests/test_filters/test_floorplan.py` (add to existing)
- Modify: `src/home_finder/filters/floorplan.py`

**Step 1: Write the failing tests**

Add to `tests/test_filters/test_floorplan.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


class TestFloorplanFilter:
    """Tests for FloorplanFilter."""

    async def test_filters_out_properties_without_floorplan(self, rightmove_property: Property):
        """Properties without floorplans should be excluded."""
        with patch.object(DetailFetcher, "fetch_floorplan_url", return_value=None):
            filter = FloorplanFilter(api_key="test-key")
            results = await filter.filter_properties([rightmove_property])

        assert len(results) == 0

    async def test_two_bed_skips_llm_analysis(self, rightmove_property: Property):
        """2+ bed properties should auto-pass without LLM call."""
        # rightmove_property has 2 bedrooms
        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            filter = FloorplanFilter(api_key="test-key")
            # Mock the anthropic client to verify it's NOT called
            filter._client = MagicMock()

            results = await filter.filter_properties([rightmove_property])

        assert len(results) == 1
        prop, analysis = results[0]
        assert analysis.is_spacious_enough is True
        assert "2+ bedrooms" in analysis.reasoning
        # Verify LLM was not called
        filter._client.messages.create.assert_not_called()

    async def test_one_bed_spacious_passes(self):
        """1-bed with spacious living room should pass."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url="https://www.rightmove.co.uk/properties/999",
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"living_room_sqm": 25, "is_spacious_enough": true, "confidence": "high", "reasoning": "Large living room"}'
            )
        ]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            filter = FloorplanFilter(api_key="test-key")
            filter._client = MagicMock()
            filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await filter.filter_properties([one_bed])

        assert len(results) == 1
        _, analysis = results[0]
        assert analysis.is_spacious_enough is True
        assert analysis.living_room_sqm == 25

    async def test_one_bed_small_filtered_out(self):
        """1-bed with small living room should be filtered out."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url="https://www.rightmove.co.uk/properties/999",
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"living_room_sqm": 12, "is_spacious_enough": false, "confidence": "high", "reasoning": "Small living room"}'
            )
        ]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            filter = FloorplanFilter(api_key="test-key")
            filter._client = MagicMock()
            filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await filter.filter_properties([one_bed])

        assert len(results) == 0

    async def test_llm_invalid_json_filters_out(self):
        """Invalid LLM response should filter out property (fail-safe)."""
        one_bed = Property(
            source=PropertySource.RIGHTMOVE,
            source_id="999",
            url="https://www.rightmove.co.uk/properties/999",
            title="1 bed flat",
            price_pcm=1800,
            bedrooms=1,
            address="Test Street",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not JSON")]

        with patch.object(
            DetailFetcher, "fetch_floorplan_url", return_value="https://example.com/floor.jpg"
        ):
            filter = FloorplanFilter(api_key="test-key")
            filter._client = MagicMock()
            filter._client.messages.create = AsyncMock(return_value=mock_response)

            results = await filter.filter_properties([one_bed])

        assert len(results) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_filters/test_floorplan.py::TestFloorplanFilter -v`
Expected: FAIL with "cannot import name 'FloorplanFilter'"

**Step 3: Write minimal implementation**

Add to `src/home_finder/filters/floorplan.py`:

```python
import asyncio

import anthropic

# Add to imports at top
from home_finder.models import Property, PropertySource

FLOORPLAN_PROMPT = """Analyze this floorplan image for a rental property.

I need to determine if the living room/lounge is spacious enough to:
1. Fit a home office setup (desk, chair, monitors)
2. Host a party of 8+ people comfortably

Please analyze the floorplan and respond with ONLY a JSON object (no markdown, no explanation outside the JSON):

{
    "living_room_sqm": <estimated size in square meters, or null if cannot determine>,
    "is_spacious_enough": <true if living room can fit office AND host 8+ people, false otherwise>,
    "confidence": <"high", "medium", or "low">,
    "reasoning": <brief explanation of your assessment>
}

Generally, a living room needs to be at least 20-25 sqm to comfortably fit both uses.
If the floorplan doesn't show measurements or you cannot estimate, use your best judgment
based on the room proportions and mark confidence as "low".
"""


class FloorplanFilter:
    """Filter properties by floorplan analysis."""

    def __init__(self, api_key: str) -> None:
        """Initialize the floorplan filter.

        Args:
            api_key: Anthropic API key.
        """
        self._api_key = api_key
        self._client: anthropic.AsyncAnthropic | None = None
        self._detail_fetcher = DetailFetcher()

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def filter_properties(
        self, properties: list[Property]
    ) -> list[tuple[Property, FloorplanAnalysis]]:
        """Filter properties by floorplan analysis.

        Args:
            properties: Properties to analyze.

        Returns:
            List of (property, analysis) tuples for properties that pass.
        """
        results: list[tuple[Property, FloorplanAnalysis]] = []

        for prop in properties:
            # Step 1: Fetch floorplan URL
            floorplan_url = await self._detail_fetcher.fetch_floorplan_url(prop)

            if not floorplan_url:
                logger.info("no_floorplan", property_id=prop.unique_id)
                continue

            # Step 2: 2+ beds auto-pass
            if prop.bedrooms >= 2:
                analysis = FloorplanAnalysis(
                    is_spacious_enough=True,
                    confidence="high",
                    reasoning="2+ bedrooms - office can go in spare room",
                )
                results.append((prop, analysis))
                continue

            # Step 3: 1-bed needs LLM analysis
            analysis = await self._analyze_floorplan(floorplan_url, prop.unique_id)

            if analysis and analysis.is_spacious_enough:
                results.append((prop, analysis))
            else:
                reason = analysis.reasoning if analysis else "analysis failed"
                logger.info(
                    "filtered_small_living_room",
                    property_id=prop.unique_id,
                    reasoning=reason,
                )

            # Rate limit: small delay between LLM calls
            await asyncio.sleep(0.5)

        return results

    async def _analyze_floorplan(
        self, floorplan_url: str, property_id: str
    ) -> FloorplanAnalysis | None:
        """Analyze a floorplan image using Claude.

        Args:
            floorplan_url: URL of the floorplan image.
            property_id: Property ID for logging.

        Returns:
            Analysis result or None if analysis failed.
        """
        try:
            client = self._get_client()
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "url", "url": floorplan_url},
                            },
                            {"type": "text", "text": FLOORPLAN_PROMPT},
                        ],
                    }
                ],
            )

            # Parse response
            response_text = response.content[0].text
            return FloorplanAnalysis.model_validate_json(response_text)

        except Exception as e:
            logger.warning(
                "floorplan_analysis_failed",
                property_id=property_id,
                error=str(e),
            )
            return None

    async def close(self) -> None:
        """Close clients."""
        await self._detail_fetcher.close()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_filters/test_floorplan.py -v`
Expected: PASS (18 tests)

**Step 5: Commit**

```bash
git add tests/test_filters/test_floorplan.py src/home_finder/filters/floorplan.py
git commit -m "feat: add FloorplanFilter with Claude LLM analysis"
```

---

## Task 6: Add Configuration Settings

**Files:**
- Modify: `src/home_finder/config.py`

**Step 1: Add settings**

Add to `src/home_finder/config.py` in the `Settings` class:

```python
    # Anthropic API (optional, needed for floorplan analysis)
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Anthropic API key for floorplan analysis",
    )

    # Floorplan filtering (optional)
    enable_floorplan_filter: bool = Field(
        default=True,
        description="Filter out properties without floorplans and analyze 1-beds",
    )
```

**Step 2: Commit**

```bash
git add src/home_finder/config.py
git commit -m "feat: add Anthropic API and floorplan filter settings"
```

---

## Task 7: Export FloorplanFilter from filters module

**Files:**
- Modify: `src/home_finder/filters/__init__.py`

**Step 1: Add exports**

Update `src/home_finder/filters/__init__.py`:

```python
"""Filters for property search criteria and commute times."""

from home_finder.filters.commute import CommuteFilter, CommuteResult
from home_finder.filters.criteria import CriteriaFilter
from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.floorplan import FloorplanAnalysis, FloorplanFilter
from home_finder.filters.location import LocationFilter

__all__ = [
    "CommuteFilter",
    "CommuteResult",
    "CriteriaFilter",
    "Deduplicator",
    "FloorplanAnalysis",
    "FloorplanFilter",
    "LocationFilter",
]
```

**Step 2: Commit**

```bash
git add src/home_finder/filters/__init__.py
git commit -m "feat: export FloorplanFilter from filters module"
```

---

## Task 8: Integrate FloorplanFilter into Pipeline

**Files:**
- Modify: `src/home_finder/main.py:236-275`
- Modify: `src/home_finder/notifiers/telegram.py:16-76`

**Step 1: Update main.py**

Add import at top of `main.py`:

```python
from home_finder.filters import (
    CommuteFilter,
    CriteriaFilter,
    Deduplicator,
    FloorplanFilter,
    LocationFilter,
)
from home_finder.filters.floorplan import FloorplanAnalysis
```

Then insert after commute filter section (after line 236), before "Step 6: Save and notify":

```python
        # Step 5.5: Floorplan analysis (if configured)
        floorplan_lookup: dict[str, FloorplanAnalysis] = {}
        if (
            settings.anthropic_api_key.get_secret_value()
            and settings.enable_floorplan_filter
        ):
            logger.info("pipeline_started", phase="floorplan_filtering")
            floorplan_filter = FloorplanFilter(
                api_key=settings.anthropic_api_key.get_secret_value()
            )

            try:
                floorplan_results = await floorplan_filter.filter_properties(
                    properties_to_notify
                )

                logger.info(
                    "floorplan_filter_summary",
                    input_count=len(properties_to_notify),
                    with_floorplan=len(floorplan_results),
                )

                floorplan_lookup = {
                    prop.unique_id: analysis for prop, analysis in floorplan_results
                }
                properties_to_notify = [prop for prop, _ in floorplan_results]
            finally:
                await floorplan_filter.close()
        else:
            logger.info(
                "skipping_floorplan_filter",
                reason="no_anthropic_key_or_disabled",
            )

        if not properties_to_notify:
            logger.info("no_properties_after_floorplan_filter")
            return
```

Update the notification loop to pass floorplan_analysis:

```python
            # Send notification
            floorplan_analysis = floorplan_lookup.get(prop.unique_id)
            success = await notifier.send_property_notification(
                prop,
                commute_minutes=commute_minutes,
                transport_mode=transport_mode,
                floorplan_analysis=floorplan_analysis,
            )
```

**Step 2: Update telegram.py**

Update imports:

```python
from home_finder.filters.floorplan import FloorplanAnalysis
from home_finder.models import Property, TransportMode
```

Update `format_property_message` signature and body:

```python
def format_property_message(
    prop: Property,
    *,
    commute_minutes: int | None = None,
    transport_mode: TransportMode | None = None,
    floorplan_analysis: FloorplanAnalysis | None = None,
) -> str:
    """Format a property as a Telegram message."""
    # ... existing code ...

    # Add floorplan info after commute info
    if floorplan_analysis:
        if floorplan_analysis.living_room_sqm:
            lines.append(
                f"<b>Living Room:</b> ~{floorplan_analysis.living_room_sqm:.0f} sqm"
            )
        lines.append(f"<b>Space:</b> {floorplan_analysis.reasoning}")

    # ... rest of existing code (source, link) ...
```

Update `send_property_notification` signature:

```python
    async def send_property_notification(
        self,
        prop: Property,
        *,
        commute_minutes: int | None = None,
        transport_mode: TransportMode | None = None,
        floorplan_analysis: FloorplanAnalysis | None = None,
    ) -> bool:
        """Send a property notification."""
        message = format_property_message(
            prop,
            commute_minutes=commute_minutes,
            transport_mode=transport_mode,
            floorplan_analysis=floorplan_analysis,
        )
        # ... rest unchanged ...
```

**Step 3: Run full test suite**

Run: `uv run pytest`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/home_finder/main.py src/home_finder/notifiers/telegram.py
git commit -m "feat: integrate FloorplanFilter into pipeline"
```

---

## Task 9: Update dry-run mode

**Files:**
- Modify: `src/home_finder/main.py` (run_dry_run function)

**Step 1: Add floorplan filter to dry-run**

Copy the same floorplan filter integration to `run_dry_run` function, after the commute filter section.

**Step 2: Update print output**

In the dry-run print loop, add:

```python
            floorplan_analysis = floorplan_lookup.get(prop.unique_id)
            if floorplan_analysis and floorplan_analysis.living_room_sqm:
                print(f"  Living Room: ~{floorplan_analysis.living_room_sqm:.0f} sqm")
```

**Step 3: Run tests**

Run: `uv run pytest`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/home_finder/main.py
git commit -m "feat: add floorplan filter to dry-run mode"
```

---

## Task 10: Fix type checking

**Files:**
- Various (based on mypy output)

**Step 1: Run type checker**

Run: `uv run mypy src`

**Step 2: Fix any type errors**

Address any type errors reported by mypy.

**Step 3: Run linter**

Run: `uv run ruff check src tests`
Run: `uv run ruff format src tests`

**Step 4: Commit**

```bash
git add -A
git commit -m "fix: resolve type errors and linting issues"
```

---

## Task 11: Manual E2E Test

**Step 1: Set up environment**

Add to `.env`:
```
HOME_FINDER_ANTHROPIC_API_KEY=sk-ant-...
```

**Step 2: Run dry-run**

Run: `uv run home-finder --dry-run`

**Step 3: Verify**

- Check logs for "pipeline_started phase=floorplan_filtering"
- Verify properties with floorplans show "Living Room" in output
- Verify 1-bed properties were analyzed by LLM

---

## Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | Add anthropic dependency | - |
| 2 | Create FloorplanAnalysis model | 4 |
| 3 | DetailFetcher - Rightmove | 3 |
| 4 | DetailFetcher - Zoopla/OpenRent/OnTheMarket | 6 |
| 5 | FloorplanFilter with LLM | 5 |
| 6 | Configuration settings | - |
| 7 | Export from filters module | - |
| 8 | Pipeline integration | - |
| 9 | Dry-run mode | - |
| 10 | Type checking | - |
| 11 | Manual E2E test | - |

**Total new tests:** ~18
