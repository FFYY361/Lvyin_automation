"""Automated football preview orchestration."""

from .cover import DEFAULT_COVER_MEDIA_ID_ENV, default_cover
from .errors import ArtifactValidationError, NoGamesForDate, PipelineError
from .models import (
    CombinationResult,
    Competition,
    PipelineRequest,
    PipelineResult,
    Stage,
)
from .service import AutoPreviewPipeline

__all__ = [
    "ArtifactValidationError",
    "AutoPreviewPipeline",
    "CombinationResult",
    "Competition",
    "NoGamesForDate",
    "PipelineError",
    "PipelineRequest",
    "PipelineResult",
    "Stage",
    "DEFAULT_COVER_MEDIA_ID_ENV",
    "default_cover",
]
