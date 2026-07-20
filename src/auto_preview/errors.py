"""Errors exposed by auto_preview."""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Base class for stable, user-facing auto_preview failures."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class ArtifactValidationError(PipelineError):
    """An existing artifact failed strict reuse validation."""


class NoGamesForDate(PipelineError):
    """No games exist for the requested date and configured tournament IDs."""
