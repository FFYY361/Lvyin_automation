from __future__ import annotations

import asyncio
import os
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from scripts import sync_ai_preview_automatic as automatic_sync
from test.football_data import clone_football_data
from thufootball.config import load_env_file
from thufootball.database import (
    FootballDataBase,
    FootballDataRepository,
    GameRecord,
    InstitutionRecord,
    TournamentRecord,
)
from thufootball.errors import ConfigurationError, Timeout


@pytest.fixture(scope="module")
def football_engine():
    load_env_file()
    database_url = os.environ.get("WEBSITE_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("WEBSITE_DATABASE_URL is not configured")
    administration = create_engine(database_url)
    schema = f"football_data_test_{uuid.uuid4().hex}"
    try:
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except OperationalError as exc:
        administration.dispose()
        pytest.skip(f"PostgreSQL is unavailable: {exc}")
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    FootballDataBase.metadata.create_all(engine)
    clone_football_data(administration, engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


@pytest.fixture
def seeded_engine(football_engine):
    return football_engine


@pytest.fixture
def transactional_factory(seeded_engine):
    connection = seeded_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


def test_repository_and_data_baseline(football_engine) -> None:
    with Session(football_engine) as session:
        repository = FootballDataRepository(session)
        assert repository.find_institution(48, "male").name == "车辆与运载学院"
        assert repository.find_institution(48, "futsal").name == "车辆与运载学院"
        assert repository.get_game(4245).tournament_id == 122
        assert len(repository.list_games([122])) == 35
        assert [item.id for item in repository.list_tournaments()[:5]] == [
            122,
            123,
            124,
            126,
            128,
        ]

        counts = (
            session.scalar(select(func.count()).select_from(InstitutionRecord)),
            session.scalar(select(func.count()).select_from(TournamentRecord)),
            session.scalar(select(func.count()).select_from(GameRecord)),
        )
        assert counts == (52, 14, 590)
        assert len(repository.load_outcome_catalog().tournaments_by_id) == 14


def test_schema_has_only_planned_columns_and_restricts_deletion(
    seeded_engine,
) -> None:
    expected = {
        "institutions": {
            "name",
            "short_name",
            "male_team_ids",
            "female_team_ids",
            "futsal_team_ids",
            "male_description",
            "female_description",
            "futsal_description",
            "player_descriptions",
            "predecessors",
        },
        "tournaments": {
            "id",
            "name",
            "competition",
            "final_rankings",
            "data",
            "is_finalized",
        },
        "games": {"id", "tournament_id", "data"},
    }
    database_inspector = inspect(seeded_engine)
    for table, columns in expected.items():
        assert {item["name"] for item in database_inspector.get_columns(table)} == columns

    with Session(seeded_engine) as session, pytest.raises(IntegrityError):
        session.execute(delete(TournamentRecord).where(TournamentRecord.id == 122))
        session.commit()


def test_game_summaries_and_manual_adjustments(seeded_engine) -> None:
    with Session(seeded_engine) as session:
        tournaments = list(session.scalars(select(TournamentRecord)))
        summaries = {
            game["game_id"]: game
            for tournament in tournaments
            for game in tournament.data["games"]
        }
        games = {
            game.id: game.data
            for game in session.scalars(select(GameRecord))
        }
        assert set(summaries) == set(games)
        assert all(games[game_id]["game"] == summary for game_id, summary in summaries.items())

        assert games[3497]["game"]["away_abandon"] is True
        assert not ({124696, 124697} & {item["event_id"] for item in games[3497]["events"]})
        assert games[4152]["game"]["home_abandon"] is True


def test_automatic_sync_updates_and_removes_stale_games_atomically(
    transactional_factory,
) -> None:
    with transactional_factory.begin() as session:
        tournament = session.get(TournamentRecord, 122)
        kept = session.get(GameRecord, 4245)
        assert tournament is not None and kept is not None
        tournament.is_finalized = False
        original_name = tournament.name
        original_rankings = deepcopy(tournament.final_rankings)
        tournament_document = deepcopy(tournament.data)
        tournament_document["games"] = [deepcopy(kept.data["game"])]
        game_document = deepcopy(kept.data)

    automatic_sync._persist_documents(
        {122: tournament_document},
        {4245: game_document},
        session_factory=transactional_factory,
    )
    with transactional_factory() as session:
        tournament = session.get(TournamentRecord, 122)
        assert tournament is not None
        assert tournament.name == original_name
        assert tournament.final_rankings == original_rankings
        assert [item.id for item in FootballDataRepository(session).list_games([122])] == [
            4245
        ]


def test_automatic_sync_excludes_and_protects_finalized_tournaments(
    transactional_factory,
) -> None:
    with transactional_factory.begin() as session:
        tournament = session.get(TournamentRecord, 122)
        assert tournament is not None
        tournament.is_finalized = True
        original_data = deepcopy(tournament.data)

    with transactional_factory() as session:
        assert 122 not in automatic_sync._sync_target_tournament_ids(session)

    with pytest.raises(ConfigurationError, match="finalized"):
        automatic_sync._persist_documents(
            {122: {**original_data, "season_ids": {"changed": True}}},
            {},
            session_factory=transactional_factory,
        )

    with transactional_factory() as session:
        tournament = session.get(TournamentRecord, 122)
        assert tournament is not None
        assert tournament.data == original_data


def test_automatic_sync_rolls_back_the_whole_transaction(
    transactional_factory,
) -> None:
    with transactional_factory.begin() as session:
        tournament = session.get(TournamentRecord, 123)
        game = session.get(GameRecord, 4246)
        assert tournament is not None and game is not None
        tournament.is_finalized = False
        original_tournament = deepcopy(tournament.data)
        original_game = deepcopy(game.data)
        original_count = len(FootballDataRepository(session).list_games([123]))

    invalid_game = deepcopy(original_game)
    invalid_game["game"]["tournament_id"] = 999_999
    with pytest.raises(IntegrityError):
        automatic_sync._persist_documents(
            {123: {**original_tournament, "season_ids": {"changed": True}}},
            {4246: invalid_game},
            session_factory=transactional_factory,
        )

    with transactional_factory() as session:
        tournament = session.get(TournamentRecord, 123)
        game = session.get(GameRecord, 4246)
        assert tournament is not None and game is not None
        assert tournament.data == original_tournament
        assert game.data == original_game
        assert len(FootballDataRepository(session).list_games([123])) == original_count


def test_automatic_sync_propagates_remote_game_failure() -> None:
    class FailingClient:
        calls = 0

        async def get_game_info(self, game_id: int) -> None:
            self.calls += 1
            raise Timeout(
                "remote game request failed",
                stage="request",
                retryable=False,
                game_id=game_id,
            )

    client = FailingClient()
    with pytest.raises(RuntimeError, match="complete game detail"):
        asyncio.run(
            automatic_sync._read_game(  # type: ignore[arg-type]
                client, asyncio.Semaphore(1), 999_999
            )
        )
    assert client.calls == 1
