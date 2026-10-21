"""Download match data and render reports with Pillow."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from .client import THUFootballClient
from .errors import (
    ConfigurationError,
    InvalidResponse,
    QueryValidationError,
)
from .models import (
    GameDetail,
    GameEvent,
    GameEventIssue,
    GameReportFile,
    GameSummary,
    PreparedGameReport,
    ReportSettings,
)
from .qr_cache import GameQRCodeCache
from .rankings import StaticOutcomeCatalog, load_outcome_catalog
from .report_drawing import (
    draw_report,
    encode_report,
    load_static_assets,
    measure_report,
)
from .report_validation import validate_game_events

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LINEUP_EVENTS = frozenset({"START", "APPEARANCE"})
_EVENT_ASSET_NAMES = {
    "ON": "event_on",
    "OFF": "event_off",
    "GOAL": "event_goal",
    "PENALTY": "event_penalty",
    "MISSPENALTY": "event_missed_penalty",
    "OWNGOAL": "event_own_goal",
    "YELLOWCARD": "event_yellow_card",
    "SECONDYELLOWCARD": "event_second_yellow_card",
    "REDCARD": "event_red_card",
}


def _positive_game_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise QueryValidationError(
            "game_id must be a positive integer",
            stage="validation",
        )
    return value


def _validate_settings(settings: object) -> ReportSettings:
    if not isinstance(settings, ReportSettings):
        raise QueryValidationError(
            "settings must be a ReportSettings value",
            stage="validation",
        )
    for field_name in (
        "include_qr_code",
        "include_time",
        "include_field",
        "include_lineup",
    ):
        if not isinstance(getattr(settings, field_name), bool):
            raise QueryValidationError(
                f"settings.{field_name} must be a boolean",
                stage="validation",
            )
    return settings


def resolve_report_team_name(
    game: GameSummary,
    side: Literal["home", "away"],
    outcome_catalog: StaticOutcomeCatalog | None = None,
    *,
    brief: bool = False,
) -> str:
    """Resolve the displayed report name, preferring the canonical full name."""

    registration = (
        game.home_tournament_team_id if side == "home" else game.away_tournament_team_id
    )
    catalog = outcome_catalog or load_outcome_catalog()
    identity = catalog.resolve_identity(registration)
    return identity.brief_name if brief else identity.institution_name


def _awarded_score(detail: GameDetail) -> int:
    return 5 if detail.players_per_side == 5 else 3


def _single_abandon_score(detail: GameDetail) -> tuple[int, int]:
    game = detail.game
    awarded = _awarded_score(detail)
    baseline = (0, awarded) if game.home_abandon is True else (awarded, 0)
    home_score = game.home_score
    away_score = game.away_score
    if home_score is None or away_score is None:
        return baseline
    abandoning_side_is_losing = (
        game.home_abandon is True and home_score < away_score
    ) or (game.away_abandon is True and away_score < home_score)
    if not abandoning_side_is_losing:
        return baseline
    current_key = (abs(home_score - away_score), max(home_score, away_score))
    baseline_key = (awarded, awarded)
    return (home_score, away_score) if current_key > baseline_key else baseline


def _single_abandon_text(
    detail: GameDetail,
    *,
    awarded_loss: bool,
    outcome_catalog: StaticOutcomeCatalog,
    score: tuple[int, int] | None = None,
) -> str:
    game = detail.game
    home_name = resolve_report_team_name(game, "home", outcome_catalog)
    away_name = resolve_report_team_name(game, "away", outcome_catalog)
    abandoned_name = home_name if game.home_abandon is True else away_name
    reason = "被判负" if awarded_loss else "弃赛"
    home_score, away_score = score or (
        (0, _awarded_score(detail))
        if game.home_abandon is True
        else (_awarded_score(detail), 0)
    )
    return (
        f"{home_name}vs{away_name}的比赛，由于{abandoned_name}{reason}，"
        f"记为{home_name} {home_score}:{away_score} {away_name}。"
    )


def _report_issues(
    detail: GameDetail,
    issues: tuple[GameEventIssue, ...],
    catalog: StaticOutcomeCatalog,
) -> tuple[GameEventIssue, ...]:
    if not issues:
        return ()
    names = {
        side: resolve_report_team_name(detail.game, side, catalog, brief=True)
        for side in ("home", "away")
    }
    return tuple(
        replace(
            issue,
            team_name=names.get(issue.side) or f"{names['home']} vs {names['away']}",
        )
        for issue in issues
    )


def _validate_report_events(
    detail: GameDetail, catalog: StaticOutcomeCatalog
) -> tuple[GameDetail, tuple[GameEventIssue, ...]]:
    validated, issues = validate_game_events(detail)
    return validated, _report_issues(detail, issues, catalog)


def prepare_game_report(
    detail: GameDetail,
    *,
    outcome_catalog: StaticOutcomeCatalog | None = None,
) -> PreparedGameReport:
    """Apply shared event-validation and abandonment rules to one report."""

    game = detail.game
    catalog = outcome_catalog or load_outcome_catalog()
    home_abandon = game.home_abandon is True
    away_abandon = game.away_abandon is True
    if home_abandon and away_abandon:
        home_name = resolve_report_team_name(game, "home", catalog)
        away_name = resolve_report_team_name(game, "away", catalog)
        warning = GameEventIssue(
            severity="info",
            code="both_sides_abandoned",
            message="双方弃赛，本场仅生成文字战报",
        )
        return PreparedGameReport(
            source_detail=detail,
            detail=detail,
            warnings=_report_issues(detail, (warning,), catalog),
            render_image=False,
            text=f"{home_name}vs{away_name}的比赛，双方弃赛",
        )

    if home_abandon or away_abandon:
        if not detail.events:
            return PreparedGameReport(
                source_detail=detail,
                detail=detail,
                warnings=(),
                render_image=False,
                text=_single_abandon_text(
                    detail, awarded_loss=False, outcome_catalog=catalog
                ),
            )

        side = "home" if home_abandon else "away"
        warning = GameEventIssue(
            severity="info",
            code="abandon_with_events_awarded_loss",
            message="该队弃赛但仍有比赛事件，按判负处理并生成战报",
            side=side,
        )
        validated, warnings = _validate_report_events(detail, catalog)
        score = _single_abandon_score(detail)
        render_game = replace(
            validated.game,
            home_score=score[0],
            away_score=score[1],
            result_text=f"{score[0]}:{score[1]}",
            home_penalty=None,
            away_penalty=None,
            home_abandon=False,
            away_abandon=False,
        )
        return PreparedGameReport(
            source_detail=detail,
            detail=replace(validated, game=render_game),
            warnings=(*_report_issues(detail, (warning,), catalog), *warnings),
            render_image=True,
            text=_single_abandon_text(
                detail,
                awarded_loss=True,
                outcome_catalog=catalog,
                score=score,
            ),
        )

    validated, warnings = _validate_report_events(detail, catalog)
    return PreparedGameReport(
        source_detail=detail,
        detail=validated,
        warnings=warnings,
        render_image=True,
        text=None,
    )


def _report_team_name(
    detail: GameDetail,
    side: Literal["home", "away"],
    outcome_catalog: StaticOutcomeCatalog,
) -> str:
    return resolve_report_team_name(detail.game, side, outcome_catalog)


def _report_subtitle(detail: GameDetail) -> str:
    game = detail.game
    subtitle = game.tournament_report_name or game.tournament_name
    if game.stage:
        subtitle += game.stage
    if game.group_name:
        subtitle += f"{game.group_name}组"
    if game.round:
        subtitle += f"第{game.round}轮"
    return subtitle


def _report_time(detail: GameDetail) -> str:
    """Format the report kickoff time with a zero-padded calendar date."""

    local = detail.game.kickoff_local
    return (
        f"{local.year}-{local.month:02d}-{local.day:02d} "
        f"{local.hour:02d}:{local.minute:02d}"
    )


def _event_name(event: GameEvent) -> str:
    prefix = f"{event.kit_number}-" if event.kit_number >= 0 else ""
    note = f"({event.note.strip()})" if event.note and event.note.strip() else ""
    return f"{prefix}{event.player_name.strip()}{note}"


def _event_time(event: GameEvent) -> str:
    if event.during_penalty_shootout:
        return "P" if event.minute is None else f"{event.minute}'P"
    if event.stoppage_minute > 0:
        return f"{event.minute}+{event.stoppage_minute}'"
    return f"{event.minute}'"


def _starters(detail: GameDetail, side: str) -> list[GameEvent]:
    return sorted(
        (
            event
            for event in detail.events
            if event.side == side and event.event_type.upper() in _LINEUP_EVENTS
        ),
        key=lambda event: event.kit_number,
    )


def _timeline_groups(detail: GameDetail) -> list[dict[str, Any]]:
    """Mirror the insertion-order grouping performed by TAFA's ``game.js``."""

    yellow_counts: dict[tuple[int, bool], int] = {}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for event in detail.events:
        event_type = event.event_type.upper()
        if event_type in _LINEUP_EVENTS:
            continue
        if event_type == "YELLOWCARD" and event.tournament_team_player_id:
            key = (
                event.tournament_team_player_id,
                event.during_penalty_shootout,
            )
            yellow_counts[key] = yellow_counts.get(key, 0) + 1
            if yellow_counts[key] == 2:
                event_type = "SECONDYELLOWCARD"

        asset_name = _EVENT_ASSET_NAMES.get(event_type)
        if asset_name is None:
            raise InvalidResponse(
                f"unsupported report event type {event_type!r}",
                stage="report",
                game_id=detail.game.game_id,
            )

        time_label = _event_time(event)
        bucket = grouped.setdefault(time_label, {"home": [], "away": []})
        side = event.side
        if event_type == "OWNGOAL":
            side = "away" if side == "home" else "home"
        bucket[side].append(
            {
                "id": event.event_id,
                "type": event_type,
                "asset": asset_name,
                "namestring": _event_name(event),
                "timestring": time_label,
                "during_penalty_shootout": event.during_penalty_shootout,
            }
        )

    return [
        {
            "time": time_label,
            "home": sides["home"],
            "away": sides["away"],
        }
        for time_label, sides in grouped.items()
    ]


