"""Safe, deterministic templates for typed football preview articles."""

from __future__ import annotations

import hashlib
import html as html_std
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path

from .errors import TemplateContractError
from .html_tools import sanitise_html
from .models import (
    PlayedMatch,
    PreviewMatch,
    PreviewSourceData,
    PreviewWeather,
    SeasonOutcome,
    validate_preview_source,
)


_PATH = r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)*"
_TOKEN = re.compile(
    rf"<!--\s*wx:(?:(?P<each>each)\s+(?P<each_path>{_PATH})\s+as\s+"
    rf"(?P<alias>[a-zA-Z_][\w]*)|(?P<empty>empty)|(?P<end>endeach))\s*-->"
    rf"|\{{\{{\s*(?P<value_path>{_PATH})(?:\s*\|\s*"
    rf"(?P<filter>[a-zA-Z_][\w]*))?\s*\}}\}}",
    re.S,
)
_UNRESOLVED = re.compile(r"\{\{|\}\}|<!--\s*wx:")
_TRIPLE_BRACE = re.compile(r"\{\{\{|\}\}\}")
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass(frozen=True)
class _TextNode:
    value: str


@dataclass(frozen=True)
class _ValueNode:
    path: str
    filter_name: str | None


@dataclass(frozen=True)
class _EachNode:
    path: str
    alias: str
    children: tuple[_Node, ...]
    empty_children: tuple[_Node, ...]


_Node = _TextNode | _ValueNode | _EachNode


@dataclass(frozen=True)
class _LoopState:
    index: int
    first: bool
    last: bool


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source

    def parse(self) -> tuple[_Node, ...]:
        if _TRIPLE_BRACE.search(self.source):
            raise self._error("不支持三花括号或富 HTML 占位符")
        nodes, position, marker = self._parse_until(
            0,
            inside_each=False,
            allow_empty=False,
        )
        if marker is not None or position != len(self.source):
            raise self._error("存在未配对的模板标记")
        return tuple(nodes)

    def _parse_until(
        self,
        position: int,
        *,
        inside_each: bool,
        allow_empty: bool,
    ) -> tuple[list[_Node], int, str | None]:
        nodes: list[_Node] = []
        while True:
            match = _TOKEN.search(self.source, position)
            if match is None:
                self._append_text(nodes, self.source[position:])
                if inside_each:
                    raise self._error("wx:each 缺少对应的 wx:endeach")
                return nodes, len(self.source), None

            self._append_text(nodes, self.source[position : match.start()])
            position = match.end()

            value_path = match.group("value_path")
            if value_path is not None:
                filter_name = match.group("filter")
                if filter_name is not None and filter_name not in _FILTERS:
                    raise self._error(f"未知格式化器 {filter_name!r}")
                nodes.append(_ValueNode(value_path, filter_name))
                continue

            if match.group("each") is not None:
                alias = match.group("alias")
                if alias in {"source", "config", "weekday_label", "loop"}:
                    raise self._error(f"循环别名 {alias!r} 是保留名称")
                children, position, marker = self._parse_until(
                    position,
                    inside_each=True,
                    allow_empty=True,
                )
                empty_children: list[_Node] = []
                if marker == "empty":
                    empty_children, position, marker = self._parse_until(
                        position,
                        inside_each=True,
                        allow_empty=False,
                    )
                if marker != "end":
                    raise self._error("wx:each 缺少对应的 wx:endeach")
                nodes.append(
                    _EachNode(
                        path=match.group("each_path"),
                        alias=alias,
                        children=tuple(children),
                        empty_children=tuple(empty_children),
                    )
                )
                continue

            if match.group("empty") is not None:
                if not inside_each or not allow_empty:
                    raise self._error("wx:empty 只能在 wx:each 中出现一次")
                return nodes, position, "empty"

            if not inside_each:
                raise self._error("wx:endeach 没有对应的 wx:each")
            return nodes, position, "end"

    def _append_text(self, nodes: list[_Node], value: str) -> None:
        if not value:
            return
        if _UNRESOLVED.search(value):
            raise self._error("包含无效或不受支持的模板标记")
        nodes.append(_TextNode(value))

    @staticmethod
    def _error(message: str) -> TemplateContractError:
        return TemplateContractError(message, stage="template-compile")


