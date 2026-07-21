"""Pure-local football preview rendering."""

from .bundle import (
    PreviewSourceDocument,
    load_preview_bundle,
    matchup_key,
    parse_preview_bundle,
    parse_preview_config,
    parse_preview_document,
    parse_weather_for_date,
    preview_article_file,
)
from .errors import (
    PreviewError,
    PreviewValidationError,
    TemplateContractError,
    UnsafeHtml,
)
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
    "PreviewSourceDocument",
    "PreviewSourceData",
    "PreviewTeam",
    "PreviewTemplate",
    "PreviewValidationError",
    "PreviewWeather",
    "SeasonOutcome",
    "TeamRef",
    "TemplateContractError",
    "UnsafeHtml",
    "load_preview_bundle",
    "load_preview_template",
    "matchup_key",
    "parse_preview_bundle",
    "parse_preview_config",
    "parse_preview_document",
    "parse_weather_for_date",
    "preview_article_file",
    "validate_preview_source",
]
