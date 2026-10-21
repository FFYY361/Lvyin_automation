from copy import deepcopy

import pytest

from backend.tournament_identity_migration import (
    convert_snapshot,
    prepare_manifest,
    source_digest,
)
from thufootball.rankings import build_outcome_catalog


def _source():
    game = {
        "game_id": 1,
        "tournament_id": 10,
        "home_team_id": 60,
        "away_team_id": 70,
        "home_tournament_team_id": 1001,
        "away_tournament_team_id": 1002,
    }
    institutions = [
        {
            "name": name,
            "short_name": name,
            "male_team_ids": [team_id],
            "female_team_ids": [],
            "futsal_team_ids": [],
            "predecessors": [],
        }
        for name, team_id in (("A", 60), ("B", 70))
    ]
    return {
        "institutions": institutions,
        "tournaments": [
            {
                "id": 10,
                "competition": "male",
                "data": {
                    "registered_teams": [
                        {"team_id": team_id, "tournament_team_id": registration}
                        for team_id, registration in ((60, 1001), (70, 1002))
                    ],
                    "games": [game],
                },
                "final_rankings": {"A男足": "冠军", "B男足": "亚军"},
            }
        ],
        "games": [{"id": 1, "data": {"game": game}}],
        "matches": [
            {
                "game_id": 1,
                "home_snapshot": {"team_id": 60},
                "away_snapshot": {"team_id": 70},
                "head_to_head_snapshot": [
                    {"game_id": 1, "home": {"team_id": 70}, "away": {"team_id": 60}}
                ],
            }
        ],
        "articles": [
            {
                "id": 1,
                "input_snapshot": {
                    "matches": [
                        {
                            "game_id": 1,
                            "home": {"team_id": 60},
                            "body": "Preserve authored content",
                        }
                    ]
                },
            }
        ],
    }


def test_manifest_converts_rankings_and_reversed_snapshots_without_mutating_source():
    source = _source()
    before = deepcopy(source)
    manifest = prepare_manifest(source)
    assert source == before
    assert manifest["source_sha256"] == source_digest(source)
    assert manifest["institutions"][0]["tournament_teams"] == {
        "male": {"10": 1001},
        "female": {},
        "futsal": {},
    }
    assert manifest["tournaments"][0]["final_rankings"] == {
        "1001": "冠军",
        "1002": "亚军",
    }
    historical = manifest["matches"][0]["head_to_head_snapshot"][0]
    assert (
        historical["home"]["tournament_team_id"],
        historical["away"]["tournament_team_id"],
    ) == (1002, 1001)
    assert (
        manifest["articles"][0]["input_snapshot"]["matches"][0]["body"]
        == "Preserve authored content"
    )


def test_ambiguous_or_missing_registration_stops_preparation():
    source = _source()
    source["tournaments"][0]["data"]["registered_teams"].append(
        {"team_id": 60, "tournament_team_id": 1003}
    )
    with pytest.raises(ValueError, match="Multiple registrations"):
        prepare_manifest(source)
    with pytest.raises(ValueError, match="Cannot resolve"):
        convert_snapshot({"game_id": 99, "home": {"team_id": 60}}, {})
    assert convert_snapshot({"game_id": -1, "home": {"team_id": 60}}, {}) == {
        "game_id": -1,
        "home": {"tournament_team_id": None},
    }


def test_same_upstream_global_id_can_have_separate_body_and_predecessor():
    def identity(name, tournament, registration):
        return {
            "name": name,
            "short_name": name,
            "tournament_teams": {
                "male": {str(tournament): registration},
                "female": {},
                "futsal": {},
            },
        }

    body = identity("New", 20, 2001)
    body["predecessors"] = [identity("Old", 10, 1001)]
    documents = [
        {
            "id": tid,
            "competition": "male",
            "data": {
                "registered_teams": [
                    {
                        "team_id": 60,
                        "tournament_id": tid,
                        "tournament_team_id": registration,
                    }
                ]
            },
        }
        for tid, registration in ((10, 1001), (20, 2001))
    ]
    catalog = build_outcome_catalog([body], [], registration_tournaments=documents)
    assert catalog.resolve_identity(1001).is_predecessor
    assert not catalog.resolve_identity(2001).is_predecessor
    assert catalog.history_tournament_team_ids(catalog.resolve_identity(1001)) == (
        1001,
    )
    assert set(catalog.history_tournament_team_ids(catalog.resolve_identity(2001))) == {
        1001,
        2001,
    }
