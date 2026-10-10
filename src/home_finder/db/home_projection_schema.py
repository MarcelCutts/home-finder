"""Durable per-home input generations and immutable listing-projection receipts."""

import aiosqlite


def _invalidate(select_homes: str, cause: str) -> str:
    """SQL fragments are internal constants, never caller-provided identifiers."""
    return f"""
        INSERT INTO home_projection_generations (home_id, input_generation, cause)
        SELECT home_id, 0, '{cause}' FROM ({select_homes}) AS affected
        WHERE home_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM home_projection_generations g WHERE g.home_id = affected.home_id
        );
        UPDATE home_projection_generations
        SET input_generation = input_generation + 1, cause = '{cause}'
        WHERE home_id IN ({select_homes});
    """


async def create_home_projection_schema(conn: aiosqlite.Connection) -> None:
    # Invalidations now include ordinary automatic decisions. Their presence
    # alone no longer means that an operator reviewed/protected a saved home.
    await conn.execute("DROP TRIGGER IF EXISTS membership_reviewed_home_delete")
    await conn.execute("""
        CREATE TRIGGER membership_reviewed_home_delete BEFORE DELETE ON properties
        WHEN EXISTS (
            SELECT 1 FROM membership_projection_invalidations i JOIN homes h USING(home_id)
            JOIN membership_decisions d USING(decision_id)
            WHERE h.origin_property_id = OLD.unique_id
                AND d.operation IN ('merge', 'split', 'reversal')
        )
        BEGIN SELECT RAISE(ABORT, 'Reviewed home history cannot be deleted'); END
    """)
    await conn.execute("DROP TRIGGER IF EXISTS membership_home_binding")
    await conn.execute("""
        CREATE TRIGGER membership_home_binding BEFORE UPDATE OF current_property_id ON homes
        WHEN OLD.current_property_id IS NOT NEW.current_property_id AND (
            EXISTS (SELECT 1 FROM source_memberships WHERE home_id = OLD.home_id)
            OR EXISTS (
                SELECT 1 FROM membership_projection_invalidations i
                JOIN membership_decisions d USING(decision_id)
                WHERE i.home_id = OLD.home_id AND d.operation IN ('merge', 'split', 'reversal')
            )
        )
        BEGIN SELECT RAISE(ABORT, 'Owned home reassociation requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_projection_generations (
            home_id TEXT PRIMARY KEY REFERENCES homes(home_id),
            input_generation INTEGER NOT NULL CHECK (input_generation >= 0),
            cause TEXT NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_listing_projection_snapshots (
            snapshot_id TEXT PRIMARY KEY CHECK (length(snapshot_id) = 64),
            home_id TEXT NOT NULL REFERENCES homes(home_id),
            policy_version INTEGER NOT NULL CHECK (policy_version > 0),
            input_generation INTEGER NOT NULL CHECK (input_generation > 0),
            membership_revision INTEGER NOT NULL CHECK (membership_revision >= 0),
            listing_material_sha256 TEXT NOT NULL CHECK (length(listing_material_sha256) = 64),
            payload_json TEXT NOT NULL CHECK (
                json_valid(payload_json) AND length(CAST(payload_json AS BLOB)) <= 4000000
            ),
            created_at TEXT NOT NULL,
            UNIQUE(home_id, policy_version, input_generation)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS home_listing_projection_heads (
            home_id TEXT PRIMARY KEY REFERENCES homes(home_id),
            snapshot_id TEXT NOT NULL REFERENCES home_listing_projection_snapshots(snapshot_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_projection_consumptions (
            decision_id TEXT NOT NULL,
            home_id TEXT NOT NULL,
            consumer TEXT NOT NULL CHECK (consumer = 'listing_inputs'),
            snapshot_id TEXT NOT NULL REFERENCES home_listing_projection_snapshots(snapshot_id),
            PRIMARY KEY(decision_id, home_id, consumer),
            FOREIGN KEY(decision_id, home_id)
                REFERENCES membership_projection_invalidations(decision_id, home_id)
        )
    """)
    # Mark existing homes pending. Migration does not certify old caches or
    # rewrite original prices, availability, assessments, personal state or images.
    await conn.execute("""
        INSERT INTO home_projection_generations
        SELECT home_id, 1, 'historical_baseline' FROM homes h
        WHERE NOT EXISTS (SELECT 1 FROM home_projection_generations g WHERE g.home_id = h.home_id)
    """)
    for table in ("home_listing_projection_snapshots", "membership_projection_consumptions"):
        for action in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                f"BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Projection evidence is immutable'); END"
            )
    for table, key in (
        ("home_projection_generations", "home_id = NEW.home_id"),
        ("home_listing_projection_heads", "home_id = NEW.home_id"),
        (
            "home_listing_projection_snapshots",
            "snapshot_id = NEW.snapshot_id OR (home_id = NEW.home_id "
            "AND policy_version = NEW.policy_version AND input_generation = NEW.input_generation)",
        ),
        (
            "membership_projection_consumptions",
            "decision_id = NEW.decision_id AND home_id = NEW.home_id AND consumer = NEW.consumer",
        ),
    ):
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS no_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}) "
            "BEGIN SELECT RAISE(ABORT, 'Projection evidence cannot be replaced'); END"
        )
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS home_projection_generation_guard
        BEFORE UPDATE ON home_projection_generations
        WHEN NEW.home_id != OLD.home_id OR NEW.input_generation != OLD.input_generation + 1
        BEGIN SELECT RAISE(ABORT, 'Projection input generation must advance exactly once'); END
    """)
    for table in ("home_projection_generations", "home_listing_projection_heads"):
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS retain_{table} BEFORE DELETE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'Projection history cannot be reset'); END"
        )
    for action in ("INSERT", "UPDATE"):
        await conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS home_projection_head_{action.lower()} BEFORE {action}
            ON home_listing_projection_heads WHEN NOT EXISTS (
                SELECT 1 FROM home_listing_projection_snapshots s
                JOIN home_projection_generations g USING(home_id)
                WHERE s.snapshot_id = NEW.snapshot_id AND s.home_id = NEW.home_id
                    AND s.input_generation = g.input_generation
            )
            BEGIN SELECT RAISE(ABORT, 'Projection head requires current home inputs'); END
        """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS home_projection_head_identity
        BEFORE UPDATE ON home_listing_projection_heads
        WHEN OLD.home_id != NEW.home_id
        BEGIN SELECT RAISE(ABORT, 'Projection head identity cannot change'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_projection_consumption_guard
        BEFORE INSERT ON membership_projection_consumptions WHEN NOT EXISTS (
            SELECT 1 FROM home_listing_projection_snapshots s
            JOIN home_projection_generations g USING(home_id)
            JOIN home_listing_projection_heads h USING(home_id)
            JOIN membership_decisions d ON d.decision_id = NEW.decision_id
            WHERE s.snapshot_id = NEW.snapshot_id AND s.home_id = NEW.home_id
                AND h.snapshot_id = s.snapshot_id AND s.input_generation = g.input_generation
                AND s.membership_revision >= d.revision
        )
        BEGIN SELECT RAISE(ABORT, 'Invalidation acknowledgement requires current projection'); END
    """)

    triggers = [
        (
            "new_home_projection",
            "AFTER INSERT ON home_routes",
            "SELECT NEW.home_id AS home_id",
            "new_home",
        ),
        (
            "route_projection_changed",
            "AFTER UPDATE ON home_routes",
            "SELECT NEW.home_id AS home_id UNION SELECT OLD.target_home_id "
            "UNION SELECT NEW.target_home_id",
            "home_route",
        ),
        (
            "membership_projection_requested",
            "AFTER INSERT ON membership_projection_invalidations",
            "SELECT NEW.home_id AS home_id",
            "membership_decision",
        ),
        (
            "observed_projection_changed",
            "AFTER INSERT ON source_observation_fields",
            "SELECT home_id FROM source_memberships WHERE source_unique_id = NEW.source_unique_id",
            "source_observation",
        ),
    ]
    # All retained source fields used by the snapshot are covered. No-op writes
    # do not invalidate; first/last seen updates still change evidence freshness.
    columns = (
        "source",
        "source_id",
        "url",
        "title",
        "price_pcm",
        "bedrooms",
        "address",
        "postcode",
        "latitude",
        "longitude",
        "description",
        "image_url",
        "available_from",
        "first_seen",
        "last_seen",
        "merged_id",
        "is_backfilled",
        "is_off_market",
        "off_market_since",
        "off_market_reason",
        "last_checked_at",
        "text_json",
    )
    changed = " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)
    triggers.append(
        (
            "source_projection_changed",
            f"AFTER UPDATE ON source_listings WHEN {changed}",
            "SELECT home_id FROM homes WHERE current_property_id IN (OLD.merged_id, NEW.merged_id)",
            "retained_source",
        )
    )
    triggers.append(
        (
            "home_binding_projection_changed",
            "AFTER UPDATE OF current_property_id ON homes "
            "WHEN OLD.current_property_id IS NOT NEW.current_property_id",
            "SELECT NEW.home_id AS home_id",
            "home_binding",
        )
    )
    for name, when, select, cause in triggers:
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {name} {when} BEGIN {_invalidate(select, cause)} END"
        )
