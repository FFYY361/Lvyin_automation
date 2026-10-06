from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from ai_preview import PromptConfig, build_prompt_bundle, build_system_message
from thufootball.rankings import build_outcome_catalog


class PromptBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        games = [
            _game(10, "2025-10-01T13:00:00+08:00", 2, 101, 1, 2),
            _game(11, "2025-10-20T13:00:00+08:00", 1, 3, 0, 3),
            _game(12, "2025-10-22T13:00:00+08:00", 3, 2, 1, 1),
            _game(
                13,
                "2025-10-25T13:00:00+08:00",
                1,
                2,
                4,
                0,
                valid=None,
            ),
            _game(
                20,
                "2025-11-01T13:00:00+08:00",
                1,
                2,
                None,
                None,
                status="scheduled",
                stage="半决赛",
            ),
        ]
        tournament = SimpleNamespace(
            id=7,
            competition="male",
            final_rankings={},
            data={
                "tournament": {
                    "id": 7,
                    "name": "2025~2026马杯男足甲级",
                    "season": "2025~2026",
                },
                "registered_teams": [],
                "games": games,
            },
        )
        game_documents = {
            10: {
                "game": games[0],
                "events": [
                    _event("START", "home", "乙首发", kit=8),
                    _event("START", "away", "甲首发", kit=9),
                    _event("GOAL", "home", "乙射手", minute=12),
                    _event("GOAL", "away", "甲射手", minute=30),
                    _event("GOAL", "away", "甲射手", minute=55),
                    _event("OFF", "away", "甲首发", minute=60),
                    _event("ON", "away", "甲替补", minute=60),
                ],
            }
        }
        for game, player in ((games[1], "甲近况球员"), (games[2], "乙近况球员")):
            game_documents[game["game_id"]] = {
                "game": game,
                "events": [_event("START", "home", player)],
            }
        game_documents[20] = {"game": games[-1], "events": []}
        institutions = {
            1: SimpleNamespace(
                name="甲学院",
                short_name="甲",
                male_team_ids=[1, 101],
                male_description="擅长地面推进。",
                player_descriptions={
                    "甲球员": {
                        "competitions": ["male"],
                        "description": "速度快。",
                    }
                },
            ),
            2: SimpleNamespace(
                name="乙书院",
                short_name="乙",
                male_team_ids=[2],
                male_description="防守组织紧凑。",
                player_descriptions={
                    "乙球员": {
                        "competitions": ["male"],
                        "description": "待补充。",
                    }
                },
            ),
        }
        self.repository = _FakeRepository(
            tournament,
            {
                game_id: SimpleNamespace(id=game_id, data=document)
                for game_id, document in game_documents.items()
            },
            institutions,
        )

    def test_builds_context_and_normalizes_reversed_head_to_head(self) -> None:
        bundle = build_prompt_bundle(
            20,
            config=PromptConfig(recent_matches_with_events=1, history_seasons=1),
            repository=self.repository,
        )

        automatic = bundle.automatic_context
        self.assertIn("# 马杯男足赛制说明", bundle.system_message)
        self.assertNotIn("# 马杯女足赛制说明", bundle.system_message)
        home = automatic["home_team"]
        record = home["current_tournament"]["record_before_match"]
        self.assertEqual(record["losses"], 1)
        history = home["history_teams"][0]
        self.assertEqual(len(history["recent_matches"]), 1)
        self.assertEqual(history["recent_matches"][0]["season"], "2025-26")
        self.assertEqual(history["earlier_matches"], [])

        direct = automatic["head_to_head"][0]
        self.assertEqual(direct["home_team"]["name"], "甲")
        self.assertEqual(direct["home_team"]["goals_for"], 2)
        self.assertEqual(direct["home_team"]["goals_against"], 1)
        self.assertEqual(direct["home_team"]["starting_lineup"], ["甲首发"])
        self.assertEqual(direct["away_team"]["starting_lineup"], ["乙首发"])
        self.assertEqual(
            [goal["team"] for goal in direct["goals"]],
            ["away_team", "home_team", "home_team"],
        )
        self.assertEqual(direct["substitutions"][0]["team"], "home_team")

        self.assertEqual(
            bundle.manual_context["home_team"]["player_descriptions"],
            [{"name": "甲球员", "description": "速度快。"}],
        )
        self.assertNotIn("game_id", json.dumps(automatic, ensure_ascii=False))
        message = bundle.render_user_message()
        self.assertIn("<match_context>", message)
        self.assertIn("<manual_context>", message)
        self.assertIn("<automatic_context>", message)

    def test_zero_recent_limit_keeps_direct_matches_detailed(self) -> None:
        bundle = build_prompt_bundle(
            20,
            config=PromptConfig(recent_matches_with_events=0, history_seasons=1),
            repository=self.repository,
        )

        home = bundle.automatic_context["home_team"]
        self.assertEqual(home["history_teams"][0]["recent_matches"], [])
        self.assertEqual(len(home["history_teams"][0]["earlier_matches"]), 1)
        self.assertEqual(len(bundle.automatic_context["head_to_head"]), 1)

    def test_selects_one_competition_rules_document(self) -> None:
        expected = {
            "男足": "# 马杯男足赛制说明",
            "女足": "# 马杯女足赛制说明",
            "五人制": "# 马杯五人制赛制说明",
        }
        for competition, heading in expected.items():
            with self.subTest(competition=competition):
                message = build_system_message(competition)
                self.assertIn(heading, message)
                self.assertEqual(message.count("赛制说明"), 1)

    def test_merge_preserves_body_history_and_separates_internal_predecessor_match(
        self,
    ):
        body = self.repository.institutions[1]
        body.predecessors = [
            {
                "name": name,
                "short_name": short,
                "male_team_ids": [team_id],
                "female_team_ids": [],
                "futsal_team_ids": [],
            }
            for name, short, team_id in (
                ("甲旧学部", "甲旧", 11),
                ("乙旧学部", "乙旧", 12),
            )
        ]
        internal = dict(
            _game(30, "2024-10-01T13:00:00+08:00", 1, 2, 2, 4),
            tournament_id=8,
            home_team_id=11,
            away_team_id=12,
        )
        own = dict(
            _game(31, "2024-10-02T13:00:00+08:00", 101, 3, 1, 0), tournament_id=8
        )
        history = SimpleNamespace(
            id=8,
            competition="male",
            final_rankings={
                "甲学院男足": "32强",
                "甲旧学部男足": "16强",
                "乙旧学部男足": "八强",
            },
            data={
                "tournament": {
                    "id": 8,
                    "name": "马杯男足甲级2024~2025",
                    "season": "2024~2025",
                },
                "games": [internal, own],
            },
        )
        self.repository.tournaments[8] = history
        for game in (internal, own):
            self.repository.games[game["game_id"]] = SimpleNamespace(
                data={"game": game, "events": []}
            )
        current_predecessor = dict(
            _game(32, "2025-10-15T13:00:00+08:00", 1, 3, 8, 0), home_team_id=11
        )
        self.repository.tournament.data["games"].append(current_predecessor)
        self.repository.games[32] = SimpleNamespace(
            data={"game": current_predecessor, "events": []}
        )
        same_season_game = dict(
            _game(33, "2025-10-21T13:00:00+08:00", 1, 3, 1, 0), tournament_id=9
        )
        self.repository.tournaments[9] = SimpleNamespace(
            id=9,
            competition="male",
            final_rankings={},
            data={
                "tournament": {
                    "id": 9,
                    "name": "马杯男足乙级2025~2026",
                    "season": "2025~2026",
                },
                "games": [same_season_game],
            },
        )
        self.repository.games[33] = SimpleNamespace(
            data={"game": same_season_game, "events": []}
        )
        same_season_game = dict(
            _game(33, "2025-10-21T13:00:00+08:00", 1, 3, 1, 0), tournament_id=9
        )
        self.repository.tournaments[9] = SimpleNamespace(
            id=9,
            competition="male",
            final_rankings={},
            data={
                "tournament": {
                    "id": 9,
                    "name": "马杯男足乙级2025~2026",
                    "season": "2025~2026",
                },
                "games": [same_season_game],
            },
        )
        self.repository.games[33] = SimpleNamespace(
            data={"game": same_season_game, "events": []}
        )

        bundle = build_prompt_bundle(20, repository=self.repository)
        home = bundle.automatic_context["home_team"]
        groups = {item["short_name"]: item for item in home["history_teams"]}
        self.assertEqual(home["season_outcomes"][0]["outcome"], "32强")
        self.assertEqual(groups["甲"]["past_seasons"][0]["wins"], 1)
        self.assertEqual(groups["甲旧"]["past_seasons"][0]["losses"], 1)
        self.assertEqual(groups["乙旧"]["past_seasons"][0]["wins"], 1)
        self.assertEqual(home["current_tournament"]["record_before_match"]["played"], 1)
        self.assertEqual(len(bundle.automatic_context["head_to_head"]), 1)
        self.assertNotIn(
            8,
            [
                match.get("goals_for")
                for match in groups["甲旧"]["recent_matches"]
                + groups["甲旧"]["earlier_matches"]
            ],
        )

        history.final_rankings.pop("甲学院男足")
        inherited = build_prompt_bundle(
            20, repository=self.repository
        ).automatic_context["home_team"]["season_outcomes"][0]
        self.assertEqual(inherited["outcome"], "八强")
        self.assertEqual(inherited["sources"][0]["name"], "乙旧")
        historical_target = build_prompt_bundle(30, repository=self.repository)
        self.assertEqual(historical_target.match_context["home_team"], "甲旧学部")
        self.assertEqual(
            len(historical_target.automatic_context["home_team"]["history_teams"]), 1
        )
        historical_target = build_prompt_bundle(30, repository=self.repository)
        self.assertEqual(historical_target.match_context["home_team"], "甲旧学部")
        self.assertEqual(
            len(historical_target.automatic_context["home_team"]["history_teams"]), 1
        )


