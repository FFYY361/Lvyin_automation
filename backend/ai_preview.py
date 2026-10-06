"""Database-backed AI preview generation and editable football descriptions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ai_preview import build_prompt_bundle
from ai_service import (
    AIServiceAuthenticationError,
    AIServiceConfigurationError,
    AIServiceError,
    AIServiceInvalidResponse,
    AIServiceNetworkError,
    AIServiceRateLimitError,
    ChatMessage,
)
from ai_service.config import (
    ModelProfile,
    load_ai_service_config,
    load_api_key,
)
from thufootball import ConfigurationError
from thufootball.database import (
    FootballDataRepository,
    InstitutionRecord,
)

from .models import AIPreviewResult, AITitleResult, Batch, Match, PromptTemplate, User
from .workflow import AIServiceFactory, WorkflowError

logger = logging.getLogger(__name__)

_PLACEHOLDERS = {"", "待补充", "待补充。", "无", "无。"}
_ACTIVE_STATUSES = {"queued", "running"}
_COMPETITION_DESCRIPTION_FIELDS = {
    "male": "male_description",
    "female": "female_description",
    "futsal": "futsal_description",
}


@dataclass(frozen=True, slots=True)
class WebsitePreviewModel:
    profile: str
    label: str
    estimated_seconds: int
    score: float
    recommended: bool = False


WEBSITE_PREVIEW_MODELS = (
    WebsitePreviewModel(
        profile="deepseek_v41_flash_thinking",
        label="DeepSeek V4.1 Flash Thinking",
        estimated_seconds=45,
        score=90.0,
        recommended=True,
    ),
    WebsitePreviewModel(
        profile="qwen38_thinking",
        label="Qwen 3.8 2.4T Thinking",
        estimated_seconds=140,
        score=90.0,
    ),
    WebsitePreviewModel(
        profile="qwen38_max_thinking",
        label="Qwen 3.8 Max Thinking",
        estimated_seconds=180,
        score=91.0,
    ),
)
_WEBSITE_MODELS_BY_PROFILE = {
    item.profile: item for item in WEBSITE_PREVIEW_MODELS
}


@dataclass(frozen=True, slots=True)
class PromptMaterial:
    messages: tuple[ChatMessage, ChatMessage]
    prompt_hash: str


_PROMPT_LABELS = {
    "preview_system": "正文系统规则",
    "preview_writing_rules": "正文写作规则",
    "preview_data_rules": "正文资料规则",
    "competition_male": "男足赛制规则",
    "competition_female": "女足赛制规则（2025–2026 及以前）",
    "competition_female_2026_2027": "女足赛制规则（2026–2027 起）",
    "competition_futsal": "五人制赛制规则",
    "title_system": "标题系统规则",
    "title_rules": "标题生成规则",
}
_PROMPT_KEYS = tuple(_PROMPT_LABELS)


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_prompt_material(session: Session, game_id: int) -> PromptMaterial:
    try:
        prompt_documents = {
            item.key: item.content for item in session.scalars(select(PromptTemplate))
        }
        bundle = build_prompt_bundle(
            game_id,
            repository=FootballDataRepository(session),
            prompt_documents=prompt_documents or None,
        )
    except (ValueError, AIServiceError, ConfigurationError) as exc:
        raise WorkflowError(
            500,
            "ai_preview_context_invalid",
            "AI 前瞻资料不完整，暂时无法组装 Prompt。",
        ) from exc
    messages = (
        ChatMessage(role="system", content=bundle.system_message),
        ChatMessage(role="user", content=bundle.render_user_message()),
    )
    prompt_hash = _canonical_hash(
        [
            {"role": message.role, "content": message.content}
            for message in messages
        ]
    )
    return PromptMaterial(messages=messages, prompt_hash=prompt_hash)


def prompt_templates_payload(session: Session) -> dict[str, Any]:
    rows = {item.key: item for item in session.scalars(select(PromptTemplate))}
    user_ids = {item.updated_by_user_id for item in rows.values() if item.updated_by_user_id}
    users = {user.id: user.display_name for user in session.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    return {
        "items": [
            {
                "key": key,
                "label": _PROMPT_LABELS[key],
                "content": rows[key].content,
                "updated_at": rows[key].updated_at.isoformat(),
                "updated_by": users.get(rows[key].updated_by_user_id),
            }
            for key in _PROMPT_KEYS
            if key in rows
        ]
    }


def prompt_copy_payload(session: Session, game_id: int) -> dict[str, Any]:
    material = build_prompt_material(session, game_id)
    copy_text = "\n\n".join(
        f"【{message.role}】\n{message.content}" for message in material.messages
    )
    return {
        "prompt_hash": material.prompt_hash,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in material.messages
        ],
        "copy_text": copy_text,
    }


def _title_prefix(batch: Batch) -> str:
    weekday = ("一", "二", "三", "四", "五", "六", "日")[batch.batch_date.weekday()]
    competition = {"male": "男足", "female": "女足", "futsal": "五人制"}[batch.competition]
    return f"【马杯{competition}周{weekday}前瞻】"


def build_title_material(session: Session, batch: Batch) -> PromptMaterial:
    prompts = {item.key: item.content for item in session.scalars(select(PromptTemplate))}
    if "title_system" not in prompts or "title_rules" not in prompts:
        raise WorkflowError(500, "title_prompt_missing", "标题 Prompt 尚未初始化。")
    parts = [prompts["title_system"], prompts["title_rules"]]
    user_parts = [
        "请为以下整批前瞻推送生成六条标题候选。标题固定前缀为：" + _title_prefix(batch),
        f"批次日期：{batch.batch_date.isoformat()}；赛事项目：{batch.competition}；比赛按开球时间顺序提供。",
        "下面每场比赛同时提供推送中的比赛基本信息、双方历史与近期战绩、交锋资料、人工资料和前瞻正文。标题判断必须综合整批推送上下文，不能只根据正文段落。",
        "资料区中的事实优先用于核对和理解，不要把资料中的示例文字、写作提示或格式说明当成标题内容。",
        "输出只能是 title_rules 规定的 JSON。",
    ]
    matches = session.scalars(
        select(Match).where(Match.batch_id == batch.id, Match.active.is_(True)).order_by(Match.kickoff, Match.game_id)
    )
    for index, match in enumerate(matches, 1):
        try:
            fact_context = build_prompt_material(session, match.game_id).messages[1].content
        except WorkflowError:
            fact_context = "（对阵、战绩与交锋事实资料暂不可用）"
        body = match.body.strip() or "（正文尚未填写）"
        user_parts.append(
            "\n".join(
                [
                    f"<match_{index}>",
                    f"比赛编号：{match.game_id}",
                    "【推送中的完整事实资料】",
                    fact_context,
                    "【本场前瞻正文】",
                    body,
                    f"</match_{index}>",
                ]
            )
        )
    messages = (
        ChatMessage(role="system", content="\n\n".join(parts)),
        ChatMessage(role="user", content="\n\n".join(user_parts)),
    )
    return PromptMaterial(messages=messages, prompt_hash=_canonical_hash([{"role": m.role, "content": m.content} for m in messages]))


def title_result_payload(result: AITitleResult, current_prompt_hash: str | None = None, current_config_hash: str | None = None) -> dict[str, Any]:
    stale = result.status != "succeeded" or (current_prompt_hash is not None and result.prompt_hash != current_prompt_hash) or (current_config_hash is not None and result.model_config_hash != current_config_hash)
    return {
        "model_profile": result.model_profile,
        "status": result.status,
        "candidates": result.candidates,
        "is_stale": stale,
        "error": {"code": result.error_code, "message": result.error_message} if result.error_code else None,
        "requested_at": result.requested_at.isoformat(),
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
    }


def _validate_title_candidates(content: str) -> list[dict[str, str]]:
    try:
        value = json.loads(content)
        candidates = value["candidates"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AIServiceInvalidResponse("标题结果不是有效 JSON") from exc
    if not isinstance(candidates, list) or len(candidates) != 6:
        raise AIServiceInvalidResponse("标题候选数量必须为 6")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise AIServiceInvalidResponse("标题候选格式无效")
        first, second = item.get("idiom_1"), item.get("idiom_2")
        if not isinstance(first, str) or not isinstance(second, str) or len(first) != 4 or len(second) != 4 or not all("\u4e00" <= c <= "\u9fff" for c in first + second):
            raise AIServiceInvalidResponse("标题必须由两个四字词语组成")
        pair = (first, second)
        if pair in seen:
            raise AIServiceInvalidResponse("标题候选不得重复")
        seen.add(pair)
        result.append({"idiom_1": first, "idiom_2": second})
    return result


def prepare_title_generation(session: Session, *, batch_id: int, model_profile: str, requested_by_user_id: int) -> tuple[AITitleResult, PromptMaterial | None]:
    profile = configured_website_profile(model_profile)
    if not _profile_available(profile):
        raise WorkflowError(503, "ai_model_key_missing", "该模型尚未配置 API Key，请联系管理员。")
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise WorkflowError(404, "batch_not_found", "批次不存在。")
    material = build_title_material(session, batch)
    config_hash = model_config_hash(profile)
    result = session.get(AITitleResult, (batch_id, model_profile))
    if result is not None and result.status in _ACTIVE_STATUSES:
        if result.prompt_hash == material.prompt_hash and result.model_config_hash == config_hash:
            return result, None
        raise WorkflowError(409, "ai_title_generation_in_progress", "该模型仍在生成旧版标题，请等待完成。")
    if result is not None and result.status == "succeeded" and result.prompt_hash == material.prompt_hash and result.model_config_hash == config_hash:
        return result, None
    now = _now()
    token = uuid.uuid4()
    if result is None:
        result = AITitleResult(batch_id=batch_id, model_profile=model_profile, prompt_hash=material.prompt_hash, model_config_hash=config_hash, request_token=token, status="queued", requested_by_user_id=requested_by_user_id, requested_at=now)
        session.add(result)
    else:
        result.prompt_hash = material.prompt_hash
        result.model_config_hash = config_hash
        result.request_token = token
        result.status = "queued"
        result.candidates = None
        result.error_code = None
        result.error_message = None
        result.requested_by_user_id = requested_by_user_id
        result.requested_at = now
        result.started_at = None
        result.finished_at = None
    session.commit()
    session.refresh(result)
    return result, material


async def run_title_generation(session_factory: sessionmaker[Session], ai_factory: AIServiceFactory, *, batch_id: int, model_profile: str, material: PromptMaterial, request_token: uuid.UUID, config_hash: str) -> None:
    with session_factory.begin() as session:
        started = session.execute(update(AITitleResult).where(AITitleResult.batch_id == batch_id, AITitleResult.model_profile == model_profile, AITitleResult.request_token == request_token, AITitleResult.status == "queued").values(status="running", started_at=_now()))
    if started.rowcount != 1:
        return
    try:
        async with ai_factory(model_profile) as service:
            generated = await service.chat(material.messages)
        candidates = _validate_title_candidates(generated.content)
    except Exception as exc:
        code, message = _safe_ai_error(exc)
        if isinstance(exc, AIServiceInvalidResponse):
            code, message = "ai_invalid_title", str(exc)
        with session_factory.begin() as session:
            session.execute(update(AITitleResult).where(AITitleResult.batch_id == batch_id, AITitleResult.model_profile == model_profile, AITitleResult.request_token == request_token).values(status="failed", error_code=code, error_message=message, finished_at=_now()))
        return
    with session_factory.begin() as session:
        session.execute(update(AITitleResult).where(AITitleResult.batch_id == batch_id, AITitleResult.model_profile == model_profile, AITitleResult.request_token == request_token).values(status="succeeded", candidates=candidates, error_code=None, error_message=None, finished_at=_now()))


def model_config_hash(profile: ModelProfile) -> str:
    return _canonical_hash(
        {
            "base_url": profile.base_url,
            "model": profile.model,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "request_options": profile.request_options,
        }
    )


def configured_website_profile(profile_name: str) -> ModelProfile:
    if profile_name not in _WEBSITE_MODELS_BY_PROFILE:
        raise WorkflowError(
            422,
            "unsupported_ai_model",
            "该模型未开放用于网站前瞻写作。",
        )
    try:
        return load_ai_service_config().get_profile(profile_name)
    except AIServiceConfigurationError as exc:
        raise WorkflowError(
            503,
            "ai_model_configuration_invalid",
            "AI 模型配置不可用，请联系管理员。",
        ) from exc


def _profile_available(profile: ModelProfile) -> bool:
    try:
        load_api_key(profile)
    except AIServiceConfigurationError:
        return False
    return True


def model_options_payload() -> tuple[list[dict[str, Any]], dict[str, str]]:
    config_hashes: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for model in WEBSITE_PREVIEW_MODELS:
        try:
            profile = configured_website_profile(model.profile)
        except WorkflowError:
            items.append(
                {
                    "profile": model.profile,
                    "label": model.label,
                    "estimated_seconds": model.estimated_seconds,
                    "score": model.score,
                    "recommended": model.recommended,
                    "available": False,
                }
            )
            continue
        config_hashes[model.profile] = model_config_hash(profile)
        items.append(
            {
                "profile": model.profile,
                "label": model.label,
                "estimated_seconds": model.estimated_seconds,
                "score": model.score,
                "recommended": model.recommended,
                "available": _profile_available(profile),
            }
        )
    return items, config_hashes


def _require_object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(
            500,
            "football_data_invalid",
            f"足球资料字段 {location} 无效。",
        )
    return value


def _require_integer(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowError(
            500,
            "football_data_invalid",
            f"足球资料字段 {location} 无效。",
        )
    return value


def _kit_sort(value: object) -> tuple[int, int]:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return (0, value)
    return (1, 0)


def _player_description(
    institution: InstitutionRecord, player_name: str
) -> str:
    raw = institution.player_descriptions.get(player_name)
    if not isinstance(raw, dict) or not isinstance(raw.get("description"), str):
        raise WorkflowError(
            409,
            "player_library_mismatch",
            f"球员“{player_name}”尚未进入院系人工资料库。",
        )
    return raw["description"]


def _side_manual_payload(
    *,
    side: str,
    game: Mapping[str, Any],
    tournament_data: Mapping[str, Any],
    competition: str,
    institution: InstitutionRecord,
    team_name: str,
) -> dict[str, Any]:
    tournament_team_id = _require_integer(
        game.get(f"{side}_tournament_team_id"),
        f"game.{side}_tournament_team_id",
    )
    registered = tournament_data.get("registered_players")
    if not isinstance(registered, list):
        raise WorkflowError(
            500,
            "football_data_invalid",
            "赛事报名球员资料无效。",
        )

    players: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in registered:
        if not isinstance(raw, dict):
            continue
        if raw.get("valid") is not True:
            continue
        if raw.get("tournament_team_id") != tournament_team_id:
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowError(
                500,
                "football_data_invalid",
                "赛事报名球员姓名无效。",
            )
        name = name.strip()
        if name in seen:
            raise WorkflowError(
                409,
                "duplicate_registered_player_name",
                f"同一球队中存在重复报名姓名“{name}”。",
            )
        seen.add(name)
        players.append(
            {
                "name": name,
                "kit_number": raw.get("kit_number"),
                "minutes": raw.get("minute"),
                "description": _player_description(institution, name),
            }
        )

    has_complete_minutes = bool(players) and all(
        isinstance(item["minutes"], int)
        and not isinstance(item["minutes"], bool)
        and item["minutes"] >= 0
        for item in players
    )
    if has_complete_minutes:
        players.sort(
            key=lambda item: (
                -item["minutes"],
                _kit_sort(item["kit_number"]),
                item["name"],
            )
        )
        sort_basis = "minutes"
    else:
        players.sort(key=lambda item: (_kit_sort(item["kit_number"]), item["name"]))
        sort_basis = "kit_number"

    return {
        "institution_name": institution.name,
        "institution_short_name": institution.short_name,
        "team_name": team_name,
        "team_description": getattr(
            institution,
            _COMPETITION_DESCRIPTION_FIELDS[competition],
        ),
        "sort_basis": sort_basis,
        "players": players,
    }


def match_manual_payload(session: Session, game_id: int) -> dict[str, Any]:
    repository = FootballDataRepository(session)
    try:
        game_record = repository.get_game(game_id)
        tournament = repository.get_tournament(game_record.tournament_id)
    except ConfigurationError as exc:
        raise WorkflowError(
            500,
            "football_data_missing",
            "该场比赛的足球资料不完整。",
        ) from exc
    game = _require_object(game_record.data.get("game"), "game")
    tournament_data = tournament.data
    home_team_id = _require_integer(game.get("home_team_id"), "game.home_team_id")
    away_team_id = _require_integer(game.get("away_team_id"), "game.away_team_id")
    try:
        home = repository.find_institution(home_team_id, tournament.competition)
        away = repository.find_institution(away_team_id, tournament.competition)
        home_identity = repository.find_team_identity(home_team_id, tournament.competition)
        away_identity = repository.find_team_identity(away_team_id, tournament.competition)
    except ConfigurationError as exc:
        raise WorkflowError(
            500,
            "football_data_missing",
            "该场比赛的院系资料不完整。",
        ) from exc
    return {
        "competition": tournament.competition,
        "home_team": _side_manual_payload(
            side="home",
            game=game,
            tournament_data=tournament_data,
            competition=tournament.competition,
            institution=home,
            team_name=home_identity.brief_name,
        ),
        "away_team": _side_manual_payload(
            side="away",
            game=game,
            tournament_data=tournament_data,
            competition=tournament.competition,
            institution=away,
            team_name=away_identity.brief_name,
        ),
    }


def result_payload(
    result: AIPreviewResult,
    *,
    current_prompt_hash: str | None = None,
    current_config_hash: str | None = None,
) -> dict[str, Any]:
    stale = result.content is not None and (
        result.status != "succeeded"
        or (
            current_prompt_hash is not None
            and result.prompt_hash != current_prompt_hash
        )
        or (
            current_config_hash is not None
            and result.model_config_hash != current_config_hash
        )
    )
    return {
        "model_profile": result.model_profile,
        "status": result.status,
        "content": result.content,
        "is_stale": stale,
        "error": (
            {"code": result.error_code, "message": result.error_message}
            if result.error_code
            else None
        ),
        "requested_at": result.requested_at.isoformat(),
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": (
            result.finished_at.isoformat() if result.finished_at else None
        ),
    }


def ai_preview_context(session: Session, game_id: int) -> dict[str, Any]:
    prompt = build_prompt_material(session, game_id)
    models, config_hashes = model_options_payload()
    results = {
        result.model_profile: result_payload(
            result,
            current_prompt_hash=prompt.prompt_hash,
            current_config_hash=config_hashes.get(result.model_profile),
        )
        for result in session.scalars(
            select(AIPreviewResult).where(AIPreviewResult.game_id == game_id)
        )
        if result.model_profile in _WEBSITE_MODELS_BY_PROFILE
    }
    return {
        "models": models,
        "manual": match_manual_payload(session, game_id),
        "results": results,
    }


def _advisory_key(game_id: int, profile: str) -> int:
    digest = hashlib.sha256(f"ai-preview:{game_id}:{profile}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def prepare_generation(
    session: Session,
    *,
    game_id: int,
    model_profile: str,
    requested_by_user_id: int,
) -> tuple[AIPreviewResult, PromptMaterial | None]:
    profile = configured_website_profile(model_profile)
    if not _profile_available(profile):
        raise WorkflowError(
            503,
            "ai_model_key_missing",
            "该模型尚未配置 API Key，请联系管理员。",
        )
    material = build_prompt_material(session, game_id)
    config_hash = model_config_hash(profile)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_key(game_id, model_profile)},
        )
    result = session.get(AIPreviewResult, (game_id, model_profile))
    if result is not None and result.status in _ACTIVE_STATUSES:
        if (
            result.prompt_hash == material.prompt_hash
            and result.model_config_hash == config_hash
        ):
            return result, None
        raise WorkflowError(
            409,
            "ai_preview_generation_in_progress",
            "该模型仍在生成旧版资料的前瞻，请等待完成后再重新生成。",
        )
    if (
        result is not None
        and result.status == "succeeded"
        and result.prompt_hash == material.prompt_hash
        and result.model_config_hash == config_hash
    ):
        return result, None

    requested_at = _now()
    request_token = uuid.uuid4()
    if result is None:
        result = AIPreviewResult(
            game_id=game_id,
            model_profile=model_profile,
            prompt_hash=material.prompt_hash,
            model_config_hash=config_hash,
            request_token=request_token,
            status="queued",
            requested_by_user_id=requested_by_user_id,
            requested_at=requested_at,
        )
        session.add(result)
    else:
        result.prompt_hash = material.prompt_hash
        result.model_config_hash = config_hash
        result.request_token = request_token
        result.status = "queued"
        result.error_code = None
        result.error_message = None
        result.requested_by_user_id = requested_by_user_id
        result.requested_at = requested_at
        result.started_at = None
        result.finished_at = None
    session.commit()
    session.refresh(result)
    return result, material


def _safe_ai_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, AIServiceConfigurationError):
        return "ai_configuration_error", "AI 模型配置不可用，请联系管理员。"
    if isinstance(exc, AIServiceAuthenticationError):
        return "ai_authentication_failed", "AI 服务认证失败，请联系管理员检查密钥。"
    if isinstance(exc, AIServiceRateLimitError):
        return "ai_rate_limited", "AI 服务额度不足或请求过于频繁，请稍后手动重试。"
    if isinstance(exc, AIServiceNetworkError):
        return "ai_network_error", "AI 服务连接失败或响应超时，请稍后手动重试。"
    if isinstance(exc, AIServiceInvalidResponse):
        return "ai_invalid_response", "AI 服务没有返回可用正文，请手动重试。"
    if isinstance(exc, AIServiceError):
        return "ai_provider_error", "AI 服务暂时不可用，请稍后手动重试。"
    return "ai_generation_failed", "AI 前瞻生成失败，请稍后手动重试。"


def _finish_generation(
    session_factory: sessionmaker[Session],
    *,
    game_id: int,
    model_profile: str,
    prompt_hash: str,
    config_hash: str,
    request_token: uuid.UUID,
    content: str | None = None,
    error: tuple[str, str] | None = None,
) -> None:
    values: dict[str, Any] = {"finished_at": _now()}
    if error is None:
        values.update(
            status="succeeded",
            content=(content or "").strip(),
            error_code=None,
            error_message=None,
        )
    else:
        values.update(
            status="failed",
            error_code=error[0],
            error_message=error[1],
        )
    with session_factory.begin() as session:
        session.execute(
            update(AIPreviewResult)
            .where(
                AIPreviewResult.game_id == game_id,
                AIPreviewResult.model_profile == model_profile,
                AIPreviewResult.prompt_hash == prompt_hash,
                AIPreviewResult.model_config_hash == config_hash,
                AIPreviewResult.request_token == request_token,
                AIPreviewResult.status.in_(_ACTIVE_STATUSES),
            )
            .values(**values)
        )


async def run_generation(
    session_factory: sessionmaker[Session],
    ai_factory: AIServiceFactory,
    *,
    game_id: int,
    model_profile: str,
    material: PromptMaterial,
    request_token: uuid.UUID,
    config_hash: str,
) -> None:
    with session_factory.begin() as session:
        started = session.execute(
            update(AIPreviewResult)
            .where(
                AIPreviewResult.game_id == game_id,
                AIPreviewResult.model_profile == model_profile,
                AIPreviewResult.prompt_hash == material.prompt_hash,
                AIPreviewResult.model_config_hash == config_hash,
                AIPreviewResult.request_token == request_token,
                AIPreviewResult.status == "queued",
            )
            .values(status="running", started_at=_now())
        )
    if started.rowcount != 1:
        return
    try:
        async with ai_factory(model_profile) as service:
            generated = await service.chat(material.messages)
    except asyncio.CancelledError:
        _finish_generation(
            session_factory,
            game_id=game_id,
            model_profile=model_profile,
            prompt_hash=material.prompt_hash,
            config_hash=config_hash,
            request_token=request_token,
            error=("service_stopped", "网站服务已重启，请手动重新生成。"),
        )
        raise
    except Exception as exc:
        if not isinstance(exc, AIServiceError):
            logger.exception("AI preview generation failed", exc_info=exc)
        _finish_generation(
            session_factory,
            game_id=game_id,
            model_profile=model_profile,
            prompt_hash=material.prompt_hash,
            config_hash=config_hash,
            request_token=request_token,
            error=_safe_ai_error(exc),
        )
        return
    _finish_generation(
        session_factory,
        game_id=game_id,
        model_profile=model_profile,
        prompt_hash=material.prompt_hash,
        config_hash=config_hash,
        request_token=request_token,
        content=generated.content,
    )


def recover_interrupted_generations(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.execute(
            update(AIPreviewResult)
            .where(AIPreviewResult.status.in_(_ACTIVE_STATUSES))
            .values(
                status="failed",
                error_code="service_restarted",
                error_message="网站服务已重启，请手动重新生成。",
                finished_at=_now(),
            )
        )
        session.execute(
            update(AITitleResult)
            .where(AITitleResult.status.in_(_ACTIVE_STATUSES))
            .values(
                status="failed",
                error_code="service_restarted",
                error_message="网站服务已重启，请手动重新生成。",
                finished_at=_now(),
            )
        )


def update_match_manual_descriptions(
    session: Session,
    game_id: int,
    *,
    home_team_description: str,
    home_player_descriptions: Mapping[str, str],
    away_team_description: str,
    away_player_descriptions: Mapping[str, str],
) -> dict[str, Any]:
    context = match_manual_payload(session, game_id)
    sides = (
        ("home_team", home_team_description, home_player_descriptions),
        ("away_team", away_team_description, away_player_descriptions),
    )
    expected_by_side = {
        side: {player["name"] for player in context[side]["players"]}
        for side, _, _ in sides
    }
    for side, _, descriptions in sides:
        if set(descriptions) != expected_by_side[side]:
            raise WorkflowError(
                409,
                "registered_player_set_changed",
                "报名球员资料已经变化，请刷新页面后重新编辑。",
            )

    institution_names = sorted(
        {context[side]["institution_name"] for side, _, _ in sides}
    )
    records = {
        record.name: record
        for record in session.scalars(
            select(InstitutionRecord)
            .where(InstitutionRecord.name.in_(institution_names))
            .order_by(InstitutionRecord.name)
            .with_for_update()
        )
    }
    competition = context["competition"]
    description_field = _COMPETITION_DESCRIPTION_FIELDS[competition]
    pending_team_values: dict[str, str] = {}
    pending_player_values: dict[str, dict[str, str]] = {}
    for side, team_description, descriptions in sides:
        institution_name = context[side]["institution_name"]
        previous = pending_team_values.setdefault(institution_name, team_description)
        if previous != team_description:
            raise WorkflowError(
                409,
                "conflicting_institution_descriptions",
                "同一院系的两侧球队描述不一致。",
            )
        target = pending_player_values.setdefault(institution_name, {})
        for player_name, description in descriptions.items():
            if player_name in target and target[player_name] != description:
                raise WorkflowError(
                    409,
                    "conflicting_player_descriptions",
                    f"球员“{player_name}”的两处描述不一致。",
                )
            target[player_name] = description

    for institution_name in institution_names:
        record = records.get(institution_name)
        if record is None:
            raise WorkflowError(404, "institution_not_found", "院系资料不存在。")
        setattr(record, description_field, pending_team_values[institution_name])
        player_data = dict(record.player_descriptions)
        for player_name, description in pending_player_values[institution_name].items():
            raw = player_data.get(player_name)
            if not isinstance(raw, dict):
                raise WorkflowError(
                    409,
                    "player_library_mismatch",
                    f"球员“{player_name}”尚未进入院系人工资料库。",
                )
            player_data[player_name] = {**raw, "description": description}
        record.player_descriptions = player_data
    session.commit()
    return match_manual_payload(session, game_id)


def _institution_players(record: InstitutionRecord) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for name, raw in record.player_descriptions.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            continue
        competitions = raw.get("competitions")
        description = raw.get("description")
        players.append(
            {
                "name": name,
                "competitions": (
                    competitions if isinstance(competitions, list) else []
                ),
                "description": description if isinstance(description, str) else "",
            }
        )
    return sorted(players, key=lambda item: item["name"])


def institution_detail_payload(record: InstitutionRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "short_name": record.short_name,
        "male_team_ids": record.male_team_ids,
        "female_team_ids": record.female_team_ids,
        "futsal_team_ids": record.futsal_team_ids,
        "predecessors": record.predecessors,
        "male_description": record.male_description,
        "female_description": record.female_description,
        "futsal_description": record.futsal_description,
        "players": _institution_players(record),
    }


def institution_summary_payload(record: InstitutionRecord) -> dict[str, Any]:
    players = _institution_players(record)
    return {
        "name": record.name,
        "short_name": record.short_name,
        "male_team_ids": record.male_team_ids,
        "female_team_ids": record.female_team_ids,
        "futsal_team_ids": record.futsal_team_ids,
        "team_descriptions_complete": {
            "male": record.male_description not in _PLACEHOLDERS,
            "female": record.female_description not in _PLACEHOLDERS,
            "futsal": record.futsal_description not in _PLACEHOLDERS,
        },
        "player_description_count": sum(
            player["description"] not in _PLACEHOLDERS for player in players
        ),
        "player_count": len(players),
    }


def update_institution_descriptions(
    session: Session,
    institution_name: str,
    *,
    male_description: str,
    female_description: str,
    futsal_description: str,
    player_descriptions: Mapping[str, str],
) -> dict[str, Any]:
    record = session.scalar(
        select(InstitutionRecord)
        .where(InstitutionRecord.name == institution_name)
        .with_for_update()
    )
    if record is None:
        raise WorkflowError(404, "institution_not_found", "院系资料不存在。")
    if set(player_descriptions) != set(record.player_descriptions):
        raise WorkflowError(
            409,
            "institution_player_set_changed",
            "院系球员清单已经变化，请刷新页面后重新编辑。",
        )
    player_data: dict[str, Any] = {}
    for player_name, raw in record.player_descriptions.items():
        if not isinstance(raw, dict):
            raise WorkflowError(
                409,
                "player_library_mismatch",
                f"球员“{player_name}”的资料结构无效。",
            )
        player_data[player_name] = {
            **raw,
            "description": player_descriptions[player_name],
        }
    record.male_description = male_description
    record.female_description = female_description
    record.futsal_description = futsal_description
    record.player_descriptions = player_data
    session.commit()
    return institution_detail_payload(record)


def require_match_access(session: Session, game_id: int, user: Any) -> Match:
    match = session.get(Match, game_id)
    if match is None:
        raise WorkflowError(404, "match_not_found", "比赛不存在。")
    if user.role != "admin" and match.claimed_by_user_id != user.id:
        raise WorkflowError(403, "match_access_denied", "你无权访问该场比赛。")
    return match