def _report_payload(
    detail: GameDetail,
    *,
    settings: ReportSettings,
    assets: Mapping[str, bytes],
    qr_code: bytes | None,
    outcome_catalog: StaticOutcomeCatalog | None = None,
) -> dict[str, Any]:
    game_id = detail.game.game_id
    catalog = outcome_catalog or load_outcome_catalog()
    if not isinstance(assets, Mapping):
        raise QueryValidationError(
            "assets must be a mapping",
            stage="validation",
            game_id=game_id,
        )

    home_starters = _starters(detail, "home")
    away_starters = _starters(detail, "away")
    metadata: list[str] = []
    if settings.include_time:
        metadata.append(_report_time(detail))
    if settings.include_field and detail.game.field_name:
        metadata.append(detail.game.field_name)

    return {
        "game_id": game_id,
        "home_name": _report_team_name(detail, "home", catalog),
        "away_name": _report_team_name(detail, "away", catalog),
        "home_score": detail.game.home_score or 0,
        "away_score": detail.game.away_score or 0,
        "subtitle": _report_subtitle(detail),
        "metadata": "    ".join(metadata),
        "include_metadata": bool(metadata),
        "include_lineup": settings.include_lineup,
        "include_qr_code": settings.include_qr_code,
        "home_starters": [_event_name(event) for event in home_starters],
        "away_starters": [_event_name(event) for event in away_starters],
        "squad_title": (
            "出场阵容"
            if any(event.event_type.upper() == "APPEARANCE" for event in detail.events)
            else "首发阵容"
        ),
        "groups": _timeline_groups(detail),
    }


