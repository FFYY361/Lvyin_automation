"""Strict persistence and fingerprint helpers for auto_report."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from wechat_official import Article, CoverFile, CoverMediaId

from .errors import ArtifactValidationError
from .models import Competition

RUN_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
DRAFT_SCHEMA_VERSION = 1


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ArtifactValidationError(
            f"无法读取文件以计算指纹：{path}",
            stage="artifact-validation",
        ) from exc


def cover_descriptor(cover: CoverFile | CoverMediaId) -> dict[str, str]:
    if isinstance(cover, CoverFile):
        return {"kind": "file", "sha256": sha256_file(cover.path)}
    return {
        "kind": "media_id",
        "sha256": sha256_bytes(cover.media_id.encode("utf-8")),
    }


def article_cover_descriptor(article: Article) -> dict[str, str]:
    return cover_descriptor(article.cover)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, payload: object) -> None:
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def read_json_object(path: Path, *, stage: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactValidationError(
            f"无法读取已有产物：{path}",
            stage=stage,
        ) from exc
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(
            f"已有产物不是合法 JSON：{path}（第 {exc.lineno} 行）",
            stage=stage,
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(
            f"已有产物必须是 JSON 对象：{path}",
            stage=stage,
        )
    return value


def new_run_state(
    report_date: date,
    competition: Competition,
) -> dict[str, Any]:
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "request": {
            "report_date": report_date.isoformat(),
            "competition": competition.value,
        },
        "report": None,
        "article": None,
    }


def load_run_state(
    path: Path,
    report_date: date,
    competition: Competition,
    *,
    report_path: Path,
    reports_directory: Path,
    article_directory: Path,
    draft_path: Path,
) -> dict[str, Any]:
    if not path.exists():
        if (
            report_path.exists()
            or reports_directory.exists()
            or article_directory.exists()
            or draft_path.exists()
        ):
            raise ArtifactValidationError(
                "已有阶段产物但 run.json 缺失；请使用 --override 明确重建",
                stage="state-validation",
            )
        return new_run_state(report_date, competition)

    payload = read_json_object(path, stage="state-validation")
    if set(payload) != {"schema_version", "request", "report", "article"}:
        raise ArtifactValidationError(
            "run.json 字段不符合当前契约",
            stage="state-validation",
        )
    if payload.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ArtifactValidationError(
            "run.json 版本不受支持",
            stage="state-validation",
        )
    expected_request = {
        "report_date": report_date.isoformat(),
        "competition": competition.value,
    }
    if payload.get("request") != expected_request:
        raise ArtifactValidationError(
            "run.json 与本次日期或赛事不匹配",
            stage="state-validation",
        )
    for field in ("report", "article"):
        value = payload.get(field)
        if value is not None and not isinstance(value, dict):
            raise ArtifactValidationError(
                f"run.json.{field} 字段损坏",
                stage="state-validation",
            )
    return payload


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_report_manifest(
    path: Path,
    *,
    report_date: date,
    competition: Competition,
    tournament_ids: tuple[int, ...],
) -> dict[str, Any]:
    payload = read_json_object(path, stage="report-validation")
    required = {
        "schema_version",
        "report_date",
        "competition",
        "status",
        "query_scope_sha256",
        "queried_at",
        "items",
        "skipped_unfinished",
    }
    if set(payload) != required:
        raise ArtifactValidationError(
            "report.json 字段不符合当前契约",
            stage="report-validation",
        )
    if payload["schema_version"] != REPORT_SCHEMA_VERSION:
        raise ArtifactValidationError(
            "report.json 版本不受支持",
            stage="report-validation",
        )
    if (
        payload["report_date"] != report_date.isoformat()
        or payload["competition"] != competition.value
    ):
        raise ArtifactValidationError(
            "report.json 与本次日期或赛事不匹配",
            stage="report-validation",
        )
    expected_scope = sha256_bytes(
        json.dumps(
            list(tournament_ids),
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if payload["query_scope_sha256"] != expected_scope:
        raise ArtifactValidationError(
            "赛事 ID 配置已变化；请使用 --override 重新查询",
            stage="report-validation",
        )
    if payload["status"] not in {"ready", "no_games", "no_finished_games"}:
        raise ArtifactValidationError(
            "report.json.status 字段损坏",
            stage="report-validation",
        )
    if not isinstance(payload["queried_at"], str) or not payload["queried_at"]:
        raise ArtifactValidationError(
            "report.json.queried_at 字段损坏",
            stage="report-validation",
        )
    if not isinstance(payload["items"], list) or not isinstance(
        payload["skipped_unfinished"], list
    ):
        raise ArtifactValidationError(
            "report.json 比赛列表字段损坏",
            stage="report-validation",
        )

    previous_key: tuple[str, int, int] | None = None
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise ArtifactValidationError(
                "report.json.items 字段损坏",
                stage="report-validation",
            )
        common = {
            "game_id",
            "tournament_id",
            "kickoff_local",
            "home_name",
            "away_name",
            "artifacts",
            "warnings",
        }
        if set(item) != common:
            raise ArtifactValidationError(
                "report.json.items 中存在未知或损坏的条目",
                stage="report-validation",
            )
        if (
            isinstance(item.get("game_id"), bool)
            or not isinstance(item.get("game_id"), int)
            or item["game_id"] <= 0
            or isinstance(item.get("tournament_id"), bool)
            or not isinstance(item.get("tournament_id"), int)
            or item["tournament_id"] not in tournament_ids
            or not all(
                isinstance(item.get(field), str) and item[field]
                for field in ("kickoff_local", "home_name", "away_name")
            )
        ):
            raise ArtifactValidationError(
                "report.json.items 中的比赛字段损坏",
                stage="report-validation",
            )
        artifacts = item["artifacts"]
        if (
            not isinstance(artifacts, list)
            or not 1 <= len(artifacts) <= 2
            or any(not isinstance(artifact, dict) for artifact in artifacts)
            or [artifact.get("kind") for artifact in artifacts]
            not in (["image"], ["text"], ["image", "text"])
            or any(
                set(artifact) != {"kind", "path", "sha256"}
                or not isinstance(artifact.get("path"), str)
                or not artifact["path"]
                or not _is_sha256(artifact.get("sha256"))
                for artifact in artifacts
            )
        ):
            raise ArtifactValidationError(
                "report.json 战报产物条目损坏",
                stage="report-validation",
            )
        key = (item["kickoff_local"], item["tournament_id"], item["game_id"])
        if previous_key is not None and key < previous_key:
            raise ArtifactValidationError(
                "report.json.items 未按比赛时间排序",
                stage="report-validation",
            )
        previous_key = key
        if (
            not isinstance(item["warnings"], list)
            or any(
                    not isinstance(warning, dict)
                    or set(warning)
                    != {
                        "severity",
                        "code",
                        "message",
                        "event_ids",
                        "player_id",
                        "side",
                        "minute",
                        "stoppage_minute",
                    }
                    or warning.get("severity") != "warning"
                    or not isinstance(warning.get("code"), str)
                    or not warning["code"]
                    or not isinstance(warning.get("message"), str)
                    or not isinstance(warning.get("event_ids"), list)
                    or any(
                        isinstance(event_id, bool)
                        or not isinstance(event_id, int)
                        or event_id <= 0
                        for event_id in warning["event_ids"]
                    )
                    or (
                        warning.get("player_id") is not None
                        and (
                            isinstance(warning["player_id"], bool)
                            or not isinstance(warning["player_id"], int)
                            or warning["player_id"] <= 0
                        )
                    )
                    or warning.get("side") not in {None, "home", "away"}
                    or any(
                        warning.get(field) is not None
                        and (
                            isinstance(warning[field], bool)
                            or not isinstance(warning[field], int)
                            or warning[field] < 0
                        )
                        for field in ("minute", "stoppage_minute")
                    )
                    for warning in item["warnings"]
                )
        ):
            raise ArtifactValidationError(
                "report.json 战报 warning 条目损坏",
                stage="report-validation",
            )

    for item in payload["skipped_unfinished"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"game_id", "tournament_id", "kickoff_local", "status"}
            or isinstance(item.get("game_id"), bool)
            or not isinstance(item.get("game_id"), int)
            or item["game_id"] <= 0
            or isinstance(item.get("tournament_id"), bool)
            or not isinstance(item.get("tournament_id"), int)
            or item["tournament_id"] not in tournament_ids
            or not isinstance(item.get("kickoff_local"), str)
            or not isinstance(item.get("status"), str)
        ):
            raise ArtifactValidationError(
                "report.json.skipped_unfinished 字段损坏",
                stage="report-validation",
            )

    if payload["status"] == "ready" and not payload["items"]:
        raise ArtifactValidationError(
            "ready 状态的 report.json 没有正文条目",
            stage="report-validation",
        )
    if payload["status"] != "ready" and payload["items"]:
        raise ArtifactValidationError(
            "skipped 状态的 report.json 不应含正文条目",
            stage="report-validation",
        )
    return payload


def publication_fingerprint(articles: list[dict[str, str]]) -> str:
    return sha256_bytes(
        json.dumps(
            articles,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _valid_publication_article(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "report_date",
        "competition",
        "article_fingerprint",
        "cover_fingerprint",
    }:
        return False
    try:
        parsed_date = date.fromisoformat(value["report_date"])
        Competition(value["competition"])
    except (TypeError, ValueError):
        return False
    return (
        parsed_date.isoformat() == value["report_date"]
        and _is_sha256(value["article_fingerprint"])
        and _is_sha256(value["cover_fingerprint"])
    )


def _valid_receipt(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "media_id",
        "created_at",
        "publication_fingerprint",
        "articles",
    }:
        return False
    articles = value.get("articles")
    return (
        isinstance(value.get("media_id"), str)
        and bool(value["media_id"])
        and isinstance(value.get("created_at"), str)
        and bool(value["created_at"])
        and _is_sha256(value.get("publication_fingerprint"))
        and isinstance(articles, list)
        and bool(articles)
        and all(_valid_publication_article(article) for article in articles)
        and publication_fingerprint(articles) == value["publication_fingerprint"]
    )


def load_draft_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": DRAFT_SCHEMA_VERSION, "receipts": []}
    payload = read_json_object(path, stage="draft-validation")
    if set(payload) != {"schema_version", "receipts"}:
        raise ArtifactValidationError(
            "draft.json 字段不符合当前契约",
            stage="draft-validation",
        )
    receipts = payload.get("receipts")
    if (
        payload.get("schema_version") != DRAFT_SCHEMA_VERSION
        or not isinstance(receipts, list)
        or any(not _valid_receipt(receipt) for receipt in receipts)
    ):
        raise ArtifactValidationError(
            "draft.json 内容损坏",
            stage="draft-validation",
        )
    return payload
