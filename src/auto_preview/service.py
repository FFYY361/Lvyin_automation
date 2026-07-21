"""High-level orchestration for automated preview runs."""

from __future__ import annotations

import json
import logging
import os
import shlex
import sys
import time
from collections.abc import Callable
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
    WechatArticleError,
    WechatOfficialService,
)

from .config import CompetitionConfig, competition_config
from .errors import ArtifactValidationError
from .inputs import GlobalInputStatus, ensure_global_inputs
from .logging_utils import configure_logging
from .models import PipelineRequest, PipelineResult, Stage
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
    read_json_object,
    sha256_bytes,
    write_json,
    write_source,
    write_text,
)


Prompt = Callable[[str], bool]
ServiceFactory = Callable[[], Any]
AUTO_PREVIEW_DIGEST = "马杯前瞻"


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
        prompt: Prompt | None = None,
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
        # 保留 prompt 参数以兼容既有调用方；pipeline 已改为仅 warning、不再询问。
        self._logger = logger

    def run_directory(self, request: PipelineRequest) -> Path:
        return (
            self._project_root
            / "runs"
            / "auto_preview"
            / f"{request.preview_date.isoformat()}_{request.competition.value}"
        )

    def _next_command(self, request: PipelineRequest, stage: Stage) -> str:
        return _quoted_command(
            [
                "python",
                str(Path("scripts") / "auto_preview.py"),
                request.preview_date.isoformat(),
                request.competition.value,
                "--stage",
                stage.value,
            ]
        )

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
    def _source_state(state: dict[str, Any]) -> dict[str, Any]:
        source_state = state.get("source")
        if not isinstance(source_state, dict):
            raise ArtifactValidationError(
                "source.json 已存在但 run.json.source 缺失或损坏",
                stage="data-validation",
            )
        if set(source_state) != {"selected_games", "accepted_placeholder_sha256"}:
            raise ArtifactValidationError(
                "run.json.source 字段不符合当前契约",
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
        return source_state

    @staticmethod
    def _validate_source(
        source: PreviewSourceData | PreviewSourceDocument,
        request: PipelineRequest,
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

    async def run(self, request: PipelineRequest) -> PipelineResult:
        config = competition_config(request.competition)
        run_directory = self.run_directory(request)
        logger = self._logger or configure_logging(
            run_directory,
            project_root=self._project_root,
        )
        source_path = run_directory / "source.json"
        article_directory = run_directory / "article"
        state_path = run_directory / "run.json"
        draft_path = run_directory / "draft.json"

        if request.override:
            logger.warning(
                "⚠ --override 已启用：将从 data 重做到 %s",
                request.stage.value,
            )
            state = new_run_state(request)
        else:
            state = load_run_state(
                state_path,
                request,
                source_path=source_path,
                article_directory=article_directory,
                draft_path=draft_path,
            )

        data_started = time.monotonic()
        if source_path.exists() and not request.override:
            source_state = self._source_state(state)
            document = self._load_existing_source(source_path)
            self._validate_source(document, request, config, source_state)
            logger.info(
                "↷ [1/3] data 验收通过，跳过查询（%.2fs）：%s",
                time.monotonic() - data_started,
                self._display_path(source_path),
            )
        else:
            if (
                not source_path.exists()
                and state.get("source") is not None
                and not request.override
            ):
                raise ArtifactValidationError(
                    "run.json 记录 data 已完成，但 source.json 缺失",
                    stage="data-validation",
                )
            if (
                not request.override
                and not source_path.exists()
                and (
                    article_directory.exists()
                    or draft_path.exists()
                    or state.get("article") is not None
                )
            ):
                raise ArtifactValidationError(
                    "上游 source.json 缺失但已有下游产物；请使用 --override",
                    stage="data-validation",
                )
            logger.info(
                "▶ [1/3] data 查询赛事 IDs=%s",
                config.current_tournament_ids,
            )
            async with self._query_service_factory() as queries:
                builder = PreviewSourceBuilder(queries, config, logger=logger)
                queried_source = await builder.build(request.preview_date)
            run_directory.mkdir(parents=True, exist_ok=True)
            source_payload = source_to_dict(queried_source)
            article_files = preview_article_files(queried_source)
            self._write_preview_articles(
                run_directory,
                article_files,
                override=request.override,
            )
            write_source(source_path, source_payload)
            document = parse_preview_document(
                source_payload,
                source_directory=run_directory,
            )
            state["source"] = {
                "selected_games": [
                    {"game_id": game_id, "tournament_id": tournament_id}
                    for game_id, tournament_id in builder.selected_games
                ],
                "accepted_placeholder_sha256": None,
            }
            state["article"] = None
            write_json(state_path, state)
            logger.info(
                "✓ [1/3] data 完成（%.2fs）：%s 场比赛，%s",
                time.monotonic() - data_started,
                len(document.matches),
                self._display_path(source_path),
            )
            logger.info(
                "✓ 已生成 %s 份正文 Markdown：%s",
                len(article_files),
                self._display_path(run_directory / "previews"),
            )

        inputs = ensure_global_inputs(
            run_directory.parent,
            request.preview_date,
            require_complete_config=request.stage is not Stage.DATA,
        )
        logger.info("人工数据：source=%s", self._display_path(source_path))
        logger.info(
            "正文 Markdown：%s",
            self._display_path(run_directory / "previews"),
        )
        self._log_manual_warnings(logger, document, inputs, source_path)

        if request.stage is Stage.DATA:
            return PipelineResult(
                status="ok",
                completed_stage=Stage.DATA,
                run_directory=run_directory,
                source_path=source_path,
            )

        source = load_preview_bundle(
            source_path,
            inputs.weather_path,
            inputs.config_path,
        )
        render_payload = preview_data_to_dict(source)
        input_sha256 = sha256_bytes(
            json.dumps(
                render_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        preview_service = PreviewService.from_template(
            self._project_root / "templates" / "qhly_preview_v1" / "template.html",
        )
        article_started = time.monotonic()
        article: Article
        existing_article: Article | None = None
        rebuild_reasons: list[str] = []
        render_article = request.override or not article_directory.exists()
        if article_directory.exists() and not request.override:
            try:
                existing_article = self._load_existing_article(article_directory)
            except ArtifactValidationError as exc:
                rebuild_reasons.append(f"article 无法加载（{exc}）")
            if existing_article is not None:
                actual_cover = article_cover_descriptor(existing_article)
                if existing_article.digest != AUTO_PREVIEW_DIGEST:
                    rebuild_reasons.append("article 摘要不是固定值“马杯前瞻”")
                try:
                    article_state = self._article_state(state)
                except ArtifactValidationError as exc:
                    rebuild_reasons.append(f"run.json.article 无法验收（{exc}）")
                else:
                    if article_state["input_sha256"] != input_sha256:
                        rebuild_reasons.append(
                            "source、正文 Markdown、天气或人员配置已变化"
                        )
                    if (
                        article_state["template_version"]
                        != preview_service.template_version
                    ):
                        rebuild_reasons.append("模板指纹已变化")
                    if article_state["cover"] != actual_cover:
                        rebuild_reasons.append("article 封面状态不一致")
                if (
                    request.cover is not None
                    and cover_descriptor(request.cover) != actual_cover
                ):
                    rebuild_reasons.append("命令指定了不同封面")
            render_article = bool(rebuild_reasons)

        if not render_article:
            assert existing_article is not None
            article = existing_article
            logger.info(
                "↷ [2/3] article 验收通过，跳过渲染（%.2fs）：%s",
                time.monotonic() - article_started,
                self._display_path(article_directory),
            )
        else:
            if rebuild_reasons:
                logger.info(
                    "↻ [2/3] article 可覆盖重渲染：%s",
                    "；".join(dict.fromkeys(rebuild_reasons)),
                )
            else:
                logger.info("▶ [2/3] article 渲染模板")
            cover = (
                request.cover
                or (existing_article.cover if existing_article is not None else None)
                or CoverFile(
                    Path(__file__).with_name("assets") / "default_cover.png"
                )
            )
            article = preview_service.render(
                source,
                cover=cover,
                author="清华绿茵",
                digest=AUTO_PREVIEW_DIGEST,
            )
            article.save(article_directory)
            persisted_article = Article.load(article_directory)
            state["article"] = {
                "input_sha256": input_sha256,
                "template_version": preview_service.template_version,
                "cover": article_cover_descriptor(persisted_article),
            }
            write_json(state_path, state)
            article = persisted_article
            logger.info(
                "✓ [2/3] article 完成（%.2fs）：%s",
                time.monotonic() - article_started,
                self._display_path(article_directory),
            )

        if request.stage is Stage.ARTICLE:
            next_command = self._next_command(request, Stage.PUBLISH)
            logger.info("下一步 publish 命令：%s", next_command)
            return PipelineResult(
                status="ok",
                completed_stage=Stage.ARTICLE,
                run_directory=run_directory,
                source_path=source_path,
                article_directory=article_directory,
                next_command=next_command,
            )

        article_state = self._article_state(state)
        cover_sha256 = article_state["cover"]["sha256"]
        publish_started = time.monotonic()
        history = load_draft_history(draft_path)
        receipts = history["receipts"]
        matching = [
            receipt
            for receipt in receipts
            if receipt["article_fingerprint"] == article.content_fingerprint
            and receipt["cover_fingerprint"] == cover_sha256
        ]
        if draft_path.exists() and not request.override and matching:
            receipt = matching[-1]
            logger.info(
                "↷ [3/3] publish 验收通过，跳过重复创建（%.2fs）",
                time.monotonic() - publish_started,
            )
            return PipelineResult(
                status="ok",
                completed_stage=Stage.PUBLISH,
                run_directory=run_directory,
                source_path=source_path,
                article_directory=article_directory,
                draft_media_id=receipt["media_id"],
            )

        if draft_path.exists() and not request.override:
            logger.info(
                "↻ [3/3] 当前 article 无匹配草稿，将创建新草稿并保留历史回执"
            )

        logger.info("▶ [3/3] publish 上传素材并创建公众号草稿")
        async with self._wechat_service_factory() as wechat:
            draft_receipt = await wechat.create_draft(article)
        receipts.append(
            {
                "media_id": draft_receipt.media_id,
                "created_at": draft_receipt.created_at.isoformat(),
                "article_fingerprint": article.content_fingerprint,
                "cover_fingerprint": cover_sha256,
            }
        )
        write_json(draft_path, history)
        logger.info(
            "✓ [3/3] publish 完成（%.2fs）：已创建公众号草稿",
            time.monotonic() - publish_started,
        )
        return PipelineResult(
            status="ok",
            completed_stage=Stage.PUBLISH,
            run_directory=run_directory,
            source_path=source_path,
            article_directory=article_directory,
            draft_media_id=draft_receipt.media_id,
        )
