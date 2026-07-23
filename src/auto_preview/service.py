"""High-level orchestration for automated preview runs."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from preview import (
    PreviewError,
    PreviewService,
    PreviewSourceData,
    PreviewSourceDocument,
    PreviewWeather,
    load_preview_bundle,
    parse_preview_document,
)
from thufootball import THUFootballQueryService
from weather import (
    DailyWeather,
    WeatherError,
    WeatherInvalidResponse,
    WeatherQueryService,
)
from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    WechatArticleError,
    WechatOfficialService,
)

from .config import CompetitionConfig, competition_config
from .errors import ArtifactValidationError, NoGamesForDate, PipelineError
from .inputs import (
    GlobalInputStatus,
    ensure_global_inputs,
    write_weather_for_date,
)
from .logging_utils import configure_logging
from .models import (
    CombinationResult,
    Competition,
    CoverInput,
    PipelineRequest,
    PipelineResult,
    Stage,
)
from .source import (
    PLACEHOLDER_PREFIX,
    PreviewSourceBuilder,
    preview_article_files,
    preview_data_to_dict,
    source_to_dict,
)
from .state import (
    article_cover_descriptor,
    cover_descriptor,
    load_draft_history,
    load_run_state,
    new_run_state,
    publication_fingerprint,
    read_json_object,
    sha256_bytes,
    write_json,
    write_source,
    write_text,
)

ServiceFactory = Callable[[], Any]
AUTO_PREVIEW_DIGEST = "马杯前瞻"
AUTO_PREVIEW_WEATHER_ADCODE = "110108"


@dataclass(frozen=True, slots=True)
class _RunPaths:
    directory: Path
    source: Path
    article: Path
    state: Path
    draft: Path


@dataclass(frozen=True, slots=True)
class _CombinationRequest:
    preview_date: date
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
    document: PreviewSourceDocument | None = None
    article: Article | None = field(default=None)


def _quoted_command(arguments: list[str]) -> str:
    if sys.platform == "win32":
        from subprocess import list2cmdline

        return list2cmdline(arguments)
    return shlex.join(arguments)


class AutoPreviewPipeline:
    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        query_service_factory: ServiceFactory | None = None,
        weather_service_factory: ServiceFactory | None = None,
        wechat_service_factory: ServiceFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self._query_service_factory = (
            query_service_factory or THUFootballQueryService.from_environment
        )
        self._weather_service_factory = (
            weather_service_factory or WeatherQueryService.from_environment
        )
        self._wechat_service_factory = (
            wechat_service_factory or WechatOfficialService.from_environment
        )
        self._logger = logger

    @staticmethod
    def _preview_weather(value: DailyWeather) -> PreviewWeather:
        return PreviewWeather(
            condition=value.condition,
            low_c=value.low_c,
            high_c=value.high_c,
            wind_direction=value.wind_direction,
            wind_level=value.wind_level,
        )

    def _weather_failure_warning(
        self,
        preview_date: date,
        inputs: GlobalInputStatus,
        error: WeatherError,
    ) -> str:
        outcome = (
            "保留已有天气"
            if not inputs.weather_placeholder
            else "保留全 null 占位，本篇显示“待更新”"
        )
        return (
            f"{self._display_path(inputs.weather_path)}："
            f"{preview_date.isoformat()} 自动查询海淀天气失败："
            f"{type(error).__name__}（{error}）；{outcome}"
        )

    async def _refresh_weather(
        self,
        inputs_by_date: dict[date, GlobalInputStatus],
        *,
        override: bool,
    ) -> tuple[set[date], set[date], list[str]]:
        requested_dates = [
            preview_date
            for preview_date, inputs in sorted(inputs_by_date.items())
            if override or inputs.weather_placeholder
        ]
        if not requested_dates:
            return set(), set(), []

        completed: set[date] = set()
        warnings: list[str] = []
        remaining = set(requested_dates)
        try:
            async with self._weather_service_factory() as weather_service:
                for preview_date in requested_dates:
                    inputs = inputs_by_date[preview_date]
                    try:
                        weather = await weather_service.get_weather(
                            AUTO_PREVIEW_WEATHER_ADCODE,
                            preview_date,
                        )
                    except WeatherError as exc:
                        warnings.append(
                            self._weather_failure_warning(preview_date, inputs, exc)
                        )
                    else:
                        if weather.forecast_date != preview_date:
                            error = WeatherInvalidResponse(
                                "weather service returned a different forecast date",
                                stage="service",
                            )
                            warnings.append(
                                self._weather_failure_warning(
                                    preview_date,
                                    inputs,
                                    error,
                                )
                            )
                        else:
                            write_weather_for_date(
                                inputs.weather_path,
                                preview_date,
                                self._preview_weather(weather),
                            )
                            completed.add(preview_date)
                    remaining.remove(preview_date)
        except WeatherError as exc:
            warnings.extend(
                self._weather_failure_warning(
                    preview_date,
                    inputs_by_date[preview_date],
                    exc,
                )
                for preview_date in sorted(remaining)
            )
        return set(requested_dates), completed, warnings

    def run_directory(self, request: _CombinationRequest) -> Path:
        return (
            self._project_root
            / "runs"
            / "auto_preview"
            / f"{request.preview_date.isoformat()}_{request.competition.value}"
        )

    def _run_paths(self, request: _CombinationRequest) -> _RunPaths:
        directory = self.run_directory(request)
        return _RunPaths(
            directory=directory,
            source=directory / "source.json",
            article=directory / "article",
            state=directory / "run.json",
            draft=directory / "draft.json",
        )

    def _next_command(self, request: PipelineRequest, stage: Stage) -> str:
        arguments = [
            "python",
            str(Path("scripts") / "auto_preview.py"),
            "--dates",
            *(value.isoformat() for value in request.preview_dates),
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

    def _display_path(self, path: str | Path) -> str:
        return os.path.relpath(Path(path).resolve(), self._project_root)

    @staticmethod
    def _has_placeholder(values: tuple[str, ...]) -> bool:
        return any(value.startswith(PLACEHOLDER_PREFIX) for value in values)

    def _manual_warnings(
        self,
        document: PreviewSourceDocument,
        inputs: GlobalInputStatus,
        source_path: Path,
    ) -> tuple[str | None, tuple[str, ...]]:
        weather_path = self._display_path(inputs.weather_path)
        preview_day = document.preview_date.isoformat()
        weather_warning: str | None = None
        if inputs.weather_created:
            weather_warning = (
                f"{weather_path} 为新生成天气配置，日期 {preview_day} "
                "的天气尚未填写，需要补充；本篇显示“待更新”"
            )
        elif inputs.weather_date_added:
            weather_warning = (
                f"{weather_path} 原本缺少日期 {preview_day}，已加入全 null 模板；"
                "天气尚未填写，需要补充；本篇显示“待更新”"
            )
        elif inputs.weather_placeholder:
            weather_warning = (
                f"{weather_path} 日期 {preview_day} 为全 null；"
                "天气尚未填写，需要补充；本篇显示“待更新”"
            )

        warnings: list[str] = []
        if document.headline.startswith(PLACEHOLDER_PREFIX):
            warnings.append(
                f"{self._display_path(source_path)}："
                "标题尚未填写，需要补充；本篇保留占位符"
            )

        if inputs.config_created:
            warnings.append(
                f"{self._display_path(inputs.config_path)} 为新生成配置："
                "需要补充编辑、责编、审核；本篇保留占位符"
            )

        for match in document.matches:
            missing: list[str] = []
            if self._has_placeholder(match.preview_paragraphs):
                missing.append("前瞻内容")
            if self._has_placeholder(match.writers):
                missing.append("作者")
            if missing:
                warnings.append(
                    f"前瞻“{match.home.short_name} vs {match.away.short_name}”："
                    f"需要补充{'、'.join(missing)}；本篇保留占位符"
                )
        return weather_warning, tuple(warnings)

    @staticmethod
    def _source_state(
        state: dict[str, Any],
        request: _CombinationRequest,
    ) -> dict[str, Any]:
        source_state = state.get("source")
        if not isinstance(source_state, dict):
            raise ArtifactValidationError(
                "source.json 已存在但 run.json.source 缺失或损坏",
                stage="data-validation",
            )
        if set(source_state) != {
            "status",
            "preview_date",
            "competition",
            "selected_games",
            "accepted_placeholder_sha256",
            "queried_at",
            "query_scope_sha256",
        }:
            raise ArtifactValidationError(
                "run.json.source 字段不符合当前契约",
                stage="data-validation",
            )
        status = source_state.get("status")
        if status not in {"ready", "no_games"}:
            raise ArtifactValidationError(
                "run.json.source.status 字段损坏",
                stage="data-validation",
            )
        if (
            source_state.get("preview_date") != request.preview_date.isoformat()
            or source_state.get("competition") != request.competition.value
        ):
            raise ArtifactValidationError(
                "run.json.source 的日期或赛事与本次请求不一致",
                stage="data-validation",
            )
        selected = source_state.get("selected_games")
        if not isinstance(selected, list) or any(
            not isinstance(item, dict)
            or set(item) != {"game_id", "tournament_id"}
            or isinstance(item["game_id"], bool)
            or not isinstance(item["game_id"], int)
            or item["game_id"] <= 0
            or isinstance(item["tournament_id"], bool)
            or not isinstance(item["tournament_id"], int)
            or item["tournament_id"] <= 0
            for item in selected
        ):
            raise ArtifactValidationError(
                "run.json.source.selected_games 字段损坏",
                stage="data-validation",
            )
        accepted = source_state.get("accepted_placeholder_sha256")
        if accepted is not None and (
            not isinstance(accepted, str) or len(accepted) != 64
        ):
            raise ArtifactValidationError(
                "run.json.source.accepted_placeholder_sha256 字段损坏",
                stage="data-validation",
            )
        queried_at = source_state.get("queried_at")
        if queried_at is not None and (
            not isinstance(queried_at, str) or not queried_at
        ):
            raise ArtifactValidationError(
                "run.json.source.queried_at 字段损坏",
                stage="data-validation",
            )
        query_scope = source_state.get("query_scope_sha256")
        if query_scope is not None and (
            not isinstance(query_scope, str) or len(query_scope) != 64
        ):
            raise ArtifactValidationError(
                "run.json.source.query_scope_sha256 字段损坏",
                stage="data-validation",
            )
        if status == "no_games" and (
            selected
            or accepted is not None
            or queried_at is None
            or query_scope is None
        ):
            raise ArtifactValidationError(
                "run.json.source 的 no_games 状态损坏",
                stage="data-validation",
            )
        return source_state

    @staticmethod
    def _validate_source(
        source: PreviewSourceData | PreviewSourceDocument,
        request: _CombinationRequest,
        config: CompetitionConfig,
        source_state: dict[str, Any],
    ) -> None:
        if source.preview_date != request.preview_date:
            raise ArtifactValidationError(
                "source.json 的 preview_date 与本次请求不一致",
                stage="data-validation",
            )
        if (
            source.column.competition_full_name != config.full_name
            or source.column.competition_short_name != config.short_name
        ):
            raise ArtifactValidationError(
                "source.json 的赛事配置与本次请求不一致",
                stage="data-validation",
            )
        selected = source_state["selected_games"]
        selected_game_ids = [item["game_id"] for item in selected]
        if selected_game_ids != [match.game_id for match in source.matches]:
            raise ArtifactValidationError(
                "source.json 的比赛 ID 与已记录查询结果不一致",
                stage="data-validation",
            )
        if any(
            item["tournament_id"] not in config.current_tournament_ids
            for item in selected
        ):
            raise ArtifactValidationError(
                "source.json 关联了配置范围外的当前赛事 ID",
                stage="data-validation",
            )

    @staticmethod
    def _article_state(state: dict[str, Any]) -> dict[str, Any]:
        article_state = state.get("article")
        required = {"input_sha256", "template_version", "cover"}
        if not isinstance(article_state, dict) or set(article_state) != required:
            raise ArtifactValidationError(
                "article 已存在但 run.json.article 缺失或损坏",
                stage="article-validation",
            )
        if not isinstance(article_state.get("input_sha256"), str):
            raise ArtifactValidationError(
                "run.json.article.input_sha256 字段损坏",
                stage="article-validation",
            )
        if not isinstance(article_state.get("template_version"), str):
            raise ArtifactValidationError(
                "run.json.article.template_version 字段损坏",
                stage="article-validation",
            )
        cover = article_state.get("cover")
        if (
            not isinstance(cover, dict)
            or set(cover) != {"kind", "sha256"}
            or cover.get("kind") not in {"file", "media_id"}
            or not isinstance(cover.get("sha256"), str)
        ):
            raise ArtifactValidationError(
                "run.json.article.cover 字段损坏",
                stage="article-validation",
            )
        return article_state

    @staticmethod
    def _load_existing_source(path: Path) -> PreviewSourceDocument:
        try:
            return parse_preview_document(
                read_json_object(path, stage="data-validation"),
                source_directory=path.resolve().parent,
            )
        except (PreviewError, OSError, ValueError) as exc:
            raise ArtifactValidationError(
                f"已有 source.json 验收失败：{exc}",
                stage="data-validation",
            ) from exc

    @staticmethod
    def _load_existing_article(path: Path) -> Article:
        try:
            return Article.load(path)
        except (WechatArticleError, OSError, ValueError) as exc:
            raise ArtifactValidationError(
                f"已有 article 验收失败：{exc}",
                stage="article-validation",
            ) from exc

    def _write_preview_articles(
        self,
        run_directory: Path,
        articles: dict[str, str],
        *,
        override: bool,
    ) -> None:
        expected_paths = {
            (run_directory / reference).resolve(): content
            for reference, content in articles.items()
        }
        previews_directory = (run_directory / "previews").resolve()
        previews_directory.mkdir(parents=True, exist_ok=True)
        for path in expected_paths:
            if path.parent != previews_directory:
                raise ArtifactValidationError(
                    f"Markdown 路径越出运行目录：{self._display_path(path)}",
                    stage="data-build",
                )
            if path.exists() and not override:
                raise ArtifactValidationError(
                    "source.json 尚未生成，但正文文件已存在："
                    f"{self._display_path(path)}；请使用 --override 明确覆盖",
                    stage="data-validation",
                )
        if override:
            for existing in previews_directory.glob("*.md"):
                if existing.resolve() not in expected_paths:
                    existing.unlink()
        for path, content in expected_paths.items():
            write_text(path, content)

    @staticmethod
    def _query_scope_sha256(config: CompetitionConfig) -> str:
        return sha256_bytes(
            json.dumps(
                list(config.current_tournament_ids),
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _remove_stale_data_artifacts(paths: _RunPaths) -> None:
        paths.source.unlink(missing_ok=True)
        if paths.article.exists():
            shutil.rmtree(paths.article)
        previews = paths.directory / "previews"
        if previews.exists():
            shutil.rmtree(previews)

    def _load_reusable_data(self, context: _CombinationContext) -> bool:
        request = context.request
        config = context.config
        paths = context.paths
        state = context.state
        query_scope_sha256 = self._query_scope_sha256(context.config)
        if paths.source.exists() and not request.override:
            source_state = self._source_state(state, request)
            if source_state["status"] != "ready":
                raise ArtifactValidationError(
                    "source.json 存在，但 run.json 记录为 no_games",
                    stage="data-validation",
                )
            document = self._load_existing_source(paths.source)
            self._validate_source(document, request, config, source_state)
            write_json(paths.state, state)
            context.document = document
            return True

        if not paths.source.exists() and state.get("source") is not None:
            source_state = self._source_state(state, request)
            if not request.override and source_state["status"] == "no_games":
                if source_state["query_scope_sha256"] == query_scope_sha256:
                    return True
                state["source"] = None
                state["article"] = None
                self._remove_stale_data_artifacts(paths)
            elif not request.override:
                raise ArtifactValidationError(
                    "run.json 记录 data 已完成，但 source.json 缺失",
                    stage="data-validation",
                )
        if (
            not request.override
            and not paths.source.exists()
            and (paths.article.exists() or state.get("article") is not None)
        ):
            raise ArtifactValidationError(
                "上游 source.json 缺失但已有下游产物；请使用 --override",
                stage="data-validation",
            )
        return False

    def _record_no_games(self, context: _CombinationContext) -> None:
        request = context.request
        paths = context.paths
        state = context.state
        query_scope_sha256 = self._query_scope_sha256(context.config)
        paths.directory.mkdir(parents=True, exist_ok=True)
        self._remove_stale_data_artifacts(paths)
        state["source"] = {
            "status": "no_games",
            "preview_date": request.preview_date.isoformat(),
            "competition": request.competition.value,
            "selected_games": [],
            "accepted_placeholder_sha256": None,
            "queried_at": datetime.now(UTC).isoformat(),
            "query_scope_sha256": query_scope_sha256,
        }
        state["article"] = None
        write_json(paths.state, state)

    def _record_queried_data(
        self,
        context: _CombinationContext,
        builder: PreviewSourceBuilder,
        queried_source: PreviewSourceData,
    ) -> None:
        request = context.request
        paths = context.paths
        state = context.state
        query_scope_sha256 = self._query_scope_sha256(context.config)
        paths.directory.mkdir(parents=True, exist_ok=True)
        source_payload = source_to_dict(queried_source)
        article_files = preview_article_files(queried_source)
        self._write_preview_articles(
            paths.directory,
            article_files,
            override=request.override,
        )
        write_source(paths.source, source_payload)
        context.document = parse_preview_document(
            source_payload,
            source_directory=paths.directory,
        )
        state["source"] = {
            "status": "ready",
            "preview_date": request.preview_date.isoformat(),
            "competition": request.competition.value,
            "selected_games": [
                {"game_id": game_id, "tournament_id": tournament_id}
                for game_id, tournament_id in builder.selected_games
            ],
            "accepted_placeholder_sha256": None,
            "queried_at": datetime.now(UTC).isoformat(),
            "query_scope_sha256": query_scope_sha256,
        }
        state["article"] = None
        write_json(paths.state, state)

    async def _query_data_group(
        self,
        contexts: list[_CombinationContext],
    ) -> None:
        if not contexts:
            return
        config = contexts[0].config
        async with self._query_service_factory() as queries:
            builder = PreviewSourceBuilder(
                queries,
                config,
                logger=contexts[0].logger,
            )
            games = await builder.query_current_games()
            for context in contexts:
                try:
                    queried_source = await builder.build(
                        context.request.preview_date,
                        games=games,
                    )
                except NoGamesForDate:
                    self._record_no_games(context)
                    continue
                self._record_queried_data(context, builder, queried_source)

    @staticmethod
    def _render_input_sha256(source: PreviewSourceData) -> str:
        payload = preview_data_to_dict(source)
        return sha256_bytes(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _article_rebuild_reasons(
        self,
        request: _CombinationRequest,
        existing: Article,
        state: dict[str, Any],
        *,
        input_sha256: str,
        template_version: str,
    ) -> list[str]:
        reasons: list[str] = []
        actual_cover = article_cover_descriptor(existing)
        if existing.digest != AUTO_PREVIEW_DIGEST:
            reasons.append("article 摘要不是固定值“马杯前瞻”")
        try:
            article_state = self._article_state(state)
        except ArtifactValidationError as exc:
            reasons.append(f"run.json.article 无法验收（{exc}）")
        else:
            if article_state["input_sha256"] != input_sha256:
                reasons.append("source、正文 Markdown、天气或人员配置已变化")
            if article_state["template_version"] != template_version:
                reasons.append("模板指纹已变化")
            if article_state["cover"] != actual_cover:
                reasons.append("article 封面状态不一致")
        if (
            request.cover is not None
            and cover_descriptor(request.cover) != actual_cover
        ):
            reasons.append("命令指定了不同封面")
        return reasons

    def _prepare_article(
        self,
        request: _CombinationRequest,
        paths: _RunPaths,
        state: dict[str, Any],
        inputs: GlobalInputStatus,
        logger: logging.Logger,
    ) -> Article:
        source = load_preview_bundle(
            paths.source,
            inputs.weather_path,
            inputs.config_path,
        )
        input_sha256 = self._render_input_sha256(source)
        preview_service = PreviewService.from_template(
            self._project_root / "templates" / "qhly_preview_v1" / "template.html",
        )
        existing: Article | None = None
        rebuild_reasons: list[str] = []
        render_article = request.override or not paths.article.exists()
        if paths.article.exists() and not request.override:
            try:
                existing = self._load_existing_article(paths.article)
            except ArtifactValidationError as exc:
                rebuild_reasons.append(f"article 无法加载（{exc}）")
            if existing is not None:
                rebuild_reasons.extend(
                    self._article_rebuild_reasons(
                        request,
                        existing,
                        state,
                        input_sha256=input_sha256,
                        template_version=preview_service.template_version,
                    )
                )
            render_article = bool(rebuild_reasons)

        if not render_article:
            assert existing is not None
            return existing

        if rebuild_reasons:
            logger.info(
                "↻ article 可覆盖重渲染：%s（%s）",
                self._display_path(paths.article),
                "；".join(dict.fromkeys(rebuild_reasons)),
            )
        cover = (
            request.cover
            or (existing.cover if existing is not None else None)
            or CoverFile(Path(__file__).with_name("assets") / "default_cover.png")
        )
        article = preview_service.render(
            source,
            cover=cover,
            author="清华绿茵",
            digest=AUTO_PREVIEW_DIGEST,
        )
        article.save(paths.article)
        persisted = Article.load(paths.article)
        state["article"] = {
            "input_sha256": input_sha256,
            "template_version": preview_service.template_version,
            "cover": article_cover_descriptor(persisted),
        }
        write_json(paths.state, state)
        return persisted

    @staticmethod
    def _publication_components(
        contexts: list[_CombinationContext],
    ) -> list[dict[str, str]]:
        components: list[dict[str, str]] = []
        for context in contexts:
            assert context.article is not None
            article_state = AutoPreviewPipeline._article_state(context.state)
            components.append(
                {
                    "preview_date": context.request.preview_date.isoformat(),
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
            load_draft_history(
                context.paths.draft,
                context.request.preview_date,
                context.request.competition,
            )
            for context in contexts
        ]
        started = time.monotonic()
        matching = self._matching_batch_receipt(
            histories,
            fingerprint,
            components,
        )
        if not override and matching is not None:
            for context, history in zip(contexts, histories, strict=True):
                write_json(context.paths.draft, history)
            logger.info(
                "↷ [3/3] publish 复用同一批次草稿（%.2fs）：%s",
                time.monotonic() - started,
                matching["media_id"],
            )
            return matching["media_id"]

        joined_items = "、".join(
            f"{context.request.preview_date.isoformat()} / "
            f"{context.request.competition.value}"
            for context in contexts
        )
        logger.info("▶ [3/3] publish 加入 %s", joined_items)
        articles = tuple(context.article for context in contexts)
        assert all(article is not None for article in articles)
        async with self._wechat_service_factory() as wechat:
            draft_receipt = await wechat.create_draft(articles)
        receipt = {
            "media_id": draft_receipt.media_id,
            "created_at": draft_receipt.created_at.isoformat(),
            "publication_fingerprint": fingerprint,
            "articles": components,
        }
        for context, history in zip(contexts, histories, strict=True):
            history["receipts"].append(receipt.copy())
            write_json(context.paths.draft, history)
        logger.info(
            "✓ [3/3] publish 完成（%.2fs）：%s",
            time.monotonic() - started,
            draft_receipt.media_id,
        )
        return draft_receipt.media_id

    @staticmethod
    def _combination_requests(
        request: PipelineRequest,
    ) -> tuple[_CombinationRequest, ...]:
        return tuple(
            _CombinationRequest(
                preview_date=preview_date,
                competition=competition,
                stage=request.stage,
                cover=request.cover,
                override=request.override,
            )
            for preview_date, competition in request.combinations
        )

    def _combination_result(
        self,
        request: _CombinationRequest,
        context: _CombinationContext | None,
        *,
        completed_stage: Stage,
        draft_media_id: str | None = None,
    ) -> CombinationResult:
        paths = self._run_paths(request)
        if context is None:
            return CombinationResult(
                preview_date=request.preview_date,
                competition=request.competition,
                status="skipped",
                completed_stage=Stage.DATA,
                run_directory=paths.directory,
                reason="no_games",
            )
        return CombinationResult(
            preview_date=request.preview_date,
            competition=request.competition,
            status="ok",
            completed_stage=completed_stage,
            run_directory=paths.directory,
            source_path=paths.source,
            article_directory=(
                paths.article if completed_stage is not Stage.DATA else None
            ),
            draft_media_id=draft_media_id,
        )

    async def run(self, request: PipelineRequest) -> PipelineResult:
        combination_requests = self._combination_requests(request)
        all_contexts: dict[tuple[date, Competition], _CombinationContext] = {}

        for combination in combination_requests:
            config = competition_config(combination.competition)
            paths = self._run_paths(combination)
            logger = self._logger or configure_logging(
                paths.directory,
                project_root=self._project_root,
            )
            if combination.override:
                state = new_run_state(
                    combination.preview_date,
                    combination.competition,
                )
            else:
                state = load_run_state(
                    paths.state,
                    combination.preview_date,
                    combination.competition,
                    source_path=paths.source,
                    article_directory=paths.article,
                    draft_path=paths.draft,
                )
            all_contexts[(combination.preview_date, combination.competition)] = (
                _CombinationContext(
                    request=combination,
                    config=config,
                    paths=paths,
                    state=state,
                    logger=logger,
                )
            )

        ordered_all_contexts = [all_contexts[key] for key in request.combinations]
        batch_logger = ordered_all_contexts[0].logger
        if request.override:
            batch_logger.warning(
                "⚠ --override 已启用：将从 data 重做到 %s",
                request.stage.value,
            )

        # Phase barrier 1: finish data for every combination before any article work.
        data_started = time.monotonic()
        batch_logger.info(
            "▶ [1/3] data 处理 %s 个组合（%s 个赛事）",
            len(ordered_all_contexts),
            len(request.competitions),
        )
        pending_by_competition: dict[Competition, list[_CombinationContext]] = {
            competition: [] for competition in request.competitions
        }
        manual_warnings: list[str] = []
        weather_warning_dates: set[date] = set()
        try:
            for context in ordered_all_contexts:
                if not self._load_reusable_data(context):
                    pending_by_competition[context.request.competition].append(context)

            for competition in request.competitions:
                await self._query_data_group(
                    pending_by_competition[competition],
                )

            ready_contexts = [
                context
                for context in ordered_all_contexts
                if context.document is not None
            ]
            inputs_by_context: dict[
                tuple[date, Competition], GlobalInputStatus
            ] = {}
            inputs_by_date: dict[date, GlobalInputStatus] = {}
            for context in ready_contexts:
                assert context.document is not None
                inputs = ensure_global_inputs(
                    context.paths.directory.parent,
                    context.request.preview_date,
                    require_complete_config=False,
                )
                key = (
                    context.request.preview_date,
                    context.request.competition,
                )
                inputs_by_context[key] = inputs
                inputs_by_date.setdefault(context.request.preview_date, inputs)

            (
                attempted_weather_dates,
                refreshed_dates,
                weather_query_warnings,
            ) = await self._refresh_weather(
                inputs_by_date,
                override=request.override,
            )
            for preview_date in sorted(refreshed_dates):
                batch_logger.info(
                    "↳ weather 已更新海淀区 %s：%s",
                    preview_date.isoformat(),
                    self._display_path(inputs_by_date[preview_date].weather_path),
                )
            manual_warnings.extend(weather_query_warnings)

            for context in ready_contexts:
                assert context.document is not None
                key = (
                    context.request.preview_date,
                    context.request.competition,
                )
                inputs = inputs_by_context[key]
                if context.request.preview_date in attempted_weather_dates:
                    inputs = replace(
                        inputs,
                        weather_created=False,
                        weather_date_added=False,
                        weather_placeholder=False,
                    )
                weather_warning, warnings = self._manual_warnings(
                    context.document,
                    inputs,
                    context.paths.source,
                )
                if (
                    weather_warning is not None
                    and context.request.preview_date not in weather_warning_dates
                ):
                    manual_warnings.append(weather_warning)
                    weather_warning_dates.add(context.request.preview_date)
                manual_warnings.extend(warnings)
        except Exception as exc:
            batch_logger.error("✗ [1/3] data 失败：%s", exc)
            raise

        if ready_contexts:
            batch_logger.info(
                "源数据：\n%s",
                "\n".join(
                    self._display_path(context.paths.source)
                    for context in ready_contexts
                ),
            )
            batch_logger.info(
                "正文 Markdown：\n%s",
                "\n".join(
                    self._display_path(context.paths.directory / "previews")
                    for context in ready_contexts
                ),
            )
        skipped_count = len(ordered_all_contexts) - len(ready_contexts)
        match_count = sum(
            len(context.document.matches)
            for context in ready_contexts
            if context.document is not None
        )
        batch_logger.info(
            "✓ [1/3] data 完成（%.2fs）：%s 个有效组合，%s 个无比赛，%s 场比赛",
            time.monotonic() - data_started,
            len(ready_contexts),
            skipped_count,
            match_count,
        )
        for warning in manual_warnings:
            batch_logger.warning("⚠ %s", warning)

        contexts = {
            (context.request.preview_date, context.request.competition): context
            for context in ready_contexts
        }

        ordered_contexts = [
            contexts[key] for key in request.combinations if key in contexts
        ]
        if not ordered_contexts:
            return PipelineResult(
                status="skipped",
                completed_stage=Stage.DATA,
                runs=tuple(
                    self._combination_result(
                        combination,
                        None,
                        completed_stage=Stage.DATA,
                    )
                    for combination in combination_requests
                ),
            )

        if request.stage is Stage.DATA:
            next_command = self._next_command(request, Stage.ARTICLE)
            return PipelineResult(
                status="ok",
                completed_stage=Stage.DATA,
                runs=tuple(
                    self._combination_result(
                        combination,
                        contexts.get(
                            (combination.preview_date, combination.competition)
                        ),
                        completed_stage=Stage.DATA,
                    )
                    for combination in combination_requests
                ),
                next_command=next_command,
            )

        if request.stage is Stage.PUBLISH and len(ordered_contexts) > 8:
            error = PipelineError(
                "公众号多图文草稿最多包含 8 篇文章；请缩小日期或赛事范围",
                stage="publish-validation",
            )
            batch_logger.error("✗ [3/3] publish 失败：%s", error)
            raise error

        # Phase barrier 2: article work starts only after all data work succeeds.
        article_started = time.monotonic()
        batch_logger.info(
            "▶ [2/3] article 处理 %s 个组合",
            len(ordered_contexts),
        )
        try:
            for context in ordered_contexts:
                inputs = ensure_global_inputs(
                    context.paths.directory.parent,
                    context.request.preview_date,
                    require_complete_config=True,
                )
                context.article = self._prepare_article(
                    context.request,
                    context.paths,
                    context.state,
                    inputs,
                    context.logger,
                )
        except Exception as exc:
            batch_logger.error("✗ [2/3] article 失败：%s", exc)
            raise
        batch_logger.info(
            "✓ [2/3] article 完成（%.2fs）：%s 篇文章",
            time.monotonic() - article_started,
            len(ordered_contexts),
        )

        if request.stage is Stage.ARTICLE:
            next_command = self._next_command(request, Stage.PUBLISH)
            return PipelineResult(
                status="ok",
                completed_stage=Stage.ARTICLE,
                runs=tuple(
                    self._combination_result(
                        combination,
                        contexts.get(
                            (combination.preview_date, combination.competition)
                        ),
                        completed_stage=Stage.ARTICLE,
                    )
                    for combination in combination_requests
                ),
                next_command=next_command,
            )

        try:
            draft_media_id = await self._publish_articles(
                ordered_contexts,
                override=request.override,
                logger=batch_logger,
            )
        except Exception as exc:
            batch_logger.error("✗ [3/3] publish 失败：%s", exc)
            raise
        return PipelineResult(
            status="ok",
            completed_stage=Stage.PUBLISH,
            runs=tuple(
                self._combination_result(
                    combination,
                    contexts.get((combination.preview_date, combination.competition)),
                    completed_stage=Stage.PUBLISH,
                    draft_media_id=(
                        draft_media_id
                        if (combination.preview_date, combination.competition)
                        in contexts
                        else None
                    ),
                )
                for combination in combination_requests
            ),
            draft_media_id=draft_media_id,
        )
