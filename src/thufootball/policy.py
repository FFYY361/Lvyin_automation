"""Local read policies for THUFootball resources."""

from __future__ import annotations

BLACKLISTED_TOURNAMENT_IDS: frozenset[int] = frozenset({6, 28})


def blacklisted_tournament_ids(
    tournament_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Return blacklisted IDs in their first-seen input order."""

    return tuple(
        tournament_id
        for tournament_id in dict.fromkeys(tournament_ids)
        if tournament_id in BLACKLISTED_TOURNAMENT_IDS
    )
