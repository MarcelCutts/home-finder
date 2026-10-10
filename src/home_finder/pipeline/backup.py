"""Create, verify or restore a private backup without starting the application."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from home_finder.db.backup import create_backup, restore_backup, verify_backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser("create", help="Snapshot SQLite into a new private directory")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--assets", type=Path, help="Optional existing image-cache directory")
    create.add_argument("--frozen", action="store_true", help="Only for immutable offline exports")
    verify = commands.add_parser("verify", help="Verify bytes and logical database inventory")
    verify.add_argument("--package", type=Path, required=True)
    restore = commands.add_parser("restore", help="Restore into a NEW isolated directory")
    restore.add_argument("--package", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    for command in (create, verify, restore):
        command.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args()
    try:
        if args.operation == "create":
            manifest = create_backup(
                args.database,
                args.destination,
                assets=args.assets,
                frozen=args.frozen,
                timeout_seconds=args.timeout_seconds,
            )
        elif args.operation == "restore":
            manifest = restore_backup(
                args.package, args.destination, timeout_seconds=args.timeout_seconds
            )
        else:
            manifest = verify_backup(args.package, timeout_seconds=args.timeout_seconds)
    except ValidationError:
        parser.exit(1, "Invalid backup manifest\n")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Backup operation failed: {exc}\n")
    print(
        json.dumps(
            {
                "operation": args.operation,
                "database_sha256": manifest.database.sha256,
                "schema_version": manifest.inventory.schema_version,
                "table_rows": {
                    name: table.rows for name, table in manifest.inventory.tables.items()
                },
                "foreign_key_violations": manifest.inventory.foreign_key_violations,
                "assets_state": manifest.assets_state,
                "asset_files": len(manifest.assets),
                "asset_bytes": sum(f.size for f in manifest.assets.values()),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
