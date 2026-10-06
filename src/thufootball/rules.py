"""Competition rules selected by their first effective season."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompetitionRules:
    first_season: int
    prompt_key: str
    prompt_file: str
    swiss: bool = False

    def is_first_stage(self, stage: str | None) -> bool:
        return stage in {"小组赛", "循环赛", "瑞士轮", "联赛阶段", "第一阶段"}

    def stage_label(self, stage: str | None) -> str | None:
        if self.swiss and self.is_first_stage(stage):
            return "瑞士轮"
        return stage


COMPETITION_RULES = {
    "male": (CompetitionRules(0, "competition_male", "men.md"),),
    "female": (
        CompetitionRules(0, "competition_female", "women.md"),
        CompetitionRules(
            2026, "competition_female_2026_2027", "women_2026_2027.md", swiss=True
        ),
    ),
    "futsal": (CompetitionRules(0, "competition_futsal", "futsal.md"),),
}


def competition_rules(competition: str, season: str | None = None) -> CompetitionRules:
    """Without a target season, retain the original rules for API compatibility."""
    first_year = int(season[:4]) if season else 0
    return next(
        rules
        for rules in reversed(COMPETITION_RULES[competition])
        if rules.first_season <= first_year
    )
