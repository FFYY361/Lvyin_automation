"""Manual preview content preservation and per-game archive storage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from preview import PreviewSourceDocument, preview_article_file

from .errors import ArtifactValidationError
from .source import PLACEHOLDER_PREFIX
from .state import read_json_object, write_json

ARCHIVE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ManualMatchContent:
    authors: tuple[str, ...] | None = None
    body: str | None = None

    @property
    def has_content(self) -> bool:
        return self.authors is not None or self.body is not None

    def with_precedence_over(
        self,
        fallback: ManualMatchContent | None,
    ) -> ManualMatchContent:
        if fallback is None:
            return self
        return ManualMatchContent(
            authors=self.authors or fallback.authors,
            body=self.body if self.body is not None else fallback.body,
        )


@dataclass(frozen=True, slots=True)
class ManualContentSnapshot:
    headline: str | None
    matches: dict[int, ManualMatchContent]


def _manual_authors(authors: tuple[str, ...]) -> tuple[str, ...] | None:
    retained = tuple(
        author for author in authors if not author.startswith(PLACEHOLDER_PREFIX)
    )
    return retained or None


def _manual_body(content: str) -> str | None:
    normalised = (
        content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    )
    paragraphs = tuple(
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", normalised)
        if block.strip()
    )
    if not paragraphs or all(
        paragraph.startswith(PLACEHOLDER_PREFIX) for paragraph in paragraphs
    ):
        return None
    return content


def snapshot_manual_content(
    document: PreviewSourceDocument,
    run_directory: Path,
) -> ManualContentSnapshot:
    """Read manual fields from a validated active preview source."""

    matches: dict[int, ManualMatchContent] = {}
    seen_game_ids: set[int] = set()
    for match in document.matches:
        if match.game_id in seen_game_ids:
            raise ArtifactValidationError(
                f"已有 source.json 包含重复 game_id：{match.game_id}",
                stage="data-validation",
            )
        seen_game_ids.add(match.game_id)
        reference = preview_article_file(
            match.home.short_name,
            match.away.short_name,
        )
        body_path = run_directory.joinpath(*reference.split("/"))
        try:
            raw_body = body_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ArtifactValidationError(
                f"无法读取已有人工正文：{body_path}",
                stage="data-validation",
            ) from exc
        content = ManualMatchContent(
            authors=_manual_authors(match.writers),
            body=_manual_body(raw_body),
        )
        if content.has_content:
            matches[match.game_id] = content
    return ManualContentSnapshot(
        headline=(
            document.headline
            if not document.headline.startswith(PLACEHOLDER_PREFIX)
            else None
        ),
        matches=matches,
    )


class ManualContentArchive:
    """Versioned, atomically-written manual content archive keyed by game ID."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, game_id: int) -> Path:
        if isinstance(game_id, bool) or not isinstance(game_id, int) or game_id <= 0:
            raise ArtifactValidationError(
                f"无法按非法 game_id 归档人工内容：{game_id!r}",
                stage="archive-validation",
            )
        return self._root / f"{game_id}.json"

    def load(self, game_id: int) -> ManualMatchContent | None:
        path = self._path(game_id)
        if not path.exists():
            return None
        try:
            payload = read_json_object(path, stage="archive-validation")
            allowed = {"schema_version", "game_id", "authors", "body"}
            if (
                not {"schema_version", "game_id"} <= set(payload)
                or not set(payload) <= allowed
                or isinstance(payload.get("schema_version"), bool)
                or payload.get("schema_version") != ARCHIVE_SCHEMA_VERSION
                or isinstance(payload.get("game_id"), bool)
                or payload.get("game_id") != game_id
            ):
                raise ValueError("字段、版本或 game_id 不符合归档契约")

            authors: tuple[str, ...] | None = None
            if "authors" in payload:
                authors_value = payload["authors"]
                if (
                    not isinstance(authors_value, list)
                    or not authors_value
                    or any(
                        not isinstance(author, str)
                        or not author.strip()
                        or author != author.strip()
                        or author.startswith(PLACEHOLDER_PREFIX)
                        for author in authors_value
                    )
                ):
                    raise ValueError("authors 必须是非空的已填写姓名数组")
                authors = tuple(authors_value)

            body: str | None = None
            if "body" in payload:
                body_value = payload["body"]
                if not isinstance(body_value, str) or _manual_body(body_value) is None:
                    raise ValueError("body 必须包含已填写的非占位正文")
                body = body_value

            content = ManualMatchContent(authors=authors, body=body)
            if not content.has_content:
                raise ValueError("归档必须至少包含 authors 或 body")
            return content
        except (ArtifactValidationError, ValueError) as exc:
            if isinstance(exc, ArtifactValidationError):
                detail = str(exc)
            else:
                detail = str(exc)
            raise ArtifactValidationError(
                f"比赛 {game_id} 的人工内容归档损坏：{detail}",
                stage="archive-validation",
            ) from exc

    def store(
        self,
        game_id: int,
        content: ManualMatchContent,
    ) -> ManualMatchContent:
        if not content.has_content:
            raise ValueError("manual archive content must not be empty")
        existing = self.load(game_id)
        merged = content.with_precedence_over(existing)
        payload: dict[str, object] = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "game_id": game_id,
        }
        if merged.authors is not None:
            payload["authors"] = list(merged.authors)
        if merged.body is not None:
            payload["body"] = merged.body
        path = self._path(game_id)
        try:
            write_json(path, payload)
        except OSError as exc:
            raise ArtifactValidationError(
                f"无法写入比赛 {game_id} 的人工内容归档：{path}",
                stage="archive-validation",
            ) from exc
        return merged

    def delete(self, game_id: int) -> None:
        try:
            self._path(game_id).unlink(missing_ok=True)
        except OSError as exc:
            raise ArtifactValidationError(
                f"无法删除已恢复的比赛 {game_id} 人工内容归档",
                stage="archive-validation",
            ) from exc
