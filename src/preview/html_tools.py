"""Deterministic local HTML normalisation for preview article bodies."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from lxml import etree, html

from .errors import UnsafeHtml


_DROP_TREE_TAGS = frozenset(
    {
        "script",
        "style",
        "iframe",
        "frame",
        "object",
        "embed",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "option",
        "audio",
        "video",
        "canvas",
        "noscript",
        "mpmusic",
        "mpvoice",
        "qqmusic",
    }
)
_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "caption",
        "circle",
        "code",
        "col",
        "colgroup",
        "dd",
        "defs",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "g",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "line",
        "lineargradient",
        "ol",
        "p",
        "path",
        "polygon",
        "polyline",
        "pre",
        "rect",
        "s",
        "section",
        "small",
        "span",
        "stop",
        "strong",
        "sub",
        "sup",
        "svg",
        "table",
        "tbody",
        "td",
        "text",
        "tfoot",
        "th",
        "thead",
        "tr",
        "tspan",
        "u",
        "ul",
    }
)
_GLOBAL_ATTRIBUTES = frozenset(
    {
        "align",
        "alt",
        "height",
        "role",
        "style",
        "title",
        "valign",
        "width",
    }
)
_TAG_ATTRIBUTES = {
    "a": frozenset({"href", "rel", "target"}),
    "img": frozenset({"src"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
    "svg": frozenset({"fill", "height", "preserveaspectratio", "viewbox", "width"}),
    "path": frozenset({"d", "fill", "stroke", "stroke-width"}),
    "g": frozenset({"fill", "stroke", "transform"}),
    "rect": frozenset({"fill", "height", "rx", "ry", "stroke", "width", "x", "y"}),
    "circle": frozenset({"cx", "cy", "fill", "r", "stroke"}),
    "line": frozenset({"stroke", "stroke-width", "x1", "x2", "y1", "y2"}),
    "polygon": frozenset({"fill", "points", "stroke"}),
    "polyline": frozenset({"fill", "points", "stroke"}),
    "text": frozenset({"fill", "text-anchor", "x", "y"}),
    "tspan": frozenset({"dx", "dy", "x", "y"}),
    "lineargradient": frozenset({"gradienttransform", "id", "x1", "x2", "y1", "y2"}),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
}
_ALLOWED_STYLE_PREFIXES = (
    "align-",
    "aspect-",
    "background",
    "border",
    "box-",
    "color",
    "display",
    "flex",
    "font",
    "gap",
    "grid",
    "height",
    "justify-",
    "letter-",
    "line-",
    "margin",
    "max-",
    "min-",
    "opacity",
    "object-",
    "overflow",
    "padding",
    "table-",
    "text-",
    "transform",
    "vertical-",
    "white-",
    "width",
    "word-",
)
_DANGEROUS_STYLE_VALUE = re.compile(
    r"(?:expression\s*\(|javascript\s*:|vbscript\s*:|-moz-binding)", re.I
)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)


def _safe_url(value: str, *, allow_data_image: bool = False) -> bool:
    stripped = value.strip()
    parsed = urlparse(stripped)
    if parsed.scheme in {"http", "https"}:
        return True
    if allow_data_image and stripped.lower().startswith("data:image/"):
        return True
    if not parsed.scheme and not parsed.netloc:
        return True
    return False


def _normalise_css_urls(value: str, base_url: str) -> str | None:
    if _DANGEROUS_STYLE_VALUE.search(value):
        return None

    invalid = False

    def replace(match: re.Match[str]) -> str:
        nonlocal invalid
        raw_url = match.group(2).strip()
        if not _safe_url(raw_url, allow_data_image=True):
            invalid = True
            return ""
        absolute = urljoin(base_url, raw_url) if base_url else raw_url
        return f'url("{absolute}")'

    result = _CSS_URL.sub(replace, value)
    return None if invalid else result.strip()


def sanitise_style(style: str, *, base_url: str = "") -> str:
    declarations: list[tuple[str, str]] = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        normalised_name = name.strip().lower()
        if not normalised_name or not any(
            normalised_name == prefix.rstrip("-")
            or normalised_name.startswith(prefix)
            for prefix in _ALLOWED_STYLE_PREFIXES
        ):
            continue
        normalised_value = _normalise_css_urls(value.strip(), base_url)
        if normalised_value:
            declarations.append((normalised_name, normalised_value))
    declarations.sort()
    return ";".join(f"{name}:{value}" for name, value in declarations)


def _sort_attributes(element: etree._Element) -> None:
    if isinstance(element.tag, str):
        attributes = sorted(element.attrib.items())
        element.attrib.clear()
        element.attrib.update(attributes)
    for child in element:
        if isinstance(child, etree._Element):
            _sort_attributes(child)


def _sanitise_element(element: etree._Element, *, base_url: str) -> None:
    for child in list(element):
        if not isinstance(child.tag, str):
            element.remove(child)
            continue
        tag = child.tag.lower()
        if tag in _DROP_TREE_TAGS or tag.startswith("mp-"):
            child.drop_tree()
            continue
        _sanitise_element(child, base_url=base_url)
        if tag not in _ALLOWED_TAGS:
            child.drop_tag()

    if not isinstance(element.tag, str):
        return
    tag = element.tag.lower()
    if tag not in _ALLOWED_TAGS:
        return

    lazy_source = element.attrib.get("data-src") if tag == "img" else None
    if lazy_source:
        element.attrib["src"] = lazy_source

    allowed = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, frozenset())
    for name in list(element.attrib):
        lower_name = name.lower()
        if lower_name.startswith("on") or lower_name not in allowed:
            del element.attrib[name]

    if "style" in element.attrib:
        safe_style = sanitise_style(element.attrib["style"], base_url=base_url)
        if safe_style:
            element.attrib["style"] = safe_style
        else:
            del element.attrib["style"]

    for attribute in ("href", "src"):
        if attribute not in element.attrib:
            continue
        value = element.attrib[attribute].strip()
        if not _safe_url(value, allow_data_image=attribute == "src"):
            del element.attrib[attribute]
            continue
        if base_url and not value.lower().startswith("data:"):
            element.attrib[attribute] = urljoin(base_url, value)

    if tag == "a" and "href" in element.attrib:
        element.attrib["rel"] = "noopener noreferrer"
        element.attrib["target"] = "_blank"


def sanitise_html(html_text: str, *, base_url: str = "") -> str:
    """Return one deterministic, safe article-body wrapper."""

    if not isinstance(html_text, str) or not html_text.strip():
        raise UnsafeHtml("article HTML must be a non-empty string", stage="html")
    try:
        fragments = html.fragments_fromstring(html_text)
    except (etree.ParserError, ValueError) as exc:
        raise UnsafeHtml("article HTML could not be parsed", stage="html") from exc
    if (
        len(fragments) == 1
        and isinstance(fragments[0], etree._Element)
        and fragments[0].tag.lower() == "section"
        and fragments[0].attrib.get("data-wechat-article-body") == "1"
    ):
        wrapper = fragments[0]
    else:
        wrapper = html.fragment_fromstring(html_text, create_parent="section")
    wrapper.attrib.clear()
    _sanitise_element(wrapper, base_url=base_url)
    wrapper.attrib["data-wechat-article-body"] = "1"
    _sort_attributes(wrapper)
    return html.tostring(wrapper, encoding="unicode", method="html", with_tail=False)


def sanitise_html_fragment(html_text: str, *, base_url: str = "") -> str:
    wrapper_text = sanitise_html(html_text, base_url=base_url)
    wrapper = html.fromstring(wrapper_text)
    pieces: list[str] = []
    if wrapper.text:
        pieces.append(wrapper.text)
    for child in wrapper:
        pieces.append(html.tostring(child, encoding="unicode", method="html"))
    return "".join(pieces)