def render_game_report(
    detail: GameDetail,
    *,
    settings: ReportSettings,
    assets: Mapping[str, bytes],
    qr_code: bytes | None = None,
    outcome_catalog: StaticOutcomeCatalog | None = None,
) -> tuple[bytes, int, int]:
    """Draw the measured report with Pillow and return its RGB PNG."""

    settings = _validate_settings(settings)
    catalog = outcome_catalog or load_outcome_catalog()
    game_id = detail.game.game_id
    if settings.include_qr_code and qr_code is None:
        raise InvalidResponse(
            "the requested report QR image is missing",
            stage="report",
            game_id=game_id,
        )
    payload = _report_payload(
        detail,
        settings=settings,
        assets=assets,
        qr_code=qr_code,
        outcome_catalog=catalog,
    )
    layout = measure_report(payload, assets, qr_code)
    image = draw_report(layout)
    return encode_report(image), image.width, image.height


def _safe_filename(detail: GameDetail, outcome_catalog: StaticOutcomeCatalog) -> str:
    game = detail.game
    score = (
        f"{game.home_score}-{game.away_score}"
        if game.home_score is not None and game.away_score is not None
        else "vs"
    )
    filename = (
        f"game_{game.game_id}_"
        f"{_report_team_name(detail, 'home', outcome_catalog)}_{score}_"
        f"{_report_team_name(detail, 'away', outcome_catalog)}.png"
    )
    filename = _INVALID_FILENAME.sub("_", filename)
    filename = re.sub(r"\s+", "_", filename).strip(" ._")
    return filename or f"game_{game.game_id}_report.png"


