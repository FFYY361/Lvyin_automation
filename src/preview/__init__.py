"""Pure-local football preview rendering."""

from .errors import PreviewError, PreviewValidationError, TemplateContractError, UnsafeHtml
from .models import (
    PlayedMatch,
    PreviewColumnConfig,
    PreviewCredits,
    PreviewMatch,
    PreviewSourceData,
    PreviewTeam,
    PreviewWeather,
    SeasonOutcome,
    TeamRef,
    load_preview_source,
    parse_preview_source,
    validate_preview_source,
)
from .service import PreviewService
from .template import PreviewTemplate, load_preview_template

__all__ = [
    "PlayedMatch",
    "PreviewColumnConfig",
    "PreviewCredits",
    "PreviewError",
    "PreviewMatch",
    "PreviewService",
    "PreviewSourceData",
    "PreviewTeam",
    "PreviewTemplate",
    "PreviewValidationError",
    "PreviewWeather",
    "SeasonOutcome",
    "TeamRef",
    "TemplateContractError",
    "UnsafeHtml",
    "load_preview_source",
    "load_preview_template",
    "parse_preview_source",
    "validate_preview_source",
]
