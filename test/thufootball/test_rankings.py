import pytest

from thufootball.errors import ConfigurationError
from thufootball.rankings import build_outcome_catalog


def _institution(name, short_name, ids):
    return {
        "name": name,
        "short_name": short_name,
        "male_team_ids": ids,
        "female_team_ids": [],
        "futsal_team_ids": [],
    }


def _catalog(ranks, *, body_ids=(), second_ranks=None):
    body = _institution("合并学院", "合院", list(body_ids))
    body["predecessors"] = [
        _institution("甲学部", "甲部", [10]),
        _institution("乙学部", "乙部", [20]),
    ]
    tournaments = [{"id": 1, "name": "马杯男足甲级2024~2025", "final_rankings": ranks}]
    if second_ranks:
        tournaments.append(
            {"id": 2, "name": "马杯男足乙级2024~2025", "final_rankings": second_ranks}
        )
    return build_outcome_catalog([body], tournaments)


def test_empty_body_identity_keeps_predecessors_distinct():
    catalog = _catalog({"甲学部男足": "八强"})
    body = catalog.teams_by_name["合并学院男足"]
    predecessor = catalog.resolve_identity(10, "男足")
    assert body.team_ids == ()
    assert predecessor.owner_name == "合并学院"
    assert predecessor.brief_name == "甲部"
    assert catalog.history_ids(body) == (10, 20)
    assert catalog.history_ids(predecessor) == (10,)


def test_body_ranking_takes_precedence_even_without_ids():
    catalog = _catalog(
        {"合并学院男足": "32强", "甲学部男足": "冠军", "乙学部男足": "八强"}
    )
    outcomes = catalog.season_outcomes(catalog.teams_by_name["合并学院男足"], (1,))
    assert [(item.team_name, item.rank) for item in outcomes] == [
        ("合并学院男足", "32强")
    ]


def test_predecessor_selection_keeps_ties_and_does_not_infer_participation():
    catalog = _catalog({"甲学部男足": "八强", "乙学部男足": "八强"}, body_ids=(30,))
    body = catalog.resolve_identity(30, "男足")
    assert {item.team_name for item in catalog.season_outcomes(body, (1,))} == {
        "甲学部男足",
        "乙学部男足",
    }
    assert catalog.season_outcomes(body, (999,)) == ()


def test_tier_precedes_finish_and_ambiguous_status_is_not_guessed():
    catalog = _catalog({"甲学部男足": "小组第三"}, second_ranks={"乙学部男足": "冠军"})
    body = catalog.teams_by_name["合并学院男足"]
    assert catalog.season_outcomes(body, (2, 1))[0].rank == "小组第三"
    catalog = _catalog({"甲学部男足": "保级", "乙学部男足": "八强"})
    with pytest.raises(ConfigurationError, match="incomparable_ranks"):
        catalog.season_outcomes(catalog.teams_by_name["合并学院男足"], (1,))


def test_an_id_cannot_belong_to_body_and_predecessor():
    body = _institution("合并学院", "合院", [10])
    body["predecessors"] = [_institution("甲学部", "甲部", [10])]
    with pytest.raises(ConfigurationError, match="team_id.10"):
        build_outcome_catalog([body], [])
