"""Three-stage orchestration for automated match-report articles."""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from auto_preview.config import CompetitionConfig, competition_config
from thufootball import (
    GameEventIssue,
    GameQuery,
    GameStatus,
    GameSummary,
    PreparedGameReport,
    ReportSettings,
    THUFootballClient,
    THUFootballQueryService,
    THUFootballReportService,
    resolve_report_team_name,
)
from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    WechatArticleError,
    WechatOfficialService,
)

from .errors import ArtifactValidationError, PipelineError
from .logging_utils import configure_logging
from .models import (
    CombinationResult,
    Competition,
    CoverInput,
    PipelineRequest,
    PipelineResult,
    Stage,
)
from .state import (
    REPORT_SCHEMA_VERSION,
    article_cover_descriptor,
    cover_descriptor,
    load_draft_history,
    load_run_state,
    new_run_state,
    publication_fingerprint,
    sha256_bytes,
    sha256_file,
    validate_report_manifest,
    write_json,
)

ServiceFactory = Callable[[], Any]
ARTICLE_TEMPLATE_VERSION = "auto-report-html-v1"
ARTICLE_AUTHOR = "清华绿茵"
ARTICLE_DIGEST = "马杯战报"
DEFAULT_REPORT_COVER_MEDIA_ID = (
    "9EWOf5TZgdym9kzzkE-2whsHxG_ocMKrqLVJH5MQEpi65l56mYkVsHsimgRVM5d0"
)
ARTICLE_TITLES = {
    Competition.MALE: "【马杯男足】今日战报",
    Competition.FEMALE: "【马杯女足】今日战报",
    Competition.FUTSAL: "【马杯五人制】今日战报",
}


@dataclass(frozen=True, slots=True)
class _RunPaths:
    directory: Path
    reports: Path
    report: Path
    article: Path
    state: Path
    draft: Path


@dataclass(frozen=True, slots=True)
class _CombinationRequest:
    report_date: date
    competition: Competition
    stage: Stage
    cover: CoverInput | None
    override: bool


@dataclass(slots=True)
class _CombinationContext:
    request: _CombinationRequest
    config: CompetitionConfig
    paths: _RunPaths
    state: dict[str, Any]
    logger: logging.Logger
    manifest: dict[str, Any] | None = None
    article: Article | None = field(default=None)


class _DefaultFootballSession:
    """Share one authenticated client between listing and report rendering."""

    def __init__(self) -> None:
        self._client = THUFootballClient()
        self._queries = THUFootballQueryService(self._client)
        self._reports = THUFootballReportService(self._client)

    async def __aenter__(self) -> "_DefaultFootballSession":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._queries.aclose()
        await self._client.aclose()

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        return await self._queries.query_games(query)

    async def download_game_report(
        self,
        game_id: int,
        output: Path,
        *,
        settings: ReportSettings,
        refresh_stats: bool,
        overwrite: bool,
    ) -> Any:
        return await self._reports.download_game_report(
            game_id,
            output,
            settings=settings,
            refresh_stats=refresh_stats,
            overwrite=overwrite,
        )

    async def get_prepared_game_report(self, game_id: int) -> PreparedGameReport:
        return await self._reports.get_prepared_game_report(game_id)

    async def render_game_detail(
        self, detail: Any, *, settings: ReportSettings
    ) -> tuple[bytes, int, int]:
        return await self._reports.render_game_detail(detail, settings=settings)


def _quoted_command(arguments: list[str]) -> str:
    if sys.platform == "win32":
        from subprocess import list2cmdline

        return list2cmdline(arguments)
    return shlex.join(arguments)


