"""Offline capability report; never open a database or contact a provider."""

from urllib.parse import urlsplit

from home_finder.config import Settings


def configuration_summary(settings: Settings) -> list[str]:
    """Report effective feature prerequisites without exposing credential values."""
    lines = [
        "Configuration valid. Offline check only; credentials and connectivity are unverified."
    ]
    password = settings.web_owner_password.get_secret_value()
    if len(password) >= 24 and password.isascii():
        lines.append("Dashboard: owner access configured.")
    else:
        lines.append("Dashboard: unavailable; set WEB_OWNER_PASSWORD to 24+ ASCII characters.")

    telegram = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    if telegram:
        lines.append("Telegram delivery: configured.")
    else:
        lines.append(
            "Telegram delivery: unavailable; requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )
        lines.append(
            "Collect without Telegram using --dry-run (still permits configured paid analysis)."
        )

    if settings.traveltime_app_id and settings.traveltime_api_key:
        lines.append("Commute measurements: configured (advisory).")
    elif settings.traveltime_app_id or settings.traveltime_api_key:
        lines.append(
            "Commute measurements: incomplete; set both TRAVELTIME_APP_ID and TRAVELTIME_API_KEY."
        )
    else:
        lines.append("Commute measurements: not configured; commute remains unknown.")

    policy = settings.analysis_spend_policy
    if not settings.enable_quality_filter:
        lines.append("Quality analysis: disabled by ENABLE_QUALITY_FILTER.")
    elif not settings.anthropic_api_key:
        lines.append("Quality analysis: skipped; requires ANTHROPIC_API_KEY.")
    elif policy is None:
        lines.append("Quality analysis: deferred; requires explicit ANALYSIS_SPEND_POLICY caps.")
    elif policy.per_call_usd == 0:
        lines.append(
            "Quality analysis: deferred; ANALYSIS_SPEND_POLICY authorizes zero per-call spend."
        )
    else:
        lines.append(
            "Quality analysis: configured; ledger and per-request budget checks still apply."
        )

    if not settings.telegram_webhook_secret:
        lines.append("Telegram callbacks: disabled; TELEGRAM_WEBHOOK_SECRET is unset.")
    else:
        origin = urlsplit(settings.web_base_url)
        owner_known = settings.telegram_owner_user_id is not None or settings.telegram_chat_id > 0
        if not telegram or not owner_known:
            lines.append(
                "Telegram callbacks: incomplete; requires bot/chat and an owner user ID for groups."
            )
        elif origin.scheme != "https" or origin.port not in {None, 443, 80, 88, 8443}:
            lines.append(
                "Telegram callbacks: requires HTTPS WEB_BASE_URL on port 443, 80, 88 or 8443."
            )
        else:
            lines.append("Telegram callbacks: configured; --serve registers the webhook.")

    if settings.carto_basemap_api_key:
        lines.append(
            "Map tiles: configured; restrict the browser-visible CARTO key to your origins."
        )
    else:
        lines.append(
            "Map tiles: disabled; CARTO_BASEMAP_API_KEY is unset (grid browsing still works)."
        )
    if not settings.get_search_areas():
        lines.append("Search: SEARCH_AREAS is empty; no areas will be collected.")
    if "max_analysis_per_run" in settings.model_fields_set:
        lines.append(
            "Legacy MAX_ANALYSIS_PER_RUN supplies each unset queue cap; it is not a total."
        )
    concurrency = (
        min(settings.quality_concurrency, policy.max_in_flight)
        if policy
        else settings.quality_concurrency
    )
    lines.append(
        f"Assessment limits: {settings.max_initial_analyses_per_run} initial, "
        f"{settings.max_reanalyses_per_run} reanalyses per run; concurrency ceiling {concurrency}."
    )
    lines.append(
        "Scheduler: only --serve --with-pipeline enables recurring collection; "
        f"waits {settings.pipeline_interval_minutes} minutes after completion "
        "plus 0-5 minutes jitter."
    )
    lines.append(
        "Setting names above use the HOME_FINDER_ prefix. See .env.example for all options."
    )
    return lines
