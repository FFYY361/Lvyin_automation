"""Stable errors for the pure-local preview pipeline."""

from __future__ import annotations


class PreviewError(RuntimeError):
    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class PreviewValidationError(PreviewError):
    """Raised when preview source data is invalid."""


class TemplateContractError(PreviewError):
    """Raised when a template cannot be compiled or rendered safely."""


class UnsafeHtml(PreviewError):
    """Raised when rendered HTML cannot be normalised safely."""