def _resolve(path: str, scopes: Sequence[Mapping[str, object]]) -> object:
    parts = path.split(".")
    first = parts[0]
    missing = object()
    value: object = missing
    for scope in reversed(scopes):
        if first in scope:
            value = scope[first]
            break
    if value is missing:
        raise TemplateContractError(
            f"无法解析模板路径 {path!r}",
            stage="template-render",
        )

    for part in parts[1:]:
        if part.startswith("_"):
            raise TemplateContractError(
                f"模板路径 {path!r} 不允许访问私有属性",
                stage="template-render",
            )
        if isinstance(value, Mapping):
            if part not in value:
                raise TemplateContractError(
                    f"模板路径 {path!r} 不存在",
                    stage="template-render",
                )
            value = value[part]
        elif is_dataclass(value) and part in {field.name for field in fields(value)}:
            value = getattr(value, part)
        else:
            raise TemplateContractError(
                f"模板路径 {path!r} 不存在",
                stage="template-render",
            )
    return value


def _weekday(value: date) -> str:
    if not isinstance(value, date):
        raise TypeError("weekday 需要 date 或 datetime")
    return _WEEKDAYS[value.weekday()]


def _date_md_weekday(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("date_md_weekday 需要 datetime")
    return f"{value.month}.{value.day} {_weekday(value)}"


def _time_hm(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("time_hm 需要 datetime")
    return value.strftime("%H:%M")


def _match_datetime(value: datetime) -> str:
    return f"{_date_md_weekday(value)} {_time_hm(value)}"


def _weather_summary(value: PreviewWeather | None) -> str:
    if value is None:
        return "待更新"
    if not isinstance(value, PreviewWeather):
        raise TypeError("weather_summary 需要 PreviewWeather 或 None")
    return f"{value.low_c}~{value.high_c}℃，{value.wind_direction}{value.wind_level}"


def _score_text(value: PlayedMatch) -> str:
    if not isinstance(value, PlayedMatch):
        raise TypeError("比分格式化器需要 PlayedMatch")
    if value.result_text is not None:
        return value.result_text
    score = f"{value.home_score}:{value.away_score}"
    if value.home_penalty is not None:
        score += f"（点球 {value.home_penalty}:{value.away_penalty}）"
    return score


def _result_line(value: PlayedMatch) -> str:
    if not isinstance(value, PlayedMatch):
        raise TypeError("result_line 需要 PlayedMatch")
    return f"{value.home.short_name}{_score_text(value)}{value.away.short_name}"


def _head_to_head_line(value: PlayedMatch) -> str:
    if not isinstance(value, PlayedMatch):
        raise TypeError("head_to_head_line 需要 PlayedMatch")
    labels = [item for item in (value.season, value.competition_label) if item]
    prefix = f"（{'-'.join(labels)}）" if labels else ""
    stage = value.stage or ""
    spacer = " " if prefix or stage else ""
    return f"{prefix}{stage}{spacer}{_result_line(value)}"


def _outcome_heading(value: SeasonOutcome) -> str:
    if not isinstance(value, SeasonOutcome):
        raise TypeError("outcome_heading 需要 SeasonOutcome")
    if value.competition_label is None:
        return value.season
    return f"{value.season}-{value.competition_label}"


def _join_names(value: Sequence[str]) -> str:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("join_names 需要字符串数组")
    return " ".join(str(name).strip() for name in value)


def _writers(value: Sequence[PreviewMatch]) -> str:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("writers 需要 PreviewMatch 数组")
    seen: set[str] = set()
    ordered: list[str] = []
    for match in value:
        if not isinstance(match, PreviewMatch):
            raise TypeError("writers 需要 PreviewMatch 数组")
        for raw_name in match.writers:
            name = raw_name.strip()
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return " ".join(ordered)


def _outcome_margin(value: bool) -> str:
    if not isinstance(value, bool):
        raise TypeError("outcome_margin 需要 loop.last")
    return "0" if value else "0 0 5px"


def _paragraph_margin(value: bool) -> str:
    if not isinstance(value, bool):
        raise TypeError("paragraph_margin 需要 loop.last")
    return "0" if value else "0 0 .6em"


def _team_name_width(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("team_name_width 需要球队名称")
    return "33%" if len(value.strip()) >= 14 else "100%"


_FILTERS = {
    "weekday": _weekday,
    "date_md_weekday": _date_md_weekday,
    "time_hm": _time_hm,
    "match_datetime": _match_datetime,
    "weather_summary": _weather_summary,
    "result_line": _result_line,
    "head_to_head_line": _head_to_head_line,
    "outcome_heading": _outcome_heading,
    "join_names": _join_names,
    "writers": _writers,
    "outcome_margin": _outcome_margin,
    "paragraph_margin": _paragraph_margin,
    "team_name_width": _team_name_width,
}


def _render_nodes(
    nodes: Sequence[_Node],
    scopes: Sequence[Mapping[str, object]],
) -> str:
    output: list[str] = []
    for node in nodes:
        if isinstance(node, _TextNode):
            output.append(node.value)
            continue
        if isinstance(node, _ValueNode):
            value = _resolve(node.path, scopes)
            if node.filter_name is not None:
                try:
                    value = _FILTERS[node.filter_name](value)  # type: ignore[arg-type]
                except (TypeError, ValueError) as exc:
                    raise TemplateContractError(
                        f"格式化 {node.path!r} 失败：{exc}",
                        stage="template-render",
                    ) from exc
            if value is None:
                raise TemplateContractError(
                    f"模板字段 {node.path!r} 不能是 None",
                    stage="template-render",
                )
            if is_dataclass(value) or isinstance(value, Mapping) or (
                isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            ):
                raise TemplateContractError(
                    f"模板字段 {node.path!r} 必须是标量或使用格式化器",
                    stage="template-render",
                )
            output.append(html_std.escape(str(value), quote=True))
            continue

        raw_items = _resolve(node.path, scopes)
        if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
            raise TemplateContractError(
                f"循环字段 {node.path!r} 必须是数组",
                stage="template-render",
            )
        if not raw_items:
            output.append(_render_nodes(node.empty_children, scopes))
            continue
        last_index = len(raw_items) - 1
        for index, item in enumerate(raw_items):
            child_scope: Mapping[str, object] = {
                node.alias: item,
                "loop": _LoopState(
                    index=index + 1,
                    first=index == 0,
                    last=index == last_index,
                ),
            }
            output.append(_render_nodes(node.children, (*scopes, child_scope)))
    return "".join(output)


class PreviewTemplate:
    """A compiled HTML template that accepts only typed preview source data."""

    def __init__(self, *, nodes: tuple[_Node, ...], source: str, version: str) -> None:
        self._nodes = nodes
        self.source = source
        self.version = version

    @classmethod
    def compile(cls, body_template: str, *, version: str | None = None) -> "PreviewTemplate":
        if not isinstance(body_template, str) or not body_template.strip():
            raise TemplateContractError(
                "body template must be a non-empty string",
                stage="template-compile",
            )
        nodes = _Parser(body_template).parse()
        if version is None:
            version = "sha256:" + hashlib.sha256(body_template.encode("utf-8")).hexdigest()[:16]
        return cls(nodes=nodes, source=body_template, version=version)

    def render_body(
        self,
        source: PreviewSourceData,
    ) -> tuple[str, str]:
        validate_preview_source(source)
        config = source.column
        weekday_label = (
            config.weekday_label_override.strip()
            if config.weekday_label_override is not None
            else _weekday(source.preview_date)
        )
        scopes: tuple[Mapping[str, object], ...] = (
            {
                "source": source,
                "config": config,
                "weekday_label": weekday_label,
            },
        )
        body = _render_nodes(self._nodes, scopes)
        normalised_body = sanitise_html(body)
        title = (
            f"【{config.competition_short_name.strip()}{weekday_label}前瞻】"
            f"|| {source.headline.strip()}"
        )
        return title, normalised_body


def load_preview_template(
    path: str | Path,
    *,
    version: str | None = None,
) -> PreviewTemplate:
    return PreviewTemplate.compile(
        Path(path).read_text(encoding="utf-8"),
        version=version,
    )
