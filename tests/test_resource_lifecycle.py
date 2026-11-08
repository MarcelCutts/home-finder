"""Network and provider clients close their resources when their async context exits.

PropertyStorage's context manager is covered in tests/test_db/test_connection_lifecycle.py.
"""

from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.filters.off_market import OffMarketChecker
from home_finder.notifiers.telegram import TelegramNotifier
from home_finder.scrapers.detail_fetcher import DetailFetcher


@pytest.mark.parametrize(
    "make_resource",
    [
        pytest.param(DetailFetcher, id="DetailFetcher"),
        pytest.param(
            lambda: PropertyQualityAnalyzer(api_key="fake-key"), id="PropertyQualityAnalyzer"
        ),
        pytest.param(
            lambda: TelegramNotifier(
                bot_token="fake:token", chat_id=0, web_base_url="", data_dir=""
            ),
            id="TelegramNotifier",
        ),
        pytest.param(OffMarketChecker, id="OffMarketChecker"),
    ],
)
@pytest.mark.parametrize("body_raises", [False, True], ids=["normal_exit", "exception"])
async def test_close_is_awaited_once_when_the_context_exits(make_resource, body_raises) -> None:
    resource = make_resource()
    resource.close = AsyncMock()

    with pytest.raises(ValueError) if body_raises else nullcontext():
        async with resource:
            if body_raises:
                raise ValueError("boom")

    resource.close.assert_awaited_once()
