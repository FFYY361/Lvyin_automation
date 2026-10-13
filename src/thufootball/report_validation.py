"""Validate and filter game events before rendering a match report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from typing import Literal

from .models import GameDetail, GameEvent, GameEventIssue

_LINEUP_EVENTS = frozenset({"START", "APPEARANCE"})
_SUBSTITUTION_EVENTS = frozenset({"OFF", "ON"})
_SCORING_EVENTS = frozenset({"GOAL", "OWNGOAL", "PENALTY", "MISSPENALTY"})
_PENALTY_SHOOTOUT_EVENTS = frozenset(
    {
        "PENALTY",
        "MISSPENALTY",
        "YELLOWCARD",
        "SECONDYELLOWCARD",
        "REDCARD",
    }
)

_Side = Literal["home", "away"]
_TimeKey = tuple[_Side, bool, int | None, int]


def _player_label(event: GameEvent) -> str:
    name = event.player_name.strip()
    if event.kit_number >= 0:
        return f"{event.kit_number}号{name or '球员'}"
    return name or "未填写姓名的球员"


def _event_label(event: GameEvent) -> str:
    return {
        "START": "首发",
        "APPEARANCE": "出场",
        "GOAL": "进球",
        "OWNGOAL": "乌龙球",
        "PENALTY": "点球",
        "MISSPENALTY": "点球罚失",
        "ON": "上场",
        "OFF": "下场",
        "YELLOWCARD": "黄牌",
        "SECONDYELLOWCARD": "第二张黄牌",
        "REDCARD": "红牌",
    }.get(event.event_type.upper(), "未知事件")


def report_has_blocking_issues(issues: Iterable[GameEventIssue]) -> bool:
    return any(issue.severity in {"warning", "error"} for issue in issues)


def format_report_issue(issue: GameEventIssue) -> str:
    return " · ".join(
        value for value in (issue.team_name, issue.time_label, issue.message) if value
    )


def _issue(
    severity: Literal["info", "warning"],
    code: str,
    message: str,
    *,
    events: tuple[GameEvent, ...] = (),
    player_id: int | None = None,
    side: _Side | None = None,
    minute: int | None = None,
    stoppage_minute: int | None = None,
    infer_player_from_event: bool = True,
    infer_time_from_event: bool = True,
) -> GameEventIssue:
    first = events[0] if events else None
    time_label = None
    if code in {"duplicate_start", "lineup_under_capacity", "lineup_over_capacity"}:
        time_label = "0 分钟"
    elif first and first.during_penalty_shootout:
        time_label = "点球大战"
    elif first and first.minute is not None:
        time_label = (
            f"{first.minute}+{first.stoppage_minute} 分钟"
            if first.stoppage_minute
            else f"{first.minute} 分钟"
        )
    return GameEventIssue(
        severity=severity,
        time_label=time_label,
        code=code,
        message=message,
        event_ids=tuple(event.event_id for event in events),
        player_id=player_id
        if not infer_player_from_event
        else (
            player_id
            if player_id is not None
            else (first.player_id if first is not None else None)
        ),
        side=side if side is not None else (first.side if first is not None else None),
        minute=minute
        if not infer_time_from_event
        else (
            minute
            if minute is not None
            else (first.minute if first is not None else None)
        ),
        stoppage_minute=(
            stoppage_minute
            if not infer_time_from_event
            else (
                stoppage_minute
                if stoppage_minute is not None
                else (first.stoppage_minute if first is not None else None)
            )
        ),
    )


def _time_key(event: GameEvent) -> _TimeKey:
    return (
        event.side,
        event.during_penalty_shootout,
        event.minute,
        event.stoppage_minute,
    )


def _filter_events(
    detail: GameDetail,
    issues: list[GameEventIssue],
) -> tuple[GameEvent, ...]:
    retained: list[GameEvent] = []
    for event in detail.events:
        if not event.valid:
            continue
        if event.event_type.upper() == "ASSIST":
            continue
        retained.append(event)
    return tuple(retained)


def _initial_lineups(
    detail: GameDetail,
    events: tuple[GameEvent, ...],
    issues: list[GameEventIssue],
) -> dict[_Side, set[int]]:
    starters: dict[_Side, list[GameEvent]] = {"home": [], "away": []}
    first_start_by_player: dict[int, GameEvent] = {}

    for event in events:
        if event.event_type.upper() != "START":
            continue
        starters[event.side].append(event)
        previous = first_start_by_player.get(event.player_id)
        if previous is not None:
            issues.append(
                _issue(
                    "warning",
                    "duplicate_start",
                    f"球员 {_player_label(event)}在首发中重复出现",
                    events=(previous, event),
                    player_id=event.player_id,
                    side=event.side,
                    minute=event.minute,
                    stoppage_minute=event.stoppage_minute,
                )
            )
        else:
            first_start_by_player[event.player_id] = event

    on_field: dict[_Side, set[int]] = {"home": set(), "away": set()}
    for side, side_events in starters.items():
        unique_players = {event.player_id for event in side_events}
        on_field[side].update(unique_players)
        player_count = len(unique_players)
        if player_count < detail.players_per_side:
            issues.append(
                _issue(
                    "info",
                    "lineup_under_capacity",
                    (f"仅{player_count}人首发，预期{detail.players_per_side}人"),
                    events=tuple(side_events),
                    side=side,
                    minute=None,
                    stoppage_minute=None,
                    infer_player_from_event=False,
                    infer_time_from_event=False,
                )
            )
        elif player_count > detail.players_per_side and detail.players_per_side != 5:
            issues.append(
                _issue(
                    "warning",
                    "lineup_over_capacity",
                    f"首发共有{player_count}人，超过规定的{detail.players_per_side}人",
                    events=tuple(side_events),
                    side=side,
                    minute=None,
                    stoppage_minute=None,
                    infer_player_from_event=False,
                    infer_time_from_event=False,
                )
            )
    return on_field


def _check_same_time_events(
    events: tuple[GameEvent, ...],
    issues: list[GameEventIssue],
) -> None:
    groups: dict[_TimeKey, list[GameEvent]] = {}
    for event in events:
        if event.event_type.upper() in _LINEUP_EVENTS:
            continue
        groups.setdefault(_time_key(event), []).append(event)

    for (side, _, minute, stoppage_minute), grouped_events in groups.items():
        has_substitution = False
        semantic_event_count = 0
        for event in grouped_events:
            if event.event_type.upper() in _SUBSTITUTION_EVENTS:
                if not has_substitution:
                    semantic_event_count += 1
                    has_substitution = True
            else:
                semantic_event_count += 1
        if semantic_event_count > 1:
            issues.append(
                _issue(
                    "info",
                    "multiple_events_same_time",
                    "该时间出现多个事件，请确认先后顺序正确",
                    events=tuple(grouped_events),
                    side=side,
                    minute=minute,
                    stoppage_minute=stoppage_minute,
                    player_id=None,
                    infer_player_from_event=False,
                )
            )


def _check_substitution_shapes(
    events: tuple[GameEvent, ...],
    issues: list[GameEventIssue],
) -> set[_TimeKey]:
    groups: dict[_TimeKey, list[GameEvent]] = {}
    for event in events:
        if event.event_type.upper() in _SUBSTITUTION_EVENTS:
            groups.setdefault(_time_key(event), []).append(event)

    invalid_groups: set[_TimeKey] = set()
    for (side, _, minute, stoppage_minute), grouped_events in groups.items():
        group_key = _time_key(grouped_events[0])
        event_types = [event.event_type.upper() for event in grouped_events]
        off_count = event_types.count("OFF")
        on_count = event_types.count("ON")
        if off_count != on_count:
            invalid_groups.add(group_key)
            issues.append(
                _issue(
                    "warning",
                    "substitution_unbalanced",
                    (
                        f"该时间有{off_count}人下场、{on_count}人上场，人数不一致，请核对换人记录"
                    ),
                    events=tuple(grouped_events),
                    side=side,
                    minute=minute,
                    stoppage_minute=stoppage_minute,
                    player_id=None,
                    infer_player_from_event=False,
                )
            )
        first_on = next(
            (
                index
                for index, event_type in enumerate(event_types)
                if event_type == "ON"
            ),
            len(event_types),
        )
        if "OFF" in event_types[first_on:]:
            invalid_groups.add(group_key)
            issues.append(
                _issue(
                    "warning",
                    "substitution_order",
                    "该时间的上场事件之后仍有下场事件，请调整为先下场、后上场",
                    events=tuple(grouped_events),
                    side=side,
                    minute=minute,
                    stoppage_minute=stoppage_minute,
                    player_id=None,
                    infer_player_from_event=False,
                )
            )
    return invalid_groups


def _dismiss(player_id: int, on_field: dict[_Side, set[int]]) -> None:
    on_field["home"].discard(player_id)
    on_field["away"].discard(player_id)


def _check_event_state(
    events: tuple[GameEvent, ...],
    on_field: dict[_Side, set[int]],
    invalid_substitution_groups: set[_TimeKey],
    issues: list[GameEventIssue],
) -> None:
    yellow_counts: defaultdict[int, int] = defaultdict(int)
    red_counts: defaultdict[int, int] = defaultdict(int)
    dismissed: set[int] = set()
    unreliable_field_state: set[_Side] = set()

    for event in events:
        event_type = event.event_type.upper()

        if event.during_penalty_shootout and event_type not in _PENALTY_SHOOTOUT_EVENTS:
            issues.append(
                _issue(
                    "warning",
                    "invalid_penalty_shootout_event",
                    f"点球大战中出现“{_event_label(event)}”记录，请核对事件",
                    events=(event,),
                )
            )

        was_dismissed = event.player_id in dismissed
        if was_dismissed:
            issues.append(
                _issue(
                    "warning",
                    "dismissed_player_event",
                    f"球员 {_player_label(event)}被罚下后仍有“{_event_label(event)}”记录，请核对事件",
                    events=(event,),
                )
            )

        if event_type in _LINEUP_EVENTS:
            continue

        if event_type == "YELLOWCARD":
            yellow_counts[event.player_id] += 1
            if yellow_counts[event.player_id] > 2:
                issues.append(
                    _issue(
                        "warning",
                        "too_many_yellow_cards",
                        f"球员 {_player_label(event)}累计有{yellow_counts[event.player_id]}张黄牌，超过2张，请核对事件",
                        events=(event,),
                    )
                )
            if not was_dismissed and yellow_counts[event.player_id] == 2:
                dismissed.add(event.player_id)
                _dismiss(event.player_id, on_field)
            continue

        if event_type == "SECONDYELLOWCARD":
            previous_count = yellow_counts[event.player_id]
            if previous_count != 1:
                issues.append(
                    _issue(
                        "warning",
                        "invalid_second_yellow",
                        (
                            f"球员 {_player_label(event)}有第二张黄牌记录，但此前黄牌数量为{previous_count}，预期1张"
                        ),
                        events=(event,),
                    )
                )
            yellow_counts[event.player_id] = max(2, previous_count + 1)
            if yellow_counts[event.player_id] > 2:
                issues.append(
                    _issue(
                        "warning",
                        "too_many_yellow_cards",
                        f"球员 {_player_label(event)}累计有{yellow_counts[event.player_id]}张黄牌，超过2张，请核对事件",
                        events=(event,),
                    )
                )
            if not was_dismissed:
                dismissed.add(event.player_id)
                _dismiss(event.player_id, on_field)
            continue

        if event_type == "REDCARD":
            red_counts[event.player_id] += 1
            if red_counts[event.player_id] > 1:
                issues.append(
                    _issue(
                        "warning",
                        "too_many_red_cards",
                        f"球员 {_player_label(event)}累计有{red_counts[event.player_id]}张红牌，请核对事件",
                        events=(event,),
                    )
                )
            if not was_dismissed:
                dismissed.add(event.player_id)
                _dismiss(event.player_id, on_field)
            continue

        if was_dismissed:
            continue

        if event_type in _SUBSTITUTION_EVENTS:
            if _time_key(event) in invalid_substitution_groups:
                unreliable_field_state.add(event.side)
                continue
            if event.side in unreliable_field_state:
                continue
            if event_type == "OFF":
                if event.player_id not in on_field[event.side]:
                    issues.append(
                        _issue(
                            "warning",
                            "off_player_not_on_field",
                            f"球员 {_player_label(event)}不在场上，却有下场记录，请核对首发和换人记录",
                            events=(event,),
                        )
                    )
                    unreliable_field_state.add(event.side)
                else:
                    on_field[event.side].remove(event.player_id)
            elif event.player_id in on_field[event.side]:
                issues.append(
                    _issue(
                        "warning",
                        "on_player_already_on_field",
                        f"球员 {_player_label(event)}已在场上，却再次上场，请核对换人记录",
                        events=(event,),
                    )
                )
                unreliable_field_state.add(event.side)
            else:
                on_field[event.side].add(event.player_id)
            continue

        if (
            event.side not in unreliable_field_state
            and not event.during_penalty_shootout
            and event_type in _SCORING_EVENTS
            and event.player_id not in on_field[event.side]
        ):
            issues.append(
                _issue(
                    "warning",
                    "scoring_player_not_on_field",
                    f"球员 {_player_label(event)}不在场上，却有“{_event_label(event)}”记录，请核对事件",
                    events=(event,),
                )
            )


def validate_game_events(
    detail: GameDetail,
) -> tuple[GameDetail, tuple[GameEventIssue, ...]]:
    """Return filtered events and non-blocking single-report diagnostics."""

    issues: list[GameEventIssue] = []
    events = _filter_events(detail, issues)
    filtered_detail = replace(detail, events=events)
    on_field = _initial_lineups(filtered_detail, events, issues)
    _check_same_time_events(events, issues)
    invalid_substitution_groups = _check_substitution_shapes(events, issues)
    _check_event_state(events, on_field, invalid_substitution_groups, issues)

    all_issues = tuple(issues)
    return filtered_detail, all_issues
