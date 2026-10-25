"""Read the durable tables protected by source-consolidation transactions."""

TABLES = (
    "properties",
    "source_listings",
    "property_images",
    "quality_analyses",
    "price_history",
    "status_events",
    "viewing_messages",
    "enquiry_log",
    "source_aliases",
    "paid_attempts",
)


async def snapshot(storage):
    result = {}
    async with storage._read_connection() as conn:
        for table in TABLES:
            async with conn.execute(f"SELECT * FROM {table} ORDER BY 1") as cur:
                result[table] = [tuple(row) for row in await cur.fetchall()]
    return result
