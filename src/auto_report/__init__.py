"""Automated THUFootball report-to-WeChat pipeline."""

from .errors import ArtifactValidationError, PipelineError
from .models import (
    CombinationResult,
    Competition,
    PipelineRequest,
    PipelineResult,
    Stage,
)
from .service import AutoReportPipeline

__all__ = [
    "ArtifactValidationError",
    "AutoReportPipeline",
    "CombinationResult",
    "Competition",
    "PipelineError",
    "PipelineRequest",
    "PipelineResult",
    "Stage",
]
