"""CLI intent is validated before settings, paid work or process startup."""

from contextlib import nullcontext
from itertools import combinations
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from home_finder import main as cli
from home_finder.config import Settings, new_runtime_analysis_config

MODES = (
    "--check-config",
    "--dry-run",
    "--scrape-only",
    "--backfill-commute",
    "--dedup-existing",
    "--generate-thumbnails",
    "--reanalyze",
    "--check-off-market",
    "--serve",
)


@pytest.fixture(autouse=True)
def _isolate_cli_logging(monkeypatch):
    """Do not bind global cached loggers to this test's temporary capture streams."""
    monkeypatch.setattr(cli, "configure_logging", Mock())


@pytest.fixture
def effects(monkeypatch, tmp_path):
    settings = Settings(
        analysis_config=new_runtime_analysis_config(),
        database_path=str(tmp_path / "home_finder.db"),
        telegram_bot_token="123456:offline-fixture",
        telegram_chat_id=123456,
        anthropic_api_key="",
    )
    effects = {"settings": Mock(return_value=settings)}
    monkeypatch.setattr(cli, "Settings", effects["settings"])
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
        ["--check-config", "--scrapers", "openrent"],
        ["--check-config", "--max-per-scraper", "1"],
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


def test_config_check_reports_missing_prerequisites_without_starting_work(
    monkeypatch, effects, capsys, tmp_path
):
    settings, calls = effects
    settings.telegram_bot_token = SecretStr("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["home-finder", "--check-config"])
    cli.main()
    output = capsys.readouterr().out
    assert "No .env in the current directory" in output
    assert "Dashboard: unavailable" in output
    assert "Quality analysis: skipped" in output
    assert "Map tiles: disabled" in output
    assert "unverified" in output
    assert {name for name, mock in calls.items() if mock.called} == {"settings"}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("flags", [[], ["--serve", "--with-pipeline"]])
@pytest.mark.parametrize("missing", ["token", "chat"])
def test_notifying_modes_require_recipient_before_any_work(monkeypatch, effects, flags, missing):
    settings, calls = effects
    if missing == "token":
        settings.telegram_bot_token = SecretStr("")
    else:
        settings.telegram_chat_id = 0
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert {name for name, mock in calls.items() if mock.called} == {"settings"}


@pytest.mark.parametrize(
    "flags,entrypoint", [(["--dry-run"], "run_pipeline"), (["--scrape-only"], "run_scrape_only")]
)
def test_non_notifying_collection_needs_no_telegram(monkeypatch, effects, flags, entrypoint):
    settings, calls = effects
    settings.telegram_bot_token = SecretStr("")
    settings.telegram_chat_id = 0
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])
    cli.main()
    calls[entrypoint].assert_awaited_once()


@pytest.mark.parametrize(
    "assignment,field",
    [
        ("HOME_FINDER_MIN_PRICE=3000", "min_price"),
        ("HOME_FINDER_MIN_BEDROOMS=3", "min_bedrooms"),
        ("HOME_FINDER_SEARCH_AREAS=private-invalid-value", "search_areas"),
        ("HOME_FINDER_FURNISH_TYPES=private-invalid-value", "furnish_types"),
    ],
)
def test_config_errors_identify_which_search_setting_to_fix(
    monkeypatch, tmp_path, assignment, field
):
    env_file = tmp_path / ".env"
    env_file.write_text(assignment)
    monkeypatch.setattr("sys.argv", ["home-finder", "--check-config", "--env-file", str(env_file)])
    logger = Mock()
    monkeypatch.setattr(cli, "logger", logger)
    with pytest.raises(SystemExit):
        cli.main()
    assert field in logger.error.call_args.kwargs["error"]
    assert "private-invalid-value" not in str(logger.error.call_args)


def test_explicit_env_file_is_loaded_by_the_cli(monkeypatch, capsys, tmp_path):
    env_file = tmp_path / "private.env"
    env_file.write_text(
        "HOME_FINDER_WEB_OWNER_PASSWORD=private-password-long-enough\n"
        "HOME_FINDER_ANTHROPIC_API_KEY=private-key\n"
        "HOME_FINDER_MAX_INITIAL_ANALYSES_PER_RUN=7\n"
    )
    monkeypatch.setattr("sys.argv", ["home-finder", "--check-config", "--env-file", str(env_file)])
    cli.main()
    output = capsys.readouterr().out
    assert "Dashboard: owner access configured" in output
    assert "Quality analysis: deferred; requires explicit ANALYSIS_SPEND_POLICY" in output
    assert "7 initial" in output
    assert "private-password-long-enough" not in output
    assert "private-key" not in output
    assert list(tmp_path.iterdir()) == [env_file]


@pytest.mark.parametrize("mode", ["--check-config", "--dry-run"])
@pytest.mark.parametrize("explicit_env_file", [False, True])
def test_malformed_dotenv_exits_before_success_or_paid_work(
    monkeypatch, capsys, caplog, tmp_path, effects, mode, explicit_env_file
):
    _, calls = effects
    monkeypatch.setattr(cli, "Settings", Settings)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    env_file = tmp_path / ("selected.env" if explicit_env_file else ".env")
    env_file.write_text(
        "HOME_FINDER_ANTHROPIC_API_KEY=private-key-must-not-appear\n"
        'HOME_FINDER_ANALYSIS_SPEND_POLICY={"per_call_usd":"1",'
        '"daily_usd":"2","monthly_usd":"3"}\n'
        'HOME_FINDER_ENABLE_QUALITY_FILTER="false\n'
    )
    flags = [mode]
    if explicit_env_file:
        flags.extend(["--env-file", str(env_file)])
    monkeypatch.setattr("sys.argv", ["home-finder", *flags])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    cli.logger.error.assert_called_once()
    error = cli.logger.error.call_args.kwargs["error"]
    assert env_file.name in error
    assert "line 3" in error
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-key-must-not-appear" not in (
        str(cli.logger.error.call_args) + caplog.text + output.err
    )
    assert "exc_info" not in cli.logger.error.call_args.kwargs
    assert all(not mock.called for mock in calls.values())
    assert list(tmp_path.iterdir()) == [env_file]


def test_missing_explicit_env_file_is_not_silently_ignored(monkeypatch, effects, tmp_path):
    _, calls = effects
    monkeypatch.setattr("sys.argv", ["home-finder", "--env-file", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert all(not mock.called for mock in calls.values())


@pytest.mark.parametrize(
    "assignment",
    [
        "HOME_FINDER_TELEGRAM_CHAT_ID=private-invalid-value",
        'HOME_FINDER_ANALYSIS_SPEND_POLICY={"private-invalid-value":',
        "HOME_FINDER_MIN_PRICE=3000\nHOME_FINDER_ANTHROPIC_API_KEY=private-invalid-value",
    ],
)
def test_invalid_configuration_logs_no_values_or_tracebacks(monkeypatch, tmp_path, assignment):
    env_file = tmp_path / ".env"
    env_file.write_text(assignment)
    monkeypatch.setattr("sys.argv", ["home-finder", "--env-file", str(env_file), "--check-config"])
    logger = Mock()
    monkeypatch.setattr(cli, "logger", logger)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    logger.error.assert_called_once()
    assert "private-invalid-value" not in str(logger.error.call_args)
    assert "exc_info" not in logger.error.call_args.kwargs
    assert "default factory" not in logger.error.call_args.kwargs["error"]
