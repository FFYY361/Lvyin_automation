"""Global manual input files shared by all auto-preview runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from preview import (
    PreviewValidationError,
    parse_preview_config,
    parse_weather_for_date,
)

from .errors import ArtifactValidationError
from .state import read_json_object, write_json

WEATHER_FILE_NAME = "weather.json"
CONFIG_FILE_NAME = "config.json"
EMPTY_WEATHER = {
    "low_c": None,
    "high_c": None,
    "wind_direction": None,
    "wind_level": None,
}
EMPTY_CONFIG = {
    "editors": ["【待填写：编辑】"],
    "reviewers": ["【待填写：责编】"],
    "approvers": ["【待填写：审核】"],
}


@dataclass(frozen=True, slots=True)
class GlobalInputStatus:
    weather_path: Path
    config_path: Path
    weather_created: bool
    weather_date_added: bool
    weather_placeholder: bool
    config_created: bool
    incomplete_config_fields: tuple[str, ...]


def _ensure_weather(
    root: Path,
    preview_date: date,
) -> tuple[Path, bool, bool, bool]:
    path = root / WEATHER_FILE_NAME
    day = preview_date.isoformat()
    created = not path.exists()
    date_added = False
    if created:
        payload: dict[str, object] = {day: dict(EMPTY_WEATHER)}
        write_json(path, payload)
        weather = None
    else:
        payload = read_json_object(path, stage="weather-validation")
        try:
            weather = parse_weather_for_date(payload, preview_date)
        except PreviewValidationError as exc:
            raise ArtifactValidationError(
                f"weather.json 校验失败：{exc}",
                stage="weather-validation",
            ) from exc
        if day not in payload:
            payload[day] = dict(EMPTY_WEATHER)
            write_json(path, payload)
            date_added = True
    return path, created, date_added, weather is None


def _load_or_create_config(root: Path) -> tuple[Path, dict[str, object], bool]:
    path = root / CONFIG_FILE_NAME
    created = not path.exists()
    if created:
        payload: dict[str, object] = {
            key: list(value) for key, value in EMPTY_CONFIG.items()
        }
        write_json(path, payload)
    else:
        payload = read_json_object(path, stage="config-validation")
    return path, payload, created


def _config_error(error: PreviewValidationError) -> ArtifactValidationError:
    return ArtifactValidationError(
        f"config.json 校验失败：{error}",
        stage="config-validation",
    )


def _validate_config(
    payload: dict[str, object],
    *,
    created: bool,
    require_complete: bool,
) -> tuple[str, ...]:
    expected = set(EMPTY_CONFIG)
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(
                "缺少字段：" + ", ".join(f"config.json.{item}" for item in missing)
            )
        if extra:
            details.append(
                "未知字段：" + ", ".join(f"config.json.{item}" for item in extra)
            )
        raise ArtifactValidationError(
            "config.json 字段不符合契约（" + "；".join(details) + "）",
            stage="config-validation",
        )

    incomplete: list[str] = []
    for field in EMPTY_CONFIG:
        value = payload[field]
        if not isinstance(value, list):
            try:
                parse_preview_config(payload)
            except PreviewValidationError as exc:
                raise _config_error(exc) from exc
        if not value:
            incomplete.append(field)
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ArtifactValidationError(
                    f"config.json.{field}[{index}] 必须是非空字符串",
                    stage="config-validation",
                )
    if not incomplete:
        try:
            parse_preview_config(payload)
        except PreviewValidationError as exc:
            raise _config_error(exc) from exc
    elif require_complete and not created:
        raise ArtifactValidationError(
            "config.json 人员数组不能为空："
            + ", ".join(f"config.json.{field}" for field in incomplete),
            stage="config-validation",
        )
    return tuple(incomplete)


def ensure_global_inputs(
    root: Path,
    preview_date: date,
    *,
    require_complete_config: bool,
) -> GlobalInputStatus:
    """Create only missing templates, then validate existing manual content."""

    root.mkdir(parents=True, exist_ok=True)
    weather_path, weather_created, weather_date_added, weather_placeholder = (
        _ensure_weather(root, preview_date)
    )
    config_path, config_payload, config_created = _load_or_create_config(root)
    incomplete = _validate_config(
        config_payload,
        created=config_created,
        require_complete=require_complete_config,
    )

    return GlobalInputStatus(
        weather_path=weather_path,
        config_path=config_path,
        weather_created=weather_created,
        weather_date_added=weather_date_added,
        weather_placeholder=weather_placeholder,
        config_created=config_created,
        incomplete_config_fields=incomplete,
    )
