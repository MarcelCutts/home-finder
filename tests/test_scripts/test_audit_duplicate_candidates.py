"""Synthetic offline coverage: review candidates never become identity decisions."""

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from scripts.audit_duplicate_candidates import audit_database, main, open_snapshot


@pytest.fixture
def database(tmp_path: Path) -> Path:
    # URI-significant characters must remain part of the filename in mode=ro.
    path = tmp_path / "private ?# snapshot.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE properties (
                unique_id TEXT PRIMARY KEY, source TEXT, source_id TEXT, url TEXT,
                title TEXT, price_pcm INTEGER, bedrooms INTEGER, address TEXT,
                postcode TEXT, latitude REAL, longitude REAL, sources TEXT
            );
            CREATE TABLE source_listings (
                unique_id TEXT PRIMARY KEY, source TEXT, url TEXT, merged_id TEXT,
                is_backfilled INTEGER DEFAULT 0
            );
        """)
    finally:
        conn.close()
    return path


def add_home(database: Path, home_id: str, **overrides: object) -> None:
    source, _, source_id = home_id.partition(":")
    values = {
        "unique_id": home_id,
        "source": source,
        "source_id": source_id,
        "url": f"https://example.com/private/{home_id}",
        "title": "Private example home",
        "price_pcm": 2000,
        "bedrooms": 1,
        "address": "Flat 1, 10 Example Road, London",
        "postcode": "N1 1AA",
        "latitude": 51.54,
        "longitude": -0.09,
        # Deliberately stale: the CLI must derive overlap from real membership rows.
        "sources": '["zoopla"]',
        **overrides,
    }
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            f"INSERT INTO properties ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )
        conn.commit()
    finally:
        conn.close()


def add_member(
    database: Path, listing_id: str, home_id: str | None, *, backfilled: bool = False
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            "INSERT INTO source_listings VALUES (?, ?, ?, ?, ?)",
            (
                listing_id,
                listing_id.partition(":")[0],
                f"https://example.com/private/{listing_id}",
                home_id,
                int(backfilled),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_overlapping_already_merged_groups_are_candidates(database: Path) -> None:
    add_home(database, "rightmove:1")
    add_home(database, "onthemarket:2")
    for member, home in (
        ("rightmove:1", "rightmove:1"),
        ("onthemarket:1", "rightmove:1"),
        ("onthemarket:2", "onthemarket:2"),
        ("rightmove:2", "onthemarket:2"),
        ("rightmove:3", "onthemarket:2"),
    ):
        add_member(database, member, home, backfilled=member == "rightmove:3")

    report = audit_database(database)

    assert report["summary"]["same_platform_pairs_returned"] == 1
    [pair] = report["candidates"]
    assert pair["overlapping_platforms"] == ["onthemarket", "rightmove"]
    assert len(pair["left"]["source_listings"]) == 3  # sorted canonical IDs
    assert pair["left"]["source_listings"][-1]["is_backfilled"] == 1
    assert len(pair["right"]["source_listings"]) == 2


@pytest.mark.parametrize("other_source", ["rightmove", "openrent"])
def test_same_and_different_canonical_platforms_are_included(
    database: Path, other_source: str
) -> None:
    add_home(database, "rightmove:1")
    add_home(database, f"{other_source}:2")
    report = audit_database(database)
    assert report["summary"]["candidate_pairs_returned"] == 1
    assert report["summary"]["same_platform_pairs_returned"] == (other_source == "rightmove")


def test_members_of_same_home_and_unlinked_rows_are_not_compared(database: Path) -> None:
    add_home(database, "rightmove:1")
    add_member(database, "rightmove:1", "rightmove:1")
    add_member(database, "rightmove:2", "rightmove:1")
    add_member(database, "openrent:3", "rightmove:1")
    add_member(database, "openrent:4", None)
    add_member(database, "openrent:5", "missing:home")

    report = audit_database(database)

    assert report["summary"]["homes_loaded"] == 1
    assert report["summary"]["comparisons_examined"] == 0
    assert report["candidates"] == []


def test_same_building_remains_unconfirmed_even_with_high_score(database: Path) -> None:
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2", address="Flat 2, 10 Example Road, London")

    report = audit_database(database)

    [pair] = report["candidates"]
    assert pair["metadata_score"]["total"] == 125
    assert pair["identity_status"] == "unconfirmed"
    assert pair["score_status"] == "uncalibrated"
    assert pair["image_evidence"] == "not_compared"
    assert "confidence" not in pair["metadata_score"]
    assert "is_match" not in pair
    assert any("Different flats in the same building" in note for note in report["limitations"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"bedrooms": 2},
        {"price_pcm": 3000},
        {"price_pcm": 0},
        {"address": "Elsewhere Lane", "postcode": "N1 9ZZ", "latitude": 52, "longitude": 0},
        {"postcode": None, "latitude": None, "longitude": None},
    ],
)
def test_metadata_gates_exclude_broad_or_incompatible_pairs(database: Path, overrides) -> None:
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2", **overrides)
    report = audit_database(database)
    assert report["summary"]["comparisons_examined"] == 1
    assert report["candidates"] == []


def test_zero_price_pair_is_not_scored_as_agreement(database: Path) -> None:
    add_home(database, "rightmove:1", price_pcm=0)
    add_home(database, "openrent:2", price_pcm=0)
    assert audit_database(database)["candidates"] == []


def test_coordinate_candidate_without_postcode_and_street_agreement(database: Path) -> None:
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2", address="Other Place", postcode=None)
    [pair] = audit_database(database)["candidates"]
    assert pair["metadata_score"]["total"] == 55  # No production match threshold is used.


@pytest.mark.parametrize(
    ("limits", "count", "compared", "truncated"),
    [
        ({"max_homes": 2}, 1, 1, "homes_truncated"),
        ({"max_comparisons": 1}, 1, 1, "comparisons_truncated"),
        ({"max_pairs": 1}, 1, 3, "candidates_truncated"),
    ],
)
def test_bounds_are_deterministic_and_report_incomplete_coverage(
    database: Path, limits, count: int, compared: int, truncated: str
) -> None:
    for index in (3, 1, 2):
        add_home(database, f"rightmove:{index}")
    report = audit_database(database, **limits)
    assert report == audit_database(database, **limits)
    assert report["summary"]["candidate_pairs_returned"] == count
    assert report["summary"]["comparisons_examined"] == compared
    assert report["summary"][truncated] is True
    assert report["candidates"][0]["left"]["home_id"] == "rightmove:1"
    assert report["candidates"][0]["right"]["home_id"] == "rightmove:2"


def test_ranks_best_pair_within_scanned_prefix(database: Path) -> None:
    add_home(database, "rightmove:1", latitude=51.5405)
    add_home(database, "rightmove:2")
    add_home(database, "rightmove:3")
    [pair] = audit_database(database, max_pairs=1)["candidates"]
    assert pair["left"]["home_id"] == "rightmove:2"
    assert pair["right"]["home_id"] == "rightmove:3"


@pytest.mark.parametrize(
    "name, value", [("max_homes", 0), ("max_pairs", 1001), ("max_comparisons", -1)]
)
def test_invalid_bounds_are_rejected_before_reading(database: Path, name: str, value: int) -> None:
    with pytest.raises(ValueError, match=name):
        audit_database(database, **{name: value})


def test_invalid_home_is_counted_without_logging_its_metadata(database: Path) -> None:
    add_home(database, "rightmove:1", longitude=None)
    report = audit_database(database)
    assert report["summary"]["homes_loaded"] == 0
    assert report["summary"]["invalid_homes_skipped"] == 1
    assert "rightmove:1" not in json.dumps(report)


def test_snapshot_connection_is_read_only_even_without_query_only(database: Path) -> None:
    with open_snapshot(database) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        conn.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM properties")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE mutation (value TEXT)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_audit_preserves_database_and_cache_without_image_access(
    database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from home_finder.matching import scoring
    from home_finder.utils import image_hash

    def forbidden(*args, **kwargs):
        pytest.fail("Offline metadata audit must not inspect or hash image caches")

    monkeypatch.setattr(scoring, "count_gallery_hash_matches", forbidden)
    monkeypatch.setattr(image_hash, "hash_from_disk", forbidden)
    monkeypatch.setattr(image_hash, "hash_cached_gallery", forbidden)
    monkeypatch.setattr(image_hash, "fetch_and_hash_image", forbidden)
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2")
    cache = tmp_path / "image_cache"
    cache.mkdir()
    (cache / "gallery_000.jpg").write_bytes(b"copied postmerge cache must be ignored")
    database.chmod(0o444)
    before = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert audit_database(database)["summary"]["candidate_pairs_returned"] == 1

    after = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_audit_does_not_construct_network_model_or_storage_clients(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anthropic
    import httpx

    from home_finder.db import PropertyStorage

    def forbidden(*args, **kwargs):
        pytest.fail("Offline audit must not construct network/model clients or writable storage")

    monkeypatch.setattr(anthropic, "Anthropic", forbidden)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(PropertyStorage, "__init__", forbidden)
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2")
    assert audit_database(database)["summary"]["candidate_pairs_returned"] == 1


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sqlite_sidecars_are_refused_without_deleting_them(database: Path, suffix: str) -> None:
    sidecar = Path(f"{database}{suffix}")
    sidecar.write_bytes(b"do not ignore or delete this")
    with pytest.raises(ValueError, match="sidecars"):
        audit_database(database)
    assert sidecar.read_bytes() == b"do not ignore or delete this"


def test_live_wal_is_not_silently_ignored(database: Path) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE only_in_wal (value TEXT)")
        conn.commit()
        with pytest.raises(ValueError, match="sidecars"):
            audit_database(database)
        assert conn.execute("SELECT COUNT(*) FROM only_in_wal").fetchone()[0] == 0
    finally:
        conn.close()


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        audit_database(missing)
    assert not missing.exists()


def test_cli_writes_private_json_and_only_aggregate_stdout(
    database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_home(database, "rightmove:1")
    add_home(database, "openrent:2")
    output = tmp_path / "private-report.json"

    assert main(["--database", str(database), "--output", str(output)]) == 0

    captured = capsys.readouterr()
    report = json.loads(output.read_text())
    assert json.loads(captured.out) == report["summary"]
    assert captured.err == ""
    assert "rightmove:1" not in captured.out
    assert "example.com" not in captured.out
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize("use_symlink", [False, True])
def test_cli_never_overwrites_output_or_symlink_targets(
    database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], use_symlink: bool
) -> None:
    output = tmp_path / "private-report.json"
    original = tmp_path / "original.json" if use_symlink else output
    original.write_text("must survive")
    if use_symlink:
        output.symlink_to(original)

    with pytest.raises(SystemExit) as exc:
        main(["--database", str(database), "--output", str(output)])

    assert exc.value.code == 1
    assert original.read_text() == "must survive"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FileExistsError" in captured.err
    assert str(output) not in captured.err


def test_schema_error_does_not_migrate_or_create_report(
    database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute("DROP TABLE properties")
        conn.commit()
    finally:
        conn.close()
    before = database.read_bytes()
    output = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        main(["--database", str(database), "--output", str(output)])
    assert capsys.readouterr().out == ""
    assert database.read_bytes() == before
    assert not output.exists()


def test_normalized_membership_schema_is_required_even_when_empty(database: Path) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute("DROP TABLE source_listings")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(sqlite3.OperationalError, match="source_listings"):
        audit_database(database)
