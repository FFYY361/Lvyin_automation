"""Three-file preview input contract and assembly helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from .errors import PreviewValidationError
from .models import (
    PreviewColumnConfig,
    PreviewCredits,
    PreviewMatch,
    PreviewSourceData,
    PreviewWeather,
    _load_json,
    parse_preview_source,
    validate_preview_source,
)

SOURCE_DOCUMENT_SCHEMA_VERSION = 2
WEATHER_FIELDS = ("low_c", "high_c", "wind_direction", "wind_level")
_MATCH_FIELDS = (
    "game_id",
    "competition_name",
    "stage",
    "kickoff",
    "venue",
    "home",
    "away",
    "head_to_head",
)
_DUMMY_CREDITS = {
    "editors": ["__bundle_validation__"],
    "reviewers": ["__bundle_validation__"],
    "approvers": ["__bundle_validation__"],
}


def _error(path: str, message: str) -> PreviewValidationError:
    return PreviewValidationError(f"{path}: {message}", stage="preview-bundle")


def _object(
    value: object,
    path: str,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(path, "必须是 JSON 对象")
    allowed = set(required) | set(optional)
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise _error(path, "包含未知字段：" + ", ".join(unknown))
    missing = sorted(key for key in required if key not in value)
    if missing:
        raise _error(path, "缺少必填字段：" + ", ".join(missing))
    return value


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "必须是非空字符串")
    return value.strip()


def _names(value: object, path: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(path, "必须是数组")
    if not value:
        raise _error(path, "至少需要 1 项")
    names = tuple(
        _nonempty_string(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    return tuple(dict.fromkeys(names))


def _date_value(value: object, path: str) -> date:
    raw = _nonempty_string(value, path)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _error(path, "必须是 YYYY-MM-DD 日期") from exc


@dataclass(frozen=True, slots=True)
class PreviewSourceDocument:
    """The manual source document before weather and global credits are injected."""

    schema_version: int
    column: PreviewColumnConfig
    preview_date: date
    headline: str
    matches: tuple[PreviewMatch, ...]

    def assemble(
        self,
        *,
        weather: PreviewWeather | None,
        credits: PreviewCredits,
    ) -> PreviewSourceData:
        source = PreviewSourceData(
            schema_version=1,
            column=self.column,
            preview_date=self.preview_date,
            headline=self.headline,
            weather=weather,
            matches=self.matches,
            credits=credits,
        )
        validate_preview_source(source)
        return source


def matchup_key(match: PreviewMatch) -> str:
    return f"{match.home.short_name} vs {match.away.short_name}"


_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def preview_article_file(home_short_name: str, away_short_name: str) -> str:
    """Return the cross-platform relative Markdown path for one matchup."""

    stem = f"{home_short_name}vs{away_short_name}"
    if (
        not stem
        or any(ord(character) < 32 for character in stem)
        or any(character in _INVALID_FILENAME_CHARACTERS for character in stem)
        or stem.endswith((" ", "."))
        or stem.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"对阵简称无法组成跨平台文件名：{stem!r}")
    return f"previews/{stem}.md"


@dataclass(frozen=True, slots=True)
class _PreviewEntry:
    path: str
    authors: tuple[str, ...]
    article: str | None
    article_file: str | None


def _markdown_paragraphs(content: str, path: str) -> tuple[str, ...]:
    normalised = (
        content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    )
    paragraphs = tuple(
        block.strip() for block in re.split(r"\n[ \t]*\n+", normalised) if block.strip()
    )
    if not paragraphs:
        raise _error(path, "Markdown 文件至少需要一个非空段落")
    return paragraphs


def _read_article_file(
    source_directory: str | Path | None,
    reference: str,
    path: str,
) -> tuple[str, ...]:
    if source_directory is None:
        raise _error(path, "使用 article_file 时必须提供 source 所在目录")
    relative = PurePosixPath(reference)
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "previews"
        or relative.suffix.lower() != ".md"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _error(path, "必须是 previews/比赛名称.md 形式的安全相对路径")
    root = Path(source_directory).resolve()
    article_path = root.joinpath(*relative.parts).resolve()
    preview_root = (root / "previews").resolve()
    if article_path.parent != preview_root:
        raise _error(path, "Markdown 路径越出了 previews 目录")
    try:
        content = article_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _error(path, f"Markdown 文件必须使用 UTF-8 编码：{reference}") from exc
    except OSError as exc:
        raise _error(path, f"无法读取 Markdown 文件：{reference}") from exc
    return _markdown_paragraphs(content, path)


def _parse_preview_entries(value: object) -> dict[str, _PreviewEntry]:
    if not isinstance(value, Mapping):
        raise _error("$.previews", "必须是 JSON 对象")
    if not value:
        raise _error("$.previews", "至少需要 1 项")
    entries: dict[str, _PreviewEntry] = {}
    for raw_key, raw_entry in value.items():
        key = _nonempty_string(raw_key, "$.previews.<key>")
        if raw_key != key:
            raise _error(
                "$.previews.<key>",
                f"对阵键必须精确书写，不能包含首尾空白：{raw_key!r}",
            )
        entry_path = f"$.previews[{key!r}]"
        entry = _object(
            raw_entry,
            entry_path,
            required=("authors",),
            optional=("article", "article_file"),
        )
        has_article = "article" in entry
        has_article_file = "article_file" in entry
        if has_article == has_article_file:
            raise _error(
                entry_path,
                "article 与 article_file 必须且只能填写一个",
            )
        article_file: str | None = None
        if has_article_file:
            raw_article_file = entry["article_file"]
            article_file = _nonempty_string(
                raw_article_file,
                f"{entry_path}.article_file",
            )
            if raw_article_file != article_file:
                raise _error(
                    f"{entry_path}.article_file",
                    "不能包含首尾空白",
                )
        entries[key] = _PreviewEntry(
            path=entry_path,
            authors=_names(entry["authors"], f"{entry_path}.authors"),
            article=(
                _nonempty_string(entry["article"], f"{entry_path}.article")
                if has_article
                else None
            ),
            article_file=article_file,
        )
    return entries


_MatchRow = tuple[dict[str, object], str, str]


def _parse_match_rows(value: object) -> list[_MatchRow]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error("$.matches", "必须是数组")
    if not value:
        raise _error("$.matches", "至少需要 1 项")

    rows: list[_MatchRow] = []
    label_game_ids: dict[str, list[object]] = {}
    for index, raw_match in enumerate(value):
        path = f"$.matches[{index}]"
        match = _object(raw_match, path, required=_MATCH_FIELDS)
        home = match["home"]
        away = match["away"]
        if not isinstance(home, Mapping):
            raise _error(f"{path}.home", "必须是 JSON 对象")
        if not isinstance(away, Mapping):
            raise _error(f"{path}.away", "必须是 JSON 对象")
        home_short = _nonempty_string(home.get("short_name"), f"{path}.home.short_name")
        away_short = _nonempty_string(away.get("short_name"), f"{path}.away.short_name")
        label = f"{home_short} vs {away_short}"
        try:
            expected_article_file = preview_article_file(home_short, away_short)
        except ValueError as exc:
            raise _error(path, str(exc)) from exc
        label_game_ids.setdefault(label, []).append(match.get("game_id"))
        rows.append((dict(match), label, expected_article_file))

    duplicates = {
        label: game_ids
        for label, game_ids in label_game_ids.items()
        if len(game_ids) > 1
    }
    if duplicates:
        detail = "；".join(
            f"{label} game_ids={game_ids}" for label, game_ids in duplicates.items()
        )
        raise _error("$.matches", "对阵简称重复：" + detail)
    return rows


def _validate_preview_mapping(
    rows: Sequence[_MatchRow],
    entries: Mapping[str, _PreviewEntry],
) -> None:
    expected = {label for _, label, _ in rows}
    actual = set(entries)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        detail: list[str] = []
        if missing:
            detail.append("缺少：" + ", ".join(missing))
        if extra:
            detail.append("多余：" + ", ".join(extra))
        raise _error("$.previews", "与 matches 不一致（" + "；".join(detail) + "）")


def _augment_matches(
    rows: Sequence[_MatchRow],
    entries: Mapping[str, _PreviewEntry],
    source_directory: str | Path | None,
) -> list[dict[str, object]]:
    augmented: list[dict[str, object]] = []
    for match, label, expected_article_file in rows:
        entry = entries[label]
        if entry.article_file is not None:
            if entry.article_file != expected_article_file:
                raise _error(
                    f"{entry.path}.article_file",
                    f"必须与比赛名称一致，预期为 {expected_article_file!r}",
                )
            paragraphs = _read_article_file(
                source_directory,
                entry.article_file,
                f"{entry.path}.article_file",
            )
        else:
            assert entry.article is not None
            paragraphs = tuple(
                line.strip() for line in entry.article.splitlines() if line.strip()
            )
            if not paragraphs:
                raise _error(f"{entry.path}.article", "至少需要一个非空段落")
        match["preview_paragraphs"] = list(paragraphs)
        match["writers"] = list(entry.authors)
        augmented.append(match)
    return augmented


def parse_preview_document(
    value: object,
    *,
    source_directory: str | Path | None = None,
) -> PreviewSourceDocument:
    """Parse the strict schema-v2 manual source document."""

    root = _object(
        value,
        "$",
        required=(
            "schema_version",
            "column",
            "preview_date",
            "headline",
            "previews",
            "matches",
        ),
    )
    version = root["schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != SOURCE_DOCUMENT_SCHEMA_VERSION
    ):
        raise _error(
            "$.schema_version",
            f"仅支持版本 {SOURCE_DOCUMENT_SCHEMA_VERSION}，实际为 {version}",
        )

    preview_entries = _parse_preview_entries(root["previews"])
    match_rows = _parse_match_rows(root["matches"])
    _validate_preview_mapping(match_rows, preview_entries)
    augmented_matches = _augment_matches(
        match_rows,
        preview_entries,
        source_directory,
    )

    assembled_payload = {
        "schema_version": 1,
        "column": root["column"],
        "preview_date": root["preview_date"],
        "headline": root["headline"],
        "weather": None,
        "matches": augmented_matches,
        "credits": _DUMMY_CREDITS,
    }
    parsed = parse_preview_source(assembled_payload)
    return PreviewSourceDocument(
        schema_version=SOURCE_DOCUMENT_SCHEMA_VERSION,
        column=parsed.column,
        preview_date=parsed.preview_date,
        headline=parsed.headline,
        matches=parsed.matches,
    )


def _parse_weather_map(value: object, preview_date: date) -> PreviewWeather | None:
    if not isinstance(value, Mapping):
        raise _error("$weather", "必须是日期到天气对象的映射")
    parsed_entries: dict[str, PreviewWeather | None] = {}
    for raw_day, raw_entry in value.items():
        day = _date_value(raw_day, "$weather.<date>")
        path = f"$weather[{day.isoformat()!r}]"
        entry = _object(raw_entry, path, required=WEATHER_FIELDS)
        null_fields = [field for field in WEATHER_FIELDS if entry[field] is None]
        if len(null_fields) == len(WEATHER_FIELDS):
            parsed_entries[day.isoformat()] = None
            continue
        if null_fields:
            raise _error(path, "部分字段为空：" + ", ".join(null_fields))
        low_c = entry["low_c"]
        high_c = entry["high_c"]
        if isinstance(low_c, bool) or not isinstance(low_c, int):
            raise _error(f"{path}.low_c", "必须是整数或 null")
        if isinstance(high_c, bool) or not isinstance(high_c, int):
            raise _error(f"{path}.high_c", "必须是整数或 null")
        if low_c > high_c:
            raise _error(path, "最低温不能高于最高温")
        parsed_entries[day.isoformat()] = PreviewWeather(
            forecast_date=day,
            low_c=low_c,
            high_c=high_c,
            wind_direction=_nonempty_string(
                entry["wind_direction"], f"{path}.wind_direction"
            ),
            wind_level=_nonempty_string(entry["wind_level"], f"{path}.wind_level"),
        )
    return parsed_entries.get(preview_date.isoformat())


def _parse_config(value: object) -> PreviewCredits:
    config = _object(
        value,
        "$config",
        required=("editors", "reviewers", "approvers"),
    )
    return PreviewCredits(
        editors=_names(config["editors"], "$config.editors"),
        reviewers=_names(config["reviewers"], "$config.reviewers"),
        approvers=_names(config["approvers"], "$config.approvers"),
    )


def parse_preview_bundle(
    source_value: object,
    weather_value: object,
    config_value: object,
    *,
    source_directory: str | Path | None = None,
) -> PreviewSourceData:
    """Assemble the three strict JSON inputs into render-ready preview data."""

    document = parse_preview_document(
        source_value,
        source_directory=source_directory,
    )
    return document.assemble(
        weather=_parse_weather_map(weather_value, document.preview_date),
        credits=_parse_config(config_value),
    )


def load_preview_bundle(
    source_path: str | Path,
    weather_path: str | Path,
    config_path: str | Path,
) -> PreviewSourceData:
    """Load and assemble source.json, weather.json, and config.json."""

    source = Path(source_path)
    return parse_preview_bundle(
        _load_json(source, stage="preview-bundle-source"),
        _load_json(weather_path, stage="preview-bundle-weather"),
        _load_json(config_path, stage="preview-bundle-config"),
        source_directory=source.resolve().parent,
    )


def parse_weather_for_date(value: object, preview_date: date) -> PreviewWeather | None:
    return _parse_weather_map(value, preview_date)


def parse_preview_config(value: object) -> PreviewCredits:
    return _parse_config(value)
