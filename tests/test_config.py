"""Tests for application configuration."""

import pytest

from home_finder.config import Settings
from home_finder.models import FurnishType, TransportMode


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="fake:token",
        telegram_chat_id=0,
        database_path=":memory:",
    )


class TestGetSearchAreas:
    def test_default_areas(self, settings: Settings) -> None:
        areas = settings.get_search_areas()
        assert isinstance(areas, list)
        assert len(areas) > 0
        assert "e3" in areas

    def test_custom_areas(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            search_areas="sw1,nw3,se5",
        )
        assert s.get_search_areas() == ["sw1", "nw3", "se5"]

    def test_strips_whitespace(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            search_areas=" e8 , n16 , e3 ",
        )
        assert s.get_search_areas() == ["e8", "n16", "e3"]

    def test_empty_string(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            search_areas="",
        )
        assert s.get_search_areas() == []

    def test_single_area(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            search_areas="e8",
        )
        assert s.get_search_areas() == ["e8"]

    def test_trailing_comma_ignored(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            search_areas="e8,e3,",
        )
        assert s.get_search_areas() == ["e8", "e3"]


class TestGetFurnishTypes:
    def test_default_furnish_types(self, settings: Settings) -> None:
        types = settings.get_furnish_types()
        assert isinstance(types, tuple)
        assert FurnishType.UNFURNISHED in types
        assert FurnishType.PART_FURNISHED in types

    def test_single_furnish_type(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            furnish_types="furnished",
        )
        assert s.get_furnish_types() == (FurnishType.FURNISHED,)

    def test_all_furnish_types(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            furnish_types="furnished,unfurnished,part_furnished",
        )
        types = s.get_furnish_types()
        assert len(types) == 3

    def test_invalid_furnish_type_raises(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            furnish_types="invalid_type",
        )
        with pytest.raises(ValueError):
            s.get_furnish_types()


class TestGetSearchCriteria:
    def test_maps_settings_to_criteria(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=3,
            destination_postcode="E8 1AA",
            max_commute_minutes=25,
        )
        criteria = s.get_search_criteria()
        assert criteria.min_price == 1500
        assert criteria.max_price == 2500
        assert criteria.min_bedrooms == 1
        assert criteria.max_bedrooms == 3
        assert criteria.destination_postcode == "E8 1AA"
        assert criteria.max_commute_minutes == 25
        assert TransportMode.CYCLING in criteria.transport_modes
        assert TransportMode.PUBLIC_TRANSPORT in criteria.transport_modes

    def test_custom_criteria(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            min_price=1000,
            max_price=3000,
            min_bedrooms=2,
            max_bedrooms=3,
            destination_postcode="SW1A 1AA",
            max_commute_minutes=45,
        )
        criteria = s.get_search_criteria()
        assert criteria.min_price == 1000
        assert criteria.max_price == 3000
        assert criteria.min_bedrooms == 2
        assert criteria.max_bedrooms == 3
        assert criteria.destination_postcode == "SW1A 1AA"
        assert criteria.max_commute_minutes == 45


class TestDataDir:
    def test_data_dir_from_db_path(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path="/some/path/data/properties.db",
        )
        assert s.data_dir == "/some/path/data"

    def test_data_dir_memory(self) -> None:
        s = Settings(
            telegram_bot_token="fake:token",
            telegram_chat_id=0,
            database_path=":memory:",
        )
        assert s.data_dir == "."
