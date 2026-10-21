"""Prepare an offline v6 migration manifest; never write to the source database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from ai_preview.source import create_prompt_client
from backend.tournament_identity_migration import (
    convert_snapshot,
    game_index,
    prepare_manifest,
    read_source,
    referenced_games,
)
from thufootball.cli import _jsonable
from thufootball.database import create_football_engine


async def convert_runs(runs_root: Path, manifest_path: Path, apply: bool):
    """Convert saved CLI inputs without rendering or changing publication receipts."""
    from sqlalchemy import text

    from auto_preview.service import AutoPreviewPipeline
    from auto_preview.source import preview_data_to_dict
    from preview import parse_preview_bundle

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    engine = create_football_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            games = {
                row.id: row.data["game"]
                for row in connection.execute(text("SELECT id, data FROM games"))
            }
        games.update(
            {int(key): value for key, value in manifest["external_games"].items()}
        )
        sources = [
            (path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(runs_root.glob("*/source.json"))
        ]
        missing = (
            set().union(*(referenced_games(value) for _, value in sources))
            - games.keys()
        )
        if missing:
            async with create_prompt_client() as client:
                for game_id in sorted(missing):
                    games[game_id] = _jsonable(
                        (await client.get_game_info(game_id)).game
                    )

        def legacy_references(value, game_id=None):
            if isinstance(value, list):
                return [legacy_references(child, game_id) for child in value]
            if not isinstance(value, dict):
                return value
            game_id = value.get("game_id", game_id)
            result = {
                key: legacy_references(child, game_id)
                for key, child in value.items()
                if key != "tournament_team_id"
            }
            if "tournament_team_id" in value:
                game = games.get(game_id, {})
                ids = [
                    game[side + "_team_id"]
                    for side in ("home", "away")
                    if game.get(side + "_tournament_team_id")
                    == value["tournament_team_id"]
                ]
                if len(ids) != 1:
                    raise ValueError(
                        f"Cannot verify saved CLI render input: game={game_id}"
                    )
                result["team_id"] = ids[0]
            return result

        jobs = []
        for source_path, original in sources:
            converted = convert_snapshot(original, games)
            if original == converted:
                continue
            content = (
                json.dumps(converted, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            jobs.append((source_path, source_path.read_bytes(), content))
            state_path = source_path.with_name("run.json")
            if not state_path.is_file():
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
            article = state.get("article")
            if article:
                source = parse_preview_bundle(
                    converted,
                    json.loads(
                        (runs_root / "weather.json").read_text(encoding="utf-8")
                    ),
                    json.loads((runs_root / "config.json").read_text(encoding="utf-8")),
                    source_directory=source_path.parent,
                )
                old_payload = legacy_references(preview_data_to_dict(source))
                old_hash = hashlib.sha256(
                    json.dumps(
                        old_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if article["input_sha256"] == old_hash:
                    article["input_sha256"] = AutoPreviewPipeline._render_input_sha256(
                        source
                    )
            source_state = state.get("source") or {}
            if (
                source_state.get("accepted_placeholder_sha256")
                == hashlib.sha256(source_path.read_bytes()).hexdigest()
            ):
                source_state["accepted_placeholder_sha256"] = hashlib.sha256(
                    content
                ).hexdigest()
            jobs.append(
                (
                    state_path,
                    state_path.read_bytes(),
                    (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                )
            )
        print(f"CLI files to convert: {len(jobs)}; apply={apply}")
        if apply:
            backup_root = manifest_path.parent / "runs-before"
            for path, before, _ in jobs:
                backup_path = backup_root / path.relative_to(runs_root)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                if backup_path.exists():
                    raise ValueError(f"Backup already exists: {backup_path}")
                backup_path.write_bytes(before)
            try:
                for path, _, after in jobs:
                    path.write_bytes(after)
            except Exception:
                for path, before, _ in jobs:
                    path.write_bytes(before)
                raise
    finally:
        engine.dispose()


async def prepare(output: Path):
    engine = create_football_engine()
    try:
        with engine.connect() as connection:
            from sqlalchemy import text

            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            source = read_source(connection)
        missing = (
            referenced_games(source["matches"] + source["articles"])
            - game_index(source).keys()
        )
        external = {}
        if missing:
            async with create_prompt_client() as client:
                for game_id in sorted(missing):
                    external[game_id] = _jsonable(
                        (await client.get_game_info(game_id)).game
                    )
        manifest = prepare_manifest(source, external)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Prepared: {output}")
        print(f"Source SHA256: {manifest['source_sha256']}")
        print(
            "Rows:",
            {
                table: len(manifest[table])
                for table in ("institutions", "tournaments", "matches", "articles")
            },
        )
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "data"
        / "backups"
        / f"tournament-identity-{datetime.now(UTC):%Y%m%dT%H%M%S}.json",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        help="Convert existing CLI source.json and run.json files",
    )
    parser.add_argument(
        "--manifest", type=Path, help="Previously prepared database migration manifest"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write CLI conversions after backing up every changed file",
    )
    args = parser.parse_args()
    if args.runs_root:
        if args.manifest is None:
            parser.error("--runs-root requires --manifest")
        asyncio.run(convert_runs(args.runs_root, args.manifest, args.apply))
    else:
        if args.apply or args.manifest:
            parser.error("--apply and --manifest require --runs-root")
        asyncio.run(prepare(args.output))


if __name__ == "__main__":
    main()
