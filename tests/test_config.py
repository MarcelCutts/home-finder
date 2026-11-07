"""Configuration loading, documented defaults and startup validation."""

import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsError

from home_finder.config import Settings
from home_finder.config_check import configuration_summary
from home_finder.models import FurnishType, TransportMode

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_example_can_be_copied_without_credentials():
    settings = Settings(_env_file=EXAMPLE)
    assert not settings.telegram_bot_token
    assert settings.telegram_chat_id == 0
    assert not settings.web_owner_password
    assert settings.analysis_spend_policy is None


def test_every_setting_has_one_valid_example_and_documented_defaults_do_not_drift(tmp_path):
    definitions = re.findall(r"^(?:# )?(HOME_FINDER_[A-Z_]+)=(.*)$", EXAMPLE.read_text(), re.M)
    counts = Counter(name for name, _ in definitions)
    assert set(counts) == {f"HOME_FINDER_{name.upper()}" for name in Settings.model_fields}
    assert all(count == 1 for count in counts.values())
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{name}={value}" for name, value in definitions))
    settings = Settings(_env_file=env_file)
    assert settings.model_fields_set == set(Settings.model_fields)
    defaults = Settings(_env_file=None)
    for name in Settings.model_fields:
        # These two explicitly illustrate opt-in policy/identity, not defaults.
        if name not in {"telegram_owner_user_id", "analysis_spend_policy"}:
            assert getattr(settings, name) == getattr(defaults, name), name
    assert settings.telegram_owner_user_id == 123456789
    assert settings.analysis_spend_policy.per_call_usd == 0


