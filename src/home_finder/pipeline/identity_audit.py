"""Read-only provider identity audit, including pre-migration database copies.

Run with ``python -m home_finder.pipeline.identity_audit --database COPY --output NEW_DIR``.
The report contains historical URLs and IDs; output is private and never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from home_finder.db.identities import IDENTITY_COLUMNS
from home_finder.models.identity import IdentityAudit, SourceIdentityInput, audit_source_identities


def read_identity_audit(database: Path) -> tuple[int, IdentityAudit]:
    """Read one consistent snapshot without initializing storage or applying migrations.

    Use SQLite's regular read-only mode so a WAL, if present, is respected. The
    digest identifies the identity inputs, not the database file or personal data.
    """
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        rows = [
            SourceIdentityInput.model_validate(dict(row))
            for row in conn.execute(
                f"SELECT {IDENTITY_COLUMNS} FROM source_listings ORDER BY unique_id"
            )
        ]
        conn.rollback()
    return version, audit_source_identities(rows)


def write_identity_audit(database: Path, output: Path) -> Path:
    """Write a deterministic mapping plus audit time into a new private directory."""
    version, audit = read_identity_audit(database)
    report = {
        "report_version": 1,
        "mode": "read_only_provider_identity_audit",
        "recorded_at": datetime.now(UTC).isoformat(),
        "database_schema_version": version,
        "audit": audit.model_dump(mode="json"),
    }
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / "identity-audit.json"
    with path.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing database copy")
    parser.add_argument("--output", type=Path, required=True, help="New private report directory")
    args = parser.parse_args()
    path = write_identity_audit(args.database, args.output)
    report = json.loads(path.read_text(encoding="utf-8"))
    summary = {
        key: value for key, value in report["audit"].items() if key not in {"rows", "groups"}
    }
    print(json.dumps({"report": str(path), **summary}, sort_keys=True))


if __name__ == "__main__":
    main()