def _output_path(
    output: str | os.PathLike[str] | None,
    detail: GameDetail,
    outcome_catalog: StaticOutcomeCatalog,
) -> Path:
    if output is None:
        return Path.cwd() / _safe_filename(detail, outcome_catalog)
    target = Path(output).expanduser()
    if target.exists() and target.is_dir():
        return target / _safe_filename(detail, outcome_catalog)
    if target.suffix.casefold() != ".png":
        target = target.with_suffix(".png")
    return target


class THUFootballReportService:
    """Read game data and save a PNG drawn with Pillow."""

    def __init__(
        self,
        client: THUFootballClient,
        outcome_catalog: StaticOutcomeCatalog | None = None,
        *,
        qr_cache: GameQRCodeCache | None = None,
    ) -> None:
        self._client = client
        self._outcome_catalog = outcome_catalog
        self._qr_cache = qr_cache

    @property
    def outcome_catalog(self) -> StaticOutcomeCatalog:
        if self._outcome_catalog is None:
            self._outcome_catalog = load_outcome_catalog()
        return self._outcome_catalog

    async def _get_report_assets(self) -> Mapping[str, bytes]:
        return await asyncio.to_thread(load_static_assets)

    async def load_game_detail(self, game_id: int) -> GameDetail:
        """Read the current unvalidated report input."""

        return await self._client.get_game_info(_positive_game_id(game_id))

    async def get_prepared_game_report(self, game_id: int) -> PreparedGameReport:
        """Read one match and apply the shared report rules."""

        return prepare_game_report(
            await self.load_game_detail(game_id),
            outcome_catalog=self.outcome_catalog,
        )

    async def get_game_detail(
        self, game_id: int
    ) -> tuple[GameDetail, tuple[GameEventIssue, ...]]:
        """Read and validate the current report input without rendering it."""

        detail = await self.load_game_detail(game_id)
        return _validate_report_events(detail, self.outcome_catalog)

    async def render_game_detail(
        self,
        detail: GameDetail,
        *,
        settings: ReportSettings = ReportSettings(),
    ) -> tuple[bytes, int, int]:
        """Render a previously loaded detail to PNG without querying it again."""

        settings = _validate_settings(settings)
        game_id = detail.game.game_id
        if settings.include_qr_code:
            qr_code, assets = await asyncio.gather(
                self._qr_cache.get(self._client, game_id)
                if self._qr_cache is not None
                else self._client.get_game_page_code(game_id),
                self._get_report_assets(),
            )
        else:
            qr_code = None
            assets = await self._get_report_assets()
        return await asyncio.to_thread(
            render_game_report,
            detail,
            settings=settings,
            assets=assets,
            qr_code=qr_code,
            outcome_catalog=self.outcome_catalog,
        )

    async def download_game_report(
        self,
        game_id: int,
        output: str | os.PathLike[str] | None = None,
        *,
        settings: ReportSettings = ReportSettings(),
        refresh_stats: bool = False,
        overwrite: bool = False,
    ) -> GameReportFile:
        game_id = _positive_game_id(game_id)
        settings = _validate_settings(settings)
        if not isinstance(refresh_stats, bool):
            raise QueryValidationError(
                "refresh_stats must be a boolean",
                stage="validation",
                game_id=game_id,
            )
        if not isinstance(overwrite, bool):
            raise QueryValidationError(
                "overwrite must be a boolean",
                stage="validation",
                game_id=game_id,
            )

        if refresh_stats:
            # SECURITY: OnReStatGameData uses GET but changes server-side match
            # statistics. Keep this behind the explicit refresh_stats opt-in.
            await self._client.refresh_game_stats(game_id)
        detail, warnings = await self.get_game_detail(game_id)
        png, width, height = await self.render_game_detail(detail, settings=settings)

        target = _output_path(output, detail, self.outcome_catalog).resolve()
        if target.exists() and not overwrite:
            raise ConfigurationError(
                "output file already exists; pass --override in the CLI "
                "or overwrite=True in Python to replace it",
                stage="output",
                game_id=game_id,
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(png)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ConfigurationError(
                "could not write the report output file",
                stage="output",
                game_id=game_id,
            ) from exc

        return GameReportFile(
            game_id=game_id,
            path=str(target),
            media_type="image/png",
            width=width,
            height=height,
            refreshed_stats=refresh_stats,
            warnings=warnings,
        )
