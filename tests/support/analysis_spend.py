"""Build an analyzer with a fake HTTP transport and inspect paid attempts."""

import httpx2

from home_finder.analysis.analyzer import PropertyQualityAnalyzer

CONTENT = [{"type": "text", "text": "Spending fixture"}]


def quality(storage, policy, handler, **kwargs):
    return PropertyQualityAnalyzer(
        "fixture",
        spend_repository=storage.spend if storage else None,
        spend_policy=policy,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        **kwargs,
    )


async def rows(storage):
    async with (
        storage._read_connection() as conn,
        conn.execute("SELECT * FROM paid_attempts ORDER BY admitted_at, attempt_id") as cur,
    ):
        return [dict(row) for row in await cur.fetchall()]
