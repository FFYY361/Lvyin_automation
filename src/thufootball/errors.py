"""Stable, sanitised error types for the THUFootball query layer."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .models import GameEventIssue


class THUFootballError(RuntimeError):
    """Base class for errors exposed by the asynchronous query client."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        tournament_id: int | None = None,
        game_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable
        self.tournament_id = tournament_id
        self.game_id = game_id


class QueryValidationError(THUFootballError):
    """Raised when a public query or client argument is invalid."""


class ConfigurationError(THUFootballError):
    """Raised when credentials or client configuration are incomplete."""


class AuthenticationError(THUFootballError):
    """Raised when THUFootball rejects the current credentials."""


class PermissionError(THUFootballError):
    """Raised when the current identity cannot read a requested resource."""


class Timeout(THUFootballError):
    """Raised after a read-only request exhausts its timeout retry."""


class RateLimited(THUFootballError):
    """Raised when THUFootball reports request throttling."""


class InvalidResponse(THUFootballError):
    """Raised when an HTTP or API response cannot be used safely."""


class SchemaError(THUFootballError):
    """Raised when a required response field has an unexpected shape."""

    def __init__(
        self,
        field_path: str,
        *,
        tournament_id: int | None = None,
        game_id: int | None = None,
    ) -> None:
        super().__init__(
            f"THUFootball response has an invalid field at {field_path}",
            stage="schema",
            tournament_id=tournament_id,
            game_id=game_id,
        )
        self.field_path = field_path


class DataConflict(THUFootballError):
    """Raised when duplicate identifiers carry conflicting core data."""


class ReportValidationError(THUFootballError):
    """Raised when report events contain one or more blocking issues."""

    def __init__(
        self,
        issues: Sequence[GameEventIssue],
        *,
        game_id: int,
    ) -> None:
        self.issues = tuple(issues)
        error_count = sum(
            issue.severity == "error" for issue in self.issues
        )
        super().__init__(
            f"report event validation failed with {error_count} error(s)",
            stage="event_validation",
            game_id=game_id,
        )


class BatchQueryError(THUFootballError):
    """Raised when at least one tournament in a multi-read batch fails."""

    def __init__(self, failures: Mapping[int, THUFootballError]) -> None:
        ordered_failures = dict(failures)
        failed_ids = tuple(ordered_failures)
        super().__init__(
            f"Tournament batch failed for IDs {failed_ids}",
            stage="query_games",
            retryable=any(error.retryable for error in ordered_failures.values()),
        )
        self.failures = MappingProxyType(ordered_failures)
        self.failed_tournament_ids = failed_ids
