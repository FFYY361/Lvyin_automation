"""Stable errors exposed by auto_report."""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Base class for stable, user-facing auto_report failures."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class ArtifactValidationError(PipelineError):
    """An existing auto_report artifact failed strict validation."""
