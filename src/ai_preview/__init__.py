"""Build AI football-preview prompts from local source data."""

from .builder import (
    PromptBundle,
    build_prompt_bundle,
    build_system_message,
    build_user_message,
)
from .config import PromptConfig, load_prompt_config

__all__ = [
    "PromptBundle",
    "PromptConfig",
    "build_prompt_bundle",
    "build_system_message",
    "build_user_message",
    "load_prompt_config",
]
