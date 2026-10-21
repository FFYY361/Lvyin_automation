import pytest

from thufootball.errors import ConfigurationError
from thufootball.rankings import build_outcome_catalog


def _institution(name, short_name, ids):
    return {
        "name": name,
        "short_name": short_name,
        "tournament_teams": {
            "male": {
                str(index + 1): registration for index, registration in enumerate(ids)
            },
            "female": {},
            "futsal": {},
        },
    }


def _catalog(ranks, *, body_ids=(), second_ranks=None):
    body = _institution("合并学院", "合院", list(body_ids))
    body["predecessors"] = [
        _institution("甲学部", "甲部", [10]),
        _institution("乙学部", "乙部", [20]),
    ]
    identities = [body] + body["predecessors"]
    by_name = {identity["name"] + "男足": identity for identity in identities}
    if "合并学院男足" in ranks and not body_ids:
        body["tournament_teams"]["male"] = {"1": 30}
    if second_ranks:
        for name in second_ranks:
            mapping = by_name[name]["tournament_teams"]["male"]
            mapping["2"] = mapping.pop("1") + 100

    def convert(values, tournament_id):
        return {
            str(by_name[name]["tournament_teams"]["male"][str(tournament_id)]): rank
            for name, rank in values.items()
        }

    tournaments = [
        {"id": 1, "name": "马杯男足甲级2024~2025", "final_rankings": convert(ranks, 1)}
    ]
    if second_ranks:
        tournaments.append(
            {
                "id": 2,
                "name": "马杯男足乙级2024~2025",
                "final_rankings": convert(second_ranks, 2),
            }
        )
    return build_outcome_catalog([body], tournaments)


def test_empty_body_identity_keeps_predecessors_distinct():
    catalog = _catalog({"甲学部男足": "八强"})
    body = catalog.teams_by_name["合并学院男足"]
    predecessor = catalog.resolve_identity(10, "男足")
    assert body.tournament_team_ids == ()
    assert predecessor.owner_name == "合并学院"
    assert predecessor.brief_name == "甲部"
    assert catalog.history_tournament_team_ids(body) == (10, 20)
    assert catalog.history_tournament_team_ids(predecessor) == (10,)


def test_raw_rankings_keep_body_and_predecessors_without_selecting_best():
    catalog = _catalog(
        {"合并学院男足": "32强", "甲学部男足": "冠军", "乙学部男足": "八强"}
    )
    outcomes = catalog.season_outcomes(catalog.teams_by_name["合并学院男足"], (1,))
    assert [(item.team_name, item.rank) for item in outcomes] == [
        ("合并学院男足", "32强"),
        ("甲学部男足", "冠军"),
        ("乙学部男足", "八强"),
    ]


def test_multiple_predecessors_are_background_only_even_when_a_ranking_is_missing():
    catalog = _catalog({"甲学部男足": "八强", "乙学部男足": "八强"}, body_ids=(30,))
    body = catalog.resolve_identity(30, "男足")
    assert {item.team_name for item in catalog.season_outcomes(body, (1,))} == {
        "甲学部男足",
        "乙学部男足",
    }
    assert catalog.season_outcomes(body, (999,)) == ()
    assert catalog.display_history_tournament_team_ids(body, ((1,),)) == ()
    catalog = _catalog({"甲学部男足": "冠军"})
    assert (
        catalog.display_history_tournament_team_ids(
            catalog.teams_by_name["合并学院男足"], ((1,),)
        )
        == ()
    )


def test_display_history_stops_at_merge_across_male_tiers_and_never_resumes():
    body = _institution("合并学院", "合院", [])
    first = _institution("甲学部", "甲部", [])
    second = _institution("乙学部", "乙部", [])
    body["tournament_teams"]["male"] = {"5": 50}
    first["tournament_teams"]["male"] = {"4": 40, "3": 30, "1": 10}
    second["tournament_teams"]["male"] = {"2": 20}
    first["tournament_teams"]["female"] = {"6": 60}
    first["tournament_teams"]["futsal"] = {"7": 70}
    body["predecessors"] = [first, second]
    catalog = build_outcome_catalog([body], [])
    body = catalog.teams_by_name["合并学院男足"]
    seasons = ((5,), (4,), (3, 2), (1,))
    assert catalog.display_history_tournament_team_ids(body, seasons) == (50, 40)
    assert catalog.history_tournament_team_ids(body) == (50, 40, 30, 10, 20)
    assert catalog.display_history_tournament_team_ids(
        catalog.resolve_identity(30), seasons
    ) == (40, 30, 10)
    for category, tournament_id, registration in (("女足", 6, 60), ("五人制", 7, 70)):
        assert catalog.display_history_tournament_team_ids(
            catalog.teams_by_name["合并学院" + category], ((tournament_id,),)
        ) == (registration,)


def test_an_id_cannot_belong_to_body_and_predecessor():
    body = _institution("合并学院", "合院", [10])
    body["predecessors"] = [_institution("甲学部", "甲部", [10])]
    with pytest.raises(ConfigurationError, match="tournament_team_id.10"):
        build_outcome_catalog([body], [])
