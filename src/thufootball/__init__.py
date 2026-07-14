"""Safe, read-only THUFootball integration package."""

from .client import THUFootballClient
from .errors import (
    AuthenticationError,
    BatchQueryError,
    ConfigurationError,
    DataConflict,
    InvalidResponse,
    PermissionError,
    QueryValidationError,
    RateLimited,
    SchemaError,
    THUFootballError,
    Timeout,
)
from .models import (
    GameDetail,
    GameEvent,
    GameQuery,
    GameStatus,
    GameSummary,
    RefereeAssignment,
    TournamentRef,
    TournamentSnapshot,
    TournamentTeam,
    UserProbe,
)
from .queries import THUFootballQueryService

__all__ = [
    "AuthenticationError",
    "BatchQueryError",
    "ConfigurationError",
    "DataConflict",
    "GameDetail",
    "GameEvent",
    "GameQuery",
    "GameStatus",
    "GameSummary",
    "InvalidResponse",
    "PermissionError",
    "QueryValidationError",
    "RateLimited",
    "RefereeAssignment",
    "SchemaError",
    "THUFootballClient",
    "THUFootballError",
    "THUFootballQueryService",
    "Timeout",
    "TournamentRef",
    "TournamentSnapshot",
    "TournamentTeam",
    "UserProbe",
]
