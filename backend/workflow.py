"""Database-backed administrator preview workflow."""

from __future__ import annotations

import hashlib
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
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
from thufootball import THUFootballQueryService
from weather import DailyWeather, WeatherQueryService
from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    WechatOfficialService,
    publication_fingerprint,
)

from .artifacts import resolve_storage_key, save_cover, sha256_bytes, sha256_file
from .config import PROJECT_ROOT, WebsiteSettings
from .models import (
    ArticleRecord,
    EditorialDefaults,
    PreviewBatch,
    Weather,
    WechatDraft,
)
from .models import PreviewMatch as PreviewMatchRecord

AUTO_PREVIEW_DIGEST = "马杯前瞻"
HAIDIAN_ADCODE = "110108"
COMPETITION_ORDER = {"male": 0, "female": 1, "futsal": 2}
SHANGHAI = timezone(timedelta(hours=8))


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


def _invalidate_batch(batch: PreviewBatch) -> None:
    batch.current_article_id = None
    batch.updated_at = _now()


def _invalidate_date(session: Session, target_date: date) -> None:
    session.execute(
        update(PreviewBatch)
        .where(PreviewBatch.preview_date == target_date)
        .values(current_article_id=None, updated_at=_now())
    )


def _deactivate_active_matches(session: Session, batch: PreviewBatch) -> bool:
    matches = list(
        session.scalars(
            select(PreviewMatchRecord).where(
                PreviewMatchRecord.batch_id == batch.id,
                PreviewMatchRecord.active.is_(True),
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
    batch: PreviewBatch,
    source: PreviewSourceData,
    games_by_id: dict[int, Any],
) -> bool:
    payloads = _source_match_payload(source)
    incoming_ids = set(payloads)
    changed = False
    current = list(
        session.scalars(
            select(PreviewMatchRecord).where(PreviewMatchRecord.batch_id == batch.id)
        )
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
        record = session.get(PreviewMatchRecord, game_id)
        if record is None:
            session.add(PreviewMatchRecord(game_id=game_id, **values))
            changed = True
            continue
        record_changed = any(getattr(record, name) != item for name, item in values.items())
        if record.batch_id != batch.id:
            old_batch = session.get(PreviewBatch, record.batch_id)
            if old_batch is not None:
                _invalidate_batch(old_batch)
            record.task_open = False
            record_changed = True
        if record_changed:
            for name, item in values.items():
                setattr(record, name, item)
            record.updated_at = _now()
            changed = True
    if changed:
        _invalidate_batch(batch)
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
) -> tuple[PreviewBatch, bool]:
    batch = session.scalar(
        select(PreviewBatch).where(
            PreviewBatch.preview_date == preview_date,
            PreviewBatch.competition == competition,
        )
    )
    if batch is not None:
        return batch, False
    defaults = session.get(EditorialDefaults, 1)
    if defaults is None:
        defaults = EditorialDefaults(id=1)
        session.add(defaults)
        session.flush()
    batch = PreviewBatch(
        preview_date=preview_date,
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
                select(PreviewBatch).where(
                    PreviewBatch.preview_date.in_(resolved_dates),
                    PreviewBatch.competition.in_(resolved_competitions),
                )
            )
        )
    existing = {
        (batch.preview_date, batch.competition): batch.id
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
                            select(PreviewBatch).where(
                                PreviewBatch.preview_date == target_date,
                                PreviewBatch.competition == competition_value,
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
    batch: PreviewBatch,
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
                source = await builder.build(batch.preview_date, games=games)
            except NoGamesForDate:
                source = None
        _advisory_lock(
            session,
            "preview-batch",
            f"{batch.preview_date.isoformat()}:{batch.competition}",
        )
        if source is None:
            _deactivate_active_matches(session, batch)
            batch.last_error_code = None
            batch.last_error_message = None
            batch.last_error_at = None
        else:
            upsert_source(session, batch, source, games_by_id)
        current_weather = session.get(Weather, batch.preview_date)
        if current_weather is None or current_weather.source != "manual":
            try:
                async with factories.weather() as service:
                    value = await service.get_weather(HAIDIAN_ADCODE, batch.preview_date)
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


def completeness(session: Session, batch: PreviewBatch) -> list[str]:
    missing: list[str] = []
    if not batch.headline.strip():
        missing.append("headline")
    weather = session.get(Weather, batch.preview_date)
    if weather is None:
        missing.append("weather")
    for field in ("editors", "reviewers", "approvers"):
        if not getattr(batch, field):
            missing.append(field)
    matches = list(
        session.scalars(
            select(PreviewMatchRecord)
            .where(
                PreviewMatchRecord.batch_id == batch.id,
                PreviewMatchRecord.active.is_(True),
            )
            .order_by(PreviewMatchRecord.kickoff, PreviewMatchRecord.game_id)
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


def batch_status(session: Session, batch: PreviewBatch) -> str:
    if completeness(session, batch):
        return "incomplete"
    if batch.current_article_id is not None:
        for draft in session.scalars(select(WechatDraft)):
            if any(
                item.get("article_id") == batch.current_article_id
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


def assemble_source(session: Session, batch: PreviewBatch) -> PreviewSourceData:
    config = competition_config(Competition(batch.competition))
    weather_record = session.get(Weather, batch.preview_date)
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
        select(PreviewMatchRecord)
        .where(
            PreviewMatchRecord.batch_id == batch.id,
            PreviewMatchRecord.active.is_(True),
        )
        .order_by(PreviewMatchRecord.kickoff, PreviewMatchRecord.game_id)
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
                    batch.preview_date,
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
        preview_date=batch.preview_date,
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
    settings: WebsiteSettings, batch: PreviewBatch
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
    batch: PreviewBatch,
) -> tuple[ArticleRecord, bool]:
    _advisory_lock(session, "preview-batch", str(batch.id))
    session.refresh(batch)
    if batch.current_article_id is not None:
        existing = session.get(ArticleRecord, batch.current_article_id)
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
            ArticleRecord.batch_id == batch.id
        )
    )
    missing = completeness(session, batch)
    record = ArticleRecord(
        batch_id=batch.id,
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
    batch.current_article_id = record.id
    batch.updated_at = _now()
    session.flush()
    return record, False


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
        batch = session.get(PreviewBatch, record.batch_id)
        if batch is None or batch.current_article_id != record.id:
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
    batch: PreviewBatch,
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
    batch: PreviewBatch,
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


def match_payload(value: PreviewMatchRecord) -> dict[str, Any]:
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
        "updated_at": value.updated_at.isoformat(),
    }


def batch_payload(session: Session, batch: PreviewBatch, *, detail: bool) -> dict[str, Any]:
    missing = completeness(session, batch)
    latest_article_id = session.scalar(
        select(ArticleRecord.id)
        .where(ArticleRecord.batch_id == batch.id)
        .order_by(ArticleRecord.version_number.desc(), ArticleRecord.id.desc())
        .limit(1)
    )
    payload: dict[str, Any] = {
        "id": batch.id,
        "preview_date": batch.preview_date.isoformat(),
        "competition": batch.competition,
        "status": batch_status(session, batch),
        "headline": batch.headline,
        "editors": batch.editors,
        "reviewers": batch.reviewers,
        "approvers": batch.approvers,
        "cover": {
            "kind": batch.cover_kind,
            "storage_key": batch.cover_storage_key,
            "content_type": batch.cover_content_type,
        },
        "current_article_id": batch.current_article_id,
        "latest_article_id": latest_article_id,
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
        payload["weather"] = weather_payload(session.get(Weather, batch.preview_date))
        payload["matches"] = [
            match_payload(match)
            for match in session.scalars(
                select(PreviewMatchRecord)
                .where(PreviewMatchRecord.batch_id == batch.id)
                .order_by(
                    PreviewMatchRecord.active.desc(),
                    PreviewMatchRecord.kickoff,
                    PreviewMatchRecord.game_id,
                )
            )
        ]
    return payload