def _query_scope_sha256(tournament_ids: tuple[int, ...]) -> str:
    return sha256_bytes(
        json.dumps(
            list(tournament_ids),
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _game_key(game: GameSummary) -> tuple[datetime, int, int]:
    return (game.kickoff_local, game.tournament_id, game.game_id)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            temporary_path = Path(stream.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _report_warning_message(
    issue: GameEventIssue,
    *,
    game_id: int,
    home_name: str,
    away_name: str,
) -> str:
    team_name = {
        "home": home_name,
        "away": away_name,
    }.get(issue.side)
    time_text = ""
    if issue.minute is not None:
        stoppage = issue.stoppage_minute or 0
        time_text = (
            f"{issue.minute}+{stoppage}分钟"
            if stoppage > 0
            else f"{issue.minute}分钟"
        )
    subject = f"{team_name or f'比赛 {game_id}'}{time_text}"

    if issue.code == "multiple_events_same_time":
        return f"{subject}存在多个事件，请注意检查前后顺序是否正确"
    if issue.code == "lineup_under_capacity":
        return f"{team_name or f'比赛 {game_id}'}首发人数不足，请确认首发名单是否完整"
    if issue.code == "invalid_event_ignored":
        return f"{subject}有无效事件已被忽略，请检查原始比赛记录"
    return (
        f"{subject}存在需要检查的战报事件"
        f"（{issue.code}），请核对原始比赛记录"
    )


class AutoReportPipeline:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        football_service_factory: ServiceFactory | None = None,
        wechat_service_factory: ServiceFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self._football_service_factory = (
            football_service_factory or _DefaultFootballSession
        )
        self._wechat_service_factory = (
            wechat_service_factory or WechatOfficialService.from_environment
        )
        self._logger = logger

    def run_directory(self, request: _CombinationRequest) -> Path:
        return (
            self._project_root
            / "runs"
            / "auto_report"
            / f"{request.report_date.isoformat()}_{request.competition.value}"
        )

    def _run_paths(self, request: _CombinationRequest) -> _RunPaths:
        directory = self.run_directory(request)
        return _RunPaths(
            directory=directory,
            reports=directory / "reports",
            report=directory / "report.json",
            article=directory / "article",
            state=directory / "run.json",
            draft=directory / "draft.json",
        )

    def _display_path(self, path: str | Path) -> str:
        return os.path.relpath(Path(path).resolve(), self._project_root)

    def _next_command(self, request: PipelineRequest, stage: Stage) -> str:
        arguments = [
            "python",
            str(Path("scripts") / "auto_report.py"),
            "--dates",
            *(value.isoformat() for value in request.report_dates),
            "--competitions",
            *(value.value for value in request.competitions),
            "--stage",
            stage.value,
        ]
        if isinstance(request.cover, CoverFile):
            arguments.extend(("--cover", str(request.cover.path)))
        elif isinstance(request.cover, CoverMediaId):
            arguments.extend(("--cover-media-id", request.cover.media_id))
        return _quoted_command(arguments)

    @staticmethod
    def _combination_requests(
        request: PipelineRequest,
    ) -> tuple[_CombinationRequest, ...]:
        return tuple(
            _CombinationRequest(
                report_date=report_date,
                competition=competition,
                stage=request.stage,
                cover=request.cover,
                override=request.override,
            )
            for report_date, competition in request.combinations
        )

    @staticmethod
    def _report_state(state: dict[str, Any]) -> dict[str, str]:
        value = state.get("report")
        if (
            not isinstance(value, dict)
            or set(value) != {"status", "manifest_sha256"}
            or value.get("status")
            not in {"ready", "no_games", "no_finished_games"}
            or not isinstance(value.get("manifest_sha256"), str)
            or len(value["manifest_sha256"]) != 64
        ):
            raise ArtifactValidationError(
                "report 已存在但 run.json.report 缺失或损坏",
                stage="report-validation",
            )
        return value

    @staticmethod
    def _article_state(state: dict[str, Any]) -> dict[str, Any]:
        value = state.get("article")
        if (
            not isinstance(value, dict)
            or set(value) != {"input_sha256", "template_version", "cover"}
            or not isinstance(value.get("input_sha256"), str)
            or len(value["input_sha256"]) != 64
            or not isinstance(value.get("template_version"), str)
        ):
            raise ArtifactValidationError(
                "article 已存在但 run.json.article 缺失或损坏",
                stage="article-validation",
            )
        cover = value.get("cover")
        if (
            not isinstance(cover, dict)
            or set(cover) != {"kind", "sha256"}
            or cover.get("kind") not in {"file", "media_id"}
            or not isinstance(cover.get("sha256"), str)
            or len(cover["sha256"]) != 64
        ):
            raise ArtifactValidationError(
                "run.json.article.cover 字段损坏",
                stage="article-validation",
            )
        return value

    def _artifact_path(
        self, paths: _RunPaths, reference: str, *, kind: str
    ) -> Path:
        relative = Path(reference)
        expected_suffix = ".png" if kind == "image" else ".txt"
        if (
            relative.is_absolute()
            or len(relative.parts) != 2
            or relative.parts[0] != "reports"
            or relative.suffix.casefold() != expected_suffix
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ArtifactValidationError(
                f"report.json 中存在不安全的战报路径：{reference}",
                stage="report-validation",
            )
        resolved = (paths.directory / relative).resolve()
        if resolved.parent != paths.reports.resolve():
            raise ArtifactValidationError(
                f"report.json 战报路径越出 reports 目录：{reference}",
                stage="report-validation",
            )
        return resolved

    def _validate_manifest_artifacts(
        self,
        context: _CombinationContext,
        manifest: dict[str, Any],
    ) -> tuple[Path, ...]:
        files: list[Path] = []
        for item in manifest["items"]:
            for artifact in item["artifacts"]:
                path = self._artifact_path(
                    context.paths, artifact["path"], kind=artifact["kind"]
                )
                if not path.is_file():
                    raise ArtifactValidationError(
                        f"战报文件缺失：{self._display_path(path)}；"
                        "请使用 --override 重建",
                        stage="report-validation",
                    )
                if sha256_file(path) != artifact["sha256"]:
                    raise ArtifactValidationError(
                        f"战报文件哈希不匹配：{self._display_path(path)}；"
                        "请使用 --override 重建",
                        stage="report-validation",
                    )
                files.append(path)
        return tuple(files)

    def _load_reusable_report(self, context: _CombinationContext) -> bool:
        if context.request.override:
            return False
        if context.state.get("report") is None:
            if (
                context.paths.report.exists()
                or context.paths.reports.exists()
                or context.paths.article.exists()
                or context.paths.draft.exists()
            ):
                raise ArtifactValidationError(
                    "已有 report 阶段产物但 run.json.report 缺失；"
                    "请使用 --override 重建",
                    stage="report-validation",
                )
            return False

        report_state = self._report_state(context.state)
        if not context.paths.report.is_file():
            raise ArtifactValidationError(
                "run.json 已记录 report，但 report.json 缺失；"
                "请使用 --override 重建",
                stage="report-validation",
            )
        if sha256_file(context.paths.report) != report_state["manifest_sha256"]:
            raise ArtifactValidationError(
                "report.json 哈希与 run.json 不一致；请使用 --override 重建",
                stage="report-validation",
            )
        manifest = validate_report_manifest(
            context.paths.report,
            report_date=context.request.report_date,
            competition=context.request.competition,
            tournament_ids=context.config.current_tournament_ids,
        )
        if manifest["status"] != report_state["status"]:
            raise ArtifactValidationError(
                "report.json 状态与 run.json 不一致；请使用 --override 重建",
                stage="report-validation",
            )
        self._validate_manifest_artifacts(context, manifest)
        context.manifest = manifest
        context.logger.info(
            "↪ [1/3] report 复用缓存：%s",
            self._display_path(context.paths.report),
        )
        return True

    async def _build_report_manifest(
        self,
        context: _CombinationContext,
        games: list[GameSummary],
        football: Any,
        *,
        queried_at: str,
    ) -> None:
        selected = sorted(
            (
                game
                for game in games
                if game.kickoff_local.date() == context.request.report_date
            ),
            key=_game_key,
        )
        unexpected_tournaments = sorted(
            {
                game.tournament_id
                for game in selected
                if game.tournament_id not in context.config.current_tournament_ids
            }
        )
        if unexpected_tournaments:
            raise PipelineError(
                "比赛列表返回了查询范围外的赛事 ID："
                + ", ".join(map(str, unexpected_tournaments)),
                stage="report",
            )
        items: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        expected_files: set[Path] = set()

        for game in selected:
            if game.status is not GameStatus.FINISHED:
                skipped.append(
                    {
                        "game_id": game.game_id,
                        "tournament_id": game.tournament_id,
                        "kickoff_local": game.kickoff_local.isoformat(),
                        "status": game.status.value,
                    }
                )
                context.logger.warning(
                    "⚠ 跳过未完赛比赛 %s（%s）",
                    game.game_id,
                    game.status.value,
                )
                continue

            home_name = resolve_report_team_name(game, "home")
            away_name = resolve_report_team_name(game, "away")
            common = {
                "game_id": game.game_id,
                "tournament_id": game.tournament_id,
                "kickoff_local": game.kickoff_local.isoformat(),
                "home_name": home_name,
                "away_name": away_name,
            }
            filename = (
                f"{game.kickoff_local:%H%M}_{game.game_id}.png"
            )
            output = (context.paths.reports / filename).resolve()
            expected_files.add(output)
            context.logger.info(
                "→ report 比赛 %s：%s vs %s",
                game.game_id,
                home_name,
                away_name,
            )
            prepared = await football.get_prepared_game_report(game.game_id)
            artifacts: list[dict[str, str]] = []
            if prepared.render_image:
                image, _width, _height = await football.render_game_detail(
                    prepared.detail, settings=ReportSettings()
                )
                _atomic_write_bytes(output, image)
                artifacts.append(
                    {
                        "kind": "image",
                        "path": f"reports/{filename}",
                        "sha256": sha256_file(output),
                    }
                )
            else:
                expected_files.remove(output)
            if prepared.text is not None:
                text_filename = f"{game.kickoff_local:%H%M}_{game.game_id}.txt"
                text_output = (context.paths.reports / text_filename).resolve()
                _atomic_write_bytes(text_output, prepared.text.encode("utf-8"))
                expected_files.add(text_output)
                artifacts.append(
                    {
                        "kind": "text",
                        "path": f"reports/{text_filename}",
                        "sha256": sha256_file(text_output),
                    }
                )
            warnings = [asdict(warning) for warning in prepared.warnings]
            for warning in prepared.warnings:
                context.logger.warning(
                    "⚠ %s",
                    _report_warning_message(
                        warning,
                        game_id=game.game_id,
                        home_name=home_name,
                        away_name=away_name,
                    ),
                )
            items.append(
                {
                    **common,
                    "artifacts": artifacts,
                    "warnings": warnings,
                }
            )

        if not selected:
            status = "no_games"
        elif not items:
            status = "no_finished_games"
        else:
            status = "ready"

        manifest = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_date": context.request.report_date.isoformat(),
            "competition": context.request.competition.value,
            "status": status,
            "query_scope_sha256": _query_scope_sha256(
                context.config.current_tournament_ids
            ),
            "queried_at": queried_at,
            "items": items,
            "skipped_unfinished": skipped,
        }
        write_json(context.paths.report, manifest)
        if context.paths.reports.exists():
            for path in context.paths.reports.iterdir():
                if (
                    path.suffix.casefold() in {".png", ".txt"}
                    and path.resolve() not in expected_files
                ):
                    path.unlink()
        if context.paths.article.exists():
            article_path = context.paths.article.resolve()
            if article_path.parent != context.paths.directory.resolve():
                raise ArtifactValidationError(
                    "拒绝删除运行目录外的旧 Article",
                    stage="artifact-validation",
                )
            shutil.rmtree(article_path)
        context.state["report"] = {
            "status": status,
            "manifest_sha256": sha256_file(context.paths.report),
        }
        context.state["article"] = None
        write_json(context.paths.state, context.state)
        context.manifest = manifest

    async def _query_report_group(
        self,
        contexts: list[_CombinationContext],
        football: Any,
    ) -> None:
        if not contexts:
            return
        config = contexts[0].config
        games = await football.query_games(
            GameQuery(
                tournament_ids=config.current_tournament_ids,
                include_unfinished=True,
            )
        )
        queried_at = datetime.now(UTC).isoformat()
        for context in contexts:
            await self._build_report_manifest(
                context,
                games,
                football,
                queried_at=queried_at,
            )

    def _manifest_sha256(self, context: _CombinationContext) -> str:
        report_state = self._report_state(context.state)
        return report_state["manifest_sha256"]

    def _render_body(self, context: _CombinationContext) -> str:
        assert context.manifest is not None
        fragments: list[str] = []
        for item in context.manifest["items"]:
            for artifact in item["artifacts"]:
                path = self._artifact_path(
                    context.paths, artifact["path"], kind=artifact["kind"]
                )
                if artifact["kind"] == "image":
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    fragments.append(
                        '<section style="margin:0;padding:0;">'
                        '<img src="data:image/png;base64,'
                        + encoded
                        + '" style="display:block;width:100%;height:auto;" />'
                        "</section>"
                    )
                else:
                    fragments.append(
                        '<p style="margin:16px 0;line-height:1.75;">'
                        + html.escape(path.read_text(encoding="utf-8"))
                        + "</p>"
                    )
        return "".join(fragments)

    def _article_input_sha256(
        self,
        context: _CombinationContext,
        cover: CoverInput,
    ) -> str:
        return sha256_bytes(
            json.dumps(
                {
                    "report_manifest_sha256": self._manifest_sha256(context),
                    "template_version": ARTICLE_TEMPLATE_VERSION,
                    "cover": cover_descriptor(cover),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _load_existing_article(path: Path) -> Article:
        try:
            return Article.load(path)
        except (WechatArticleError, OSError, ValueError) as exc:
            raise ArtifactValidationError(
                f"已有 article 验收失败：{exc}",
                stage="article-validation",
            ) from exc

    @staticmethod
    def _default_cover() -> CoverMediaId:
        return CoverMediaId(DEFAULT_REPORT_COVER_MEDIA_ID)

    def _prepare_article(self, context: _CombinationContext) -> Article:
        assert context.manifest is not None
        self._validate_manifest_artifacts(context, context.manifest)
        desired_cover = context.request.cover or self._default_cover()
        input_sha256 = self._article_input_sha256(context, desired_cover)
        desired_cover_state = cover_descriptor(desired_cover)
        existing: Article | None = None

        if context.paths.article.exists() and not context.request.override:
            existing = self._load_existing_article(context.paths.article)
            article_state = self._article_state(context.state)
            if (
                article_state["input_sha256"] == input_sha256
                and article_state["template_version"] == ARTICLE_TEMPLATE_VERSION
                and article_state["cover"] == article_cover_descriptor(existing)
                and article_state["cover"] == desired_cover_state
                and existing.title
                == ARTICLE_TITLES[context.request.competition]
                and existing.author == ARTICLE_AUTHOR
                and existing.digest == ARTICLE_DIGEST
            ):
                context.logger.info(
                    "↪ [2/3] article 复用缓存：%s",
                    self._display_path(context.paths.article),
                )
                return existing
        elif (
            context.state.get("article") is not None
            and not context.request.override
        ):
            self._article_state(context.state)

        if (
            context.paths.article.exists()
            and context.state.get("article") is None
            and not context.request.override
        ):
            raise ArtifactValidationError(
                "article 目录已存在但 run.json.article 缺失；"
                "请使用 --override 重建",
                stage="article-validation",
            )

        article = Article(
            title=ARTICLE_TITLES[context.request.competition],
            body_html=self._render_body(context),
            cover=desired_cover,
            author=ARTICLE_AUTHOR,
            digest=ARTICLE_DIGEST,
        )
        article.save(context.paths.article)
        persisted = Article.load(context.paths.article)
        persisted_cover = (
            persisted.cover.path.resolve()
            if isinstance(persisted.cover, CoverFile)
            else None
        )
        for candidate in context.paths.article.glob("cover.*"):
            if candidate.resolve() != persisted_cover:
                candidate.unlink()
        context.state["article"] = {
            "input_sha256": input_sha256,
            "template_version": ARTICLE_TEMPLATE_VERSION,
            "cover": article_cover_descriptor(persisted),
        }
        write_json(context.paths.state, context.state)
        return persisted

    @staticmethod
    def _publication_components(
        contexts: list[_CombinationContext],
    ) -> list[dict[str, str]]:
        components: list[dict[str, str]] = []
        for context in contexts:
            assert context.article is not None
            article_state = AutoReportPipeline._article_state(context.state)
            components.append(
                {
                    "report_date": context.request.report_date.isoformat(),
                    "competition": context.request.competition.value,
                    "article_fingerprint": context.article.content_fingerprint,
                    "cover_fingerprint": article_state["cover"]["sha256"],
                }
            )
        return components

    @staticmethod
    def _matching_batch_receipt(
        histories: list[dict[str, Any]],
        fingerprint: str,
        components: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        if not histories:
            return None
        candidates = [
            receipt
            for receipt in histories[0]["receipts"]
            if receipt["publication_fingerprint"] == fingerprint
            and receipt["articles"] == components
        ]
        for candidate in reversed(candidates):
            if all(
                any(
                    receipt["media_id"] == candidate["media_id"]
                    and receipt["publication_fingerprint"] == fingerprint
                    and receipt["articles"] == components
                    for receipt in history["receipts"]
                )
                for history in histories[1:]
            ):
                return candidate
        return None

    async def _publish_articles(
        self,
        contexts: list[_CombinationContext],
        *,
        override: bool,
        logger: logging.Logger,
    ) -> str:
        if not 1 <= len(contexts) <= 8:
            raise PipelineError(
                "公众号多图文草稿必须包含 1–8 篇文章",
                stage="publish-validation",
            )
        components = self._publication_components(contexts)
        fingerprint = publication_fingerprint(components)
        histories = [
            load_draft_history(context.paths.draft) for context in contexts
        ]
        matching = self._matching_batch_receipt(
            histories,
            fingerprint,
            components,
        )
        if not override and matching is not None:
            logger.info(
                "↪ [3/3] publish 复用同一批次草稿：%s",
                matching["media_id"],
            )
            return matching["media_id"]

        articles = tuple(context.article for context in contexts)
        assert all(article is not None for article in articles)
        async with self._wechat_service_factory() as wechat:
            receipt = await wechat.create_draft(articles)
        stored = {
            "media_id": receipt.media_id,
            "created_at": receipt.created_at.isoformat(),
            "publication_fingerprint": fingerprint,
            "articles": components,
        }
        for context, history in zip(contexts, histories, strict=True):
            history["receipts"].append(stored.copy())
            write_json(context.paths.draft, history)
        logger.info("✓ [3/3] publish 草稿已创建：%s", receipt.media_id)
        return receipt.media_id

    def _combination_result(
        self,
        context: _CombinationContext,
        *,
        completed_stage: Stage,
        draft_media_id: str | None = None,
    ) -> CombinationResult:
        assert context.manifest is not None
        status = context.manifest["status"]
        skipped = status != "ready"
        files = self._validate_manifest_artifacts(context, context.manifest)
        return CombinationResult(
            report_date=context.request.report_date,
            competition=context.request.competition,
            status="skipped" if skipped else "ok",
            completed_stage=Stage.REPORT if skipped else completed_stage,
            run_directory=context.paths.directory,
            report_manifest_path=context.paths.report,
            report_files=files,
            article_directory=(
                context.paths.article
                if not skipped and completed_stage is not Stage.REPORT
                else None
            ),
            draft_media_id=draft_media_id if not skipped else None,
            reason=status if skipped else None,
        )

    async def run(self, request: PipelineRequest) -> PipelineResult:
        combinations = self._combination_requests(request)
        contexts: list[_CombinationContext] = []
        for combination in combinations:
            paths = self._run_paths(combination)
            logger = self._logger or configure_logging(
                paths.directory,
                project_root=self._project_root,
            )
            state = (
                new_run_state(
                    combination.report_date,
                    combination.competition,
                )
                if combination.override
                else load_run_state(
                    paths.state,
                    combination.report_date,
                    combination.competition,
                    report_path=paths.report,
                    reports_directory=paths.reports,
                    article_directory=paths.article,
                    draft_path=paths.draft,
                )
            )
            contexts.append(
                _CombinationContext(
                    request=combination,
                    config=competition_config(combination.competition),
                    paths=paths,
                    state=state,
                    logger=logger,
                )
            )

        batch_logger = contexts[0].logger
        if request.override:
            batch_logger.warning(
                "⚠ --override 已启用：将重新查询并重做到 %s；"
                "不会刷新服务端统计",
                request.stage.value,
            )

        report_started = time.monotonic()
        batch_logger.info(
            "▶ [1/3] report 处理 %s 个组合（%s 个赛事）",
            len(contexts),
            len(request.competitions),
        )
        pending: dict[Competition, list[_CombinationContext]] = {
            competition: [] for competition in request.competitions
        }
        try:
            for context in contexts:
                if not self._load_reusable_report(context):
                    pending[context.request.competition].append(context)
            if any(pending.values()):
                async with self._football_service_factory() as football:
                    for competition in request.competitions:
                        await self._query_report_group(
                            pending[competition],
                            football,
                        )
        except Exception as exc:
            batch_logger.error("✗ [1/3] report 失败：%s", exc)
            raise

        ready_contexts = [
            context
            for context in contexts
            if context.manifest is not None
            and context.manifest["status"] == "ready"
        ]
        batch_logger.info(
            "✓ [1/3] report 完成（%.2fs）：%s 个文章组合，%s 个 skipped",
            time.monotonic() - report_started,
            len(ready_contexts),
            len(contexts) - len(ready_contexts),
        )

        if not ready_contexts:
            return PipelineResult(
                status="skipped",
                completed_stage=Stage.REPORT,
                runs=tuple(
                    self._combination_result(
                        context,
                        completed_stage=Stage.REPORT,
                    )
                    for context in contexts
                ),
            )

        if request.stage is Stage.REPORT:
            return PipelineResult(
                status="ok",
                completed_stage=Stage.REPORT,
                runs=tuple(
                    self._combination_result(
                        context,
                        completed_stage=Stage.REPORT,
                    )
                    for context in contexts
                ),
                next_command=self._next_command(request, Stage.ARTICLE),
            )

        if request.stage is Stage.PUBLISH and len(ready_contexts) > 8:
            raise PipelineError(
                "公众号多图文草稿最多包含 8 篇文章；"
                "请缩小日期或赛事范围",
                stage="publish-validation",
            )

        article_started = time.monotonic()
        batch_logger.info(
            "▶ [2/3] article 处理 %s 个组合",
            len(ready_contexts),
        )
        try:
            for context in ready_contexts:
                context.article = self._prepare_article(context)
        except Exception as exc:
            batch_logger.error("✗ [2/3] article 失败：%s", exc)
            raise
        batch_logger.info(
            "✓ [2/3] article 完成（%.2fs）：%s 篇文章",
            time.monotonic() - article_started,
            len(ready_contexts),
        )

        if request.stage is Stage.ARTICLE:
            return PipelineResult(
                status="ok",
                completed_stage=Stage.ARTICLE,
                runs=tuple(
                    self._combination_result(
                        context,
                        completed_stage=(
                            Stage.ARTICLE
                            if context.manifest["status"] == "ready"
                            else Stage.REPORT
                        ),
                    )
                    for context in contexts
                ),
                next_command=self._next_command(request, Stage.PUBLISH),
            )

        draft_media_id = await self._publish_articles(
            ready_contexts,
            override=request.override,
            logger=batch_logger,
        )
        return PipelineResult(
            status="ok",
            completed_stage=Stage.PUBLISH,
            runs=tuple(
                self._combination_result(
                    context,
                    completed_stage=(
                        Stage.PUBLISH
                        if context.manifest["status"] == "ready"
                        else Stage.REPORT
                    ),
                    draft_media_id=draft_media_id,
                )
                for context in contexts
            ),
            draft_media_id=draft_media_id,
        )
