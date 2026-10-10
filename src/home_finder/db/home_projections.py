"""Rebuild listing inputs atomically; never acknowledge unfinished downstream consumers."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from home_finder.db.listing_members import member_from_row
from home_finder.db.listing_text import captured_text
from home_finder.db.observations import (
    ConnectionScope,
    ObservationTransaction,
    projected_field_rows,
)
from home_finder.logging import get_logger
from home_finder.models.home_group import HomeRoute
from home_finder.models.home_projection import (
    LISTING_PROJECTION_VERSION,
    MAX_HOME_PROJECTION_BYTES,
    HomeListingProjection,
    ProjectedListingInput,
    ProjectionBuild,
    ProjectionStatus,
)
from home_finder.models.identity import SourceIdentityInput, resolve_source_identity
from home_finder.models.listing_text import merge_listing_text
from home_finder.models.observations import canonical_json, project_field_rows, sha256_text

logger = get_logger(__name__)

_RETAINED_COLUMNS = (
    "title",
    "description",
    "bedrooms",
    "address",
    "postcode",
    "latitude",
    "longitude",
    "image_url",
    "available_from",
    "first_seen",
    "last_seen",
    "last_checked_at",
    "off_market_since",
    "off_market_reason",
)


class ProjectionConflict(ValueError):
    """A reviewed input generation changed or a stored projection failed validation."""


async def listing_projection(conn: aiosqlite.Connection, home_id: str) -> HomeListingProjection:
    """Build a bounded candidate from one caller-owned database snapshot."""
    async with conn.execute(
        "SELECT h.current_property_id, r.target_home_id, r.state, r.revision, g.input_generation "
        "FROM homes h JOIN home_routes r USING(home_id) "
        "JOIN home_projection_generations g USING(home_id) WHERE h.home_id = ?",
        (home_id,),
    ) as cur:
        home = await cur.fetchone()
    if home is None:
        raise ProjectionConflict("unknown_home_projection")
    async with conn.execute(
        "SELECT COALESCE(MAX(d.revision), 0) FROM membership_projection_invalidations i "
        "JOIN membership_decisions d USING(decision_id) WHERE i.home_id = ?",
        (home_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    revision = max(row[0], home["revision"] or 0)
    async with conn.execute(
        "SELECT s.*, m.revision AS membership_revision FROM source_memberships m "
        "JOIN source_listings s ON s.unique_id = m.source_unique_id "
        "WHERE m.home_id = ? ORDER BY s.unique_id LIMIT 501",
        (home_id,),
    ) as cur:
        sources = list(await cur.fetchall())
    if len(sources) > 500:
        raise ValueError("Home projection exceeds the 500-member review bound")
    listings = []
    size = 0
    for source in sources:
        if source["merged_id"] != home["current_property_id"]:
            raise ProjectionConflict("source_ownership_projection_mismatch")
        revision = max(revision, source["membership_revision"] or 0)
        fields = await projected_field_rows(conn, source["unique_id"])
        member = member_from_row(source)
        member = member.model_copy(
            update={
                "text": merge_listing_text(
                    member.text, await captured_text(conn, source["unique_id"])
                )
            }
        )
        material = {
            name: sha256_text(
                canonical_json(
                    sorted(
                        {
                            (f["field_state"], f["value_json"])
                            for f in fields
                            if f["field_name"] == name
                        }
                    )
                )
            )
            for name in {f["field_name"] for f in fields}
        }
        item = ProjectedListingInput(
            member=member,
            identity=resolve_source_identity(
                SourceIdentityInput(
                    unique_id=source["unique_id"],
                    source=source["source"],
                    source_id=source["source_id"],
                    url=source["url"],
                    is_backfilled=bool(source["is_backfilled"]),
                    merged_id=source["merged_id"],
                )
            ),
            observed=project_field_rows(source["unique_id"], fields),
            field_material_hashes=material,
            retained_columns={key: source[key] for key in _RETAINED_COLUMNS},
        )
        size += len(item.model_dump_json().encode())
        if size > MAX_HOME_PROJECTION_BYTES:
            raise ValueError("Home projection exceeds the 4 MB review bound")
        listings.append(item)
    result = HomeListingProjection(
        home_id=home_id,
        property_id=home["current_property_id"],
        route=HomeRoute(
            home_id=home_id, target_home_id=home["target_home_id"], state=home["state"]
        ),
        input_generation=home["input_generation"],
        membership_revision=revision,
        listings=tuple(listings),
    )
    if len(result.payload_json.encode()) > MAX_HOME_PROJECTION_BYTES:
        raise ValueError("Home projection exceeds the 4 MB review bound")
    return result


def decode_listing_projection(row: aiosqlite.Row) -> HomeListingProjection:
    value = HomeListingProjection.model_validate_json(row["payload_json"])
    if (
        value.sha256 != row["snapshot_id"]
        or value.listing_material_sha256 != row["listing_material_sha256"]
        or value.home_id != row["home_id"]
        or value.input_generation != row["input_generation"]
        or value.membership_revision != row["membership_revision"]
        or value.policy_version != row["policy_version"]
    ):
        raise ProjectionConflict("stored_projection_checksum_mismatch")
    return value


async def projection_status(
    conn: aiosqlite.Connection, *, limit: int, pending_only: bool
) -> list[ProjectionStatus]:
    """Read bounded currency metadata inside the caller's owned snapshot."""
    if not 1 <= limit <= 100:
        raise ValueError("Projection status requires a limit between 1 and 100")
    async with conn.execute(
        "SELECT g.home_id, g.input_generation, g.cause, h.snapshot_id, "
        "s.input_generation AS built_generation, "
        "(s.snapshot_id IS NULL OR s.input_generation != g.input_generation "
        "OR s.policy_version != ?) AS pending "
        "FROM home_projection_generations g "
        "LEFT JOIN home_listing_projection_heads h USING(home_id) "
        "LEFT JOIN home_listing_projection_snapshots s ON s.snapshot_id = h.snapshot_id "
        "WHERE (? = 0 OR pending) ORDER BY g.home_id LIMIT ?",
        (LISTING_PROJECTION_VERSION, pending_only, limit),
    ) as cur:
        return [ProjectionStatus.model_validate(dict(row)) for row in await cur.fetchall()]


