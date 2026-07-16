"""Small HTML helpers used only while publishing body images."""

from __future__ import annotations

import re

from lxml import etree, html

from .models import MediaReference


_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)


def _sort_attributes(element: etree._Element) -> None:
    if isinstance(element.tag, str):
        attributes = sorted(element.attrib.items())
        element.attrib.clear()
        element.attrib.update(attributes)
    for child in element:
        if isinstance(child, etree._Element):
            _sort_attributes(child)


def collect_media_references(body_html: str) -> tuple[MediaReference, ...]:
    root = html.fromstring(body_html)
    references: list[MediaReference] = []
    seen: set[tuple[str, str, str]] = set()
    tree = root.getroottree()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        location = tree.getpath(element)
        if element.tag.lower() == "img" and element.attrib.get("src"):
            value = element.attrib["src"]
            key = (value, "image", location)
            if key not in seen:
                seen.add(key)
                references.append(MediaReference(value, "image", location))
        style = element.attrib.get("style", "")
        for match in _CSS_URL.finditer(style):
            value = match.group(2).strip()
            key = (value, "background", location)
            if key not in seen:
                seen.add(key)
                references.append(MediaReference(value, "background", location))
    return tuple(references)


def replace_media_urls(body_html: str, replacements: dict[str, str]) -> str:
    root = html.fromstring(body_html)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.lower() == "img":
            source = element.attrib.get("src")
            if source in replacements:
                element.attrib["src"] = replacements[source]
        style = element.attrib.get("style")
        if style:
            for old, new in replacements.items():
                style = style.replace(f'url("{old}")', f'url("{new}")')
                style = style.replace(f"url('{old}')", f'url("{new}")')
                style = style.replace(f"url({old})", f'url("{new}")')
            element.attrib["style"] = style
    _sort_attributes(root)
    return html.tostring(root, encoding="unicode", method="html", with_tail=False)
