from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from thufootball import (
    GameDetail,
    GameEvent,
    GameStatus,
    GameSummary,
    ReportValidationError,
)
from thufootball.report_validation import validate_game_events


def _summary() -> GameSummary:
    kickoff = datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
    return GameSummary(
        game_id=4245,
        tournament_id=122,
        tournament_name="测试赛事",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=GameStatus.FINISHED,
        record_active=True,
        valid=True,
        stage=None,
        group_name=None,
        round=None,
        home_tournament_team_id=100,
        home_team_id=10,
        home_team_name="主队",
        away_tournament_team_id=200,
        away_team_id=20,
        away_team_name="客队",
        home_score=1,
        away_score=0,
        result_text="1:0",
        penalty_shootout=False,
        home_penalty=0,
        away_penalty=0,
        home_abandon=False,
        away_abandon=False,
        field_name="测试场地",
    )


def _event(
    event_id: int,
    event_type: str,
    player_id: int,
    *,
    side: str = "home",
    minute: int = 0,
    stoppage_minute: int = 0,
    shootout: bool = False,
    valid: bool = True,
) -> GameEvent:
    return GameEvent(
        event_id=event_id,
        tournament_team_id=100 if side == "home" else 200,
        tournament_team_player_id=1_000 + player_id,
        player_id=player_id,
        player_name=f"球员{player_id}",
        side=side,  # type: ignore[arg-type]
        event_type=event_type,
        minute=minute,
        stoppage_minute=stoppage_minute,
        kit_number=player_id,
        during_penalty_shootout=shootout,
        valid=valid,
        sequence=event_id,
        time_ordering=event_id,
    )


def _starts() -> tuple[GameEvent, ...]:
    return (
        _event(1, "START", 1),
        _event(2, "START", 2),
        _event(3, "START", 11, side="away"),
        _event(4, "START", 12, side="away"),
    )


def _detail(
    *events: GameEvent,
    players_per_side: int = 2,
) -> GameDetail:
    return GameDetail(
        game=_summary(),
        events=(*_starts(), *events),
        referees=(),
        players_per_side=players_per_side,
    )


def _codes(error: ReportValidationError) -> list[str]:
    return [issue.code for issue in error.issues]


