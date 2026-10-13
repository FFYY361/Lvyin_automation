"""Back up and migrate the September 2026 institution configuration.

Run --backup-only before Alembic, then run without options after upgrading.
The migration changes institution reference configuration only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from backend.models import PromptTemplate
from thufootball.database import (
    FootballDataRepository,
    InstitutionRecord,
    TournamentRecord,
    create_football_engine,
    create_football_session_factory,
)

RENAMES = (
    ("物理系-天文系-高等研究院", "物理系-杨振宁高等研究院-天文系", "物理-高研-天文"),
    ("生命学院", "生命科学学院", "生命"),
    ("网络研究院", "网络科学与网络空间研究院", "网研"),
    ("外国语言文学系-人文学院-语言教学中心", "外国语言文学系-人文学院", "外文-人文"),
    ("交叉信息研究院-人工智能学院", "交叉信息研究院-人工智能学院", "交叉-AI"),
)
MERGED_NAME = "土木水利学院"
PREDECESSOR_NAMES = ("土木工程系-建设管理系", "水利水电工程系")
CONFIG_FIELDS = (
    "name",
    "short_name",
    "male_team_ids",
    "female_team_ids",
    "futsal_team_ids",
)

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


def migrate(session) -> None:
    tournaments = list(session.scalars(select(TournamentRecord)))
    for old_name, new_name, short_name in RENAMES:
        record = session.get(InstitutionRecord, old_name)
        if record is None:
            record = session.get(InstitutionRecord, new_name)
        if record is None:
            raise ValueError(f"Missing institution: {old_name}")
        if old_name != new_name and record.name == old_name:
            if session.get(InstitutionRecord, new_name) is not None:
                raise ValueError(f"Both old and new institution exist: {new_name}")
            record.name = new_name
        record.short_name = short_name
        if old_name != new_name:
            for tournament in tournaments:
                ranks = dict(tournament.final_rankings)
                for category in ("男足", "女足", "五人制"):
                    old_key, new_key = old_name + category, new_name + category
                    if old_key in ranks:
                        if new_key in ranks:
                            raise ValueError(f"Duplicate ranking key: {new_key}")
                        ranks[new_key] = ranks.pop(old_key)
                tournament.final_rankings = ranks
    session.flush()

    predecessors = [session.get(InstitutionRecord, name) for name in PREDECESSOR_NAMES]
    merged = session.get(InstitutionRecord, MERGED_NAME)
    if merged is None:
        if any(record is None for record in predecessors):
            raise ValueError("Incomplete predecessor configuration")
        players = {}
        for record in predecessors:
            for name, profile in record.player_descriptions.items():
                if name in players:
                    raise ValueError(
                        f"Overlapping player profiles require review: {name}"
                    )
                players[name] = deepcopy(profile)
        merged = InstitutionRecord(
            name=MERGED_NAME,
            short_name="土水",
            male_team_ids=[],
            female_team_ids=[],
            futsal_team_ids=[],
            male_description="",
            female_description="",
            futsal_description="",
            player_descriptions=players,
            predecessors=[
                {field: deepcopy(getattr(record, field)) for field in CONFIG_FIELDS}
                for record in predecessors
            ],
        )
        session.add(merged)
        for record in predecessors:
            session.delete(record)
    elif any(record is not None for record in predecessors):
        raise ValueError("Merged institution and old rows both exist")
    session.flush()
    FootballDataRepository(session).load_outcome_catalog()


def restore(session, path: Path) -> None:
    backup = json.loads(path.read_text(encoding="utf-8"))
    for record in session.scalars(select(InstitutionRecord)):
        session.delete(record)
    session.flush()
    session.add_all(InstitutionRecord(**row) for row in backup["institutions"])
    for row in backup["rankings"]:
        session.get(TournamentRecord, row["id"]).final_rankings = row["final_rankings"]
    for row in backup["prompts"]:
        record = session.get(PromptTemplate, row["key"])
        if record is not None:
            record.content = row["content"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-only", action="store_true")
    parser.add_argument(
        "--restore",
        type=Path,
        help="Restore reference data from a backup; keep schema and game facts",
    )
    args = parser.parse_args()
    engine = create_football_engine()
    try:
        backup_reference_data(engine)
        if not args.backup_only:
            with create_football_session_factory(engine).begin() as session:
                if args.restore:
                    restore(session, args.restore)
                else:
                    migrate(session)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
