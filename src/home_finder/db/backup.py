"""Consistent SQLite snapshots and isolated restores; never replace an authority."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DATABASE_BYTES = 8 * 1024**3
MAX_ASSET_BYTES = 20 * 1024**3
MAX_ASSET_FILES = 100_000
MAX_TABLE_ROWS = 1_000_000
MAX_MANIFEST_BYTES = 64 * 1024**2


class BackupError(ValueError):
    """A snapshot cannot be verified or safely published."""


class FileDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    size: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TableDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: list[str]
    rows: int = Field(ge=0, le=MAX_TABLE_ROWS, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatabaseInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=0, strict=True)
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: dict[str, TableDigest]
    foreign_key_violations: int = Field(ge=0, strict=True)


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    started_at: AwareDatetime
    completed_at: AwareDatetime
    source_path: str
    source_mode: Literal["live_read_only", "frozen_immutable"]
    source_frozen_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    backup_sqlite_version: str
    database: FileDigest
    inventory: DatabaseInventory
    assets_state: Literal["not_included", "copied_over_capture_window"]
    source_assets_path: str | None
    assets: dict[str, FileDigest]

    @field_validator("assets")
    @classmethod
    def safe_assets(cls, assets: dict[str, FileDigest]) -> dict[str, FileDigest]:
        if len(assets) > MAX_ASSET_FILES or sum(f.size for f in assets.values()) > MAX_ASSET_BYTES:
            raise ValueError("Asset limits exceeded")
        for name in assets:
            path = PurePosixPath(name)
            if (
                not name
                or path.is_absolute()
                or str(path) != name
                or ".." in path.parts
                or "\\" in name
                or "\x00" in name
                or name == "."
            ):
                raise ValueError("Unsafe asset path")
        return assets

    @model_validator(mode="after")
    def consistent_metadata(self) -> BackupManifest:
        if self.completed_at < self.started_at:
            raise ValueError("Invalid capture window")
        if self.database.size > MAX_DATABASE_BYTES:
            raise ValueError("Database limit exceeded")
        if self.assets_state == "not_included" and (self.assets or self.source_assets_path):
            raise ValueError("Omitted assets cannot have a manifest")
        if self.assets_state != "not_included" and self.source_assets_path is None:
            raise ValueError("Captured assets require their origin")
        if (self.source_mode == "frozen_immutable") != (self.source_frozen_sha256 is not None):
            raise ValueError("Frozen source requires a checksum")
        return self


def _deadline(seconds: float) -> float:
    if not 0 < seconds <= 3600:
        raise ValueError("Timeout must be positive and at most 3600 seconds")
    return time.monotonic() + seconds


def _check_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise BackupError("Backup/verification deadline exceeded")


def _reject_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.is_symlink() or (sidecar.exists() and sidecar.stat().st_size):
            raise BackupError("Immutable input has a nonempty WAL/journal")


def _open_read_only(path: Path, *, frozen: bool = False) -> sqlite3.Connection:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise BackupError("Database must be an existing regular file")
    if frozen:
        _reject_sidecars(path)
    conn = sqlite3.connect(
        path.as_uri() + "?mode=ro" + ("&immutable=1" if frozen else ""),
        uri=True,
        timeout=1,
    )
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN")
        conn.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()  # Pin the read snapshot.
    except BaseException:
        conn.close()
        raise
    return conn


def _cell(value: Any) -> Any:
    # Distinguish SQLite value types and hash blobs without putting bytes in JSON.
    if isinstance(value, bytes):
        return ["blob", len(value), hashlib.sha256(value).hexdigest()]
    if isinstance(value, float):
        return ["float", value.hex()]
    return [type(value).__name__, value]


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _inventory(conn: sqlite3.Connection, deadline: float) -> DatabaseInventory:
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10_000)
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("Database integrity check failed")
    schema = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()
    tables = {}
    for kind, name, _, _ in schema:
        if kind != "table":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        cursor = conn.execute(f"SELECT * FROM {quoted}")
        columns = [column[0] for column in cursor.description]
        rows = []
        for row in cursor:
            _check_time(deadline)
            rows.append(_json_digest([_cell(cell) for cell in row]))
            if len(rows) > MAX_TABLE_ROWS:
                raise BackupError("Table exceeds inventory row limit")
        digest = hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()
        tables[name] = TableDigest(columns=columns, rows=len(rows), sha256=digest)
    return DatabaseInventory(
        schema_version=conn.execute("PRAGMA user_version").fetchone()[0],
        schema_sha256=_json_digest(schema),
        tables=tables,
        foreign_key_violations=sum(1 for _ in conn.execute("PRAGMA foreign_key_check")),
    )


def inventory_database(
    database: Path, *, frozen: bool = False, timeout_seconds: float = 300
) -> DatabaseInventory:
    """Fingerprint one consistent read snapshot without migration or settings."""
    deadline = _deadline(timeout_seconds)
    with closing(_open_read_only(database, frozen=frozen)) as conn:
        return _inventory(conn, deadline)


def _regular_fd(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise BackupError("Only regular files are accepted")
    return fd


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _file_digest(
    path: Path, deadline: float, *, copy_to: Path | None = None, limit: int = MAX_ASSET_BYTES
) -> FileDigest:
    with os.fdopen(_regular_fd(path), "rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size > limit:
            raise BackupError("File size limit exceeded")
        digest = hashlib.sha256()
        size = 0
        output = None
        try:
            if copy_to is not None:
                missing = []
                parent = copy_to.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for parent in reversed(missing):
                    parent.mkdir(mode=0o700)
                fd = os.open(copy_to, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                output = os.fdopen(fd, "wb")
            while block := source.read(1024**2):
                _check_time(deadline)
                size += len(block)
                if size > limit:
                    raise BackupError("File size limit exceeded")
                digest.update(block)
                if output is not None:
                    output.write(block)
            after = os.fstat(source.fileno())
            current = path.stat(follow_symlinks=False)
            if _file_identity(before) != _file_identity(after) or (
                _file_identity(after) != _file_identity(current)
            ):
                raise BackupError("File changed during capture/verification")
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        finally:
            if output is not None:
                output.close()
    result = FileDigest(size=size, sha256=digest.hexdigest())
    if copy_to is not None and _file_digest(copy_to, deadline, limit=limit) != result:
        raise BackupError("Copied file checksum mismatch")
    return result


def _regular_files(root: Path) -> Iterator[Path]:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("Asset root must be an existing directory without symlinks")

    def failed(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise BackupError("Asset directories cannot contain symlinks")
        for name in sorted(files):
            path = Path(directory) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise BackupError("Asset tree contains a symlink or special file")
            yield path


def _asset_inventory(root: Path, deadline: float) -> dict[str, tuple[int, int, int, int, int]]:
    result: dict[str, tuple[int, int, int, int, int]] = {}
    total = 0
    for path in _regular_files(root):
        _check_time(deadline)
        info = path.stat()
        total += info.st_size
        if len(result) >= MAX_ASSET_FILES or total > MAX_ASSET_BYTES:
            raise BackupError("Asset limits exceeded")
        result[path.relative_to(root).as_posix()] = _file_identity(info)
    return result


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _new_directory(path: Path) -> Iterator[Path]:
    # mkdir is the exclusive ownership boundary. Existing destinations are never
    # removed, replaced or reused, even if empty or left by an interrupted run.
    path = path.absolute()
    path.mkdir(mode=0o700)
    try:
        yield path
    except BaseException:
        shutil.rmtree(path)
        raise


def create_backup(
    database: Path,
    destination: Path,
    *,
    assets: Path | None = None,
    frozen: bool = False,
    timeout_seconds: float = 300,
) -> BackupManifest:
    """Publish a private package after DB and optional files are verified.

    Files cover a capture window, not a transaction shared with the database.
    Fence asset writers for cutover. No application initialization occurs here.
    """
    deadline = _deadline(timeout_seconds)
    started = datetime.now(UTC)
    database = database.resolve(strict=True)
    if assets is not None:
        if assets.is_symlink():
            raise BackupError("Asset root cannot be a symlink")
        assets = assets.resolve(strict=True)
        if destination.resolve().is_relative_to(assets):
            raise BackupError("Backup destination cannot be inside its asset source")
    if frozen:
        _reject_sidecars(database)
    before = _file_digest(database, deadline, limit=MAX_DATABASE_BYTES) if frozen else None
    with closing(_open_read_only(database, frozen=frozen)) as source:
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        if source.execute("PRAGMA page_count").fetchone()[0] * page_size > MAX_DATABASE_BYTES:
            raise BackupError("Database limit exceeded")
        with _new_directory(destination) as output:
            target_path = output / "properties.db"
            target_path.touch(mode=0o600, exist_ok=False)
            with closing(sqlite3.connect(target_path)) as target:

                def progress(status: int, remaining: int, total: int) -> None:
                    _check_time(deadline)

                source.backup(target, pages=256, progress=progress, sleep=0.05)
                target.execute("PRAGMA journal_mode=DELETE").fetchone()
            with closing(_open_read_only(target_path, frozen=True)) as copied:
                inventory = _inventory(copied, deadline)
            copied_assets: dict[str, FileDigest] = {}
            if assets is not None:
                (output / "image_cache").mkdir(mode=0o700)
                asset_inventory = _asset_inventory(assets, deadline)
                for name, identity in asset_inventory.items():
                    copied_assets[name] = _file_digest(
                        assets / name,
                        deadline,
                        copy_to=output / "image_cache" / name,
                        limit=identity[2],
                    )
                if _asset_inventory(assets, deadline) != asset_inventory:
                    raise BackupError("Asset tree changed during capture")
            if before is not None:
                _reject_sidecars(database)
                if _file_digest(database, deadline, limit=MAX_DATABASE_BYTES) != before:
                    raise BackupError("Frozen source changed during capture")
            manifest = BackupManifest(
                started_at=started,
                completed_at=datetime.now(UTC),
                source_path=str(database),
                source_mode="frozen_immutable" if frozen else "live_read_only",
                source_frozen_sha256=before.sha256 if before else None,
                backup_sqlite_version=sqlite3.sqlite_version,
                database=_file_digest(target_path, deadline, limit=MAX_DATABASE_BYTES),
                inventory=inventory,
                assets_state="copied_over_capture_window" if assets is not None else "not_included",
                source_assets_path=str(assets) if assets is not None else None,
                assets=copied_assets,
            )
            with target_path.open("rb") as db_file:
                os.fsync(db_file.fileno())
            _publish_manifest(output, manifest)
            return manifest


def _publish_manifest(output: Path, manifest: BackupManifest) -> None:
    for directory, _, _ in os.walk(output, topdown=False):
        _sync_directory(Path(directory))
    fd = os.open(output / "manifest.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(manifest.model_dump_json(indent=2))
        target.flush()
        os.fsync(target.fileno())
    _sync_directory(output)
    _sync_directory(output.parent)


def verify_backup(package: Path, *, timeout_seconds: float = 300) -> BackupManifest:
    """Verify exact bytes, structure and inventory before a package can be restored."""
    deadline = _deadline(timeout_seconds)
    with os.fdopen(_regular_fd(package / "manifest.json"), "rb") as source:
        body = source.read(MAX_MANIFEST_BYTES + 1)
    if len(body) > MAX_MANIFEST_BYTES:
        raise BackupError("Manifest limit exceeded")
    manifest = BackupManifest.model_validate_json(body)
    expected = {"properties.db", "manifest.json"}
    if manifest.assets_state != "not_included":
        expected.add("image_cache")
    if {p.name for p in package.iterdir()} != expected:
        raise BackupError("Unexpected or missing package files")
    db_path = package / "properties.db"
    if _file_digest(db_path, deadline, limit=manifest.database.size) != manifest.database:
        raise BackupError("Database checksum mismatch")
    with closing(_open_read_only(db_path, frozen=True)) as conn:
        if _inventory(conn, deadline) != manifest.inventory:
            raise BackupError("Database inventory mismatch")
    root = package / "image_cache"
    if manifest.assets_state != "not_included":
        found = _asset_inventory(root, deadline)
        if found.keys() != manifest.assets.keys():
            raise BackupError("Asset file set differs from manifest")
        for name in found:
            if (
                _file_digest(root / name, deadline, limit=manifest.assets[name].size)
                != (manifest.assets[name])
            ):
                raise BackupError("Asset checksum mismatch")
    return manifest


def restore_backup(
    package: Path, destination: Path, *, timeout_seconds: float = 300
) -> BackupManifest:
    """Restore into a new isolated directory; never initialize or run the application."""
    deadline = _deadline(timeout_seconds)
    if destination.resolve().is_relative_to(package.resolve()):
        raise BackupError("Restore destination cannot be inside its backup package")
    manifest = verify_backup(package, timeout_seconds=timeout_seconds)
    with _new_directory(destination) as output:
        if (
            _file_digest(
                package / "properties.db",
                deadline,
                copy_to=output / "properties.db",
                limit=manifest.database.size,
            )
            != manifest.database
        ):
            raise BackupError("Database changed after verification")
        if manifest.assets_state != "not_included":
            (output / "image_cache").mkdir(mode=0o700)
        for name, expected in manifest.assets.items():
            if (
                _file_digest(
                    package / "image_cache" / name,
                    deadline,
                    copy_to=output / "image_cache" / name,
                    limit=expected.size,
                )
                != expected
            ):
                raise BackupError("Asset changed after verification")
        _publish_manifest(output, manifest)
    return manifest
