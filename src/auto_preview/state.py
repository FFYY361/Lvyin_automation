"""Artifact persistence, fingerprints, and strict run-state validation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from wechat_official import Article, CoverFile, CoverMediaId

from .errors import ArtifactValidationError
from .models import Competition

RUN_SCHEMA_VERSION = 3
DRAFT_SCHEMA_VERSION = 2


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ArtifactValidationError(
            f"无法读取文件以计算指纹：{path}", stage="artifact-validation"
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_json(path: Path, payload: object) -> None:
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def write_text(path: Path, content: str) -> None:
    _atomic_write(path, content)


def write_source(path: Path, payload: Mapping[str, object]) -> None:
    write_json(path, payload)


def _read_object(path: Path, *, stage: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取已有产物：{path}", stage=stage) from exc
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(
            f"已有产物不是合法 JSON：{path}（第 {exc.lineno} 行）",
            stage=stage,
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"已有产物必须是 JSON 对象：{path}", stage=stage)
    return value


def read_json_object(path: Path, *, stage: str) -> dict[str, Any]:
    return _read_object(path, stage=stage)


def new_run_state(
    preview_date: date,
    competition: Competition,
) -> dict[str, Any]:
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "request": {
            "preview_date": preview_date.isoformat(),
            "competition": competition.value,
        },
        "source": None,
        "article": None,
    }


def load_run_state(
    path: Path,
    preview_date: date,
    competition: Competition,
    *,
    source_path: Path,
    article_directory: Path,
    draft_path: Path,
) -> dict[str, Any]:
    if not path.exists():
        if source_path.exists() or article_directory.exists() or draft_path.exists():
            raise ArtifactValidationError(
                "已有阶段产物但 run.json 缺失；请使用 --override 明确重建",
                stage="state-validation",
            )
        return new_run_state(preview_date, competition)
    payload = _read_object(path, stage="state-validation")
    if set(payload) != {"schema_version", "request", "source", "article"}:
        raise ArtifactValidationError(
            "run.json 字段不符合当前契约", stage="state-validation"
        )
    schema_version = payload.get("schema_version")
    if schema_version not in {2, RUN_SCHEMA_VERSION}:
        raise ArtifactValidationError("run.json 版本不受支持", stage="state-validation")
    expected_request = {
        "preview_date": preview_date.isoformat(),
        "competition": competition.value,
    }
    if payload.get("request") != expected_request:
        raise ArtifactValidationError(
            "run.json 与本次日期或赛事不匹配", stage="state-validation"
        )
    if payload.get("source") is not None and not isinstance(payload["source"], dict):
        raise ArtifactValidationError(
            "run.json.source 字段损坏", stage="state-validation"
        )
    if payload.get("article") is not None and not isinstance(payload["article"], dict):
        raise ArtifactValidationError(
            "run.json.article 字段损坏", stage="state-validation"
        )
    if schema_version == 2 and isinstance(payload.get("source"), dict):
        source = payload["source"]
        if set(source) != {"selected_games", "accepted_placeholder_sha256"}:
            raise ArtifactValidationError(
                "旧版 run.json.source 字段损坏", stage="state-validation"
            )
        payload["source"] = {
            "status": "ready",
            "preview_date": preview_date.isoformat(),
            "competition": competition.value,
            "selected_games": source["selected_games"],
            "accepted_placeholder_sha256": source["accepted_placeholder_sha256"],
            "queried_at": None,
            "query_scope_sha256": None,
        }
    payload["schema_version"] = RUN_SCHEMA_VERSION
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
        "preview_date",
        "competition",
        "article_fingerprint",
        "cover_fingerprint",
    }:
        return False
    try:
        parsed_date = date.fromisoformat(value["preview_date"])
        Competition(value["competition"])
    except (TypeError, ValueError):
        return False
    return (
        parsed_date.isoformat() == value["preview_date"]
        and isinstance(value["article_fingerprint"], str)
        and len(value["article_fingerprint"]) == 64
        and isinstance(value["cover_fingerprint"], str)
        and len(value["cover_fingerprint"]) == 64
    )


def _valid_v2_receipt(value: object) -> bool:
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
        and isinstance(value.get("publication_fingerprint"), str)
        and len(value["publication_fingerprint"]) == 64
        and isinstance(articles, list)
        and bool(articles)
        and all(_valid_publication_article(article) for article in articles)
        and publication_fingerprint(articles) == value["publication_fingerprint"]
    )


def load_draft_history(
    path: Path,
    preview_date: date,
    competition: Competition,
) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": DRAFT_SCHEMA_VERSION, "receipts": []}
    payload = _read_object(path, stage="draft-validation")
    if set(payload) != {"schema_version", "receipts"}:
        raise ArtifactValidationError(
            "draft.json 字段不符合当前契约", stage="draft-validation"
        )
    schema_version = payload.get("schema_version")
    if schema_version not in {1, DRAFT_SCHEMA_VERSION}:
        raise ArtifactValidationError(
            "draft.json 版本不受支持", stage="draft-validation"
        )
    receipts = payload.get("receipts")
    if not isinstance(receipts, list):
        raise ArtifactValidationError(
            "draft.json.receipts 字段损坏", stage="draft-validation"
        )
    if schema_version == DRAFT_SCHEMA_VERSION:
        if any(not _valid_v2_receipt(receipt) for receipt in receipts):
            raise ArtifactValidationError(
                "draft.json.receipts 字段损坏", stage="draft-validation"
            )
        return payload

    normalized: list[dict[str, Any]] = []
    for receipt in receipts:
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "media_id",
                "created_at",
                "article_fingerprint",
                "cover_fingerprint",
            }
            or not all(isinstance(value, str) and value for value in receipt.values())
            or len(receipt["article_fingerprint"]) != 64
            or len(receipt["cover_fingerprint"]) != 64
        ):
            raise ArtifactValidationError(
                "draft.json.receipts 字段损坏", stage="draft-validation"
            )
        articles = [
            {
                "preview_date": preview_date.isoformat(),
                "competition": competition.value,
                "article_fingerprint": receipt["article_fingerprint"],
                "cover_fingerprint": receipt["cover_fingerprint"],
            }
        ]
        normalized.append(
            {
                "media_id": receipt["media_id"],
                "created_at": receipt["created_at"],
                "publication_fingerprint": publication_fingerprint(articles),
                "articles": articles,
            }
        )
    return {"schema_version": DRAFT_SCHEMA_VERSION, "receipts": normalized}
