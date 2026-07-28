"""Validate and filter game events before rendering a match report."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Literal

from .errors import ReportValidationError
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
_TimeKey = tuple[_Side, bool, int, int]


def _issue(
    severity: Literal["warning", "error"],
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
    return GameEventIssue(
        severity=severity,
        code=code,
        message=message,
        event_ids=tuple(event.event_id for event in events),
        player_id=player_id if not infer_player_from_event else (
            player_id
            if player_id is not None
            else (first.player_id if first is not None else None)
        ),
        side=side if side is not None else (
            first.side if first is not None else None
        ),
        minute=minute if not infer_time_from_event else (
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
            issues.append(
                _issue(
                    "warning",
                    "invalid_event_ignored",
                    "Invalid event was ignored before report validation.",
                    events=(event,),
                )
            )
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
                    "error",
                    "duplicate_start",
                    "Player appears in more than one START event.",
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
                    "warning",
                    "lineup_under_capacity",
                    (
                        f"{side} has {player_count} START players; "
                        f"expected {detail.players_per_side}."
                    ),
                    events=tuple(side_events),
                    side=side,
                    minute=None,
                    stoppage_minute=None,
                    infer_player_from_event=False,
                    infer_time_from_event=False,
                )
            )
        elif (
            player_count > detail.players_per_side
            and detail.players_per_side != 5
        ):
            issues.append(
                _issue(
                    "error",
                    "lineup_over_capacity",
                    (
                        f"{side} has {player_count} START players; "
                        f"expected {detail.players_per_side}."
                    ),
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
                    "warning",
                    "multiple_events_same_time",
                    "Team has multiple semantic events at the same time.",
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
                    "error",
                    "substitution_unbalanced",
                    (
                        "Substitution must contain the same number of "
                        "OFF and ON events."
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
            (index for index, event_type in enumerate(event_types)
             if event_type == "ON"),
            len(event_types),
        )
        if "OFF" in event_types[first_on:]:
            invalid_groups.add(group_key)
            issues.append(
                _issue(
                    "error",
                    "substitution_order",
                    "All OFF events must precede all ON events.",
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

        if (
            event.during_penalty_shootout
            and event_type not in _PENALTY_SHOOTOUT_EVENTS
        ):
            issues.append(
                _issue(
                    "error",
                    "invalid_penalty_shootout_event",
                    "Event type is not allowed during a penalty shootout.",
                    events=(event,),
                )
            )

        was_dismissed = event.player_id in dismissed
        if was_dismissed:
            issues.append(
                _issue(
                    "error",
                    "dismissed_player_event",
                    "Dismissed player has a later event.",
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
                        "error",
                        "too_many_yellow_cards",
                        "Player has more than two yellow cards.",
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
                        "error",
                        "invalid_second_yellow",
                        (
                            "SECONDYELLOWCARD requires exactly one prior "
                            "yellow card."
                        ),
                        events=(event,),
                    )
                )
            yellow_counts[event.player_id] = max(2, previous_count + 1)
            if yellow_counts[event.player_id] > 2:
                issues.append(
                    _issue(
                        "error",
                        "too_many_yellow_cards",
                        "Player has more than two yellow cards.",
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
                        "error",
                        "too_many_red_cards",
                        "Player has more than one red card.",
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
                            "error",
                            "off_player_not_on_field",
                            "OFF player is not currently on the field.",
                            events=(event,),
                        )
                    )
                    unreliable_field_state.add(event.side)
                else:
                    on_field[event.side].remove(event.player_id)
            elif event.player_id in on_field[event.side]:
                issues.append(
                    _issue(
                        "error",
                        "on_player_already_on_field",
                        "ON player is already on the field.",
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
                    "error",
                    "scoring_player_not_on_field",
                    "Scoring event player is not currently on the field.",
                    events=(event,),
                )
            )


def validate_game_events(
    detail: GameDetail,
) -> tuple[GameDetail, tuple[GameEventIssue, ...]]:
    """Return a filtered detail and warnings, or raise all validation issues."""

    issues: list[GameEventIssue] = []
    events = _filter_events(detail, issues)
    filtered_detail = replace(detail, events=events)
    on_field = _initial_lineups(filtered_detail, events, issues)
    _check_same_time_events(events, issues)
    invalid_substitution_groups = _check_substitution_shapes(events, issues)
    _check_event_state(events, on_field, invalid_substitution_groups, issues)

    all_issues = tuple(issues)
    if any(issue.severity == "error" for issue in all_issues):
        raise ReportValidationError(all_issues, game_id=detail.game.game_id)
    return filtered_detail, all_issues