class HomeProjectionRepository:
    def __init__(
        self, read_connection: ConnectionScope, transaction: ObservationTransaction
    ) -> None:
        self._read_connection = read_connection
        self._transaction = transaction

    async def inspect(self, home_id: str) -> HomeListingProjection:
        """Preview exact current inputs without writing or acknowledging anything."""
        async with self._read_connection() as conn:
            return await listing_projection(conn, home_id)

    async def status(
        self, *, limit: int = 100, pending_only: bool = False
    ) -> list[ProjectionStatus]:
        async with self._read_connection() as conn:
            return await projection_status(conn, limit=limit, pending_only=pending_only)

    async def current(self, home_id: str) -> HomeListingProjection | None:
        """Return only an acknowledged current input snapshot; stale heads remain in history."""
        async with self._read_connection() as conn:
            async with conn.execute(
                "SELECT s.* FROM home_listing_projection_heads h "
                "JOIN home_listing_projection_snapshots s ON s.snapshot_id = h.snapshot_id "
                "JOIN home_projection_generations g ON g.home_id = h.home_id "
                "WHERE h.home_id = ? AND s.input_generation = g.input_generation "
                "AND s.policy_version = ?",
                (home_id, LISTING_PROJECTION_VERSION),
            ) as cur:
                row = await cur.fetchone()
            return decode_listing_projection(row) if row else None

    async def get(self, snapshot_id: str) -> HomeListingProjection | None:
        """Read a retained historical snapshot without presenting it as current."""
        async with self._read_connection() as conn:
            async with conn.execute(
                "SELECT * FROM home_listing_projection_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ) as cur:
                row = await cur.fetchone()
            return decode_listing_projection(row) if row else None

    async def rebuild(
        self, home_id: str, *, expected_generation: int | None = None
    ) -> ProjectionBuild:
        """Own candidate read, immutable snapshot, head and listing-input acknowledgement."""
        try:
            async with self._transaction() as conn:
                snapshot = await listing_projection(conn, home_id)
                if (
                    expected_generation is not None
                    and snapshot.input_generation != expected_generation
                ):
                    raise ProjectionConflict("stale_home_input_generation")
                async with conn.execute(
                    "SELECT * FROM home_listing_projection_snapshots "
                    "WHERE home_id = ? AND policy_version = ? AND input_generation = ?",
                    (home_id, snapshot.policy_version, snapshot.input_generation),
                ) as cur:
                    existing = await cur.fetchone()
                if existing is not None:
                    if decode_listing_projection(existing) != snapshot:
                        raise ProjectionConflict("inputs_changed_without_invalidation")
                else:
                    await conn.execute(
                        "INSERT INTO home_listing_projection_snapshots "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            snapshot.sha256,
                            home_id,
                            snapshot.policy_version,
                            snapshot.input_generation,
                            snapshot.membership_revision,
                            snapshot.listing_material_sha256,
                            snapshot.payload_json,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                await conn.execute(
                    "UPDATE home_listing_projection_heads SET snapshot_id = ? WHERE home_id = ?",
                    (snapshot.sha256, home_id),
                )
                await conn.execute(
                    "INSERT INTO home_listing_projection_heads SELECT ?, ? "
                    "WHERE NOT EXISTS (SELECT 1 FROM home_listing_projection_heads "
                    "WHERE home_id = ?)",
                    (home_id, snapshot.sha256, home_id),
                )
                cur = await conn.execute(
                    "INSERT INTO membership_projection_consumptions "
                    "SELECT i.decision_id, i.home_id, 'listing_inputs', ? "
                    "FROM membership_projection_invalidations i "
                    "WHERE i.home_id = ? AND NOT EXISTS ("
                    "SELECT 1 FROM membership_projection_consumptions c "
                    "WHERE c.decision_id = i.decision_id AND c.home_id = i.home_id "
                    "AND c.consumer = 'listing_inputs')",
                    (snapshot.sha256, home_id),
                )
                acknowledged = cur.rowcount
                await cur.close()
                result = ProjectionBuild(
                    home_id=home_id,
                    snapshot_id=snapshot.sha256,
                    input_generation=snapshot.input_generation,
                    listing_material_sha256=snapshot.listing_material_sha256,
                    membership_invalidations_acknowledged=acknowledged,
                    replayed=existing is not None,
                )
        except BaseException as exc:
            logger.warning(
                "home_projection_rebuild_failed", home_id=home_id, error_type=type(exc).__name__
            )
            raise
        logger.info(
            "home_listing_projection_rebuilt",
            **result.model_dump(),
            members=len(snapshot.listings),
            payload_bytes=len(snapshot.payload_json.encode()),
        )
        return result

    async def rebuild_pending(self, *, limit: int = 25) -> list[ProjectionBuild]:
        """Commit each bounded home independently; retries reuse completed snapshots."""
        pending = await self.status(limit=limit, pending_only=True)
        result = []
        for item in pending:
            try:
                result.append(
                    await self.rebuild(item.home_id, expected_generation=item.input_generation)
                )
            except ProjectionConflict as exc:
                if str(exc) != "stale_home_input_generation":
                    raise
                logger.info("home_projection_refresh_deferred", home_id=item.home_id)
        return result
