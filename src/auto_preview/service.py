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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from preview import (
    PreviewError,
    PreviewService,
    PreviewSourceData,
    PreviewSourceDocument,
    load_preview_bundle,
    parse_preview_document,
)
from thufootball import THUFootballQueryService
from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    WechatArticleError,
    WechatOfficialService,
)

from .config import CompetitionConfig, competition_config
from .errors import ArtifactValidationError, NoGamesForDate, PipelineError
from .inputs import GlobalInputStatus, ensure_global_inputs
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
    document: PreviewSourceDocument
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
        self._wechat_service_factory = (
            wechat_service_factory or WechatOfficialService.from_environment
        )
        self._logger = logger

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

    def _log_manual_warnings(
        self,
        logger: logging.Logger,
        document: PreviewSourceDocument,
        inputs: GlobalInputStatus,
        source_path: Path,
    ) -> None:
        weather_path = self._display_path(inputs.weather_path)
        preview_day = document.preview_date.isoformat()
        if inputs.weather_created:
            logger.warning(
                "⚠ %s 为新生成天气配置，日期 %s 的天气尚未填写，需要补充；本篇显示“待更新”",
                weather_path,
                preview_day,
            )
        elif inputs.weather_date_added:
            logger.warning(
                "⚠ %s 原本缺少日期 %s，已加入全 null 模板；天气尚未填写，需要补充；本篇显示“待更新”",
                weather_path,
                preview_day,
            )
        elif inputs.weather_placeholder:
            logger.warning(
                "⚠ %s 日期 %s 为全 null；天气尚未填写，需要补充；本篇显示“待更新”",
                weather_path,
                preview_day,
            )

        if document.headline.startswith(PLACEHOLDER_PREFIX):
            logger.warning(
                "⚠ %s：标题尚未填写，需要补充；本篇保留占位符",
                self._display_path(source_path),
            )

        if inputs.config_created:
            logger.warning(
                "⚠ %s 为新生成配置：需要补充编辑、责编、审核；本篇保留占位符",
                self._display_path(inputs.config_path),
            )

        for match in document.matches:
            missing: list[str] = []
            if self._has_placeholder(match.preview_paragraphs):
                missing.append("前瞻内容")
            if self._has_placeholder(match.writers):
                missing.append("作者")
            if missing:
                logger.warning(
                    "⚠ 前瞻“%s vs %s”：需要补充%s；本篇保留占位符",
                    match.home.short_name,
                    match.away.short_name,
                    "、".join(missing),
                )

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

    async def _prepare_data(
        self,
        request: _CombinationRequest,
        config: CompetitionConfig,
        paths: _RunPaths,
        state: dict[str, Any],
        logger: logging.Logger,
    ) -> PreviewSourceDocument | None:
        started = time.monotonic()
        query_scope_sha256 = self._query_scope_sha256(config)
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
            logger.info(
                "↷ [1/3] data 验收通过，跳过查询（%.2fs）：%s",
                time.monotonic() - started,
                self._display_path(paths.source),
            )
            return document

        if not paths.source.exists() and state.get("source") is not None:
            source_state = self._source_state(state, request)
            if not request.override and source_state["status"] == "no_games":
                if source_state["query_scope_sha256"] == query_scope_sha256:
                    logger.info(
                        "↷ [1/3] data 复用已缓存的 no_games 结果（%.2fs）",
                        time.monotonic() - started,
                    )
                    return None
                logger.info("↷ [1/3] data 赛事 ID 查询范围已变化，重新查询")
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

        logger.info(
            "▶ [1/3] data 查询赛事 IDs=%s",
            config.current_tournament_ids,
        )
        try:
            async with self._query_service_factory() as queries:
                builder = PreviewSourceBuilder(queries, config, logger=logger)
                queried_source = await builder.build(request.preview_date)
        except NoGamesForDate:
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
            logger.info(
                "↷ [1/3] data 当日无比赛，已缓存跳过结果（%.2fs）",
                time.monotonic() - started,
            )
            return None
        paths.directory.mkdir(parents=True, exist_ok=True)
        source_payload = source_to_dict(queried_source)
        article_files = preview_article_files(queried_source)
        self._write_preview_articles(
            paths.directory,
            article_files,
            override=request.override,
        )
        write_source(paths.source, source_payload)
        document = parse_preview_document(
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
        logger.info(
            "✓ [1/3] data 完成（%.2fs）：%s 场比赛，%s",
            time.monotonic() - started,
            len(document.matches),
            self._display_path(paths.source),
        )
        logger.info(
            "✓ 已生成 %s 份正文 Markdown：%s",
            len(article_files),
            self._display_path(paths.directory / "previews"),
        )
        return document

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
        started = time.monotonic()
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
            logger.info(
                "↷ [2/3] article 验收通过，跳过渲染（%.2fs）：%s",
                time.monotonic() - started,
                self._display_path(paths.article),
            )
            return existing

        if rebuild_reasons:
            logger.info(
                "↻ [2/3] article 可覆盖重渲染：%s",
                "；".join(dict.fromkeys(rebuild_reasons)),
            )
        else:
            logger.info("▶ [2/3] article 渲染模板")
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
        logger.info(
            "✓ [2/3] article 完成（%.2fs）：%s",
            time.monotonic() - started,
            self._display_path(paths.article),
        )
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
                context.logger.info(
                    "↷ [3/3] publish 复用同一批次草稿（%.2fs）：%s",
                    time.monotonic() - started,
                    matching["media_id"],
                )
            return matching["media_id"]

        for context in contexts:
            context.logger.info(
                "▶ [3/3] publish 加入 %s / %s",
                context.request.preview_date.isoformat(),
                context.request.competition.value,
            )
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
            context.logger.info(
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
        contexts: dict[tuple[date, Competition], _CombinationContext] = {}

        # Phase barrier 1: finish data for every combination before any article work.
        for combination in combination_requests:
            config = competition_config(combination.competition)
            paths = self._run_paths(combination)
            logger = self._logger or configure_logging(
                paths.directory,
                project_root=self._project_root,
            )
            if combination.override:
                logger.warning(
                    "⚠ --override 已启用：将从 data 重做到 %s",
                    combination.stage.value,
                )
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
            try:
                document = await self._prepare_data(
                    combination,
                    config,
                    paths,
                    state,
                    logger,
                )
            except Exception as exc:
                logger.error("✗ [1/3] data 失败：%s", exc)
                raise
            if document is None:
                continue
            inputs = ensure_global_inputs(
                paths.directory.parent,
                combination.preview_date,
                require_complete_config=False,
            )
            logger.info("人工数据：source=%s", self._display_path(paths.source))
            logger.info(
                "正文 Markdown：%s",
                self._display_path(paths.directory / "previews"),
            )
            self._log_manual_warnings(logger, document, inputs, paths.source)
            contexts[(combination.preview_date, combination.competition)] = (
                _CombinationContext(
                    request=combination,
                    config=config,
                    paths=paths,
                    state=state,
                    logger=logger,
                    document=document,
                )
            )

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
            for context in ordered_contexts:
                context.logger.error("✗ [3/3] publish 失败：%s", error)
            raise error

        # Phase barrier 2: article work starts only after all data work succeeds.
        for context in ordered_contexts:
            inputs = ensure_global_inputs(
                context.paths.directory.parent,
                context.request.preview_date,
                require_complete_config=True,
            )
            try:
                context.article = self._prepare_article(
                    context.request,
                    context.paths,
                    context.state,
                    inputs,
                    context.logger,
                )
            except Exception as exc:
                context.logger.error("✗ [2/3] article 失败：%s", exc)
                raise

        if request.stage is Stage.ARTICLE:
            next_command = self._next_command(request, Stage.PUBLISH)
            for context in ordered_contexts:
                context.logger.info("下一步 publish 命令：%s", next_command)
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
            )
        except Exception as exc:
            for context in ordered_contexts:
                context.logger.error("✗ [3/3] publish 失败：%s", exc)
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
