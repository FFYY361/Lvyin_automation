"""Download match data and render reports with TAFA's browser canvas code."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from .client import THUFootballClient
from .errors import ConfigurationError, InvalidResponse, QueryValidationError
from .models import (
    GameDetail,
    GameEvent,
    GameReportFile,
    GameSummary,
    ReportSettings,
)
from .rankings import load_static_outcome_catalog
from .report_validation import validate_game_events

_REPORT_WIDTH = 1600
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LINEUP_EVENTS = frozenset({"START", "APPEARANCE"})
_EVENT_ASSET_NAMES = {
    "ON": "event_on",
    "OFF": "event_off",
    "GOAL": "event_goal",
    "PENALTY": "event_penalty",
    "MISSPENALTY": "event_missed_penalty",
    "OWNGOAL": "event_own_goal",
    "YELLOWCARD": "event_yellow_card",
    "SECONDYELLOWCARD": "event_second_yellow_card",
    "REDCARD": "event_red_card",
}
_WEBSITE_ASSET_NAMES = (
    "start",
    "end",
    "legend_goal",
    "legend_penalty",
    "legend_missed_penalty",
    "legend_own_goal",
    *_EVENT_ASSET_NAMES.values(),
)
_REPORT_DATA_PATTERN = re.compile(
    rb'<pre id="report-data">data:image/png;base64,([^<]+)</pre>'
)
_REPORT_ERROR_PATTERN = re.compile(rb'<pre id="report-data">ERROR:([^<]*)</pre>')

# These are the same local fallbacks loaded by member/game_new.php. Pinning the
# paths to TAFA's copies keeps text measurement and layer geometry aligned with
# the website instead of reimplementing jCanvas approximately.
_JQUERY_URL = (
    "https://www.tafa.org.cn/member/static/jquery-3.6.4.min.js"
)
_JCANVAS_URL = "https://www.tafa.org.cn/member/static/jcanvas.min.js"


def _positive_game_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise QueryValidationError(
            "game_id must be a positive integer",
            stage="validation",
        )
    return value


def _validate_settings(settings: object) -> ReportSettings:
    if not isinstance(settings, ReportSettings):
        raise QueryValidationError(
            "settings must be a ReportSettings value",
            stage="validation",
        )
    for field_name in (
        "include_qr_code",
        "include_time",
        "include_field",
        "include_lineup",
    ):
        if not isinstance(getattr(settings, field_name), bool):
            raise QueryValidationError(
                f"settings.{field_name} must be a boolean",
                stage="validation",
            )
    return settings


def resolve_report_team_name(
    game: GameSummary,
    side: Literal["home", "away"],
) -> str:
    """Resolve the displayed report name, preferring the static full name."""

    team_id = game.home_team_id if side == "home" else game.away_team_id
    catalog = load_static_outcome_catalog()
    static_names = catalog.team_names_by_id.get(team_id)
    if static_names:
        return catalog.teams_by_name[static_names[0]].institution_name
    if side == "home":
        return (
            game.home_team_report_name
            or game.home_team_brief_name
            or game.home_team_name
        )
    return (
        game.away_team_report_name
        or game.away_team_brief_name
        or game.away_team_name
    )


def _report_team_name(
    detail: GameDetail,
    side: Literal["home", "away"],
) -> str:
    return resolve_report_team_name(detail.game, side)


def _report_subtitle(detail: GameDetail) -> str:
    game = detail.game
    subtitle = game.tournament_report_name or game.tournament_name
    if game.stage:
        subtitle += game.stage
    if game.group_name:
        subtitle += f"{game.group_name}组"
    if game.round:
        subtitle += f"第{game.round}轮"
    return subtitle


def _report_time(detail: GameDetail) -> str:
    """Format the report kickoff time with a zero-padded calendar date."""

    local = detail.game.kickoff_local
    return (
        f"{local.year}-{local.month:02d}-{local.day:02d} "
        f"{local.hour:02d}:{local.minute:02d}"
    )


def _event_name(event: GameEvent) -> str:
    prefix = f"{event.kit_number}-" if event.kit_number >= 0 else ""
    note = f"({event.note.strip()})" if event.note and event.note.strip() else ""
    return f"{prefix}{event.player_name.strip()}{note}"


def _event_time(event: GameEvent) -> str:
    if event.during_penalty_shootout:
        return f"{event.minute}'P"
    if event.stoppage_minute > 0:
        return f"{event.minute}'+{event.stoppage_minute}'"
    return f"{event.minute}'"


def _starters(detail: GameDetail, side: str) -> list[GameEvent]:
    return sorted(
        (
            event
            for event in detail.events
            if event.side == side and event.event_type.upper() in _LINEUP_EVENTS
        ),
        key=lambda event: event.kit_number,
    )


def _timeline_groups(detail: GameDetail) -> list[dict[str, Any]]:
    """Mirror the insertion-order grouping performed by TAFA's ``game.js``."""

    yellow_counts: dict[tuple[int, bool], int] = {}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for event in detail.events:
        event_type = event.event_type.upper()
        if event_type in _LINEUP_EVENTS:
            continue
        if event_type == "YELLOWCARD" and event.tournament_team_player_id:
            key = (
                event.tournament_team_player_id,
                event.during_penalty_shootout,
            )
            yellow_counts[key] = yellow_counts.get(key, 0) + 1
            if yellow_counts[key] == 2:
                event_type = "SECONDYELLOWCARD"

        asset_name = _EVENT_ASSET_NAMES.get(event_type)
        if asset_name is None:
            raise InvalidResponse(
                f"unsupported report event type {event_type!r}",
                stage="report",
                game_id=detail.game.game_id,
            )

        time_label = _event_time(event)
        bucket = grouped.setdefault(time_label, {"home": [], "away": []})
        side = event.side
        if event_type == "OWNGOAL":
            side = "away" if side == "home" else "home"
        bucket[side].append(
            {
                "id": event.event_id,
                "type": event_type,
                "asset": asset_name,
                "namestring": _event_name(event),
                "timestring": time_label,
                "during_penalty_shootout": event.during_penalty_shootout,
            }
        )

    return [
        {
            "time": time_label,
            "home": sides["home"],
            "away": sides["away"],
        }
        for time_label, sides in grouped.items()
    ]


