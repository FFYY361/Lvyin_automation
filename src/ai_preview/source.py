"""Live current-season records layered over archived database records."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

from auto_preview.config import COMPETITIONS
from thufootball import THUFootballClient
from thufootball.cli import _jsonable
from thufootball.config import load_env_file
from thufootball.credentials import (
    AutomaticCredentialManager,
    AutoRefreshingTHUFootballClient,
)
from thufootball.database import (
    FootballDataRepository,
    create_football_engine,
    create_football_session_factory,
)
from thufootball.errors import ConfigurationError

from .builder import PromptBundle, build_prompt_bundle


def create_prompt_client() -> THUFootballClient:
    load_env_file()
    manager = AutomaticCredentialManager.from_environment()
    options = {
        "openid": os.environ.get("THUFOOTBALL_OPENID") or None,
        "session_key": os.environ.get("THUFOOTBALL_SESSION_KEY") or None,
        "load_environment": False,
    }
    if manager.configured:
        return AutoRefreshingTHUFootballClient(
            **options, credential_refresher=manager.refresh
        )
    return THUFootballClient(**options)


class PromptRepository:
    """An operation-local overlay; never writes live records to the database."""

    def __init__(self, database: FootballDataRepository) -> None:
        self.database = database
        self.tournaments: dict[int, Any] = {}
        self.games: dict[int, Any] = {}
        self.summaries: dict[int, dict[str, Any]] = {}
        self.required_details: set[int] = set()
        self.collect_details = False
        self._catalog = None

    def __getattr__(self, name: str):
        return getattr(self.database, name)

    def load_outcome_catalog(self):
        if self._catalog is None:
            self._catalog = self.database.load_outcome_catalog()
        return self._catalog

    def list_tournaments(self):
        records = {record.id: record for record in self.database.list_tournaments()}
        records.update(self.tournaments)
        return list(records.values())

    def get_tournament(self, tournament_id: int):
        if tournament_id in self.tournaments:
            return self.tournaments[tournament_id]
        return self.database.get_tournament(tournament_id)

    def get_game(self, game_id: int):
        if game_id in self.games:
            return self.games[game_id]
        if game_id in self.summaries:
            if not self.collect_details:
                raise ConfigurationError(
                    f"live game detail is missing for id={game_id}",
                    stage="configuration",
                )
            # The first assembly selects details using the unchanged builder rules.
            # Its bundle is discarded; only the requested IDs are retained.
            self.required_details.add(game_id)
            game = self.summaries[game_id]
            return SimpleNamespace(
                id=game_id,
                tournament_id=game["tournament_id"],
                data={"game": game, "events": []},
            )
        record = self.database.get_game(game_id)
        if record.data["game"]["tournament_id"] in self.tournaments:
            raise ConfigurationError(
                f"game {game_id} is absent from the live tournament",
                stage="configuration",
            )
        return record


async def prepare_prompt_repository(
    match_id: int,
    *,
    repository: FootballDataRepository,
    client: THUFootballClient,
    tournament_id: int | None = None,
    include_details: bool = True,
    **builder_options: Any,
) -> PromptRepository:
    stored = PromptRepository(repository)
    target = None
    if tournament_id is None:
        try:
            tournament_id = repository.get_game(match_id).tournament_id
        except ConfigurationError:
            target = _jsonable(await client.get_game_info(match_id))
            tournament_id = target["game"]["tournament_id"]
    scope = next(
        (
            config
            for config in COMPETITIONS.values()
            if tournament_id in config.current_tournament_ids
        ),
        None,
    )
    if scope is None:
        return stored

    documents = await asyncio.gather(
        *(client.get_tournament_document(item) for item in scope.current_tournament_ids)
    )
    for item, document in zip(scope.current_tournament_ids, documents):
        stored.tournaments[item] = SimpleNamespace(
            id=item,
            competition=scope.competition.value,
            name=document["tournament"]["name"],
            final_rankings={},
            data=document,
        )
        stored.summaries.update({game["game_id"]: game for game in document["games"]})
    # The target only needs its basic fields and roster, already in GetTournInfo.
    game = stored.summaries.get(match_id)
    if game is None:
        raise ConfigurationError(
            f"target game {match_id} is absent from the live tournament",
            stage="configuration",
        )
    stored.games[match_id] = SimpleNamespace(
        id=match_id,
        tournament_id=tournament_id,
        data=target or {"game": game, "events": []},
    )
    if include_details:
        stored.collect_details = True
        build_prompt_bundle(match_id, repository=stored, **builder_options)
        stored.collect_details = False
        semaphore = asyncio.Semaphore(4)

        async def read_detail(game_id: int) -> None:
            async with semaphore:
                data = _jsonable(await client.get_game_info(game_id))
            stored.games[game_id] = SimpleNamespace(
                id=game_id,
                tournament_id=data["game"]["tournament_id"],
                data=data,
            )

        await asyncio.gather(
            *(read_detail(item) for item in sorted(stored.required_details))
        )
    return stored


async def build_live_prompt_bundle(
    match_id: int,
    *,
    repository: FootballDataRepository | None = None,
    client: THUFootballClient | None = None,
    **builder_options: Any,
) -> PromptBundle:
    if repository is None:
        engine = create_football_engine()
        try:
            with create_football_session_factory(engine)() as session:
                return await build_live_prompt_bundle(
                    match_id,
                    repository=FootballDataRepository(session),
                    client=client,
                    **builder_options,
                )
        finally:
            engine.dispose()
    if client is None:
        async with create_prompt_client() as active_client:
            return await build_live_prompt_bundle(
                match_id, repository=repository, client=active_client, **builder_options
            )
    stored = await prepare_prompt_repository(
        match_id, repository=repository, client=client, **builder_options
    )
    return build_prompt_bundle(match_id, repository=stored, **builder_options)