class FilteringAndLineupTests(unittest.TestCase):
    def test_invalid_events_warn_and_assists_disappear_silently(self) -> None:
        invalid = _event(10, "REDCARD", 1, minute=10, valid=False)
        assist = _event(11, "ASSIST", 1, minute=11)
        goal = _event(12, "GOAL", 1, minute=12)

        filtered, warnings = validate_game_events(
            _detail(invalid, assist, goal)
        )

        self.assertEqual(
            [event.event_id for event in filtered.events],
            [1, 2, 3, 4, 12],
        )
        self.assertEqual(
            [warning.code for warning in warnings],
            ["invalid_event_ignored"],
        )

    def test_lineup_under_capacity_warns_for_each_team(self) -> None:
        _, warnings = validate_game_events(
            _detail(players_per_side=3)
        )

        self.assertEqual(
            [warning.code for warning in warnings],
            ["lineup_under_capacity", "lineup_under_capacity"],
        )
        self.assertEqual([warning.side for warning in warnings], ["home", "away"])

    def test_lineup_over_capacity_is_an_error(self) -> None:
        detail = _detail(
            _event(5, "START", 3),
            players_per_side=2,
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(detail)

        self.assertIn("lineup_over_capacity", _codes(caught.exception))

    def test_five_a_side_allows_more_than_five_starters(self) -> None:
        extra_starters = (
            _event(5, "START", 3),
            _event(6, "START", 4),
            _event(7, "START", 5),
            _event(8, "START", 6),
            _event(9, "START", 13, side="away"),
            _event(10, "START", 14, side="away"),
            _event(11, "START", 15, side="away"),
            _event(12, "START", 16, side="away"),
        )

        _, warnings = validate_game_events(
            _detail(*extra_starters, players_per_side=5)
        )

        self.assertEqual(warnings, ())

    def test_duplicate_start_is_an_error(self) -> None:
        detail = _detail(_event(5, "START", 1))

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(detail)

        duplicate = next(
            issue
            for issue in caught.exception.issues
            if issue.code == "duplicate_start"
        )
        self.assertEqual(duplicate.event_ids, (1, 5))

    def test_appearance_is_rendered_but_not_put_on_field(self) -> None:
        appearance = _event(5, "APPEARANCE", 3)
        goal = _event(6, "GOAL", 3, minute=1)

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(_detail(appearance, goal))

        self.assertIn("scoring_player_not_on_field", _codes(caught.exception))


class SameTimeAndSubstitutionTests(unittest.TestCase):
    def test_one_for_one_and_two_for_two_are_valid_semantic_events(self) -> None:
        cases = (
            (
                _event(10, "OFF", 1, minute=10),
                _event(11, "ON", 3, minute=10),
            ),
            (
                _event(10, "OFF", 1, minute=10),
                _event(11, "OFF", 2, minute=10),
                _event(12, "ON", 3, minute=10),
                _event(13, "ON", 4, minute=10),
            ),
        )

        for events in cases:
            with self.subTest(count=len(events)):
                _, warnings = validate_game_events(_detail(*events))
                self.assertEqual(warnings, ())

    def test_two_teams_substituting_at_same_time_do_not_interact(self) -> None:
        events = (
            _event(10, "OFF", 1, minute=10),
            _event(11, "ON", 3, minute=10),
            _event(12, "OFF", 11, side="away", minute=10),
            _event(13, "ON", 13, side="away", minute=10),
        )

        _, warnings = validate_game_events(_detail(*events))

        self.assertNotIn(
            "multiple_events_same_time",
            [warning.code for warning in warnings],
        )

    def test_one_event_per_team_at_same_time_does_not_warn(self) -> None:
        events = (
            _event(10, "YELLOWCARD", 1, minute=10),
            _event(11, "YELLOWCARD", 11, side="away", minute=10),
        )

        _, warnings = validate_game_events(_detail(*events))

        self.assertEqual(warnings, ())

    def test_multiple_semantic_events_warn_per_team(self) -> None:
        cases = (
            (
                _event(10, "OFF", 1, minute=10),
                _event(11, "ON", 3, minute=10),
                _event(12, "YELLOWCARD", 2, minute=10),
            ),
            (
                _event(10, "GOAL", 1, minute=10),
                _event(11, "YELLOWCARD", 2, minute=10),
            ),
            (
                _event(10, "YELLOWCARD", 1, minute=10),
                _event(11, "YELLOWCARD", 2, minute=10),
            ),
        )

        for events in cases:
            with self.subTest(types=[event.event_type for event in events]):
                _, warnings = validate_game_events(_detail(*events))
                same_time = [
                    issue
                    for issue in warnings
                    if issue.code == "multiple_events_same_time"
                ]
                self.assertEqual(len(same_time), 1)
                self.assertEqual(
                    same_time[0].event_ids,
                    tuple(event.event_id for event in events),
                )

    def test_stoppage_times_are_distinct(self) -> None:
        events = (
            _event(10, "YELLOWCARD", 1, minute=80),
            _event(
                11,
                "YELLOWCARD",
                2,
                minute=80,
                stoppage_minute=1,
            ),
        )

        _, warnings = validate_game_events(_detail(*events))

        self.assertEqual(warnings, ())

    def test_substitution_shape_errors_are_aggregated(self) -> None:
        events = (
            _event(10, "ON", 3, minute=10),
            _event(11, "OFF", 1, minute=10),
            _event(12, "OFF", 2, minute=10),
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(_detail(*events))

        codes = _codes(caught.exception)
        self.assertIn("substitution_unbalanced", codes)
        self.assertIn("substitution_order", codes)
        self.assertNotIn("on_player_already_on_field", codes)
        self.assertNotIn("off_player_not_on_field", codes)

    def test_invalid_substitution_does_not_cascade_field_state_errors(
        self,
    ) -> None:
        events = (
            _event(10, "ON", 3, minute=10),
            _event(11, "GOAL", 3, minute=11),
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(_detail(*events))

        codes = _codes(caught.exception)
        self.assertIn("substitution_unbalanced", codes)
        self.assertNotIn("scoring_player_not_on_field", codes)

    def test_substitution_members_must_match_field_state(self) -> None:
        cases = (
            (
                (
                    _event(10, "OFF", 3, minute=10),
                    _event(11, "ON", 4, minute=10),
                ),
                "off_player_not_on_field",
            ),
            (
                (
                    _event(10, "OFF", 1, minute=10),
                    _event(11, "ON", 2, minute=10),
                ),
                "on_player_already_on_field",
            ),
        )

        for events, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ReportValidationError) as caught:
                    validate_game_events(_detail(*events))
                self.assertIn(expected_code, _codes(caught.exception))


class ScoringAndDisciplineTests(unittest.TestCase):
    def test_ordinary_scoring_events_require_player_on_field(self) -> None:
        for event_type in ("GOAL", "OWNGOAL", "PENALTY", "MISSPENALTY"):
            with self.subTest(event_type=event_type, state="on"):
                validate_game_events(
                    _detail(_event(10, event_type, 1, minute=10))
                )
            with self.subTest(event_type=event_type, state="off"):
                with self.assertRaises(ReportValidationError) as caught:
                    validate_game_events(
                        _detail(_event(10, event_type, 3, minute=10))
                    )
                self.assertIn(
                    "scoring_player_not_on_field",
                    _codes(caught.exception),
                )

    def test_two_regular_yellows_dismiss_and_third_yellow_exceeds_limit(self) -> None:
        events = (
            _event(10, "YELLOWCARD", 1, minute=10),
            _event(11, "YELLOWCARD", 1, minute=11),
            _event(12, "YELLOWCARD", 1, minute=12),
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(_detail(*events))

        codes = _codes(caught.exception)
        self.assertIn("dismissed_player_event", codes)
        self.assertIn("too_many_yellow_cards", codes)

    def test_second_red_exceeds_limit(self) -> None:
        events = (
            _event(10, "REDCARD", 1, minute=10),
            _event(11, "REDCARD", 1, minute=11),
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(_detail(*events))

        codes = _codes(caught.exception)
        self.assertIn("dismissed_player_event", codes)
        self.assertIn("too_many_red_cards", codes)

    def test_explicit_second_yellow_requires_exactly_one_prior_yellow(self) -> None:
        validate_game_events(
            _detail(
                _event(10, "YELLOWCARD", 1, minute=10),
                _event(11, "SECONDYELLOWCARD", 1, minute=11),
            )
        )

        with self.assertRaises(ReportValidationError) as caught:
            validate_game_events(
                _detail(_event(10, "SECONDYELLOWCARD", 1, minute=10))
            )

        self.assertIn("invalid_second_yellow", _codes(caught.exception))

    def test_every_retained_event_after_dismissal_is_rejected(self) -> None:
        later_types = (
            "ON",
            "OFF",
            "GOAL",
            "OWNGOAL",
            "PENALTY",
            "MISSPENALTY",
            "YELLOWCARD",
            "SECONDYELLOWCARD",
            "REDCARD",
            "APPEARANCE",
        )
        for event_type in later_types:
            with self.subTest(event_type=event_type):
                events = (
                    _event(10, "REDCARD", 1, minute=10),
                    _event(11, event_type, 1, minute=11),
                )
                with self.assertRaises(ReportValidationError) as caught:
                    validate_game_events(_detail(*events))
                self.assertIn(
                    "dismissed_player_event",
                    _codes(caught.exception),
                )

    def test_assist_and_invalid_events_do_not_trigger_post_dismissal_error(
        self,
    ) -> None:
        events = (
            _event(10, "REDCARD", 1, minute=10),
            _event(11, "ASSIST", 1, minute=11),
            _event(12, "GOAL", 1, minute=12, valid=False),
        )

        filtered, warnings = validate_game_events(_detail(*events))

        self.assertEqual(
            [event.event_id for event in filtered.events],
            [1, 2, 3, 4, 10],
        )
        self.assertEqual(
            [warning.code for warning in warnings],
            ["invalid_event_ignored"],
        )


class PenaltyShootoutTests(unittest.TestCase):
    def test_allowed_shootout_events_skip_on_field_scoring_check(self) -> None:
        for event_type in (
            "PENALTY",
            "MISSPENALTY",
            "YELLOWCARD",
            "SECONDYELLOWCARD",
            "REDCARD",
        ):
            with self.subTest(event_type=event_type):
                try:
                    validate_game_events(
                        _detail(
                            _event(
                                10,
                                event_type,
                                3,
                                minute=1,
                                shootout=True,
                            )
                        )
                    )
                except ReportValidationError as error:
                    self.assertNotIn(
                        "invalid_penalty_shootout_event",
                        _codes(error),
                    )
                    self.assertNotIn(
                        "scoring_player_not_on_field",
                        _codes(error),
                    )

    def test_other_shootout_events_are_rejected(self) -> None:
        for event_type in ("GOAL", "OWNGOAL", "ON", "OFF", "APPEARANCE"):
            with self.subTest(event_type=event_type):
                with self.assertRaises(ReportValidationError) as caught:
                    validate_game_events(
                        _detail(
                            _event(
                                10,
                                event_type,
                                1,
                                minute=1,
                                shootout=True,
                            )
                        )
                    )
                self.assertIn(
                    "invalid_penalty_shootout_event",
                    _codes(caught.exception),
                )


if __name__ == "__main__":
    unittest.main()