def test_precedence_is_constructor_then_environment_then_dotenv_then_default(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HOME_FINDER_MAX_PRICE=2400\nHOME_FINDER_ENABLE_QUALITY_FILTER=false\n")
    monkeypatch.setenv("HOME_FINDER_MAX_PRICE", "2600")
    settings = Settings(_env_file=env_file, max_price=2800)
    assert settings.max_price == 2800
    assert not settings.enable_quality_filter
    assert Settings(_env_file=env_file).max_price == 2600
    monkeypatch.delenv("HOME_FINDER_MAX_PRICE")
    assert Settings(_env_file=env_file).max_price == 2400
    assert Settings(_env_file=None).max_price == 2500


def test_dotenv_is_local_to_working_directory_unless_explicitly_selected(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HOME_FINDER_MAX_PRICE=2300\n")
    child = tmp_path / "worktree"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    assert Settings().max_price == 2500
    selected = Settings(_env_file=env_file)
    assert selected.max_price == 2300
    assert selected.database_path == "data/properties.db"
    monkeypatch.chdir(tmp_path)
    assert Settings().max_price == 2300


@pytest.mark.parametrize("source", ["environment", "dotenv"])
@pytest.mark.parametrize("name", ["HOME_FINDER_MAX_PRCE", "home_finder_scrape_interval_minutes"])
def test_unknown_application_keys_fail_without_printing_values(monkeypatch, tmp_path, source, name):
    env_file = tmp_path / ".env"
    secret = "private-value-must-not-appear"
    if source == "environment":
        monkeypatch.setenv(name, secret)
    else:
        env_file.write_text(f"{name}={secret}\n")
    with pytest.raises(SettingsError) as exc:
        Settings(_env_file=env_file)
    assert name.upper() in str(exc.value)
    assert secret not in str(exc.value)


def test_other_tools_keys_are_ignored_and_application_names_are_case_insensitive(
    monkeypatch, tmp_path
):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=unrelated\nANTHROPIC_API_KEY=experiment-key\n")
    monkeypatch.setenv("home_finder_max_price", "2700")
    settings = Settings(_env_file=env_file)
    assert settings.max_price == 2700
    assert not settings.anthropic_api_key


@pytest.mark.parametrize("value", ["", "NaN", "null", '"25"'])
def test_empty_or_malformed_numeric_env_is_not_silently_defaulted(monkeypatch, value):
    monkeypatch.setenv("HOME_FINDER_MAX_COMMUTE_MINUTES", value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_price": 2600, "max_price": 2500},
        {"min_bedrooms": 3, "max_bedrooms": 2},
        {"web_port": 0},
        {"web_port": 65536},
        {"database_path": " "},
        {"web_host": ""},
        {"destination_postcode": " "},
        {"web_base_url": "https://user:password@example.com"},
        {"web_base_url": "https://example.com/dashboard"},
        {"web_base_url": "https://example.com?token=secret"},
        {"web_base_url": "https://example.com?"},
        {"web_base_url": "https://example.com#"},
        {"web_base_url": "https://example.com:70000"},
        {"web_base_url": "https://example.com\n"},
        {"proxy_url": "ftp://user:password@example.com"},
        {"proxy_url": "socks5://host:70000"},
        {"telegram_webhook_secret": "spaces are invalid"},
        {"telegram_webhook_secret": "x" * 257},
    ],
)
def test_invalid_settings_fail_before_work_starts(overrides):
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_supported_origins_and_proxies_preserve_usable_values():
    settings = Settings(
        web_base_url="http://localhost:8000/",
        proxy_url="socks5h://user:p%40ss@localhost:1080",
        telegram_webhook_secret="letters_123-dashes",
    )
    assert settings.web_base_url == "http://localhost:8000"
    assert settings.proxy_url.get_secret_value() == "socks5h://user:p%40ss@localhost:1080"
    assert settings.telegram_webhook_secret.get_secret_value() == "letters_123-dashes"


def test_settings_repr_json_and_validation_errors_do_not_expose_credentials():
    secrets = {
        "telegram_bot_token": "private-bot-token",
        "traveltime_api_key": "private-traveltime-key",
        "anthropic_api_key": "private-anthropic-key",
        "web_owner_password": "private-owner-password-long-enough",
        "telegram_webhook_secret": "private-webhook-secret",
        "carto_basemap_api_key": "private-carto-key",
        "proxy_url": "http://private-proxy-user:private-proxy-password@localhost:8080",
    }
    settings = Settings(**secrets)
    with pytest.raises(ValidationError) as exc:
        Settings(**secrets, min_price=3000)
    rendered = repr(settings) + settings.model_dump_json() + str(exc.value)
    assert all(value not in rendered for value in secrets.values())


@pytest.mark.parametrize(
    "overrides,message",
    [
        (
            {"anthropic_api_key": "private-key", "enable_quality_filter": False},
            "Quality analysis: disabled",
        ),
        (
            {
                "anthropic_api_key": "private-key",
                "analysis_spend_policy": {
                    "per_call_usd": "0",
                    "daily_usd": "0",
                    "monthly_usd": "0",
                },
            },
            "authorizes zero per-call spend",
        ),
        ({"traveltime_app_id": "private-app-id"}, "Commute measurements: incomplete"),
        ({"web_owner_password": "非ASCII" * 10}, "Dashboard: unavailable"),
        (
            {
                "telegram_bot_token": "private-token",
                "telegram_chat_id": -100,
                "telegram_webhook_secret": "private-secret",
                "web_base_url": "https://example.com",
            },
            "Telegram callbacks: incomplete",
        ),
        (
            {
                "telegram_bot_token": "private-token",
                "telegram_chat_id": 100,
                "telegram_webhook_secret": "private-secret",
                "web_base_url": "http://localhost:8000",
            },
            "Telegram callbacks: requires HTTPS",
        ),
    ],
)
def test_check_distinguishes_disabled_incomplete_and_deferred_features(overrides, message):
    report = "\n".join(configuration_summary(Settings(**overrides)))
    assert message in report
    assert "private-" not in report


def test_check_reports_effective_queue_limits_without_claiming_provider_success():
    settings = Settings(
        telegram_bot_token="private-token",
        telegram_chat_id=-100,
        telegram_owner_user_id=123,
        telegram_webhook_secret="private-secret",
        web_base_url="https://example.com",
        web_owner_password="private-password-with-enough-characters",
        carto_basemap_api_key="private-key",
        anthropic_api_key="private-key",
        traveltime_app_id="private-app-id",
        traveltime_api_key="private-key",
        analysis_spend_policy={
            "per_call_usd": "1",
            "daily_usd": "5",
            "monthly_usd": "10",
            "max_in_flight": 2,
        },
        max_initial_analyses_per_run=3,
        max_reanalyses_per_run=7,
        quality_concurrency=15,
    )
    report = "\n".join(configuration_summary(settings))
    assert "Quality analysis: configured" in report
    assert "Telegram callbacks: configured" in report
    assert "3 initial, 7 reanalyses per run; concurrency ceiling 2" in report
    assert "unverified" in report
    assert "private-" not in report


@pytest.mark.parametrize("legacy_limit", [1, 75, 500])
def test_legacy_analysis_limit_defaults_each_queue(monkeypatch, legacy_limit):
    monkeypatch.setenv("HOME_FINDER_MAX_ANALYSIS_PER_RUN", str(legacy_limit))
    monkeypatch.delenv("HOME_FINDER_MAX_INITIAL_ANALYSES_PER_RUN", raising=False)
    monkeypatch.delenv("HOME_FINDER_MAX_REANALYSES_PER_RUN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.max_initial_analyses_per_run == legacy_limit
    assert settings.max_reanalyses_per_run == legacy_limit


def test_explicit_queue_limits_override_legacy_default(monkeypatch):
    monkeypatch.setenv("HOME_FINDER_MAX_ANALYSIS_PER_RUN", "5")
    monkeypatch.setenv("HOME_FINDER_MAX_INITIAL_ANALYSES_PER_RUN", "3")
    settings = Settings(_env_file=None, max_reanalyses_per_run=2)
    assert settings.max_initial_analyses_per_run == 3
    assert settings.max_reanalyses_per_run == 2


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
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
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="sw1,nw3,se5",
        )
        assert s.get_search_areas() == ["sw1", "nw3", "se5"]

    def test_strips_whitespace(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas=" e8 , n16 , e3 ",
        )
        assert s.get_search_areas() == ["e8", "n16", "e3"]

    def test_empty_string(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="",
        )
        assert s.get_search_areas() == []

    def test_single_area(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="e8",
        )
        assert s.get_search_areas() == ["e8"]

    def test_trailing_comma_ignored(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
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
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            furnish_types="furnished",
        )
        assert s.get_furnish_types() == (FurnishType.FURNISHED,)

    def test_all_furnish_types(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            furnish_types="furnished,unfurnished,part_furnished",
        )
        types = s.get_furnish_types()
        assert len(types) == 3

    def test_invalid_furnish_type_raises(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            Settings(
                telegram_bot_token=SecretStr("fake:token"),
                telegram_chat_id=0,
                furnish_types="invalid_type",
            )


class TestGetSearchCriteria:
    def test_maps_settings_to_criteria(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
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
            telegram_bot_token=SecretStr("fake:token"),
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
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path="/some/path/data/properties.db",
        )
        assert s.data_dir == "/some/path/data"

    def test_data_dir_memory(self) -> None:
        s = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            database_path=":memory:",
        )
        assert s.data_dir == "."
