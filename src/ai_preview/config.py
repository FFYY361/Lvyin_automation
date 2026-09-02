"""Configuration for AI preview prompt assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")


@dataclass(frozen=True)
class PromptConfig:
    recent_matches_with_events: int = 3
    history_seasons: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.recent_matches_with_events, bool)
            or not isinstance(self.recent_matches_with_events, int)
            or self.recent_matches_with_events < 0
        ):
            raise ValueError("recent_matches_with_events 必须是非负整数")
        if (
            isinstance(self.history_seasons, bool)
            or not isinstance(self.history_seasons, int)
            or self.history_seasons < 1
        ):
            raise ValueError("history_seasons 必须是正整数")


def load_prompt_config(path: Path | None = None) -> PromptConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 Prompt 配置：{config_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Prompt 配置必须是 JSON 对象")

    expected = {"recent_matches_with_events", "history_seasons"}
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        extra = sorted(set(raw) - expected)
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if extra:
            details.append("未知 " + ", ".join(extra))
        raise ValueError("Prompt 配置字段错误：" + "；".join(details))

    recent = raw["recent_matches_with_events"]
    seasons = raw["history_seasons"]
    if isinstance(recent, bool) or not isinstance(recent, int) or recent < 0:
        raise ValueError("recent_matches_with_events 必须是非负整数")
    if isinstance(seasons, bool) or not isinstance(seasons, int) or seasons < 1:
        raise ValueError("history_seasons 必须是正整数")
    return PromptConfig(
        recent_matches_with_events=recent,
        history_seasons=seasons,
    )