def _image_data_url(
    payload: object,
    *,
    label: str,
    game_id: int,
) -> str:
    if not isinstance(payload, bytes):
        raise InvalidResponse(
            f"{label} is missing",
            stage="report",
            game_id=game_id,
        )
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            image_format = image.format
    except (OSError, UnidentifiedImageError) as exc:
        raise InvalidResponse(
            f"{label} returned an unreadable image",
            stage="report",
            game_id=game_id,
        ) from exc

    media_type = {
        "GIF": "image/gif",
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(image_format or "", "image/png")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _report_payload(
    detail: GameDetail,
    *,
    settings: ReportSettings,
    assets: Mapping[str, bytes],
    qr_code: bytes | None,
) -> dict[str, Any]:
    game_id = detail.game.game_id
    if not isinstance(assets, Mapping):
        raise QueryValidationError(
            "assets must be a mapping",
            stage="validation",
            game_id=game_id,
        )

    encoded_assets = {
        name: _image_data_url(
            assets.get(name),
            label=f"website report asset {name!r}",
            game_id=game_id,
        )
        for name in _WEBSITE_ASSET_NAMES
    }
    if settings.include_qr_code:
        encoded_assets["qr_code"] = _image_data_url(
            qr_code,
            label="GetGamePageCode",
            game_id=game_id,
        )

    home_starters = _starters(detail, "home")
    away_starters = _starters(detail, "away")
    metadata: list[str] = []
    if settings.include_time:
        metadata.append(_report_time(detail))
    if settings.include_field and detail.game.field_name:
        metadata.append(detail.game.field_name)

    return {
        "game_id": game_id,
        "home_name": _report_team_name(detail, "home"),
        "away_name": _report_team_name(detail, "away"),
        "home_score": detail.game.home_score or 0,
        "away_score": detail.game.away_score or 0,
        "subtitle": _report_subtitle(detail),
        "metadata": "    ".join(metadata),
        "include_metadata": settings.include_time or settings.include_field,
        "include_lineup": settings.include_lineup,
        "include_qr_code": settings.include_qr_code,
        "home_starters": [_event_name(event) for event in home_starters],
        "away_starters": [_event_name(event) for event in away_starters],
        "squad_title": (
            "出场阵容"
            if any(
                event.event_type.upper() == "APPEARANCE"
                for event in detail.events
            )
            else "首发阵容"
        ),
        "groups": _timeline_groups(detail),
        "assets": encoded_assets,
    }


_REPORT_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light">
<style>
html, body { margin: 0; padding: 0; background: white; }
canvas { display: block; }
#report-data { display: none; }
</style>
<script src="__JQUERY_URL__"></script>
<script src="__JCANVAS_URL__"></script>
</head>
<body>
<canvas id="canvas"></canvas>
<pre id="report-data"></pre>
<script>
"use strict";

const payloadBytes = Uint8Array.from(
    atob("__PAYLOAD_BASE64__"),
    character => character.charCodeAt(0)
);
const report = JSON.parse(new TextDecoder().decode(payloadBytes));

function loadImage(source) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error("Could not load report image"));
        image.src = source;
        if (image.complete && image.naturalWidth > 0) {
            resolve(image);
        }
    });
}

