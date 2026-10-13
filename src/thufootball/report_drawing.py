"""Measure and draw report content at its final resolution with Pillow."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .errors import ConfigurationError, InvalidResponse

WIDTH = 1600
LEGEND_ITEMS = (
    ("legend_goal", "进球"),
    ("legend_penalty", "点球"),
    ("legend_missed_penalty", "点球罚失"),
    ("legend_own_goal", "乌龙球"),
)
_FONT_FILES = {
    "title": ("simhei.ttf", 50),
    "lineup": ("NotoSansSC-VF.ttf", 36),
    "player": ("NotoSansSC-VF.ttf", 40),
    "score": ("trebucbd.ttf", 60),
    "penalty": ("trebucbd.ttf", 50),
    "minute": ("trebuc.ttf", 36),
    "subtitle": ("simhei.ttf", 25),
    "squad": ("simhei.ttf", 38),
    "legend": ("simhei.ttf", 24),
    "metadata": ("STFANGSO.TTF", 22),
}
ASSET_DIRECTORY = Path(__file__).with_name("assets")


def _font_directory() -> Path:
    configured = os.environ.get("THUFOOTBALL_FONT_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    raise ConfigurationError(
        "set THUFOOTBALL_FONT_DIR to the directory containing report fonts",
        stage="configuration",
    )


@lru_cache(maxsize=32)
def _load_font(directory: Path, role: str) -> ImageFont.FreeTypeFont:
    filename, size = _FONT_FILES[role]
    path = directory / filename
    try:
        # Pillow otherwise searches system font directories for the basename.
        if not path.is_file():
            raise OSError("font file does not exist")
        font = ImageFont.truetype(str(path), size)
        if filename == "NotoSansSC-VF.ttf":
            axes = font.get_variation_axes()
            weights = [axis["default"] for axis in axes]
            for index, axis in enumerate(axes):
                if axis["name"] == b"Weight":
                    weights[index] = 400
            font.set_variation_by_axes(weights)
        return font
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"report font is missing or unreadable: {path}", stage="configuration"
        ) from exc


@lru_cache(maxsize=64)
def _decode_asset(payload: bytes) -> Image.Image:
    with Image.open(BytesIO(payload)) as source:
        source.load()
        return source.convert("RGBA")


def decode_image(payload: bytes | None, label: str, game_id: int) -> Image.Image:
    try:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("empty image")
        return _decode_asset(payload)
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise InvalidResponse(
            f"{label} returned an unreadable image", stage="report", game_id=game_id
        ) from exc


@lru_cache(maxsize=1)
def load_static_assets() -> Mapping[str, bytes]:
    """Read bundled originals once; never request them during report generation."""
    from .client import _REPORT_ASSET_ENDPOINTS

    try:
        return {
            name: (ASSET_DIRECTORY / Path(endpoint).name).read_bytes()
            for name, endpoint in _REPORT_ASSET_ENDPOINTS.items()
        }
    except OSError as exc:
        raise ConfigurationError(
            f"bundled report icon is missing or unreadable: {exc.filename}",
            stage="configuration",
        ) from exc


@dataclass
class TextBlock:
    lines: tuple[str, ...]
    font: ImageFont.FreeTypeFont
    bounds: tuple[tuple[int, int, int, int], ...]
    width: int
    height: int
    pitch: int
    stroke: int = 0


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for character in paragraph:
            candidate = line + character
            # Include the ink boundary as well as the advance (italic overhangs).
            bounds = font.getbbox(candidate)
            width = max(font.getlength(candidate), bounds[2] - bounds[0])
            if line and width > max_width:
                lines.append(line.rstrip())
                line = character.lstrip()
            else:
                line = candidate
        lines.append(line.rstrip())
    return lines


def _text(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, *, stroke: int = 0
) -> TextBlock:
    lines = tuple(_wrap(text, font, max_width - 2 * stroke))
    bounds = tuple(font.getbbox(line, stroke_width=stroke) for line in lines)
    pitch = max(font.size, *(box[3] - box[1] for box in bounds))
    return TextBlock(
        lines,
        font,
        bounds,
        math.ceil(max((box[2] - box[0] for box in bounds), default=0)),
        pitch * len(lines),
        pitch,
        stroke,
    )


def _lineup(names: list[str], font: ImageFont.FreeTypeFont) -> TextBlock:
    lines: list[str] = []
    line = ""
    for name in names:
        candidate = f"{line}    {name}" if line else name
        if font.getlength(candidate) <= 600:
            line = candidate
        else:
            if line:
                lines.append(line)
            wrapped = _wrap(name, font, 600)
            lines.extend(wrapped[:-1])
            line = wrapped[-1]
    if line:
        lines.append(line)
    return _text("\n".join(lines), font, 600)


@dataclass
class ReportLayout:
    height: int = 0
    texts: list[tuple[TextBlock, float, float, bool]] = field(default_factory=list)
    images: list[tuple[Image.Image, int, int]] = field(default_factory=list)
    rectangles: list[tuple[int, int, int, int]] = field(default_factory=list)
    lines: list[tuple[tuple[int, int, int, int], int]] = field(default_factory=list)
    dots: list[tuple[int, int]] = field(default_factory=list)


def measure_report(
    content: Mapping[str, Any], assets: Mapping[str, bytes], qr_code: bytes | None
) -> ReportLayout:
    """First pass: resolve images/fonts and compute every element's bounds."""
    from .client import _REPORT_ASSET_ENDPOINTS

    game_id = content["game_id"]
    images = {
        name: decode_image(assets.get(name), f"report asset {name!r}", game_id)
        for name in _REPORT_ASSET_ENDPOINTS
    }
    fonts = {role: _load_font(_font_directory(), role) for role in _FONT_FILES}
    layout = ReportLayout()

    def centered(text: str, role: str, x: float, y: float, width: int) -> TextBlock:
        block = _text(text, fonts[role], width, stroke=int(role == "title"))
        layout.texts.append((block, x, y, True))
        return block

    home = _text(content["home_name"], fonts["title"], 600, stroke=1)
    away = _text(content["away_name"], fonts["title"], 600, stroke=1)
    title_height = max(home.height, away.height)
    layout.texts.extend(((home, 400, 20, True), (away, 1200, 20, True)))
    score = f"{content['home_score']}:{content['away_score']}"
    score_block = _text(score, fonts["score"], 190)
    layout.texts.append(
        (score_block, 800, 20 + (title_height - score_block.height) / 2, True)
    )
    y = 20 + title_height + 20
    shootout = [
        event
        for group in content["groups"]
        for side in ("home", "away")
        for event in group[side]
        if event["during_penalty_shootout"]
    ]
    if shootout:
        scores = [
            sum(
                event["type"] == "PENALTY" and event["during_penalty_shootout"]
                for group in content["groups"]
                for event in group[side]
            )
            for side in ("home", "away")
        ]
        centered(f"({scores[0]}:{scores[1]})", "penalty", 800, y, 190)
        y += 80
    subtitle = centered(content["subtitle"], "subtitle", 800, y, 1500)
    y += subtitle.height + 15
    if content["metadata"]:
        metadata = centered(content["metadata"], "metadata", 800, y, 1500)
        y += metadata.height + 15
    if content["include_lineup"]:
        home = _lineup(content["home_starters"], fonts["lineup"])
        away = _lineup(content["away_starters"], fonts["lineup"])
        lineup_height = max(home.height, away.height)
        layout.texts.extend(((home, 0, y, False), (away, 1000, y, False)))
        squad = _text(content["squad_title"], fonts["squad"], 360)
        squad_center = y + lineup_height / 2 - 10
        layout.texts.append((squad, 800, squad_center - squad.height / 2, True))
        # The central gap lets the start icon align with the final lineup row.
        y = squad_center + max(squad.height + 30, lineup_height / 2)
    start = images["start"]
    layout.images.append((start, 800 - start.width // 2, round(y - start.height / 2)))
    y += 30
    axis_start = round(y)
    layout.dots.append((800, axis_start))
    y += 50

    for group in content["groups"]:
        cards = {}
        for side in ("home", "away"):
            rows = []
            for event in group[side]:
                block = _text(event["namestring"], fonts["player"], 550)
                icon = images[event["asset"]]
                rows.append((block, icon, max(block.height, icon.height)))
            if rows:
                width = max(row[0].width for row in rows) + 100
                height = 20 + sum(row[2] for row in rows) + 10 * (len(rows) - 1)
                cards[side] = (rows, width, height)
        if not cards:
            continue
        minute = _text(group["time"], fonts["minute"], 120)
        half_height = max(
            max(card[2] for card in cards.values()) / 2 + 10, minute.height + 5
        )
        center_y = round(y + half_height)
        for side, (rows, width, height) in cards.items():
            home_side = side == "home"
            left = 680 - width if home_side else 920
            top = round(center_y - height / 2)
            layout.rectangles.append((left, top, left + width, top + height))
            connector = (
                (680, center_y, 800, center_y)
                if home_side
                else (800, center_y, 920, center_y)
            )
            layout.lines.append((connector, 3))
            layout.texts.append(
                (
                    minute,
                    740 if home_side else 860,
                    center_y - 20 - minute.height / 2,
                    True,
                )
            )
            row_y = top + 10
            for block, icon, row_height in rows:
                layout.images.append(
                    (icon, left + 20, round(row_y + (row_height - icon.height) / 2))
                )
                layout.texts.append(
                    (block, left + 70, row_y + (row_height - block.height) / 2, False)
                )
                row_y += row_height + 10
        y = center_y + half_height + 40

    axis_end = round(y)
    layout.lines.insert(0, ((800, axis_start, 800, axis_end), 6))
    layout.dots.append((800, axis_end))
    end = images["end"]
    end_top = axis_end + 20 - end.height // 2
    layout.images.append((end, 800 - end.width // 2, end_top))
    legend_y = end_top + end.height + 20
    if content["include_qr_code"]:
        legend_y += 20
    legends = [
        (images[name], _text(label, fonts["legend"], 300))
        for name, label in LEGEND_ITEMS
    ]
    legend_width = sum(icon.width + 8 + block.width for icon, block in legends) + 60
    x = (WIDTH - legend_width) / 2
    bottom = legend_y
    for icon, block in legends:
        height = max(icon.height, block.height)
        layout.images.append((icon, round(x), round(legend_y - icon.height / 2)))
        layout.texts.append(
            (block, x + icon.width + 8, legend_y - block.height / 2, False)
        )
        bottom = max(bottom, legend_y + height / 2)
        x += icon.width + 8 + block.width + 20
    if content["include_qr_code"]:
        # QR codes intentionally bypass the static-image decode cache.
        try:
            with Image.open(BytesIO(qr_code or b"")) as source:
                source.load()
                qr = source.convert("RGBA").resize((140, 140), Image.Resampling.LANCZOS)
        except (OSError, ValueError) as exc:
            raise InvalidResponse(
                "GetGamePageCode returned an unreadable image",
                stage="report",
                game_id=game_id,
            ) from exc
        qr_top = round(legend_y - 120)
        layout.images.append((qr, 1450, qr_top))
        bottom = max(bottom, qr_top + 140)
    layout.height = math.ceil(bottom + 30)
    return layout


def draw_report(layout: ReportLayout) -> Image.Image:
    """Second pass: draw measured elements directly on the final RGB canvas."""
    image = Image.new("RGB", (WIDTH, layout.height), "white")
    draw = ImageDraw.Draw(image)
    for coordinates, width in layout.lines:
        draw.line(coordinates, fill="black", width=width)
    for x, y in layout.dots:
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill="black")
    for rectangle in layout.rectangles:
        draw.rounded_rectangle(
            rectangle, radius=10, fill="white", outline="black", width=3
        )
    for icon, x, y in layout.images:
        image.paste(icon, (x, y), icon)
    for block, x, y, centered in layout.texts:
        for index, (line, bounds) in enumerate(
            zip(block.lines, block.bounds, strict=True)
        ):
            ink_width = bounds[2] - bounds[0]
            ink_height = bounds[3] - bounds[1]
            line_x = x - ink_width / 2 if centered else x
            line_y = y + index * block.pitch + (block.pitch - ink_height) / 2
            draw.text(
                (line_x - bounds[0], line_y - bounds[1]),
                line,
                font=block.font,
                fill="black",
                stroke_width=block.stroke,
            )
    return image


def encode_report(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=6, dpi=(96, 96))
    return output.getvalue()
