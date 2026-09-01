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
        self.assertEqual(len(home["recent_matches"]), 1)
        self.assertEqual(home["recent_matches"][0]["season"], "2025-26")
        self.assertEqual(home["earlier_matches"], [])

        direct = automatic["head_to_head"][0]
        self.assertEqual(direct["home_team"]["name"], "甲学院男子足球队")
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
        self.assertEqual(home["recent_matches"], [])
        self.assertEqual(len(home["earlier_matches"]), 1)
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


class _FakeRepository:
    def __init__(self, tournament, games, institutions) -> None:
        self.tournament = tournament
        self.games = games
        self.institutions = institutions

    def list_tournaments(self):
        return [self.tournament]

    def get_tournament(self, tournament_id: int):
        assert tournament_id == self.tournament.id
        return self.tournament

    def get_game(self, game_id: int):
        return self.games[game_id]

    def find_institution(self, team_id: int, competition: str):
        assert competition == "male"
        return self.institutions[1 if team_id in {1, 101} else 2]


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
