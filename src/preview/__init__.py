"""Pure-local football preview rendering."""

from .bundle import (
    SOURCE_DOCUMENT_SCHEMA_VERSION,
    PreviewSourceDocument,
    load_preview_bundle,
    matchup_key,
    parse_preview_bundle,
    parse_preview_config,
    parse_preview_document,
    parse_weather_for_date,
    preview_article_file,
)
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
    "PreviewSourceDocument",
    "PreviewSourceData",
    "PreviewTeam",
    "PreviewTemplate",
    "PreviewValidationError",
    "PreviewWeather",
    "SeasonOutcome",
    "SOURCE_DOCUMENT_SCHEMA_VERSION",
    "TeamRef",
    "TemplateContractError",
    "UnsafeHtml",
    "load_preview_bundle",
    "load_preview_source",
    "load_preview_template",
    "matchup_key",
    "parse_preview_bundle",
    "parse_preview_config",
    "parse_preview_document",
    "parse_preview_source",
    "parse_weather_for_date",
    "preview_article_file",
    "validate_preview_source",
]
