"""Database-backed administrator preview workflow."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from auto_preview import Competition, NoGamesForDate
from auto_preview.config import competition_config
from auto_preview.source import (
    APPROVER_PLACEHOLDER,
    EDITOR_PLACEHOLDER,
    HEADLINE_PLACEHOLDER,
    REVIEWER_PLACEHOLDER,
    WRITER_PLACEHOLDER,
    PreviewSourceBuilder,
    preview_data_to_dict,
)
from auto_report.models import Competition as ReportCompetition
from auto_report.service import (
    ARTICLE_AUTHOR as REPORT_ARTICLE_AUTHOR,
)
from auto_report.service import (
    ARTICLE_DIGEST as REPORT_ARTICLE_DIGEST,
)
from auto_report.service import (
    ARTICLE_TEMPLATE_VERSION as REPORT_ARTICLE_TEMPLATE_VERSION,
)
from auto_report.service import (
    ARTICLE_TITLES as REPORT_ARTICLE_TITLES,
)
from auto_report.service import (
    DEFAULT_REPORT_COVER_MEDIA_ID,
)
from preview import (
    PlayedMatch,
    PreviewColumnConfig,
    PreviewCredits,
    PreviewMatch,
    PreviewService,
    PreviewSourceData,
    PreviewTeam,
    PreviewWeather,
    SeasonOutcome,
    TeamRef,
    format_result_text,
    parse_preview_paragraphs,
    validate_preview_source,
)
from thufootball import (
    GameDetail,
    GameEventIssue,
    GameStatus,
    PreparedGameReport,
    ReportSettings,
    ReportValidationError,
    THUFootballClient,
    THUFootballQueryService,
    THUFootballReportService,
)
from weather import DailyWeather, WeatherQueryService
from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    WechatOfficialService,
    publication_fingerprint,
)

from .artifacts import (
    parse_report_storage_descriptor,
    report_storage_descriptor,
    resolve_report_storage_key,
    resolve_storage_key,
    save_cover,
    save_report,
    sha256_bytes,
    sha256_file,
)
from .config import PROJECT_ROOT, WebsiteSettings
from .models import (
    ArticleRecord,
    Batch,
    EditorialDefaults,
    Match,
    Weather,
    WechatDraft,
)

AUTO_PREVIEW_DIGEST = "马杯前瞻"
HAIDIAN_ADCODE = "110108"
COMPETITION_ORDER = {"male": 0, "female": 1, "futsal": 2}
SHANGHAI = timezone(timedelta(hours=8))
MATCH_REPORT_TEMPLATE_VERSION = "thufootball-report-canvas-v1"


class WorkflowError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


ServiceFactory = Callable[[], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True, slots=True)
class ExternalFactories:
    queries: ServiceFactory = THUFootballQueryService.from_environment
    weather: ServiceFactory = WeatherQueryService.from_environment
    wechat: ServiceFactory = WechatOfficialService.from_environment
    reports: ServiceFactory = lambda: WebsiteReportSession()


class WebsiteReportSession:
    """Share one authenticated THUFootball client while rendering reports."""

    def __init__(self) -> None:
        self._client = THUFootballClient()
        self._reports = THUFootballReportService(self._client)

    async def __aenter__(self) -> "WebsiteReportSession":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.aclose()

    async def get_prepared_game_report(self, game_id: int) -> PreparedGameReport:
        return await self._reports.get_prepared_game_report(game_id)

    async def render_game_detail(
        self, detail: GameDetail, *, settings: ReportSettings
    ) -> tuple[bytes, int, int]:
        return await self._reports.render_game_detail(detail, settings=settings)


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_key(namespace: str, value: str) -> int:
    digest = hashlib.sha256(f"{namespace}:{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _advisory_lock(session: Session, namespace: str, value: str) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _lock_key(namespace, value)},
        )


def _invalidate_batch(batch: Batch) -> None:
    batch.current_preview_article_id = None
    batch.updated_at = _now()


def _invalidate_date(session: Session, target_date: date) -> None:
    session.execute(
        update(Batch)
        .where(Batch.batch_date == target_date)
        .values(current_preview_article_id=None, updated_at=_now())
    )


def _deactivate_active_matches(session: Session, batch: Batch) -> bool:
    matches = list(
        session.scalars(
            select(Match).where(
                Match.batch_id == batch.id,
                Match.active.is_(True),
            )
        )
    )
    if not matches:
        return False
    changed_at = _now()
    for match in matches:
        match.active = False
        match.task_open = False
        match.updated_at = changed_at
    _invalidate_batch(batch)
    batch.current_report_article_id = None
    return True


def _weather_values(value: DailyWeather) -> dict[str, Any]:
    return {
        "adcode": value.adcode,
        "region_name": value.region_name,
        "condition": value.condition,
        "low_c": value.low_c,
        "high_c": value.high_c,
        "wind_direction": value.wind_direction,
        "wind_level": value.wind_level,
        "report_time": value.report_time,
    }


_WEATHER_RENDER_FIELDS = (
    "adcode",
    "region_name",
    "condition",
    "low_c",
    "high_c",
    "wind_direction",
    "wind_level",
)


def upsert_automatic_weather(session: Session, value: DailyWeather) -> bool:
    existing = session.get(Weather, value.forecast_date)
    if existing is not None and existing.source == "manual":
        return False
    values = _weather_values(value)
    if existing is None:
        session.add(Weather(date=value.forecast_date, source="auto", **values))
        _invalidate_date(session, value.forecast_date)
        return True
    content_changed = any(
        getattr(existing, name) != values[name] for name in _WEATHER_RENDER_FIELDS
    )
    record_changed = existing.source != "auto" or any(
        getattr(existing, name) != item for name, item in values.items()
    )
    if record_changed:
        for name, item in values.items():
            setattr(existing, name, item)
        existing.source = "auto"
    if content_changed:
        _invalidate_date(session, value.forecast_date)
    return content_changed


def set_manual_weather(
    session: Session,
    target_date: date,
    *,
    condition: str,
    low_c: int,
    high_c: int,
    wind_direction: str,
    wind_level: str,
) -> Weather:
    existing = session.get(Weather, target_date)
    content = {
        "adcode": HAIDIAN_ADCODE,
        "region_name": "海淀区",
        "condition": condition.strip(),
        "low_c": low_c,
        "high_c": high_c,
        "wind_direction": wind_direction.strip(),
        "wind_level": wind_level.strip(),
    }
    values = {
        **content,
        "source": "manual",
        "report_time": _now(),
    }
    if existing is None:
        existing = Weather(date=target_date, **values)
        session.add(existing)
        content_changed = True
    else:
        content_changed = any(
            getattr(existing, name) != item for name, item in content.items()
        )
        if content_changed or existing.source != "manual":
            for name, item in values.items():
                setattr(existing, name, item)
    if content_changed:
        _invalidate_date(session, target_date)
    session.flush()
    return existing


def _source_match_payload(source: PreviewSourceData) -> dict[int, dict[str, Any]]:
    payload = preview_data_to_dict(source)
    matches = payload["matches"]
    assert isinstance(matches, list)
    return {int(item["game_id"]): item for item in matches}


def upsert_source(
    session: Session,
    batch: Batch,
    source: PreviewSourceData,
    games_by_id: dict[int, Any],
) -> bool:
    payloads = _source_match_payload(source)
    incoming_ids = set(payloads)
    changed = False
    current = list(
        session.scalars(
            select(Match).where(Match.batch_id == batch.id)
        )
    )
    report_order_before = sorted(
        (record.kickoff, record.game_id)
        for record in current
        if record.active and record.status == "finished"
    )
    for record in current:
        if record.game_id not in incoming_ids and record.active:
            record.active = False
            record.task_open = False
            record.updated_at = _now()
            changed = True

    for game_id, payload in payloads.items():
        game = games_by_id[game_id]
        values = {
            "batch_id": batch.id,
            "tournament_id": game.tournament_id,
            "tournament_name": game.tournament_name,
            "competition_name": payload["competition_name"],
            "stage": payload["stage"],
            "kickoff": game.kickoff_local,
            "venue": payload["venue"],
            "home_snapshot": payload["home"],
            "away_snapshot": payload["away"],
            "head_to_head_snapshot": payload["head_to_head"],
            "active": True,
        }
        record = session.get(Match, game_id)
        if record is None:
            session.add(Match(game_id=game_id, status=game.status.value, **values))
            changed = True
            continue
        record_changed = any(getattr(record, name) != item for name, item in values.items())
        if record.batch_id != batch.id:
            old_batch = session.get(Batch, record.batch_id)
            if old_batch is not None:
                _invalidate_batch(old_batch)
            record.task_open = False
            record_changed = True
        if record_changed:
            for name, item in values.items():
                setattr(record, name, item)
            record.updated_at = _now()
            changed = True
        if record.status != game.status.value:
            record.status = game.status.value
            record.updated_at = _now()
    if changed:
        _invalidate_batch(batch)
    session.flush()
    report_order_after = [
        tuple(row)
        for row in session.execute(
            select(Match.kickoff, Match.game_id)
            .where(
                Match.batch_id == batch.id,
                Match.active.is_(True),
                Match.status == "finished",
            )
            .order_by(Match.kickoff, Match.game_id)
        )
    ]
    if report_order_before != report_order_after:
        batch.current_report_article_id = None
        batch.updated_at = _now()
    batch.last_error_code = None
    batch.last_error_message = None
    batch.last_error_at = None
    session.flush()
    return changed


def _get_or_create_batch(
    session: Session,
    settings: WebsiteSettings,
    preview_date: date,
    competition: str,
) -> tuple[Batch, bool]:
    batch = session.scalar(
        select(Batch).where(
            Batch.batch_date == preview_date,
            Batch.competition == competition,
        )
    )
    if batch is not None:
        return batch, False
    defaults = session.get(EditorialDefaults, 1)
    if defaults is None:
        defaults = EditorialDefaults(id=1)
        session.add(defaults)
        session.flush()
    batch = Batch(
        batch_date=preview_date,
        competition=competition,
        editors=list(defaults.editors),
        reviewers=list(defaults.reviewers),
        approvers=list(defaults.approvers),
        cover_kind="media_id",
        cover_storage_key=settings.default_cover_media_id,
        cover_content_type=None,
    )
    session.add(batch)
    session.flush()
    return batch, True


async def _query_weather(
    dates: list[date], factories: ExternalFactories
) -> tuple[dict[date, DailyWeather], dict[date, str]]:
    values: dict[date, DailyWeather] = {}
    warnings: dict[date, str] = {}
    try:
        async with factories.weather() as service:
            for target_date in dates:
                try:
                    values[target_date] = await service.get_weather(
                        HAIDIAN_ADCODE, target_date
                    )
                except Exception as exc:
                    warnings[target_date] = str(exc)
    except Exception as exc:
        for target_date in dates:
            warnings[target_date] = str(exc)
    return values, warnings


async def create_batches(
    factory: sessionmaker[Session],
    settings: WebsiteSettings,
    factories: ExternalFactories,
    dates: list[date],
    competitions: list[str],
) -> list[dict[str, Any]]:
    resolved_dates = sorted(set(dates))
    resolved_competitions = sorted(
        set(competitions), key=COMPETITION_ORDER.__getitem__
    )
    with factory() as session:
        existing_batches = list(
            session.scalars(
                select(Batch).where(
                    Batch.batch_date.in_(resolved_dates),
                    Batch.competition.in_(resolved_competitions),
                )
            )
        )
    existing = {
        (batch.batch_date, batch.competition): batch.id
        for batch in existing_batches
    }
    results: list[dict[str, Any]] = [
        {
            "date": target_date.isoformat(),
            "competition": competition_value,
            "status": "reused",
            "batch_id": existing[(target_date, competition_value)],
        }
        for target_date in resolved_dates
        for competition_value in resolved_competitions
        if (target_date, competition_value) in existing
    ]
    missing_by_competition = {
        competition_value: [
            target_date
            for target_date in resolved_dates
            if (target_date, competition_value) not in existing
        ]
        for competition_value in resolved_competitions
    }
    missing_dates = sorted(
        {
            target_date
            for dates_for_competition in missing_by_competition.values()
            for target_date in dates_for_competition
        }
    )
    if not missing_dates:
        return sorted(
            results,
            key=lambda item: (
                item["date"],
                COMPETITION_ORDER[item["competition"]],
            ),
        )
    weather_values, weather_warnings = await _query_weather(missing_dates, factories)
    for competition_value in resolved_competitions:
        dates_for_competition = missing_by_competition[competition_value]
        if not dates_for_competition:
            continue
        try:
            async with factories.queries() as queries:
                builder = PreviewSourceBuilder(
                    queries,
                    competition_config(Competition(competition_value)),
                    logger=logging.getLogger("backend.preview-data"),
                )
                games = await builder.query_current_games()
                games_by_id = {game.game_id: game for game in games}
                sources: dict[date, PreviewSourceData | None] = {}
                for target_date in dates_for_competition:
                    try:
                        sources[target_date] = await builder.build(
                            target_date, games=games
                        )
                    except NoGamesForDate:
                        sources[target_date] = None
        except Exception as exc:
            for target_date in dates_for_competition:
                results.append(
                    {
                        "date": target_date.isoformat(),
                        "competition": competition_value,
                        "status": "failed",
                        "error": {"code": "query_failed", "message": str(exc)},
                    }
                )
            continue

        for target_date in dates_for_competition:
            source = sources[target_date]
            with factory() as session:
                lock_value = f"{target_date.isoformat()}:{competition_value}"
                try:
                    with session.begin():
                        _advisory_lock(session, "preview-batch", lock_value)
                        current_batch = session.scalar(
                            select(Batch).where(
                                Batch.batch_date == target_date,
                                Batch.competition == competition_value,
                            )
                        )
                        if current_batch is not None:
                            results.append(
                                {
                                    "date": target_date.isoformat(),
                                    "competition": competition_value,
                                    "status": "reused",
                                    "batch_id": current_batch.id,
                                }
                            )
                            continue
                        if source is None:
                            results.append(
                                {
                                    "date": target_date.isoformat(),
                                    "competition": competition_value,
                                    "status": "skipped",
                                    "reason": "no_games",
                                }
                            )
                            continue
                        batch, _ = _get_or_create_batch(
                            session,
                            settings,
                            target_date,
                            competition_value,
                        )
                        upsert_source(session, batch, source, games_by_id)
                        weather = weather_values.get(target_date)
                        if weather is not None:
                            upsert_automatic_weather(session, weather)
                        warning = weather_warnings.get(target_date)
                        results.append(
                            {
                                "date": target_date.isoformat(),
                                "competition": competition_value,
                                "status": "created",
                                "batch_id": batch.id,
                                **({"warning": warning} if warning else {}),
                            }
                        )
                except Exception as exc:
                    results.append(
                        {
                            "date": target_date.isoformat(),
                            "competition": competition_value,
                            "status": "failed",
                            "error": {"code": "write_failed", "message": str(exc)},
                        }
                    )
    return sorted(
        results,
        key=lambda item: (
            item["date"],
            COMPETITION_ORDER[item["competition"]],
        ),
    )


async def refresh_batch(
    session: Session,
    batch: Batch,
    factories: ExternalFactories,
) -> None:
    config = competition_config(Competition(batch.competition))
    try:
        async with factories.queries() as queries:
            builder = PreviewSourceBuilder(
                queries, config, logger=logging.getLogger("backend.preview-data")
            )
            games = await builder.query_current_games()
            games_by_id = {game.game_id: game for game in games}
            try:
                source = await builder.build(batch.batch_date, games=games)
            except NoGamesForDate:
                source = None
        _advisory_lock(
            session,
            "preview-batch",
            f"{batch.batch_date.isoformat()}:{batch.competition}",
        )
        if source is None:
            _deactivate_active_matches(session, batch)
            batch.last_error_code = None
            batch.last_error_message = None
            batch.last_error_at = None
        else:
            upsert_source(session, batch, source, games_by_id)
        current_weather = session.get(Weather, batch.batch_date)
        if current_weather is None or current_weather.source != "manual":
            try:
                async with factories.weather() as service:
                    value = await service.get_weather(HAIDIAN_ADCODE, batch.batch_date)
                upsert_automatic_weather(session, value)
            except Exception as exc:
                batch.last_error_code = "weather_query_failed"
                batch.last_error_message = str(exc)
                batch.last_error_at = _now()
    except Exception as exc:
        batch.last_error_code = "query_failed"
        batch.last_error_message = str(exc)
        batch.last_error_at = _now()
        session.commit()
        raise WorkflowError(502, "query_failed", str(exc)) from exc
    session.commit()


def completeness(session: Session, batch: Batch) -> list[str]:
    missing: list[str] = []
    if not batch.headline.strip():
        missing.append("headline")
    weather = session.get(Weather, batch.batch_date)
    if weather is None:
        missing.append("weather")
    for field in ("editors", "reviewers", "approvers"):
        if not getattr(batch, field):
            missing.append(field)
    matches = list(
        session.scalars(
            select(Match)
            .where(
                Match.batch_id == batch.id,
                Match.active.is_(True),
            )
            .order_by(Match.kickoff, Match.game_id)
        )
    )
    if not matches:
        missing.append("matches")
    for match in matches:
        if not match.writers:
            missing.append(f"matches.{match.game_id}.writers")
        if not match.body.strip():
            missing.append(f"matches.{match.game_id}.body")
    return missing


def batch_status(session: Session, batch: Batch) -> str:
    if completeness(session, batch):
        return "incomplete"
    if batch.current_preview_article_id is not None:
        for draft in session.scalars(select(WechatDraft)):
            if any(
                item.get("article_id") == batch.current_preview_article_id
                for item in draft.articles
                if isinstance(item, dict)
            ):
                return "drafted"
    return "ready"


def _team_ref(value: dict[str, Any]) -> TeamRef:
    return TeamRef(
        team_id=int(value["team_id"]),
        name=str(value["name"]),
        short_name=str(value["short_name"]),
    )


def _played(value: dict[str, Any]) -> PlayedMatch:
    return PlayedMatch(
        game_id=int(value["game_id"]),
        home=_team_ref(value["home"]),
        away=_team_ref(value["away"]),
        home_score=value.get("home_score"),
        away_score=value.get("away_score"),
        home_penalty=value.get("home_penalty"),
        away_penalty=value.get("away_penalty"),
        result_text=value.get("result_text"),
        season=value.get("season"),
        competition_label=value.get("competition_label"),
        stage=value.get("stage"),
    )


def _team(value: dict[str, Any]) -> PreviewTeam:
    return PreviewTeam(
        team_id=int(value["team_id"]),
        name=str(value["name"]),
        short_name=str(value["short_name"]),
        previous_outcomes=tuple(
            SeasonOutcome(
                season=str(item["season"]),
                competition_label=item.get("competition_label"),
                outcome=str(item["outcome"]),
            )
            for item in value.get("previous_outcomes", [])
        ),
        current_results=tuple(
            _played(item) for item in value.get("current_results", [])
        ),
    )


def assemble_source(session: Session, batch: Batch) -> PreviewSourceData:
    config = competition_config(Competition(batch.competition))
    weather_record = session.get(Weather, batch.batch_date)
    weather = (
        None
        if weather_record is None
        else PreviewWeather(
            condition=weather_record.condition,
            low_c=weather_record.low_c,
            high_c=weather_record.high_c,
            wind_direction=weather_record.wind_direction,
            wind_level=weather_record.wind_level,
        )
    )
    matches: list[PreviewMatch] = []
    for record in session.scalars(
        select(Match)
        .where(
            Match.batch_id == batch.id,
            Match.active.is_(True),
        )
        .order_by(Match.kickoff, Match.game_id)
    ):
        paragraphs = (
            parse_preview_paragraphs(record.body)
            if record.body.strip()
            else (
                f"【待填写：{record.home_snapshot['short_name']} 对阵 "
                f"{record.away_snapshot['short_name']} 前瞻】",
            )
        )
        matches.append(
            PreviewMatch(
                game_id=record.game_id,
                competition_name=record.competition_name,
                stage=record.stage,
                kickoff=record.kickoff.astimezone(SHANGHAI),
                venue=record.venue,
                home=_team(record.home_snapshot),
                away=_team(record.away_snapshot),
                head_to_head=tuple(
                    _played(item) for item in record.head_to_head_snapshot
                ),
                preview_paragraphs=paragraphs,
                writers=tuple(record.writers) or (WRITER_PLACEHOLDER,),
            )
        )
    if not matches:
        matches.append(
            PreviewMatch(
                game_id=-1,
                competition_name=config.full_name,
                stage="赛程待补充",
                kickoff=datetime.combine(
                    batch.batch_date,
                    time(hour=12),
                    tzinfo=SHANGHAI,
                ),
                venue="待定",
                home=PreviewTeam(team_id=1, name="主队待定", short_name="主队待定"),
                away=PreviewTeam(team_id=2, name="客队待定", short_name="客队待定"),
                head_to_head=(),
                preview_paragraphs=("【待补充比赛与前瞻内容】",),
                writers=(WRITER_PLACEHOLDER,),
            )
        )
    source = PreviewSourceData(
        column=PreviewColumnConfig(
            competition_full_name=config.full_name,
            competition_short_name=config.short_name,
        ),
        preview_date=batch.batch_date,
        headline=batch.headline.strip() or HEADLINE_PLACEHOLDER,
        weather=weather,
        matches=tuple(matches),
        credits=PreviewCredits(
            editors=tuple(batch.editors) or (EDITOR_PLACEHOLDER,),
            reviewers=tuple(batch.reviewers) or (REVIEWER_PLACEHOLDER,),
            approvers=tuple(batch.approvers) or (APPROVER_PLACEHOLDER,),
        ),
    )
    validate_preview_source(source)
    return source


def _cover_for_batch(
    settings: WebsiteSettings, batch: Batch
) -> tuple[CoverFile | CoverMediaId, str]:
    if batch.cover_kind == "media_id":
        value = batch.cover_storage_key.strip()
        return CoverMediaId(value), sha256_bytes(value.encode())
    path = resolve_storage_key(settings.artifact_root, batch.cover_storage_key)
    if not path.is_file():
        raise WorkflowError(409, "cover_missing", "cover file is missing")
    return CoverFile(path), sha256_file(path)


def render_batch(
    session: Session,
    settings: WebsiteSettings,
    batch: Batch,
) -> tuple[ArticleRecord, bool]:
    _advisory_lock(session, "preview-batch", str(batch.id))
    session.refresh(batch)
    if batch.current_preview_article_id is not None:
        existing = session.get(ArticleRecord, batch.current_preview_article_id)
        if existing is None or existing.batch_id != batch.id:
            raise WorkflowError(409, "article_pointer_invalid", "current article is invalid")
        return existing, True
    source = assemble_source(session, batch)
    cover, cover_sha256 = _cover_for_batch(settings, batch)
    renderer = PreviewService.from_template(
        PROJECT_ROOT / "templates" / "qhly_preview_v1" / "template.html"
    )
    article = renderer.render(
        source,
        cover=cover,
        author="清华绿茵",
        digest=AUTO_PREVIEW_DIGEST,
    )
    latest_version = session.scalar(
        select(func.max(ArticleRecord.version_number)).where(
            ArticleRecord.batch_id == batch.id,
            ArticleRecord.article_type == "preview",
        )
    )
    missing = completeness(session, batch)
    record = ArticleRecord(
        batch_id=batch.id,
        article_type="preview",
        version_number=(latest_version or 0) + 1,
        input_snapshot=preview_data_to_dict(source),
        title=article.title,
        body_html=article.body_html,
        author=article.author,
        digest=article.digest,
        source_url=article.source_url,
        template_version=renderer.template_version,
        content_fingerprint=article.content_fingerprint,
        cover_kind=batch.cover_kind,
        cover_storage_key=batch.cover_storage_key,
        cover_sha256=cover_sha256,
        is_complete=not missing,
        missing_fields=missing,
    )
    session.add(record)
    session.flush()
    batch.current_preview_article_id = record.id
    batch.updated_at = _now()
    session.flush()
    return record, False


def _report_input_sha256(detail: GameDetail) -> str:
    def default(value: Any) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"unsupported report input value: {type(value).__name__}")

    payload = {
        "detail": asdict(detail),
        "settings": asdict(ReportSettings()),
        "template_version": MATCH_REPORT_TEMPLATE_VERSION,
    }
    return sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=default,
        ).encode("utf-8")
    )


def report_content_paths(settings: WebsiteSettings, match: Match) -> dict[str, Path]:
    if match.report_storage_key is None or match.report_content_sha256 is None:
        raise WorkflowError(404, "report_not_found", "match report has not been rendered")
    try:
        storage_keys = parse_report_storage_descriptor(match.report_storage_key)
    except ValueError as exc:
        raise WorkflowError(409, "report_path_invalid", str(exc)) from exc
    descriptor, fingerprint = report_storage_descriptor(storage_keys)
    if descriptor != match.report_storage_key or fingerprint != match.report_content_sha256:
        raise WorkflowError(409, "report_changed", "match report descriptor changed")
    paths: dict[str, Path] = {}
    for kind, storage_key in storage_keys.items():
        try:
            path = resolve_report_storage_key(settings.artifact_root, storage_key)
        except ValueError as exc:
            raise WorkflowError(409, "report_path_invalid", str(exc)) from exc
        if not path.is_file():
            raise WorkflowError(409, "report_missing", "match report artifact is missing")
        expected_sha256 = path.stem
        if len(expected_sha256) != 64 or sha256_file(path) != expected_sha256:
            raise WorkflowError(409, "report_changed", "match report artifact changed")
        paths[kind] = path
    return paths


async def _render_match_with_service(
    session: Session,
    settings: WebsiteSettings,
    match: Match,
    reports: Any,
) -> tuple[bool, tuple[GameEventIssue, ...]]:
    prepared = await reports.get_prepared_game_report(match.game_id)
    if prepared.source_detail.game.status is not GameStatus.FINISHED:
        raise WorkflowError(
            409,
            "match_not_finished",
            f"match {match.game_id} is not finished; refresh the batch first",
        )
    input_sha256 = _report_input_sha256(prepared.source_detail)
    if match.report_input_sha256 == input_sha256:
        try:
            report_content_paths(settings, match)
        except WorkflowError:
            pass
        else:
            return True, prepared.warnings

    contents: dict[str, tuple[bytes, str]] = {}
    if prepared.render_image:
        image, _width, _height = await reports.render_game_detail(
            prepared.detail, settings=ReportSettings()
        )
        contents["image"] = (image, "png")
    if prepared.text is not None:
        contents["text"] = (prepared.text.encode("utf-8"), "txt")
    try:
        storage_keys = {
            kind: save_report(settings.artifact_root, content, extension=extension)[0]
            for kind, (content, extension) in contents.items()
        }
        storage_key, content_sha256 = report_storage_descriptor(storage_keys)
    except ValueError as exc:
        raise WorkflowError(500, "report_artifact_invalid", str(exc)) from exc
    match.report_input_sha256 = input_sha256
    match.report_storage_key = storage_key
    match.report_content_sha256 = content_sha256
    match.report_rendered_at = _now()
    match.updated_at = _now()
    batch = session.get(Batch, match.batch_id)
    if batch is None:
        raise WorkflowError(409, "batch_missing", "match batch is missing")
    batch.current_report_article_id = None
    batch.updated_at = _now()
    session.flush()
    return False, prepared.warnings


def _report_workflow_error(exc: Exception) -> WorkflowError:
    if isinstance(exc, WorkflowError):
        return exc
    if isinstance(exc, ReportValidationError):
        return WorkflowError(
            422,
            "report_event_validation_failed",
            str(exc),
            details={"issues": [asdict(issue) for issue in exc.issues]},
        )
    return WorkflowError(502, "report_query_failed", str(exc))


def _report_success_diagnostic(
    match: Match,
    reused: bool,
    issues: tuple[GameEventIssue, ...],
) -> dict[str, Any]:
    return {
        "game_id": match.game_id,
        "status": "success",
        "reused": reused,
        "issues": [asdict(issue) for issue in issues],
        "error": None,
    }


def _report_failure_diagnostic(
    match: Match,
    error: WorkflowError,
) -> dict[str, Any]:
    details = error.details or {}
    issues = details.get("issues")
    return {
        "game_id": match.game_id,
        "status": "failed",
        "reused": None,
        "issues": issues if isinstance(issues, list) else [],
        "error": {"code": error.code, "message": error.message},
    }


async def render_match_report(
    session: Session,
    settings: WebsiteSettings,
    factories: ExternalFactories,
    match: Match,
) -> tuple[bool, list[dict[str, Any]]]:
    _advisory_lock(session, "match-report", str(match.game_id))
    session.refresh(match)
    try:
        async with factories.reports() as reports:
            reused, issues = await _render_match_with_service(
                session, settings, match, reports
            )
            return reused, [_report_success_diagnostic(match, reused, issues)]
    except Exception as exc:
        error = _report_workflow_error(exc)
        raise WorkflowError(
            error.status_code,
            error.code,
            error.message,
            details={"diagnostics": [_report_failure_diagnostic(match, error)]},
        ) from exc


def _report_body(settings: WebsiteSettings, matches: list[Match]) -> str:
    fragments: list[str] = []
    for match in matches:
        paths = report_content_paths(settings, match)
        image_path = paths.get("image")
        if image_path is not None:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            fragments.append(
                '<section style="margin:0;padding:0;">'
                '<img src="data:image/png;base64,'
                + encoded
                + '" style="display:block;width:100%;height:auto;" />'
                "</section>"
            )
        text_path = paths.get("text")
        if text_path is not None:
            fragments.append(
                '<p style="margin:16px 0;line-height:1.75;">'
                + html.escape(text_path.read_text(encoding="utf-8"))
                + "</p>"
            )
    return "".join(fragments)


async def render_report_batch(
    session: Session,
    settings: WebsiteSettings,
    factories: ExternalFactories,
    batch: Batch,
) -> tuple[ArticleRecord, bool, list[dict[str, Any]]]:
    _advisory_lock(session, "report-batch", str(batch.id))
    session.refresh(batch)
    matches = list(
        session.scalars(
            select(Match)
            .where(
                Match.batch_id == batch.id,
                Match.active.is_(True),
                Match.status == "finished",
            )
            .order_by(Match.kickoff, Match.game_id)
        )
    )
    if not matches:
        raise WorkflowError(
            409, "no_finished_matches", "batch has no saved finished matches"
        )
    diagnostics: list[dict[str, Any]] = []
    failure_statuses: list[int] = []
    try:
        async with factories.reports() as reports:
            for match in matches:
                try:
                    reused, issues = await _render_match_with_service(
                        session, settings, match, reports
                    )
                    diagnostics.append(
                        _report_success_diagnostic(match, reused, issues)
                    )
                except Exception as exc:
                    error = _report_workflow_error(exc)
                    failure_statuses.append(error.status_code)
                    diagnostics.append(_report_failure_diagnostic(match, error))
    except Exception as exc:
        error = _report_workflow_error(exc)
        diagnostics = [
            _report_failure_diagnostic(match, error) for match in matches
        ]
        failure_statuses = [error.status_code] * len(matches)
    if failure_statuses:
        status_code = (
            502
            if any(value >= 500 for value in failure_statuses)
            else max(failure_statuses)
        )
        raise WorkflowError(
            status_code,
            "report_batch_render_failed",
            f"report rendering failed for {len(failure_statuses)} match(es)",
            details={"diagnostics": diagnostics},
        )

    snapshot = {
        "matches": [
            {
                "game_id": match.game_id,
                "report_input_sha256": match.report_input_sha256,
                "report_content_sha256": match.report_content_sha256,
            }
            for match in matches
        ]
    }
    if batch.current_report_article_id is not None:
        existing = session.get(ArticleRecord, batch.current_report_article_id)
        if (
            existing is None
            or existing.batch_id != batch.id
            or existing.article_type != "report"
        ):
            raise WorkflowError(409, "article_pointer_invalid", "current report article is invalid")
        if existing.input_snapshot == snapshot:
            return existing, True, diagnostics

    cover = CoverMediaId(DEFAULT_REPORT_COVER_MEDIA_ID)
    body_html = _report_body(settings, matches)
    article = Article(
        title=REPORT_ARTICLE_TITLES[ReportCompetition(batch.competition)],
        body_html=body_html,
        cover=cover,
        author=REPORT_ARTICLE_AUTHOR,
        digest=REPORT_ARTICLE_DIGEST,
    )
    latest_version = session.scalar(
        select(func.max(ArticleRecord.version_number)).where(
            ArticleRecord.batch_id == batch.id,
            ArticleRecord.article_type == "report",
        )
    )
    cover_sha256 = sha256_bytes(DEFAULT_REPORT_COVER_MEDIA_ID.encode("utf-8"))
    record = ArticleRecord(
        batch_id=batch.id,
        article_type="report",
        version_number=(latest_version or 0) + 1,
        input_snapshot=snapshot,
        title=article.title,
        body_html=article.body_html,
        author=article.author,
        digest=article.digest,
        source_url=article.source_url,
        template_version=REPORT_ARTICLE_TEMPLATE_VERSION,
        content_fingerprint=article.content_fingerprint,
        cover_kind="media_id",
        cover_storage_key=DEFAULT_REPORT_COVER_MEDIA_ID,
        cover_sha256=cover_sha256,
        is_complete=True,
        missing_fields=[],
    )
    session.add(record)
    session.flush()
    batch.current_report_article_id = record.id
    batch.updated_at = _now()
    session.flush()
    return record, False, diagnostics


def article_domain(
    settings: WebsiteSettings, record: ArticleRecord
) -> Article:
    if record.cover_kind == "media_id":
        actual = sha256_bytes(record.cover_storage_key.encode())
        cover: CoverFile | CoverMediaId = CoverMediaId(record.cover_storage_key)
    else:
        path = resolve_storage_key(settings.artifact_root, record.cover_storage_key)
        if not path.is_file():
            raise WorkflowError(409, "cover_missing", "article cover file is missing")
        actual = sha256_file(path)
        cover = CoverFile(path)
    if actual != record.cover_sha256:
        raise WorkflowError(409, "cover_changed", "article cover fingerprint changed")
    return Article(
        title=record.title,
        body_html=record.body_html,
        cover=cover,
        author=record.author,
        digest=record.digest,
        source_url=record.source_url,
    )


async def create_wechat_draft(
    session: Session,
    settings: WebsiteSettings,
    factories: ExternalFactories,
    article_ids: list[int],
    *,
    confirm: bool,
) -> dict[str, Any]:
    records: list[ArticleRecord] = []
    for article_id in article_ids:
        record = session.get(ArticleRecord, article_id)
        if record is None:
            raise WorkflowError(404, "article_not_found", f"article {article_id} not found")
        batch = session.get(Batch, record.batch_id)
        current_id = (
            batch.current_preview_article_id
            if batch is not None and record.article_type == "preview"
            else batch.current_report_article_id
            if batch is not None
            else None
        )
        if batch is None or current_id != record.id:
            raise WorkflowError(409, "article_stale", f"article {article_id} is not current")
        if not record.is_complete:
            raise WorkflowError(
                409,
                "article_incomplete",
                f"article {article_id} is incomplete",
                details={"missing_fields": record.missing_fields},
            )
        records.append(record)
    components = [
        {
            "article_id": record.id,
            "content_fingerprint": record.content_fingerprint,
            "cover_sha256": record.cover_sha256,
        }
        for record in records
    ]
    fingerprint = publication_fingerprint(components)
    _advisory_lock(session, "wechat-draft", fingerprint)
    existing = session.scalar(
        select(WechatDraft).where(
            WechatDraft.publication_fingerprint == fingerprint
        )
    )
    if existing is not None:
        return {"status": "reused", "draft": draft_payload(existing)}
    if not confirm:
        return {
            "status": "ready",
            "publication_fingerprint": fingerprint,
            "articles": components,
        }
    domain_articles = tuple(article_domain(settings, record) for record in records)
    try:
        async with factories.wechat() as wechat:
            receipt = await wechat.create_draft(domain_articles)
    except Exception as exc:
        raise WorkflowError(502, "wechat_failed", str(exc)) from exc
    draft = WechatDraft(
        articles=components,
        publication_fingerprint=fingerprint,
        media_id=receipt.media_id,
        wechat_created_at=receipt.created_at,
    )
    session.add(draft)
    session.flush()
    return {"status": "created", "draft": draft_payload(draft)}


def save_batch_cover(
    session: Session,
    settings: WebsiteSettings,
    batch: Batch,
    content: bytes,
) -> None:
    try:
        key, content_type = save_cover(settings.artifact_root, content)
    except ValueError as exc:
        raise WorkflowError(422, "invalid_cover", str(exc)) from exc
    if (
        batch.cover_kind != "file"
        or batch.cover_storage_key != key
        or batch.cover_content_type != content_type
    ):
        batch.cover_kind = "file"
        batch.cover_storage_key = key
        batch.cover_content_type = content_type
        _invalidate_batch(batch)


def set_batch_cover_media_id(
    batch: Batch,
    media_id: str,
) -> None:
    if batch.cover_kind != "media_id" or batch.cover_storage_key != media_id:
        batch.cover_kind = "media_id"
        batch.cover_storage_key = media_id
        batch.cover_content_type = None
        _invalidate_batch(batch)


def article_payload(record: ArticleRecord, current_id: int | None = None) -> dict[str, Any]:
    return {
        "id": record.id,
        "batch_id": record.batch_id,
        "article_type": record.article_type,
        "version_number": record.version_number,
        "title": record.title,
        "body_html": record.body_html,
        "author": record.author,
        "digest": record.digest,
        "source_url": record.source_url,
        "template_version": record.template_version,
        "content_fingerprint": record.content_fingerprint,
        "cover_kind": record.cover_kind,
        "cover_storage_key": record.cover_storage_key,
        "cover_sha256": record.cover_sha256,
        "is_complete": record.is_complete,
        "missing_fields": record.missing_fields,
        "input_snapshot": record.input_snapshot,
        "created_at": record.created_at.isoformat(),
        "is_current": current_id == record.id if current_id is not None else None,
    }


def draft_payload(draft: WechatDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "articles": draft.articles,
        "publication_fingerprint": draft.publication_fingerprint,
        "media_id": draft.media_id,
        "wechat_created_at": draft.wechat_created_at.isoformat(),
        "created_at": draft.created_at.isoformat(),
    }


def weather_payload(value: Weather | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "date": value.date.isoformat(),
        "adcode": value.adcode,
        "region_name": value.region_name,
        "condition": value.condition,
        "low_c": value.low_c,
        "high_c": value.high_c,
        "wind_direction": value.wind_direction,
        "wind_level": value.wind_level,
        "source": value.source,
        "report_time": value.report_time.isoformat(),
    }


def match_payload(value: Match) -> dict[str, Any]:
    def played_payload(item: dict[str, Any]) -> dict[str, Any]:
        payload = dict(item)
        payload["result_text"] = format_result_text(
            item.get("home_score"),
            item.get("away_score"),
            item.get("home_penalty"),
            item.get("away_penalty"),
        )
        return payload

    def team_payload(item: dict[str, Any]) -> dict[str, Any]:
        payload = dict(item)
        payload["current_results"] = [
            played_payload(result) for result in item.get("current_results", [])
        ]
        return payload

    report_storage: dict[str, str] = {}
    if value.report_storage_key is not None:
        try:
            report_storage = parse_report_storage_descriptor(value.report_storage_key)
        except ValueError:
            report_storage = {}

    return {
        "game_id": value.game_id,
        "batch_id": value.batch_id,
        "tournament_id": value.tournament_id,
        "tournament_name": value.tournament_name,
        "competition_name": value.competition_name,
        "stage": value.stage,
        "kickoff": value.kickoff.isoformat(),
        "venue": value.venue,
        "home": team_payload(value.home_snapshot),
        "away": team_payload(value.away_snapshot),
        "head_to_head": [
            played_payload(result) for result in value.head_to_head_snapshot
        ],
        "active": value.active,
        "task_open": value.task_open,
        "claimed_by_user_id": value.claimed_by_user_id,
        "writers": value.writers,
        "body": value.body,
        "body_version": value.body_version,
        "status": value.status,
        "report": {
            "available": bool(report_storage),
            "content_sha256": value.report_content_sha256,
            "rendered_at": (
                value.report_rendered_at.isoformat()
                if value.report_rendered_at is not None
                else None
            ),
        },
        "updated_at": value.updated_at.isoformat(),
    }


def batch_payload(session: Session, batch: Batch, *, detail: bool) -> dict[str, Any]:
    missing = completeness(session, batch)
    latest_preview_article_id = session.scalar(
        select(ArticleRecord.id)
        .where(
            ArticleRecord.batch_id == batch.id,
            ArticleRecord.article_type == "preview",
        )
        .order_by(ArticleRecord.version_number.desc(), ArticleRecord.id.desc())
        .limit(1)
    )
    latest_report_article_id = session.scalar(
        select(ArticleRecord.id)
        .where(
            ArticleRecord.batch_id == batch.id,
            ArticleRecord.article_type == "report",
        )
        .order_by(ArticleRecord.version_number.desc(), ArticleRecord.id.desc())
        .limit(1)
    )
    payload: dict[str, Any] = {
        "id": batch.id,
        "batch_date": batch.batch_date.isoformat(),
        "competition": batch.competition,
        "preview_status": batch_status(session, batch),
        "headline": batch.headline,
        "editors": batch.editors,
        "reviewers": batch.reviewers,
        "approvers": batch.approvers,
        "cover": {
            "kind": batch.cover_kind,
            "storage_key": batch.cover_storage_key,
            "content_type": batch.cover_content_type,
        },
        "current_preview_article_id": batch.current_preview_article_id,
        "latest_preview_article_id": latest_preview_article_id,
        "current_report_article_id": batch.current_report_article_id,
        "latest_report_article_id": latest_report_article_id,
        "missing_fields": missing,
        "last_error": (
            None
            if batch.last_error_code is None
            else {
                "code": batch.last_error_code,
                "message": batch.last_error_message,
                "at": batch.last_error_at.isoformat() if batch.last_error_at else None,
            }
        ),
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
    }
    if detail:
        payload["weather"] = weather_payload(session.get(Weather, batch.batch_date))
        payload["matches"] = [
            match_payload(match)
            for match in session.scalars(
                select(Match)
                .where(Match.batch_id == batch.id)
                .order_by(
                    Match.active.desc(),
                    Match.kickoff,
                    Match.game_id,
                )
            )
        ]
    return payload
