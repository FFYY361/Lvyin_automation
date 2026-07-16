"""Asynchronous WeChat Official Account media and draft adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from .errors import (
    DraftValidationError,
    DraftWriteError,
    MediaUploadError,
    WechatAuthenticationError,
    WechatConfigurationError,
    WechatPermissionError,
    WechatRateLimited,
    WechatTimeout,
)
from .config import DEFAULT_ENV_FILE, load_wechat_env
from .html_tools import collect_media_references
from .models import Article, DraftReceipt


DEFAULT_API_BASE_URL = "https://api.weixin.qq.com"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_AUTH_ERROR_CODES = frozenset({40001, 40013, 40014, 40125, 42001})
_PERMISSION_ERROR_CODES = frozenset({40164, 48001, 48002, 48004})
_RATE_LIMIT_CODES = frozenset({45009, 45011, 45028})
_WECHAT_IMAGE_HOST_SUFFIXES = ("mmbiz.qpic.cn", "mmbiz.qlogo.cn")


@dataclass(frozen=True)
class _AccessToken:
    value: str
    refresh_at: float
    expires_at: float


def _non_empty_secret(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WechatConfigurationError(
            f"{name} must be configured", stage="wechat-configuration"
        )
    return value.strip()


def _api_error(payload: Mapping[str, Any]) -> tuple[int | None, str]:
    raw_code = payload.get("errcode")
    code = raw_code if isinstance(raw_code, int) and not isinstance(raw_code, bool) else None
    raw_message = payload.get("errmsg")
    message = raw_message if isinstance(raw_message, str) else "unknown WeChat API error"
    return code, message[:200]


def _wechat_observed_ip(message: str) -> str | None:
    """Extract only the source IP from a 40164 message, never its request ID."""

    candidates: list[str] = []
    direct = re.search(r"\binvalid\s+ip\s+([0-9A-Fa-f:.]+)", message, re.IGNORECASE)
    if direct:
        candidates.append(direct.group(1))
    candidates.extend(re.findall(r"[0-9A-Fa-f:.]{3,}", message))
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate.strip("[](),.;"))
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(address)
    return None


class WechatOfficialClient:
    """Expose only token, media and draft operations; never publish articles."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_API_BASE_URL,
        timeout: httpx.Timeout | None = None,
        clock: Callable[[], float] = time.time,
        token_refresh_margin: int = 300,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._app_id = _non_empty_secret(app_id, "WECHAT_APP_ID")
        self._app_secret = _non_empty_secret(app_secret, "WECHAT_APP_SECRET")
        if not isinstance(base_url, str) or not base_url.strip():
            raise WechatConfigurationError(
                "base_url must be a non-empty string", stage="wechat-configuration"
            )
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._clock = clock
        self._token_refresh_margin = token_refresh_margin
        self._max_image_bytes = max_image_bytes
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self._timeout)
        self._token: _AccessToken | None = None
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_environment(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_API_BASE_URL,
        env_path: str | Path = DEFAULT_ENV_FILE,
    ) -> "WechatOfficialClient":
        load_wechat_env(env_path)
        return cls(
            app_id=os.getenv("WECHAT_APP_ID", ""),
            app_secret=os.getenv("WECHAT_APP_SECRET", ""),
            http_client=http_client,
            base_url=base_url,
        )

    async def __aenter__(self) -> "WechatOfficialClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    def _invalidate_token(self) -> None:
        self._token = None

    def _raise_api_error(
        self,
        payload: Mapping[str, Any],
        *,
        stage: str,
        media: bool = False,
    ) -> None:
        code, message = _api_error(payload)
        suffix = f" (errcode {code})" if code is not None else ""
        if code in _AUTH_ERROR_CODES:
            raise WechatAuthenticationError(
                "WeChat rejected the configured credential" + suffix,
                stage=stage,
                error_code=code,
            )
        if code in _PERMISSION_ERROR_CODES:
            observed_ip = _wechat_observed_ip(message) if code == 40164 else None
            ip_suffix = f"; observed source IP: {observed_ip}" if observed_ip else ""
            raise WechatPermissionError(
                "WeChat denied the required account or IP permission"
                + suffix
                + ip_suffix,
                stage=stage,
                error_code=code,
                observed_ip=observed_ip,
            )
        if code in _RATE_LIMIT_CODES:
            raise WechatRateLimited(
                "WeChat API rate or quota limit was reached" + suffix,
                stage=stage,
                retryable=True,
                error_code=code,
            )
        error_type = MediaUploadError if media else DraftWriteError
        raise error_type(
            f"WeChat API rejected {stage}: {message}{suffix}",
            stage=stage,
            retryable=code == -1,
            error_code=code,
        )

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = await self._http.request(
                method,
                url,
                params=params,
                json=json_body,
                files=files,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise WechatTimeout(
                "WeChat API request timed out", stage="wechat-http", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise DraftWriteError(
                "WeChat API request failed", stage="wechat-http", retryable=True
            ) from exc
        if not 200 <= response.status_code < 300:
            raise DraftWriteError(
                f"WeChat API returned HTTP {response.status_code}",
                stage="wechat-http",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        try:
            payload = response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise DraftWriteError(
                "WeChat API returned invalid JSON", stage="wechat-response"
            ) from exc
        if not isinstance(payload, Mapping):
            raise DraftWriteError(
                "WeChat API returned a non-object JSON value", stage="wechat-response"
            )
        return payload

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        now = float(self._clock())
        if not force_refresh and self._token is not None and now < self._token.refresh_at:
            return self._token.value
        async with self._token_lock:
            now = float(self._clock())
            if not force_refresh and self._token is not None and now < self._token.refresh_at:
                return self._token.value
            payload = await self._send(
                "POST",
                f"{self._base_url}/cgi-bin/stable_token",
                json_body={
                    "grant_type": "client_credential",
                    "appid": self._app_id,
                    "secret": self._app_secret,
                    "force_refresh": force_refresh,
                },
            )
            token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not isinstance(token, str) or not token or not isinstance(expires_in, int):
                self._raise_api_error(payload, stage="wechat-auth")
            refresh_at = now + max(1, expires_in - self._token_refresh_margin)
            self._token = _AccessToken(
                value=token,
                refresh_at=refresh_at,
                expires_at=now + expires_in,
            )
            return token

    async def _request_with_token(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        media: bool = False,
    ) -> Mapping[str, Any]:
        for attempt in range(2):
            token = await self.get_access_token(force_refresh=attempt == 1)
            request_params = dict(params or {})
            request_params["access_token"] = token
            payload = await self._send(
                method,
                f"{self._base_url}{path}",
                params=request_params,
                json_body=json_body,
                files=files,
            )
            code, _ = _api_error(payload)
            if code in {40001, 40014, 42001} and attempt == 0:
                self._invalidate_token()
                continue
            if code not in (None, 0):
                self._raise_api_error(payload, stage=path, media=media)
            return payload
        raise AssertionError("unreachable token retry state")

    def _validate_image(self, filename: str, content: bytes) -> str:
        if not content:
            raise MediaUploadError("image is empty", stage="media-validation")
        if len(content) > self._max_image_bytes:
            raise MediaUploadError(
                "image exceeds configured byte limit", stage="media-validation"
            )
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type not in {"image/jpeg", "image/png", "image/gif"}:
            raise MediaUploadError(
                "image must be JPEG, PNG or GIF", stage="media-validation"
            )
        return mime_type

    async def upload_content_image(
        self, *, filename: str, content: bytes
    ) -> str:
        mime_type = self._validate_image(filename, content)
        payload = await self._request_with_token(
            "POST",
            "/cgi-bin/media/uploadimg",
            files={"media": (Path(filename).name, content, mime_type)},
            media=True,
        )
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise MediaUploadError(
                "content image upload did not return a URL", stage="media-upload"
            )
        return url

    async def upload_cover(self, *, filename: str, content: bytes) -> str:
        mime_type = self._validate_image(filename, content)
        payload = await self._request_with_token(
            "POST",
            "/cgi-bin/material/add_material",
            params={"type": "image"},
            files={"media": (Path(filename).name, content, mime_type)},
            media=True,
        )
        media_id = payload.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise MediaUploadError(
                "cover upload did not return a media_id", stage="cover-upload"
            )
        return media_id

    def validate_draft(self, article: Article, thumb_media_id: str) -> None:
        if not article.title.strip():
            raise DraftValidationError("draft title is required", stage="draft-validation")
        if not article.body_html.strip():
            raise DraftValidationError("draft body is required", stage="draft-validation")
        if not isinstance(thumb_media_id, str) or not thumb_media_id.strip():
            raise DraftValidationError(
                "permanent cover media_id is required", stage="draft-validation"
            )
        external_images: list[str] = []
        for reference in collect_media_references(article.body_html):
            parsed = urlparse(reference.url)
            host = (parsed.hostname or "").casefold()
            if parsed.scheme in {"http", "https"} and not any(
                host == suffix or host.endswith("." + suffix)
                for suffix in _WECHAT_IMAGE_HOST_SUFFIXES
            ):
                external_images.append(reference.url)
        if external_images:
            raise DraftValidationError(
                f"draft contains {len(external_images)} unhosted image URL(s)",
                stage="draft-validation",
            )

    async def add_draft(
        self,
        article: Article,
        *,
        thumb_media_id: str,
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        self.validate_draft(article, thumb_media_id)
        if fans_only_comments and not open_comments:
            raise DraftValidationError(
                "fans_only_comments requires open_comments",
                stage="draft-validation",
            )
        item: dict[str, Any] = {
            "article_type": "news",
            "title": article.title,
            "author": article.author,
            "digest": article.digest,
            "content": article.body_html,
            "content_source_url": article.source_url,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1 if open_comments else 0,
            "only_fans_can_comment": 1 if fans_only_comments else 0,
        }
        payload = await self._request_with_token(
            "POST", "/cgi-bin/draft/add", json_body={"articles": [item]}
        )
        media_id = payload.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise DraftWriteError(
                "draft creation did not return a media_id", stage="draft-add"
            )
        return DraftReceipt(
            media_id=media_id,
            content_fingerprint=article.content_fingerprint,
            created_at=datetime.now(UTC),
        )

    async def get_draft(self, media_id: str) -> Mapping[str, Any]:
        if not isinstance(media_id, str) or not media_id.strip():
            raise DraftValidationError("draft media_id is required", stage="draft-validation")
        return await self._request_with_token(
            "POST", "/cgi-bin/draft/get", json_body={"media_id": media_id}
        )

    async def count_drafts(self) -> int:
        payload = await self._request_with_token("GET", "/cgi-bin/draft/count")
        total_count = payload.get("total_count")
        if isinstance(total_count, bool) or not isinstance(total_count, int):
            raise DraftWriteError(
                "draft count response did not contain total_count",
                stage="draft-count",
            )
        return total_count

    async def delete_draft(self, media_id: str) -> None:
        if not isinstance(media_id, str) or not media_id.strip():
            raise DraftValidationError("draft media_id is required", stage="draft-validation")
        await self._request_with_token(
            "POST", "/cgi-bin/draft/delete", json_body={"media_id": media_id}
        )
