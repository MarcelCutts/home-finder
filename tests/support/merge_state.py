"""Read every table, so "unchanged after a refused merge" cannot miss one."""


async def snapshot(storage):
    """Return each table's rows in a stable order.

    The table list is read from the schema rather than written out, so tables
    added by later migrations (paid attempts, visual checkpoints, notification
    deliveries) are compared too.
    """
    result = {}
    async with storage._read_connection() as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        ) as cur:
            tables = [row[0] for row in await cur.fetchall()]
        for table in tables:
            async with conn.execute(f'SELECT * FROM "{table}" ORDER BY 1') as cur:
                result[table] = [tuple(row) for row in await cur.fetchall()]
    return result
