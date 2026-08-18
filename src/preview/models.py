"""Clean, typed source models for football preview articles."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from .errors import PreviewValidationError

CHINA_UTC_OFFSET = timedelta(hours=8)


@dataclass(frozen=True)
class PreviewWeather:
    condition: str
    low_c: int
    high_c: int
    wind_direction: str
    wind_level: str


@dataclass(frozen=True)
class TeamRef:
    team_id: int
    name: str
    short_name: str


@dataclass(frozen=True)
class SeasonOutcome:
    season: str
    competition_label: str | None
    outcome: str


@dataclass(frozen=True)
class PlayedMatch:
    game_id: int
    home: TeamRef
    away: TeamRef
    home_score: int | None = None
    away_score: int | None = None
    home_penalty: int | None = None
    away_penalty: int | None = None
    result_text: str | None = None
    season: str | None = None
    competition_label: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class PreviewTeam:
    team_id: int
    name: str
    short_name: str
    previous_outcomes: tuple[SeasonOutcome, ...] = ()
    current_results: tuple[PlayedMatch, ...] = ()


@dataclass(frozen=True)
class PreviewMatch:
    game_id: int
    competition_name: str
    stage: str
    kickoff: datetime
    venue: str
    home: PreviewTeam
    away: PreviewTeam
    head_to_head: tuple[PlayedMatch, ...]
    preview_paragraphs: tuple[str, ...]
    writers: tuple[str, ...]


@dataclass(frozen=True)
class PreviewCredits:
    editors: tuple[str, ...]
    reviewers: tuple[str, ...]
    approvers: tuple[str, ...]


@dataclass(frozen=True)
class PreviewSourceData:
    column: PreviewColumnConfig
    preview_date: date
    headline: str
    weather: PreviewWeather | None
    matches: tuple[PreviewMatch, ...]
    credits: PreviewCredits

    @property
    def ordered_writers(self) -> tuple[str, ...]:
        """Return trimmed writers in first-appearance order across all matches."""

        seen: set[str] = set()
        ordered: list[str] = []
        for match in self.matches:
            for raw_name in match.writers:
                name = raw_name.strip()
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
        return tuple(ordered)


@dataclass(frozen=True)
class PreviewColumnConfig:
    competition_full_name: str
    competition_short_name: str
    weekday_label_override: str | None = None


def _error(path: str, message: str, *, stage: str) -> PreviewValidationError:
    return PreviewValidationError(f"{path}: {message}", stage=stage)


class _ObjectReader:
    def __init__(
        self,
        value: object,
        *,
        path: str,
        required: Sequence[str],
        optional: Sequence[str] = (),
        stage: str,
    ) -> None:
        if not isinstance(value, Mapping):
            raise _error(path, "必须是 JSON 对象", stage=stage)
        self.value = value
        self.path = path
        self.stage = stage
        allowed = set(required) | set(optional)
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise _error(
                path,
                "包含未知字段：" + ", ".join(unknown),
                stage=stage,
            )
        missing = sorted(key for key in required if key not in value)
        if missing:
            raise _error(
                path,
                "缺少必填字段：" + ", ".join(missing),
                stage=stage,
            )

    def get(self, name: str, default: object = None) -> object:
        return self.value.get(name, default)

    def child_path(self, name: str) -> str:
        return f"{self.path}.{name}"


def _string(value: object, path: str, *, stage: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "必须是非空字符串", stage=stage)
    return value.strip()


def _optional_string(value: object, path: str, *, stage: str) -> str | None:
    if value is None:
        return None
    return _string(value, path, stage=stage)


def _integer(
    value: object,
    path: str,
    *,
    stage: str,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(path, "必须是整数", stage=stage)
    if minimum is not None and value < minimum:
        raise _error(path, f"必须大于或等于 {minimum}", stage=stage)
    return value


def _optional_integer(
    value: object,
    path: str,
    *,
    stage: str,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _integer(value, path, stage=stage, minimum=minimum)


def _game_id(value: object, path: str, *, stage: str) -> int:
    parsed = _integer(value, path, stage=stage)
    if parsed == -1 or parsed >= 1:
        return parsed
    raise _error(path, "必须是正整数；未知时使用 -1", stage=stage)


def _date(value: object, path: str, *, stage: str) -> date:
    raw = _string(value, path, stage=stage)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _error(path, "必须是 YYYY-MM-DD 日期", stage=stage) from exc


def _datetime(value: object, path: str, *, stage: str) -> datetime:
    raw = _string(value, path, stage=stage)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _error(path, "必须是 ISO 8601 日期时间", stage=stage) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != CHINA_UTC_OFFSET:
        raise _error(path, "必须显式使用 +08:00 时区", stage=stage)
    return parsed


T = TypeVar("T")


def _tuple_of(
    value: object,
    path: str,
    parser: Callable[[object, str, str], T],
    *,
    stage: str,
    minimum_items: int = 0,
) -> tuple[T, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(path, "必须是数组", stage=stage)
    if len(value) < minimum_items:
        raise _error(path, f"至少需要 {minimum_items} 项", stage=stage)
    return tuple(
        parser(item, f"{path}[{index}]", stage) for index, item in enumerate(value)
    )


def _parse_string(value: object, path: str, stage: str) -> str:
    return _string(value, path, stage=stage)


def _parse_team_ref(value: object, path: str, stage: str) -> TeamRef:
    obj = _ObjectReader(
        value,
        path=path,
        required=("team_id", "name", "short_name"),
        stage=stage,
    )
    return TeamRef(
        team_id=_integer(
            obj.get("team_id"), obj.child_path("team_id"), stage=stage, minimum=1
        ),
        name=_string(obj.get("name"), obj.child_path("name"), stage=stage),
        short_name=_string(
            obj.get("short_name"), obj.child_path("short_name"), stage=stage
        ),
    )


def _parse_outcome(value: object, path: str, stage: str) -> SeasonOutcome:
    obj = _ObjectReader(
        value,
        path=path,
        required=("season", "outcome"),
        optional=("competition_label",),
        stage=stage,
    )
    return SeasonOutcome(
        season=_string(obj.get("season"), obj.child_path("season"), stage=stage),
        competition_label=_optional_string(
            obj.get("competition_label"),
            obj.child_path("competition_label"),
            stage=stage,
        ),
        outcome=_string(obj.get("outcome"), obj.child_path("outcome"), stage=stage),
    )


def _parse_played_match(value: object, path: str, stage: str) -> PlayedMatch:
    obj = _ObjectReader(
        value,
        path=path,
        required=("game_id", "home", "away"),
        optional=(
            "season",
            "competition_label",
            "stage",
            "home_score",
            "away_score",
            "home_penalty",
            "away_penalty",
            "result_text",
        ),
        stage=stage,
    )
    return PlayedMatch(
        game_id=_game_id(obj.get("game_id"), obj.child_path("game_id"), stage=stage),
        home=_parse_team_ref(obj.get("home"), obj.child_path("home"), stage),
        away=_parse_team_ref(obj.get("away"), obj.child_path("away"), stage),
        home_score=_optional_integer(
            obj.get("home_score"), obj.child_path("home_score"), stage=stage, minimum=0
        ),
        away_score=_optional_integer(
            obj.get("away_score"), obj.child_path("away_score"), stage=stage, minimum=0
        ),
        home_penalty=_optional_integer(
            obj.get("home_penalty"),
            obj.child_path("home_penalty"),
            stage=stage,
            minimum=0,
        ),
        away_penalty=_optional_integer(
            obj.get("away_penalty"),
            obj.child_path("away_penalty"),
            stage=stage,
            minimum=0,
        ),
        result_text=_optional_string(
            obj.get("result_text"), obj.child_path("result_text"), stage=stage
        ),
        season=_optional_string(
            obj.get("season"), obj.child_path("season"), stage=stage
        ),
        competition_label=_optional_string(
            obj.get("competition_label"),
            obj.child_path("competition_label"),
            stage=stage,
        ),
        stage=_optional_string(obj.get("stage"), obj.child_path("stage"), stage=stage),
    )


def _parse_team(value: object, path: str, stage: str) -> PreviewTeam:
    obj = _ObjectReader(
        value,
        path=path,
        required=(
            "team_id",
            "name",
            "short_name",
            "previous_outcomes",
            "current_results",
        ),
        stage=stage,
    )
    return PreviewTeam(
        team_id=_integer(
            obj.get("team_id"), obj.child_path("team_id"), stage=stage, minimum=1
        ),
        name=_string(obj.get("name"), obj.child_path("name"), stage=stage),
        short_name=_string(
            obj.get("short_name"), obj.child_path("short_name"), stage=stage
        ),
        previous_outcomes=_tuple_of(
            obj.get("previous_outcomes"),
            obj.child_path("previous_outcomes"),
            _parse_outcome,
            stage=stage,
        ),
        current_results=_tuple_of(
            obj.get("current_results"),
            obj.child_path("current_results"),
            _parse_played_match,
            stage=stage,
        ),
    )


def _parse_preview_match(value: object, path: str, stage: str) -> PreviewMatch:
    obj = _ObjectReader(
        value,
        path=path,
        required=(
            "game_id",
            "competition_name",
            "stage",
            "kickoff",
            "venue",
            "home",
            "away",
            "head_to_head",
            "preview_paragraphs",
            "writers",
        ),
        stage=stage,
    )
    return PreviewMatch(
        game_id=_game_id(obj.get("game_id"), obj.child_path("game_id"), stage=stage),
        competition_name=_string(
            obj.get("competition_name"), obj.child_path("competition_name"), stage=stage
        ),
        stage=_string(obj.get("stage"), obj.child_path("stage"), stage=stage),
        kickoff=_datetime(obj.get("kickoff"), obj.child_path("kickoff"), stage=stage),
        venue=_string(obj.get("venue"), obj.child_path("venue"), stage=stage),
        home=_parse_team(obj.get("home"), obj.child_path("home"), stage),
        away=_parse_team(obj.get("away"), obj.child_path("away"), stage),
        head_to_head=_tuple_of(
            obj.get("head_to_head"),
            obj.child_path("head_to_head"),
            _parse_played_match,
            stage=stage,
        ),
        preview_paragraphs=_tuple_of(
            obj.get("preview_paragraphs"),
            obj.child_path("preview_paragraphs"),
            _parse_string,
            stage=stage,
            minimum_items=1,
        ),
        writers=_tuple_of(
            obj.get("writers"),
            obj.child_path("writers"),
            _parse_string,
            stage=stage,
            minimum_items=1,
        ),
    )


def _parse_column_config(value: object, path: str, stage: str) -> PreviewColumnConfig:
    obj = _ObjectReader(
        value,
        path=path,
        required=("competition_full_name", "competition_short_name"),
        optional=("weekday_label_override",),
        stage=stage,
    )
    return PreviewColumnConfig(
        competition_full_name=_string(
            obj.get("competition_full_name"),
            obj.child_path("competition_full_name"),
            stage=stage,
        ),
        competition_short_name=_string(
            obj.get("competition_short_name"),
            obj.child_path("competition_short_name"),
            stage=stage,
        ),
        weekday_label_override=_optional_string(
            obj.get("weekday_label_override"),
            obj.child_path("weekday_label_override"),
            stage=stage,
        ),
    )


def _parse_preview_fields(
    *,
    column: object,
    preview_date: object,
    headline: object,
    matches: object,
) -> tuple[PreviewColumnConfig, date, str, tuple[PreviewMatch, ...]]:
    stage = "preview-source"
    return (
        _parse_column_config(column, "$.column", stage),
        _date(preview_date, "$.preview_date", stage=stage),
        _string(headline, "$.headline", stage=stage),
        _tuple_of(
            matches,
            "$.matches",
            _parse_preview_match,
            stage=stage,
            minimum_items=1,
        ),
    )


def _validate_nonempty(value: str, path: str, *, stage: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "必须是非空字符串", stage=stage)


def _validate_positive_id(value: int, path: str, *, stage: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _error(path, "必须是正整数", stage=stage)


def _validate_game_id(value: int, path: str, *, stage: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not (value == -1 or value >= 1)
    ):
        raise _error(path, "必须是正整数；未知时使用 -1", stage=stage)


def _validate_names(values: Sequence[str], path: str, *, stage: str) -> None:
    if not values:
        raise _error(path, "至少需要 1 项", stage=stage)
    for index, value in enumerate(values):
        _validate_nonempty(value, f"{path}[{index}]", stage=stage)


def _validate_team_ref(team: TeamRef, path: str, *, stage: str) -> None:
    _validate_positive_id(team.team_id, f"{path}.team_id", stage=stage)
    _validate_nonempty(team.name, f"{path}.name", stage=stage)
    _validate_nonempty(team.short_name, f"{path}.short_name", stage=stage)


def _validate_played_match(match: PlayedMatch, path: str, *, stage: str) -> None:
    _validate_game_id(match.game_id, f"{path}.game_id", stage=stage)
    _validate_team_ref(match.home, f"{path}.home", stage=stage)
    _validate_team_ref(match.away, f"{path}.away", stage=stage)
    if match.home.team_id == match.away.team_id:
        raise _error(path, "主客队不能相同", stage=stage)

    score_pair = (match.home_score is None, match.away_score is None)
    if score_pair[0] != score_pair[1]:
        raise _error(path, "常规比分必须同时提供主队和客队得分", stage=stage)
    penalty_pair = (match.home_penalty is None, match.away_penalty is None)
    if penalty_pair[0] != penalty_pair[1]:
        raise _error(path, "点球比分必须同时提供主队和客队得分", stage=stage)
    if match.home_penalty is not None and match.home_score is None:
        raise _error(path, "点球比分需要同时提供常规比分", stage=stage)
    if match.home_score is None:
        raise _error(path, "完赛记录必须提供完整比分", stage=stage)
    from .template import format_result_text

    expected_result_text = format_result_text(
        match.home_score,
        match.away_score,
        match.home_penalty,
        match.away_penalty,
    )
    if match.result_text is not None and match.result_text != expected_result_text:
        raise _error(
            f"{path}.result_text",
            "必须与结构化比分一致",
            stage=stage,
        )
    for field in ("home_score", "away_score", "home_penalty", "away_penalty"):
        value = getattr(match, field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise _error(f"{path}.{field}", "必须是非负整数", stage=stage)
    for field in ("season", "competition_label", "stage", "result_text"):
        value = getattr(match, field)
        if value is not None:
            _validate_nonempty(value, f"{path}.{field}", stage=stage)


def _validate_preview_team(team: PreviewTeam, path: str, *, stage: str) -> None:
    _validate_positive_id(team.team_id, f"{path}.team_id", stage=stage)
    _validate_nonempty(team.name, f"{path}.name", stage=stage)
    _validate_nonempty(team.short_name, f"{path}.short_name", stage=stage)
    for index, outcome in enumerate(team.previous_outcomes):
        outcome_path = f"{path}.previous_outcomes[{index}]"
        _validate_nonempty(outcome.season, f"{outcome_path}.season", stage=stage)
        if outcome.competition_label is not None:
            _validate_nonempty(
                outcome.competition_label,
                f"{outcome_path}.competition_label",
                stage=stage,
            )
        _validate_nonempty(outcome.outcome, f"{outcome_path}.outcome", stage=stage)
    for index, result in enumerate(team.current_results):
        _validate_played_match(result, f"{path}.current_results[{index}]", stage=stage)


def validate_preview_source(source: PreviewSourceData) -> None:
    """Validate both decoded and directly instantiated source objects."""

    stage = "preview-source"
    _validate_preview_config(source.column, path="$.column", stage=stage)
    if not isinstance(source.preview_date, date) or isinstance(
        source.preview_date, datetime
    ):
        raise _error("$.preview_date", "必须是 date", stage=stage)
    _validate_nonempty(source.headline, "$.headline", stage=stage)
    if not source.matches:
        raise _error("$.matches", "至少需要 1 项", stage=stage)

    if source.weather is not None:
        weather = source.weather
        _validate_nonempty(weather.condition, "$.weather.condition", stage=stage)
        if isinstance(weather.low_c, bool) or not isinstance(weather.low_c, int):
            raise _error("$.weather.low_c", "必须是整数", stage=stage)
        if isinstance(weather.high_c, bool) or not isinstance(weather.high_c, int):
            raise _error("$.weather.high_c", "必须是整数", stage=stage)
        if weather.low_c > weather.high_c:
            raise _error("$.weather", "最低温不能高于最高温", stage=stage)
        _validate_nonempty(
            weather.wind_direction, "$.weather.wind_direction", stage=stage
        )
        _validate_nonempty(weather.wind_level, "$.weather.wind_level", stage=stage)

    for index, match in enumerate(source.matches):
        path = f"$.matches[{index}]"
        _validate_game_id(match.game_id, f"{path}.game_id", stage=stage)
        _validate_nonempty(
            match.competition_name, f"{path}.competition_name", stage=stage
        )
        _validate_nonempty(match.stage, f"{path}.stage", stage=stage)
        _validate_nonempty(match.venue, f"{path}.venue", stage=stage)
        if not isinstance(match.kickoff, datetime):
            raise _error(f"{path}.kickoff", "必须是 datetime", stage=stage)
        if (
            match.kickoff.tzinfo is None
            or match.kickoff.utcoffset() != CHINA_UTC_OFFSET
        ):
            raise _error(f"{path}.kickoff", "必须显式使用 +08:00 时区", stage=stage)
        if match.kickoff.date() != source.preview_date:
            raise _error(f"{path}.kickoff", "日期必须与 preview_date 相同", stage=stage)
        _validate_preview_team(match.home, f"{path}.home", stage=stage)
        _validate_preview_team(match.away, f"{path}.away", stage=stage)
        if match.home.team_id == match.away.team_id:
            raise _error(path, "主客队不能相同", stage=stage)
        for h2h_index, result in enumerate(match.head_to_head):
            _validate_played_match(
                result, f"{path}.head_to_head[{h2h_index}]", stage=stage
            )
        _validate_names(
            match.preview_paragraphs, f"{path}.preview_paragraphs", stage=stage
        )
        _validate_names(match.writers, f"{path}.writers", stage=stage)

    _validate_names(source.credits.editors, "$.credits.editors", stage=stage)
    _validate_names(source.credits.reviewers, "$.credits.reviewers", stage=stage)
    _validate_names(source.credits.approvers, "$.credits.approvers", stage=stage)


def _validate_preview_config(
    config: PreviewColumnConfig,
    *,
    path: str = "$.column",
    stage: str = "preview-source",
) -> None:
    _validate_nonempty(
        config.competition_full_name,
        f"{path}.competition_full_name",
        stage=stage,
    )
    _validate_nonempty(
        config.competition_short_name,
        f"{path}.competition_short_name",
        stage=stage,
    )
    if config.weekday_label_override is not None:
        _validate_nonempty(
            config.weekday_label_override,
            f"{path}.weekday_label_override",
            stage=stage,
        )


def _load_json(path: str | Path, *, stage: str) -> object:
    source_path = Path(path)
    try:
        return json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _error("$", f"无法读取 {source_path}", stage=stage) from exc
    except json.JSONDecodeError as exc:
        raise _error(
            "$",
            f"JSON 格式错误（第 {exc.lineno} 行，第 {exc.colno} 列）",
            stage=stage,
        ) from exc
