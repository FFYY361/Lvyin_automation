"""Back up football reference data; the September 2026 mutation is retired.

Use the historical release for that old data migration. Current deployments use
migrate_tournament_team_ids.py and Alembic v6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    sys.path.insert(0, str(root))

from thufootball.database import create_football_engine


def backup_reference_data(engine) -> Path:
    with engine.connect() as connection:
        connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        snapshot = {
            "institutions": [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM institutions ORDER BY name")
                ).mappings()
            ],
            "rankings": [
                dict(row)
                for row in connection.execute(
                    text("SELECT id, final_rankings FROM tournaments ORDER BY id")
                ).mappings()
            ],
            "prompts": [
                dict(row)
                for row in connection.execute(
                    text("SELECT key, content FROM prompt_templates ORDER BY key")
                ).mappings()
            ],
        }
    directory = PROJECT_ROOT / "data" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    path = (
        directory
        / f"institutions-202609-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    )
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(payload)
    print(f"Backup: {path}")
    print(f"SHA256: {hashlib.sha256(payload).hexdigest()}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-only", action="store_true")
    args = parser.parse_args()
    if not args.backup_only:
        parser.error(
            "The September 2026 mutation is retired; use migrate_tournament_team_ids.py for v6"
        )
    engine = create_football_engine()
    try:
        backup_reference_data(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
