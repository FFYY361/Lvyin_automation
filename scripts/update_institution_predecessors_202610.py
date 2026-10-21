"""Update predecessor relationships after v6; preview by default, --apply to save."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for root in (ROOT, ROOT / "src"):
    sys.path.insert(0, str(root))

from sqlalchemy import select
from sqlalchemy.orm import Session

from thufootball.database import (
    FootballDataRepository,
    InstitutionRecord,
    create_football_engine,
)
from thufootball.rankings import build_outcome_catalog

CROSS = "交叉信息研究院-人工智能学院"
CROSS_PREDECESSOR = {
    "name": "交叉信息研究院",
    "short_name": "交叉",
    "tournament_teams": {
        "male": {"88": 988, "100": 1523},
        "female": {},
        "futsal": {"93": 1425, "111": 1630},
    },
}


def updated_institutions(source: list[dict]) -> list[dict]:
    records = {row["name"]: deepcopy(row) for row in source}
    cross = records[CROSS]
    if cross["predecessors"] not in ([], [CROSS_PREDECESSOR]):
        raise ValueError("Unexpected existing predecessor for cross-AI")
    for competition, mapping in CROSS_PREDECESSOR["tournament_teams"].items():
        for tournament_id, registration in mapping.items():
            current = cross["tournament_teams"][competition].pop(tournament_id, None)
            if current != registration and not (
                current is None and cross["predecessors"] == [CROSS_PREDECESSOR]
            ):
                raise ValueError(f"Unexpected cross registration: {tournament_id}")
    cross["predecessors"] = [deepcopy(CROSS_PREDECESSOR)]
    return sorted(records.values(), key=lambda row: row["name"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = create_football_engine()
    try:
        with Session(engine) as session, session.begin():
            repository = FootballDataRepository(session)
            rows = (
                list(
                    session.scalars(
                        select(InstitutionRecord)
                        .order_by(InstitutionRecord.name)
                        .with_for_update()
                    )
                )
                if args.apply
                else repository.list_institutions()
            )
            before = [
                {
                    column.name: deepcopy(getattr(row, column.name))
                    for column in InstitutionRecord.__table__.columns
                }
                for row in rows
            ]
            old_catalog = repository.load_outcome_catalog()
            after = updated_institutions(before)
            documents = [
                {
                    "id": row.id,
                    "name": row.name,
                    "competition": row.competition,
                    "final_rankings": row.final_rankings,
                    "data": row.data,
                }
                for row in repository.list_tournaments()
            ]
            finalized = {
                row.id for row in repository.list_tournaments() if row.is_finalized
            }
            new_catalog = build_outcome_catalog(
                after,
                [row for row in documents if row["id"] in finalized],
                registration_tournaments=documents,
            )
            if (
                old_catalog.registration_tournament_ids
                != new_catalog.registration_tournament_ids
            ):
                raise ValueError("Registration coverage changed")
            existing = {row.name: row for row in rows}
            source = {row["name"]: row for row in before}
            changed = [row for row in after if row != source.get(row["name"])]
            print(
                json.dumps(
                    {
                        "apply": args.apply,
                        "changed": [
                            {
                                key: row[key]
                                for key in (
                                    "name",
                                    "short_name",
                                    "tournament_teams",
                                    "predecessors",
                                )
                            }
                            for row in changed
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if args.apply and changed:
                backup = (
                    ROOT
                    / "data"
                    / "backups"
                    / f"institution-predecessors-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.json"
                )
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(
                    json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"Backup: {backup}")
                for record in changed:
                    if record["name"] in existing:
                        row = existing[record["name"]]
                        row.tournament_teams = record["tournament_teams"]
                        row.predecessors = record["predecessors"]
                    else:
                        session.add(InstitutionRecord(**record))
                session.flush()
                repository.load_outcome_catalog()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