class _FakeRepository:
    def __init__(self, tournament, games, institutions) -> None:
        self.tournament = tournament
        self.tournaments = {tournament.id: tournament}
        self.games = games
        self.institutions = institutions

    def list_tournaments(self):
        return list(self.tournaments.values())

    def get_tournament(self, tournament_id: int):
        return self.tournaments[tournament_id]

    def load_outcome_catalog(self):
        records = {record.name: record for record in self.institutions.values()}
        institutions = [
            {
                "name": record.name,
                "short_name": record.short_name,
                "male_team_ids": record.male_team_ids,
                "female_team_ids": [],
                "futsal_team_ids": [],
                "predecessors": getattr(record, "predecessors", []),
            }
            for record in records.values()
        ]
        institutions.append(
            {
                "name": "丙学院",
                "short_name": "丙",
                "male_team_ids": [3],
                "female_team_ids": [],
                "futsal_team_ids": [],
            }
        )
        tournaments = [
            {
                "id": record.id,
                "name": record.data["tournament"]["name"],
                "final_rankings": record.final_rankings,
            }
            for record in self.tournaments.values()
            if record.final_rankings
        ]
        return build_outcome_catalog(institutions, tournaments)

    def get_game(self, game_id: int):
        return self.games[game_id]

    def find_institution(self, team_id: int, competition: str):
        assert competition == "male"
        identity = self.load_outcome_catalog().resolve_identity(team_id, "男足")
        return next(
            record
            for record in self.institutions.values()
            if record.name == identity.owner_name
        )


