"""FastAPI request contracts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompetitionValue(StrEnum):
    MALE = "male"
    FEMALE = "female"
    FUTSAL = "futsal"


class BatchStatus(StrEnum):
    INCOMPLETE = "incomplete"
    READY = "ready"
    DRAFTED = "drafted"


def validate_username(value: str) -> str:
    if not 1 <= len(value) <= 64:
        raise ValueError("username must contain 1-64 characters")
    if any(character.isspace() for character in value):
        raise ValueError("username must not contain whitespace")
    return value


def _names(values: list[str]) -> list[str]:
    if len(values) > 20:
        raise ValueError("a personnel list cannot contain more than 20 names")
    resolved: list[str] = []
    for raw in values:
        name = raw.strip()
        if not name:
            raise ValueError("personnel names must not be empty")
        if len(name) > 100:
            raise ValueError("personnel names cannot exceed 100 characters")
        resolved.append(name)
    return list(dict.fromkeys(resolved))


class LoginRequest(BaseModel):
    username: str
    password: str = Field(min_length=1, max_length=1024)

    _username = field_validator("username")(validate_username)


class THUFootballCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openid: str = Field(min_length=1, max_length=2048)
    session_key: str = Field(min_length=1, max_length=2048)

    @field_validator("openid", "session_key")
    @classmethod
    def validate_credential(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("credential must not be empty")
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ValueError("credential contains an invalid character")
        return value


class CreateBatchesRequest(BaseModel):
    dates: list[date] = Field(min_length=1, max_length=31)
    competitions: list[CompetitionValue] = Field(min_length=1, max_length=3)


class EditorialRequest(BaseModel):
    editors: list[str]
    reviewers: list[str]
    approvers: list[str]

    _editors = field_validator("editors")(_names)
    _reviewers = field_validator("reviewers")(_names)
    _approvers = field_validator("approvers")(_names)


class UpdateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str | None = Field(default=None, max_length=200)
    editors: list[str] | None = None
    reviewers: list[str] | None = None
    approvers: list[str] | None = None

    @field_validator("editors", "reviewers", "approvers")
    @classmethod
    def validate_names(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _names(value)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateBatchRequest":
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        for field in ("editors", "reviewers", "approvers"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must be an array")
        return self


class UpdateMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    writers: list[str] | None = None
    body: str | None = Field(default=None, max_length=100_000)

    @field_validator("writers")
    @classmethod
    def validate_writers(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _names(value)

    @model_validator(mode="after")
    def content_is_present(self) -> "UpdateMatchRequest":
        if self.writers is None and self.body is None:
            raise ValueError("writers or body must be supplied")
        return self


class WeatherRequest(BaseModel):
    condition: str = Field(min_length=1, max_length=100)
    low_c: int = Field(ge=-50, le=60)
    high_c: int = Field(ge=-50, le=60)
    wind_direction: str = Field(min_length=1, max_length=50)
    wind_level: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def temperatures_are_ordered(self) -> "WeatherRequest":
        if self.low_c > self.high_c:
            raise ValueError("low_c must not exceed high_c")
        return self


class CoverMediaIdRequest(BaseModel):
    media_id: str = Field(min_length=1, max_length=512)

    @field_validator("media_id")
    @classmethod
    def normalize_media_id(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("media_id must not be empty")
        return result


class CreateWechatDraftRequest(BaseModel):
    article_ids: list[int] = Field(min_length=1, max_length=8)
    confirm: bool = False

    @field_validator("article_ids")
    @classmethod
    def unique_article_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("article_ids must be positive")
        if len(set(values)) != len(values):
            raise ValueError("article_ids must not contain duplicates")
        return values