async function renderReport() {
    if (typeof window.jQuery !== "function" ||
            typeof jQuery.fn.drawText !== "function") {
        throw new Error("TAFA jCanvas dependencies did not load");
    }
    await document.fonts.ready;
    const imageEntries = await Promise.all(
        Object.entries(report.assets).map(async ([name, source]) => {
            return [name, await loadImage(source)];
        })
    );
    const images = Object.fromEntries(imageEntries);
    const $canvas = jQuery("#canvas");
    const groups = report.groups;
    let penalty = false;
    let curY = 20;
    let hy = 180;

    $canvas.attr("width", 1600);

    function drawfirst(color, layername, text, x, y) {
        $canvas.drawText({
            layer: true,
            fillStyle: color,
            fontFamily: "WenQuanYi Micro Hei",
            fontSize: 36,
            name: layername,
            text: text,
            fromCenter: false,
            x: x,
            y: y,
            align: "left",
            maxWidth: 600
        });
    }

    function wrapTitle(text, maxWidth) {
        const context = $canvas[0].getContext("2d");
        const lines = [];
        context.save();
        context.font = "bold 50px simHei";
        for (const paragraph of String(text).split(/\r?\n/)) {
            let line = "";
            for (const character of Array.from(paragraph)) {
                const candidate = line + character;
                if (line && context.measureText(candidate).width > maxWidth) {
                    lines.push(line.trimEnd());
                    line = character.trimStart();
                } else {
                    line = candidate;
                }
            }
            lines.push(line.trimEnd());
        }
        context.restore();
        return lines.join("\n");
    }

    function drawtitle(color, layername, text, x, y) {
        $canvas.drawText({
            layer: true,
            fillStyle: "#000",
            fontFamily: "simHei",
            fontStyle: "bold",
            name: layername,
            text: wrapTitle(text, 600),
            x: x,
            y: y,
            fontSize: 50,
            maxWidth: 600
        });
    }

    function drawcenter(font, size, style, layername, text, y) {
        $canvas.drawText({
            layer: true,
            fillStyle: "#000",
            fontStyle: style,
            name: layername,
            x: 800,
            y: y,
            fontSize: size,
            fontFamily: font,
            text: text
        });
    }

    function drawline(layername, x1, x2, y) {
        $canvas.drawLine({
            layer: true,
            name: layername,
            strokeStyle: "#000",
            strokeWidth: 3,
            rounded: true,
            x1: x1,
            y1: y,
            x2: x2,
            y2: y
        });
    }

    function drawtime(layername, time, x, y) {
        $canvas.drawText({
            layer: true,
            name: layername,
            fillStyle: "#000",
            fontFamily: "Trebuchet MS",
            fontSize: 36,
            text: time,
            x: x,
            y: y
        });
    }

    function drawname(color, layername, name, x, y) {
        $canvas.drawText({
            layer: true,
            name: layername,
            fillStyle: color,
            fontFamily: "WenQuanYi Micro Hei",
            fontSize: 40,
            text: name,
            x: x,
            y: y,
            align: "left",
            maxWidth: 550
        });
    }

    function drawplayer(layername, name, x, y) {
        $canvas.drawText({
            layer: true,
            name: layername,
            fillStyle: "#000",
            fontFamily: "WenQuanYi Micro Hei",
            fontSize: 40,
            text: name,
            fromCenter: false,
            x: x,
            y: y,
            align: "left",
            maxWidth: 550
        });
    }

    function drawrect(layername, x, y, width, height) {
        $canvas.drawRect({
            layer: true,
            name: layername,
            strokeStyle: "#000",
            strokeWidth: 3,
            x: x,
            y: y,
            width: width,
            height: height,
            cornerRadius: 10
        });
    }

    function drawicon(layername, event, x, y) {
        $canvas.drawImage({
            layer: true,
            name: layername,
            source: images[event.asset],
            x: x,
            y: y,
            fromCenter: false
        });
    }

    for (const group of groups) {
        const hside = group.home;
        const aside = group.away;
        let hrectheight = 10;
        let arectheight = 10;
        if (hside.length !== 0) {
            for (const event of hside) {
                drawname(
                    "rgba(0, 0, 0, 0)",
                    "measure" + event.namestring,
                    event.namestring,
                    800,
                    0
                );
                hrectheight += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
            if (hside[0].during_penalty_shootout) {
                penalty = true;
            }
        }
        if (aside.length !== 0) {
            for (const event of aside) {
                drawname(
                    "rgba(0, 0, 0, 0)",
                    "measure" + event.namestring,
                    event.namestring,
                    800,
                    0
                );
                arectheight += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
            if (aside[0].during_penalty_shootout) {
                penalty = true;
            }
        }
        let halfh = Math.max(hrectheight, arectheight) / 2 + 10;
        if (hside.length === 0 && aside.length === 0) {
            halfh = -20;
        }
        hy += 2 * halfh + 40;
    }

    const hfstr = report.home_starters
        .map(name => name + "    ")
        .join("");
    const afstr = report.away_starters
        .map(name => name + "    ")
        .join("");

    drawtitle(
        "rgba(0, 0, 0, 0)",
        "measurehtitle",
        report.home_name,
        0,
        curY
    );
    drawtitle(
        "rgba(0, 0, 0, 0)",
        "measureatitle",
        report.away_name,
        0,
        curY
    );
    const htitle = Math.max(
        $canvas.measureText("measurehtitle").height,
        $canvas.measureText("measureatitle").height
    );
    hy += htitle;

    drawfirst("rgba(0, 0, 0, 0)", "measurehfirst", hfstr, 0, curY);
    drawfirst("rgba(0, 0, 0, 0)", "measureafirst", afstr, 0, curY);
    if (report.include_lineup) {
        const hf = Math.max(
            $canvas.measureText("measurehfirst").height,
            $canvas.measureText("measureafirst").height
        ) / 2;
        hy += Math.max(hf, 70) + hf;
    }
    hy += 80;
    if (report.include_metadata) {
        hy += 40;
    }
    if (report.include_qr_code) {
        hy += 20;
    }
    if (penalty) {
        hy += 80;
    }

    $canvas.attr("height", hy);
    $canvas.drawRect({
        layer: true,
        name: "background",
        fillStyle: "white",
        x: 0,
        y: 0,
        width: 1600,
        height: hy,
        fromCenter: false
    });

    curY += htitle / 2;
    drawtitle("#000", "homename", report.home_name, 400, curY);
    drawtitle("#000", "awayname", report.away_name, 1200, curY);
    curY += htitle / 2 + 20;
    if (penalty) {
        curY += 80;
    }

    drawcenter("simHei", 25, "normal", "subtitle", report.subtitle, curY);
    curY += $canvas.measureText("subtitle").height + 15;
    if (report.include_metadata) {
        drawcenter(
            "STFangsong",
            22,
            "normal",
            "time_field",
            report.metadata,
            curY
        );
        curY += $canvas.measureText("time_field").height + 15;
    }

    if (report.include_lineup) {
        drawfirst("#000", "homefirst", hfstr, 0, curY);
        drawfirst("#000", "awayfirst", afstr, 1000, curY);
        const firstheight = Math.max(
            $canvas.measureText("homefirst").height,
            $canvas.measureText("awayfirst").height
        );
        curY += firstheight / 2 - 10;
        drawcenter(
            "simHei",
            38,
            "normal",
            "First",
            report.squad_title,
            curY
        );
        curY += Math.max(
            $canvas.measureText("First").height + 30,
            firstheight / 2
        );
    }

    $canvas.drawImage({
        layer: true,
        name: "starticon",
        source: images.start,
        x: 800,
        y: curY
    });
    curY += 30;
    $canvas.drawArc({
        layer: true,
        name: "startpoint",
        fillStyle: "black",
        x: 800,
        y: curY,
        radius: 8
    });
    const startY = curY;
    curY += 50;

    let phg = 0;
    let pag = 0;
    for (const group of groups) {
        const time = group.time;
        const hside = group.home;
        const aside = group.away;
        let hrectwidth = 0;
        let hrectheight = 10;
        let arectwidth = 0;
        let arectheight = 10;

        if (hside.length !== 0) {
            for (const event of hside) {
                drawname(
                    "rgba(0, 0, 0, 0)",
                    "measure" + event.namestring,
                    event.namestring,
                    800,
                    0
                );
                hrectwidth = Math.max(
                    hrectwidth,
                    $canvas.measureText("measure" + event.namestring).width
                );
                hrectheight += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
            hrectwidth += 100;
        }
        if (aside.length !== 0) {
            for (const event of aside) {
                drawname(
                    "rgba(0, 0, 0, 0)",
                    "measure" + event.namestring,
                    event.namestring,
                    800,
                    0
                );
                arectwidth = Math.max(
                    arectwidth,
                    $canvas.measureText("measure" + event.namestring).width
                );
                arectheight += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
            arectwidth += 100;
        }

        let halfh = Math.max(hrectheight, arectheight) / 2 + 10;
        if (hside.length === 0 && aside.length === 0) {
            halfh = -20;
        }
        curY += halfh;

        if (hside.length !== 0) {
            drawline("htimeline" + time, 680, 800, curY);
            drawtime(
                "htime" + time,
                hside[0].timestring,
                740,
                curY - 20
            );
            drawrect(
                "hrect" + time,
                680 - hrectwidth / 2,
                curY,
                hrectwidth,
                hrectheight
            );
        }
        if (aside.length !== 0) {
            drawline("atimeline" + time, 800, 920, curY);
            drawtime(
                "atime" + time,
                aside[0].timestring,
                860,
                curY - 20
            );
            drawrect(
                "arect" + time,
                920 + arectwidth / 2,
                curY,
                arectwidth,
                arectheight
            );
        }

        if (hside.length !== 0) {
            let ey = curY - hrectheight / 2 + 10;
            for (const event of hside) {
                drawicon(
                    "icon" + event.id,
                    event,
                    680 - hrectwidth + 20,
                    ey
                );
                drawplayer(
                    "name" + event.id,
                    event.namestring,
                    680 - hrectwidth + 70,
                    ey
                );
                if (event.type === "PENALTY" &&
                        event.during_penalty_shootout) {
                    phg += 1;
                }
                ey += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
        }
        if (aside.length !== 0) {
            let ey = curY - arectheight / 2 + 10;
            for (const event of aside) {
                drawicon("icon" + event.id, event, 940, ey);
                drawplayer(
                    "name" + event.id,
                    event.namestring,
                    990,
                    ey
                );
                if (event.type === "PENALTY" &&
                        event.during_penalty_shootout) {
                    pag += 1;
                }
                ey += (
                    $canvas.measureText("measure" + event.namestring).height
                    + 10
                );
            }
        }
        curY += halfh + 40;
    }

    if (penalty) {
        const pscore = "(" + phg.toString() + ":" + pag.toString() + ")";
        drawcenter(
            "Trebuchet MS",
            50,
            "bold",
            "pscore",
            pscore,
            htitle / 2 + 90
        );
    }

    const score = (
        report.home_score.toString()
        + ":"
        + report.away_score.toString()
    );
    drawcenter(
        "Trebuchet MS",
        60,
        "bold",
        "score",
        score,
        htitle / 2 + 20
    );
    $canvas.drawLine({
        layer: true,
        name: "timeline",
        strokeStyle: "#000",
        strokeWidth: 6,
        rounded: true,
        x1: 800,
        y1: startY,
        x2: 800,
        y2: curY
    });
    $canvas.drawArc({
        layer: true,
        name: "endpoint",
        fillStyle: "black",
        x: 800,
        y: curY,
        radius: 8
    });
    curY += 20;
    $canvas.drawImage({
        layer: true,
        name: "endicon",
        source: images.end,
        x: 800,
        y: curY
    });

    curY += 40;
    if (report.include_qr_code) {
        curY += 20;
    }

    const legendItems = [
        ["legend_goal", "goaltext", "进球"],
        ["legend_penalty", "pgtext", "点球"],
        ["legend_missed_penalty", "pmtext", "点球罚失"],
        ["legend_own_goal", "ogtext", "乌龙球"]
    ];
    const measuredLegendItems = [];
    for (const [imageName, textName, text] of legendItems) {
        const measureName = "measure_" + textName;
        $canvas.drawText({
            layer: true,
            name: measureName,
            fillStyle: "rgba(0, 0, 0, 0)",
            x: 0,
            y: 0,
            fontSize: 24,
            fontFamily: "simHei",
            text: text
        });
        const textWidth = $canvas.measureText(measureName).width;
        const imageWidth = images[imageName].naturalWidth;
        measuredLegendItems.push({
            imageName: imageName,
            textName: textName,
            text: text,
            imageWidth: imageWidth,
            imageHeight: images[imageName].naturalHeight,
            textWidth: textWidth,
            width: imageWidth + 8 + textWidth
        });
    }
    const legendWidth = measuredLegendItems.reduce(
        (width, item) => width + item.width,
        20 * (measuredLegendItems.length - 1)
    );
    let legendX = 800 - legendWidth / 2;
    for (const item of measuredLegendItems) {
        $canvas.drawImage({
            layer: true,
            name: item.imageName,
            source: images[item.imageName],
            x: legendX + item.imageWidth / 2,
            y: curY
        });
        $canvas.drawText({
            layer: true,
            name: item.textName,
            fillStyle: "#111",
            x: legendX + item.imageWidth + 8 + item.textWidth / 2,
            y: curY,
            fontSize: 24,
            fontFamily: "simHei",
            text: item.text
        });
        legendX += item.width + 20;
    }

    if (report.include_qr_code) {
        $canvas.drawImage({
            layer: true,
            name: "qrcode",
            source: images.qr_code,
            width: 140,
            height: 140,
            x: 1520,
            y: curY - 50
        });
    }

    await new Promise(resolve => requestAnimationFrame(resolve));
    await new Promise(resolve => requestAnimationFrame(resolve));
    document.getElementById("report-data").textContent =
        document.getElementById("canvas").toDataURL("image/png");
}

renderReport().catch(error => {
    document.getElementById("report-data").textContent =
        "ERROR:" + String(error && error.message ? error.message : error);
});
</script>
</body>
</html>
"""


def _build_report_html(payload: Mapping[str, Any]) -> str:
    import json

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.b64encode(serialized).decode("ascii")
    return (
        _REPORT_HTML.replace("__JQUERY_URL__", _JQUERY_URL)
        .replace("__JCANVAS_URL__", _JCANVAS_URL)
        .replace("__PAYLOAD_BASE64__", encoded)
    )


def _configured_browser() -> Path | None:
    configured = os.environ.get("THUFOOTBALL_CHROMIUM")
    if not configured:
        return None
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    resolved = shutil.which(configured)
    if resolved:
        return Path(resolved).resolve()
    raise ConfigurationError(
        "THUFOOTBALL_CHROMIUM does not identify a browser executable",
        stage="configuration",
    )


def _find_browser() -> Path:
    configured = _configured_browser()
    if configured is not None:
        return configured

    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if not root:
            continue
        candidates.extend(
            (
                Path(root) / "Microsoft/Edge/Application/msedge.exe",
                Path(root) / "Google/Chrome/Application/chrome.exe",
            )
        )
    candidates.extend(
        (
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    for command in (
        "msedge",
        "microsoft-edge",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        resolved = shutil.which(command)
        if resolved:
            return Path(resolved).resolve()

    raise ConfigurationError(
        "pixel-exact report rendering requires Microsoft Edge or Chromium; "
        "install one or set THUFOOTBALL_CHROMIUM",
        stage="configuration",
    )


def _render_html_to_png(
    html_source: str,
    *,
    game_id: int,
) -> bytes:
    browser = _find_browser()
    try:
        with tempfile.TemporaryDirectory(
            prefix="thufootball-report-"
        ) as directory:
            temporary = Path(directory)
            html_path = temporary / "report.html"
            profile_path = temporary / "browser-profile"
            html_path.write_text(html_source, encoding="utf-8")
            command = [
                str(browser),
                "--headless",
                "--hide-scrollbars",
                "--no-first-run",
                "--disable-extensions",
                f"--user-data-dir={profile_path}",
                "--virtual-time-budget=10000",
                "--dump-dom",
                html_path.resolve().as_uri(),
            ]
            run_options: dict[str, Any] = {}
            if os.name == "nt":
                run_options["creationflags"] = subprocess.CREATE_NO_WINDOW
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=45,
                **run_options,
            )
    except subprocess.TimeoutExpired as exc:
        raise InvalidResponse(
            "browser report renderer timed out",
            stage="report",
            retryable=True,
            game_id=game_id,
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            "could not start the browser report renderer",
            stage="configuration",
            game_id=game_id,
        ) from exc

    if completed.returncode != 0:
        raise InvalidResponse(
            "browser report renderer failed",
            stage="report",
            retryable=True,
            game_id=game_id,
        )

    match = _REPORT_DATA_PATTERN.search(completed.stdout)
    if match is None:
        error_match = _REPORT_ERROR_PATTERN.search(completed.stdout)
        message = "browser report renderer produced no PNG"
        if error_match is not None:
            detail = error_match.group(1).decode("utf-8", errors="replace")
            message = f"browser report renderer failed: {detail}"
        raise InvalidResponse(
            message,
            stage="report",
            retryable=True,
            game_id=game_id,
        )
    try:
        return base64.b64decode(match.group(1), validate=True)
    except ValueError as exc:
        raise InvalidResponse(
            "browser report renderer returned malformed PNG data",
            stage="report",
            game_id=game_id,
        ) from exc


def render_game_report(
    detail: GameDetail,
    *,
    settings: ReportSettings,
    assets: Mapping[str, bytes],
    qr_code: bytes | None = None,
) -> tuple[bytes, int, int]:
    """Run TAFA's jCanvas layout in Chromium and return its canvas PNG."""

    settings = _validate_settings(settings)
    game_id = detail.game.game_id
    if settings.include_qr_code and qr_code is None:
        raise InvalidResponse(
            "the requested report QR image is missing",
            stage="report",
            game_id=game_id,
        )
    payload = _report_payload(
        detail,
        settings=settings,
        assets=assets,
        qr_code=qr_code,
    )
    png = _render_html_to_png(
        _build_report_html(payload),
        game_id=game_id,
    )
    try:
        with Image.open(BytesIO(png)) as image:
            image.load()
            width, height = image.size
            image_format = image.format
            rendered = image.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise InvalidResponse(
            "browser report renderer returned an unreadable image",
            stage="report",
            game_id=game_id,
        ) from exc
    if image_format != "PNG" or width != _REPORT_WIDTH:
        raise InvalidResponse(
            "browser report renderer returned unexpected image dimensions",
            stage="report",
            game_id=game_id,
        )
    output = BytesIO()
    rendered.save(
        output,
        format="PNG",
        optimize=True,
        dpi=(96, 96),
    )
    return output.getvalue(), width, height


def _safe_filename(detail: GameDetail) -> str:
    game = detail.game
    score = (
        f"{game.home_score}-{game.away_score}"
        if game.home_score is not None and game.away_score is not None
        else "vs"
    )
    filename = (
        f"game_{game.game_id}_"
        f"{_report_team_name(detail, 'home')}_{score}_"
        f"{_report_team_name(detail, 'away')}.png"
    )
    filename = _INVALID_FILENAME.sub("_", filename)
    filename = re.sub(r"\s+", "_", filename).strip(" ._")
    return filename or f"game_{game.game_id}_report.png"


def _output_path(
    output: str | os.PathLike[str] | None,
    detail: GameDetail,
) -> Path:
    if output is None:
        return Path.cwd() / _safe_filename(detail)
    target = Path(output).expanduser()
    if target.exists() and target.is_dir():
        return target / _safe_filename(detail)
    if target.suffix.casefold() != ".png":
        target = target.with_suffix(".png")
    return target


class THUFootballReportService:
    """Read game data and save a PNG drawn by TAFA's browser canvas code."""

    def __init__(self, client: THUFootballClient) -> None:
        self._client = client

    async def download_game_report(
        self,
        game_id: int,
        output: str | os.PathLike[str] | None = None,
        *,
        settings: ReportSettings = ReportSettings(),
        refresh_stats: bool = False,
        overwrite: bool = False,
    ) -> GameReportFile:
        game_id = _positive_game_id(game_id)
        settings = _validate_settings(settings)
        if not isinstance(refresh_stats, bool):
            raise QueryValidationError(
                "refresh_stats must be a boolean",
                stage="validation",
                game_id=game_id,
            )
        if not isinstance(overwrite, bool):
            raise QueryValidationError(
                "overwrite must be a boolean",
                stage="validation",
                game_id=game_id,
            )

        if refresh_stats:
            # SECURITY: OnReStatGameData uses GET but changes server-side match
            # statistics. Keep this behind the explicit refresh_stats opt-in.
            await self._client.refresh_game_stats(game_id)
        detail = await self._client.get_game_info(game_id)
        detail, warnings = validate_game_events(detail)
        qr_code = (
            await self._client.get_game_page_code(game_id)
            if settings.include_qr_code
            else None
        )
        asset_payloads = await asyncio.gather(
            *(
                self._client.get_report_asset(name)
                for name in _WEBSITE_ASSET_NAMES
            )
        )
        assets = dict(
            zip(_WEBSITE_ASSET_NAMES, asset_payloads, strict=True)
        )
        png, width, height = await asyncio.to_thread(
            render_game_report,
            detail,
            settings=settings,
            assets=assets,
            qr_code=qr_code,
        )

        target = _output_path(output, detail).resolve()
        if target.exists() and not overwrite:
            raise ConfigurationError(
                "output file already exists; pass --override in the CLI "
                "or overwrite=True in Python to replace it",
                stage="output",
                game_id=game_id,
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(png)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ConfigurationError(
                "could not write the report output file",
                stage="output",
                game_id=game_id,
            ) from exc

        return GameReportFile(
            game_id=game_id,
            path=str(target),
            media_type="image/png",
            width=width,
            height=height,
            refreshed_stats=refresh_stats,
            warnings=warnings,
        )