def _game(
    game_id: int,
    kickoff: str,
    home_id: int,
    away_id: int,
    home_score: int | None,
    away_score: int | None,
    *,
    status: str = "finished",
    stage: str = "小组赛",
    valid: bool | None = True,
) -> dict[str, object]:
    names = {
        1: "甲学院男子足球队",
        101: "甲学院旧队名",
        2: "乙书院男子足球队",
        3: "丙学院男子足球队",
    }
    return {
        "game_id": game_id,
        "tournament_id": 7,
        "kickoff_local": kickoff,
        "status": status,
        "valid": valid,
        "stage": stage,
        "group_name": None,
        "round": None,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_team_name": names[home_id],
        "away_team_name": names[away_id],
        "home_team_brief_name": names[home_id][:3],
        "away_team_brief_name": names[away_id][:3],
        "home_score": home_score,
        "away_score": away_score,
        "home_penalty": 0,
        "away_penalty": 0,
        "home_abandon": False,
        "away_abandon": False,
        "field_name": "东大操场",
    }


def _event(
    event_type: str,
    side: str,
    player: str,
    *,
    minute: int = 0,
    kit: int = 1,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "side": side,
        "player_name": player,
        "minute": minute,
        "stoppage_minute": 0,
        "kit_number": kit,
        "during_penalty_shootout": False,
        "valid": True,
        "sequence": 1,
        "time_ordering": 0,
    }


if __name__ == "__main__":
    unittest.main()
