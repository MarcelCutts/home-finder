"""Additive membership history and guards against unjournaled owner changes."""

import aiosqlite


async def create_membership_schema(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_decisions (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL UNIQUE,
            operation TEXT NOT NULL CHECK (operation IN
                ('initial_link', 'staging_link', 'cleanup', 'merge', 'split', 'reversal')),
            actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
            reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
            evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
            expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
            command_json TEXT NOT NULL CHECK (json_valid(command_json)),
            reverses TEXT UNIQUE REFERENCES membership_decisions(decision_id),
            created_at TEXT NOT NULL,
            CHECK ((operation = 'reversal') = (reverses IS NOT NULL))
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_changes (
            decision_id TEXT NOT NULL REFERENCES membership_decisions(decision_id),
            source_unique_id TEXT NOT NULL,
            previous_home_id TEXT REFERENCES homes(home_id),
            next_home_id TEXT REFERENCES homes(home_id),
            PRIMARY KEY (decision_id, source_unique_id),
            CHECK (previous_home_id IS NOT next_home_id)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_membership_changes_source
        ON membership_changes(source_unique_id, decision_id)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_projection_invalidations (
            decision_id TEXT NOT NULL REFERENCES membership_decisions(decision_id),
            home_id TEXT NOT NULL REFERENCES homes(home_id),
            PRIMARY KEY (decision_id, home_id)
        )
    """)
    # No FK to the mutable source row: unpromoted source deletion must not erase
    # a baseline. A subsequent reappearance retains the same provenance.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_baselines (
            source_unique_id TEXT PRIMARY KEY,
            home_id TEXT REFERENCES homes(home_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS membership_origins (
            source_unique_id TEXT PRIMARY KEY,
            home_id TEXT NOT NULL REFERENCES homes(home_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_memberships (
            source_unique_id TEXT PRIMARY KEY REFERENCES membership_baselines(source_unique_id),
            home_id TEXT REFERENCES homes(home_id),
            revision INTEGER REFERENCES membership_decisions(revision)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_memberships_home ON source_memberships(home_id)
    """)
    # Backfill the actual owner of each representation, without resolving the
    # known conflicting provider identities or inventing decisions for history.
    await conn.execute("""
        INSERT INTO membership_baselines SELECT s.unique_id, h.home_id
        FROM source_listings s LEFT JOIN homes h ON h.current_property_id = s.merged_id
        WHERE s.unique_id NOT IN (SELECT source_unique_id FROM membership_baselines)
    """)
    await conn.execute("""
        INSERT INTO source_memberships SELECT source_unique_id, home_id, NULL
        FROM membership_baselines WHERE source_unique_id NOT IN
            (SELECT source_unique_id FROM source_memberships)
    """)
    await conn.execute("""
        INSERT INTO membership_origins SELECT source_unique_id, home_id
        FROM membership_baselines WHERE home_id IS NOT NULL AND source_unique_id NOT IN
            (SELECT source_unique_id FROM membership_origins)
    """)
    async with conn.execute("""
        SELECT 1 FROM source_listings s
        JOIN source_memberships m ON m.source_unique_id = s.unique_id
        LEFT JOIN homes h ON h.home_id = m.home_id
        WHERE s.merged_id IS NOT h.current_property_id LIMIT 1
    """) as cursor:
        if await cursor.fetchone():
            raise ValueError("Cannot establish membership for a missing home")
    for table in (
        "membership_decisions",
        "membership_changes",
        "membership_baselines",
        "membership_origins",
        "membership_projection_invalidations",
    ):
        for operation in ("UPDATE", "DELETE"):
            await conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'Membership evidence is immutable'); END"
            )
    # REPLACE can delete conflicting rows without DELETE triggers when recursive
    # triggers are off. Refuse replacement at INSERT as well as UPDATE/DELETE.
    keys = {
        "membership_decisions": "decision_id = NEW.decision_id OR revision = NEW.revision",
        "membership_changes": (
            "decision_id = NEW.decision_id AND source_unique_id = NEW.source_unique_id"
        ),
        "membership_projection_invalidations": (
            "decision_id = NEW.decision_id AND home_id = NEW.home_id"
        ),
        "membership_baselines": "source_unique_id = NEW.source_unique_id",
        "membership_origins": "source_unique_id = NEW.source_unique_id",
        "source_memberships": "source_unique_id = NEW.source_unique_id",
    }
    for table, condition in keys.items():
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS no_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {condition}) "
            "BEGIN SELECT RAISE(ABORT, 'Membership evidence cannot be replaced'); END"
        )
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_initial_owner BEFORE INSERT ON source_listings
        WHEN NEW.merged_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'Source ownership requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_new_source AFTER INSERT ON source_listings
        BEGIN
            INSERT INTO membership_baselines SELECT NEW.unique_id, NULL
                WHERE NOT EXISTS (SELECT 1 FROM membership_baselines
                    WHERE source_unique_id = NEW.unique_id);
            INSERT INTO source_memberships SELECT NEW.unique_id, NULL, NULL
                WHERE NOT EXISTS (SELECT 1 FROM source_memberships
                    WHERE source_unique_id = NEW.unique_id);
            SELECT RAISE(ABORT, 'Owned source replacement requires a membership decision')
                WHERE EXISTS (SELECT 1 FROM source_memberships
                    WHERE source_unique_id = NEW.unique_id AND home_id IS NOT NULL);
        END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_owner_guard
        BEFORE UPDATE OF merged_id ON source_listings
        WHEN OLD.merged_id IS NOT NEW.merged_id AND (
            NOT EXISTS (SELECT 1 FROM source_memberships WHERE source_unique_id = OLD.unique_id)
            OR NEW.merged_id IS NOT (
                SELECT h.current_property_id FROM source_memberships m
                LEFT JOIN homes h ON h.home_id = m.home_id WHERE m.source_unique_id = OLD.unique_id
            )
        )
        BEGIN SELECT RAISE(ABORT, 'Source ownership requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_source_delete BEFORE DELETE ON source_listings
        WHEN OLD.merged_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'Owned source deletion requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_source_identity
        BEFORE UPDATE OF unique_id ON source_listings WHEN OLD.unique_id != NEW.unique_id
        BEGIN SELECT RAISE(ABORT, 'Source identity cannot bypass membership history'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_reviewed_home_delete BEFORE DELETE ON properties
        WHEN EXISTS (SELECT 1 FROM membership_projection_invalidations i JOIN homes h USING(home_id)
            WHERE h.origin_property_id = OLD.unique_id)
        BEGIN SELECT RAISE(ABORT, 'Reviewed home history cannot be deleted'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_home_binding
        BEFORE UPDATE OF current_property_id ON homes
        WHEN OLD.current_property_id IS NOT NEW.current_property_id
            AND (EXISTS (SELECT 1 FROM source_memberships WHERE home_id = OLD.home_id)
                OR EXISTS (SELECT 1 FROM membership_projection_invalidations
                    WHERE home_id = OLD.home_id))
        BEGIN SELECT RAISE(ABORT, 'Owned home reassociation requires a membership decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_projection_guard BEFORE UPDATE ON source_memberships
        WHEN NEW.source_unique_id != OLD.source_unique_id OR NOT EXISTS (
            SELECT 1 FROM membership_changes c JOIN membership_decisions d USING(decision_id)
            WHERE c.source_unique_id = OLD.source_unique_id
                AND c.previous_home_id IS OLD.home_id AND c.next_home_id IS NEW.home_id
                AND d.revision = NEW.revision AND NEW.revision > COALESCE(OLD.revision, 0)
        )
        BEGIN SELECT RAISE(ABORT, 'Membership projection requires a new recorded decision'); END
    """)
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS membership_projection_delete
        BEFORE DELETE ON source_memberships
        BEGIN SELECT RAISE(ABORT, 'Membership projection cannot be deleted'); END
    """)
