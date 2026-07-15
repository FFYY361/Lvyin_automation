"""Read and extract an authorised, already-published WeChat article."""

from __future__ import annotations

import hashlib
import html as html_std
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree, html

from .errors import (
    SourceAccessBlocked,
    SourceInvalidResponse,
    SourceValidationError,
    WechatTimeout,
)
from .html_tools import collect_media_references, sanitise_html
from .models import ArticleSource


DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_BLOCK_HINTS = (
    "环境异常",
    "访问过于频繁",
    "请完成验证",
    "安全验证",
    "verify",
    "captcha",
)
_ARTICLE_XPATHS = (
    "//*[@id='js_content']",
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' rich_media_content ')]",
    "//article",
)


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    lower_host = host.casefold().rstrip(".")
    return any(
        lower_host == allowed.casefold()
        or lower_host.endswith("." + allowed.casefold())
        for allowed in allowed_hosts
    )


def validate_article_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise SourceValidationError("article URL is invalid", stage="source-url") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourceValidationError(
            "article URL must use HTTPS and include a host", stage="source-url"
        )
    if not _host_allowed(parsed.hostname, allowed_hosts):
        raise SourceValidationError(
            "article URL host is not in the configured allowlist", stage="source-url"
        )


def _first_text(document: etree._Element, xpaths: tuple[str, ...]) -> str | None:
    for xpath in xpaths:
        values = document.xpath(xpath)
        for value in values:
            if isinstance(value, etree._Element):
                text = " ".join(value.itertext()).strip()
            else:
                text = str(value).strip()
            if text:
                return " ".join(text.split())
    return None


def extract_article(raw_html: str, *, source_url: str) -> ArticleSource:
    """Extract metadata and a safe body from a complete published page."""

    try:
        document = html.fromstring(raw_html)
    except (etree.ParserError, ValueError) as exc:
        raise SourceInvalidResponse(
            "article page could not be parsed", stage="source-extract"
        ) from exc

    visible_text = " ".join(document.itertext()).casefold()
    candidates: list[etree._Element] = []
    for xpath in _ARTICLE_XPATHS:
        candidates = [item for item in document.xpath(xpath) if isinstance(item, etree._Element)]
        if candidates:
            break
    if not candidates:
        if any(hint.casefold() in visible_text for hint in _BLOCK_HINTS):
            raise SourceAccessBlocked(
                "article source requires verification or is rate limited",
                stage="source-fetch",
                retryable=True,
            )
        raise SourceInvalidResponse(
            "article body root was not found", stage="source-extract"
        )

    title = _first_text(
        document,
        (
            "//*[@id='activity-name']",
            "//meta[@property='og:title']/@content",
            "//h1[1]",
            "//title[1]",
        ),
    )
    if not title:
        raise SourceInvalidResponse("article title was not found", stage="source-extract")
    author = _first_text(
        document,
        (
            "//*[@id='js_name']",
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' rich_media_meta_nickname ')]",
            "//meta[@name='author']/@content",
        ),
    )

    raw_body = html.tostring(candidates[0], encoding="unicode", method="html")
    body_html = sanitise_html(raw_body, base_url=source_url)
    media = collect_media_references(body_html)
    fingerprint_source = json.dumps(
        {"title": title, "body_html": body_html},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ArticleSource(
        title=title,
        author=author,
        source_url=source_url,
        body_html=body_html,
        media=media,
        content_fingerprint=hashlib.sha256(fingerprint_source).hexdigest(),
    )


class PublishedArticleReader:
    """Fetch only allowlisted public article hosts; no Cookie or bypass logic."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        allowed_hosts: tuple[str, ...] = ("mp.weixin.qq.com",),
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._allowed_hosts = allowed_hosts
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
            },
        )

    async def __aenter__(self) -> "PublishedArticleReader":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def read(self, url: str) -> ArticleSource:
        validate_article_url(url, self._allowed_hosts)
        current_url = url
        response: httpx.Response | None = None
        for redirect_count in range(4):
            try:
                response = await self._http.get(
                    current_url, timeout=self._timeout, follow_redirects=False
                )
            except httpx.TimeoutException as exc:
                raise WechatTimeout(
                    "article source request timed out",
                    stage="source-fetch",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                raise SourceInvalidResponse(
                    "article source request failed", stage="source-fetch", retryable=True
                ) from exc
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location or redirect_count == 3:
                raise SourceInvalidResponse(
                    "article source redirect could not be resolved", stage="source-fetch"
                )
            current_url = urljoin(current_url, location)
            validate_article_url(current_url, self._allowed_hosts)
        assert response is not None
        if not 200 <= response.status_code < 300:
            raise SourceInvalidResponse(
                f"article source returned HTTP {response.status_code}",
                stage="source-fetch",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        final_url = current_url
        validate_article_url(final_url, self._allowed_hosts)
        return extract_article(response.text, source_url=final_url)


def save_article_source(article: ArticleSource, output_dir: str | Path) -> tuple[Path, Path]:
    """Save a standalone local preview plus machine-readable metadata."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / "source.html"
    body_path = directory / "body.html"
    metadata_path = directory / "source.json"
    standalone = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html_std.escape(article.title)}</title>"
        "<style>body{margin:0 auto;max-width:677px;padding:20px;font-family:-apple-system,"
        "BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;box-sizing:border-box}"
        "img{max-width:100%;height:auto}</style></head><body>"
        f"<h1>{html_std.escape(article.title)}</h1>{article.body_html}</body></html>"
    )
    html_path.write_text(standalone, encoding="utf-8")
    body_path.write_text(article.body_html, encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "title": article.title,
                "author": article.author,
                "source_url": article.source_url,
                "content_fingerprint": article.content_fingerprint,
                "media": [
                    {"url": item.url, "kind": item.kind, "location": item.location}
                    for item in article.media
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return html_path, metadata_path
