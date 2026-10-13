"""CLI intent is validated before settings, paid work or process startup."""

from contextlib import nullcontext
from itertools import combinations
from unittest.mock import AsyncMock, Mock

import pytest

from home_finder import main as cli
from home_finder.config import Settings, new_runtime_analysis_config

MODES = (
    "--dry-run",
    "--scrape-only",
    "--backfill-commute",
    "--dedup-existing",
    "--generate-thumbnails",
    "--reanalyze",
    "--check-off-market",
    "--serve",
)


@pytest.fixture
def effects(monkeypatch):
    settings = Settings(
        _env_file=None,
        analysis_config=new_runtime_analysis_config(),
        database_path=":memory:",
        telegram_bot_token="",
        telegram_chat_id=0,
        anthropic_api_key="",
    )
    effects = {"settings": Mock(return_value=settings)}
    monkeypatch.setattr(cli, "Settings", effects["settings"])
    monkeypatch.setattr(cli, "configure_logging", Mock())
    monkeypatch.setattr(cli, "logger", Mock())
    for name in (
        "run_pipeline",
        "run_scrape_only",
        "run_backfill_commute",
        "run_dedup_existing",
        "run_reanalysis",
        "run_check_off_market",
    ):
        effects[name] = AsyncMock()
        monkeypatch.setattr(cli, name, effects[name])
    effects["backfill_thumbnails"] = Mock(return_value=(0, 0, 0))
    monkeypatch.setattr(cli, "backfill_thumbnails", effects["backfill_thumbnails"])
    monkeypatch.setattr(cli, "runner_lock", lambda _: nullcontext(True))
    effects["create_app"] = Mock(return_value=object())
    monkeypatch.setattr("home_finder.web.app.create_app", effects["create_app"])
    effects["serve"] = Mock()
    monkeypatch.setattr("uvicorn.run", effects["serve"])
    return settings, effects


@pytest.mark.parametrize("flags", list(combinations(MODES, 2)))
def test_modes_are_exclusive_before_initialization(monkeypatch, effects, flags, capsys):
    _, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert all(not mock.called for mock in calls.values())


@pytest.mark.parametrize(
    "flags",
    [
        ["--no-pipeline"],
        ["--with-pipeline"],
        ["--all"],
        ["--request-only"],
        ["--outcode", "E8"],
        ["--reanalyze", "--all", "--outcode", "E8"],
        ["--serve", "--with-pipeline", "--no-pipeline"],
        ["--serve", "--scrapers", "openrent"],
        ["--reanalyze", "--max-per-scraper", "2"],
        ["--scrape-only", "--full-scrape"],
        ["--serve", "--full-scrape"],
        ["--max-per-scraper", "0"],
        ["--max-per-scraper", "-1"],
        ["--scrapers", ""],
        ["--scrapers", "openrent,typo"],
    ],
)
def test_orphan_or_ignored_modifiers_fail_before_initialization(monkeypatch, effects, flags):
    _, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert all(not mock.called for mock in calls.values())


@pytest.mark.parametrize(
    "flags,entrypoint,kwargs",
    [
        (
            [],
            "run_pipeline",
            {"max_per_scraper": None, "only_scrapers": None, "full_scrape": False},
        ),
        (
            ["--dry-run", "--max-per-scraper", "2", "--scrapers", " OpenRent,ZOOPLA"],
            "run_pipeline",
            {
                "max_per_scraper": 2,
                "only_scrapers": {"openrent", "zoopla"},
                "full_scrape": False,
                "dry_run": True,
            },
        ),
        (
            ["--scrape-only", "--max-per-scraper", "1"],
            "run_scrape_only",
            {"max_per_scraper": 1, "only_scrapers": None},
        ),
        (["--backfill-commute"], "run_backfill_commute", {}),
        (["--dedup-existing"], "run_dedup_existing", {}),
        (
            ["--reanalyze", "--outcode", "e8,n1", "--request-only"],
            "run_reanalysis",
            {"outcodes": ["E8", "N1"], "reanalyze_all": False, "request_only": True},
        ),
        (
            ["--reanalyze", "--all"],
            "run_reanalysis",
            {"outcodes": None, "reanalyze_all": True, "request_only": False},
        ),
        (
            ["--check-off-market", "--scrapers", "openrent"],
            "run_check_off_market",
            {"only_scrapers": {"openrent"}},
        ),
    ],
)
def test_valid_modes_dispatch_only_the_requested_work(
    monkeypatch, effects, flags, entrypoint, kwargs
):
    settings, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    cli.main()
    calls[entrypoint].assert_awaited_once_with(settings, **kwargs)
    assert {name for name, mock in calls.items() if mock.called} == {"settings", entrypoint}


@pytest.mark.parametrize(
    "flags,enabled",
    [
        (["--serve"], False),
        (["--serve", "--no-pipeline"], False),
        (["--serve", "--with-pipeline"], True),
    ],
)
def test_serving_requires_explicit_collection_activation(monkeypatch, effects, flags, enabled):
    settings, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    cli.main()
    calls["create_app"].assert_called_once_with(settings, run_pipeline=enabled)
    calls["serve"].assert_called_once_with(
        calls["create_app"].return_value,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )
    assert {name for name, mock in calls.items() if mock.called} == {
        "settings",
        "create_app",
        "serve",
    }


def test_thumbnail_mode_never_dispatches_collection(monkeypatch, effects):
    settings, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", "--generate-thumbnails"])
    cli.main()
    calls["backfill_thumbnails"].assert_called_once_with(settings.data_dir)
    assert {name for name, mock in calls.items() if mock.called} == {
        "settings",
        "backfill_thumbnails",
    }


def test_help_has_no_initialization_effects(monkeypatch, effects, capsys):
    _, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "--with-pipeline" in capsys.readouterr().out
    assert all(not mock.called for mock in calls.values())
