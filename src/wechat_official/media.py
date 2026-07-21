"""Download authorised article images, upload them and rewrite the body."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .client import WechatOfficialClient
from .errors import MediaUploadError, WechatTimeout
from .html_tools import collect_media_references, replace_media_urls
from .models import MediaPublishResult

DEFAULT_MEDIA_HOSTS = ("mmbiz.qpic.cn", "mmbiz.qlogo.cn")


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    lower = host.casefold().rstrip(".")
    return any(lower == item or lower.endswith("." + item) for item in allowed_hosts)


def _safe_remote_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MediaUploadError(
            "remote media URL must use HTTPS", stage="media-download"
        )
    if not _host_allowed(parsed.hostname, allowed_hosts):
        raise MediaUploadError(
            "remote media host is not in the configured allowlist",
            stage="media-download",
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise MediaUploadError(
            "remote media URL must not target a private address", stage="media-download"
        )


def _data_image(value: str) -> tuple[str, bytes] | None:
    if not value.lower().startswith("data:image/"):
        return None
    try:
        header, encoded = value.split(",", 1)
        mime_type = header[5:].split(";", 1)[0].casefold()
        if ";base64" not in header.casefold():
            raise ValueError
        extension = mimetypes.guess_extension(mime_type) or ".png"
        return "inline" + extension, base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaUploadError(
            "inline image data is invalid", stage="media-download"
        ) from exc


class MediaPublisher:
    def __init__(
        self,
        wechat_client: WechatOfficialClient,
        *,
        http_client: httpx.AsyncClient | None = None,
        allowed_source_hosts: tuple[str, ...] = DEFAULT_MEDIA_HOSTS,
        max_download_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._wechat = wechat_client
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            headers={"Referer": "https://mp.weixin.qq.com/"},
        )
        self._allowed_source_hosts = allowed_source_hosts
        self._max_download_bytes = max_download_bytes

    async def __aenter__(self) -> "MediaPublisher":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _download(self, url: str) -> tuple[str, bytes]:
        inline = _data_image(url)
        if inline is not None:
            return inline
        _safe_remote_url(url, self._allowed_source_hosts)
        current_url = url
        response: httpx.Response | None = None
        for redirect_count in range(3):
            _safe_remote_url(current_url, self._allowed_source_hosts)
            try:
                response = await self._http.get(current_url, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise WechatTimeout(
                    "media download timed out", stage="media-download", retryable=True
                ) from exc
            except httpx.RequestError as exc:
                raise MediaUploadError(
                    "media download failed", stage="media-download", retryable=True
                ) from exc
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location or redirect_count == 2:
                raise MediaUploadError(
                    "media redirect could not be resolved", stage="media-download"
                )
            current_url = urljoin(current_url, location)
        assert response is not None
        if not 200 <= response.status_code < 300:
            raise MediaUploadError(
                f"media download returned HTTP {response.status_code}",
                stage="media-download",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        content = response.content
        if not content or len(content) > self._max_download_bytes:
            raise MediaUploadError(
                "downloaded image is empty or too large", stage="media-download"
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/gif"}:
            raise MediaUploadError(
                "downloaded resource is not a supported image", stage="media-download"
            )
        path_name = unquote(PurePosixPath(urlparse(current_url).path).name) or "image"
        if mimetypes.guess_type(path_name)[0] != content_type:
            path_name += mimetypes.guess_extension(content_type) or ".png"
        return path_name, content

    async def publish_body_images(self, body_html: str) -> MediaPublishResult:
        references = collect_media_references(body_html)
        unique_urls = tuple(dict.fromkeys(item.url for item in references))
        replacements: dict[str, str] = {}
        for url in unique_urls:
            filename, content = await self._download(url)
            replacements[url] = await self._wechat.upload_content_image(
                filename=filename, content=content
            )
        return MediaPublishResult(
            body_html=replace_media_urls(body_html, replacements),
            replacements=replacements,
        )
