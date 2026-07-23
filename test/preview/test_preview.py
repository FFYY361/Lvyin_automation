from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from lxml import html as lxml_html

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from preview import (
    PreviewService,
    PreviewTemplate,
    PreviewValidationError,
    PreviewWeather,
    SeasonOutcome,
    TemplateContractError,
    load_preview_bundle,
    load_preview_template,
    parse_preview_bundle,
    parse_preview_document,
)
from preview.cli import main as cli_main
from preview.html_tools import sanitise_html
from preview.template import (
    DEFAULT_HEADER_BACKGROUND_URL,
    FEMALE_HEADER_BACKGROUND_URL,
    VENUE_SHORT_NAMES,
    _head_to_head_line,
    _outcome_heading,
    _venue_short_name,
    _weather_summary,
)
from wechat_official import Article, CoverMediaId


class HtmlToolsTests(unittest.TestCase):
    def test_normalisation_is_deterministic_and_idempotent(self) -> None:
        raw = (
            '<div onclick="x()" style="padding: 2px; color: red"><p>A &amp; B</p></div>'
        )
        first = sanitise_html(raw)
        second = sanitise_html(raw)
        third = sanitise_html(first)
        self.assertEqual(first, second)
        self.assertEqual(first, third)


_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "template.html"
_DATA_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_data.json"
_WOMEN_DATA_PATH = (
    _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_data_women_saturday.json"
)
_FUTSAL_DATA_PATH = (
    _PROJECT_ROOT
    / "templates"
    / "qhly_preview_v1"
    / "example_data_futsal_saturday.json"
)
_WEATHER_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_weather.json"
_CONFIG_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_config.json"


def _load_example(path: Path = _DATA_PATH):
    return load_preview_bundle(path, _WEATHER_PATH, _CONFIG_PATH)


def _raw_source() -> dict[str, object]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def _parse_source(raw: dict[str, object]):
    return _parse_bundle(
        raw,
        json.loads(_WEATHER_PATH.read_text(encoding="utf-8")),
        json.loads(_CONFIG_PATH.read_text(encoding="utf-8")),
    )


def _parse_bundle(source: object, weather: object, config: object):
    return parse_preview_bundle(
        source,
        weather,
        config,
        source_directory=_DATA_PATH.parent,
    )


def _keep_first_match(raw: dict[str, object]) -> None:
    matches = raw["matches"]
    assert isinstance(matches, list)
    raw["matches"] = matches[:1]
    previews = raw["previews"]
    assert isinstance(previews, dict)
    first_key = next(iter(previews))
    raw["previews"] = {first_key: previews[first_key]}


def _render(template: PreviewTemplate, source) -> Article:
    return PreviewService(template).render(
        source,
        cover=CoverMediaId("test-cover"),
        author="清华绿茵",
    )


class PreviewSourceTests(unittest.TestCase):
    def test_women_and_futsal_saturday_examples_render(self) -> None:
        cases = (
            (_WOMEN_DATA_PATH, "【马杯女足周六前瞻】", "马约翰杯女子足球赛"),
            (_FUTSAL_DATA_PATH, "【马杯五人制周六前瞻】", "马约翰杯五人制足球赛"),
        )
        template = load_preview_template(_TEMPLATE_PATH)
        for path, title_prefix, full_name in cases:
            with self.subTest(path=path.name):
                source = _load_example(path)
                rendered = _render(template, source)
                self.assertEqual(source.preview_date.weekday(), 5)
                self.assertEqual(source.matches[0].game_id, -1)
                self.assertEqual(source.matches[0].preview_paragraphs, ("前瞻文章",))
                self.assertTrue(rendered.title.startswith(title_prefix))
                self.assertIn(full_name, rendered.body_html)
                self.assertIn("前瞻文章", rendered.body_html)
                self.assertGreaterEqual(rendered.body_html.count("暂无数据"), 4)
                self.assertIn(">无</p>", rendered.body_html)
                self.assertEqual(
                    rendered.body_html.count(
                        "display:flex;flex-flow:row;height:42px;margin-bottom:15px"
                    ),
                    2,
                )
                self.assertEqual(
                    rendered.body_html.count(
                        'padding:0 13px;white-space:nowrap"><p style="line-height:42px;margin:0"'
                    ),
                    2,
                )
                self.assertEqual(
                    rendered.body_html.count("border-width:21px 0 21px 17px"),
                    2,
                )
                self.assertNotIn("align-self:stretch", rendered.body_html)
                self.assertNotIn("line-height:.1", rendered.body_html)

    def test_strict_decoder_rejects_unknown_field_with_path(self) -> None:
        raw = _raw_source()
        raw["legacy_html"] = "<p>旧字段</p>"
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_document(raw, source_directory=_DATA_PATH.parent)
        self.assertIn("$", str(caught.exception))
        self.assertIn("legacy_html", str(caught.exception))

    def test_writer_credit_is_trimmed_stable_and_deduplicated(self) -> None:
        raw = _raw_source()
        previews = raw["previews"]
        self.assertIsInstance(previews, dict)
        entries = list(previews.values())
        entries[0]["authors"] = [" 唐伟 ", "唐伟", "王镜尧"]
        entries[1]["authors"] = ["王镜尧", "赵六"]

        source = _parse_source(raw)

        self.assertEqual(source.ordered_writers, ("唐伟", "王镜尧", "赵六"))
        rendered = _render(load_preview_template(_TEMPLATE_PATH), source)
        self.assertIn("前瞻作者 | 唐伟 王镜尧 赵六", rendered.body_html)

    def test_blank_writer_reports_exact_path(self) -> None:
        raw = _raw_source()
        next(iter(raw["previews"].values()))["authors"] = [" "]
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_source(raw)
        self.assertIn(".authors[0]", str(caught.exception))

    def test_match_date_and_score_pairs_are_validated(self) -> None:
        raw = _raw_source()
        raw["matches"][1]["kickoff"] = "2026-04-13T19:00:00+08:00"
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_source(raw)
        self.assertIn("$.matches[1].kickoff", str(caught.exception))

        raw = _raw_source()
        del raw["matches"][0]["home"]["current_results"][0]["away_score"]
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_source(raw)
        self.assertIn("$.matches[0].home.current_results[0]", str(caught.exception))

    def test_unknown_game_id_uses_only_minus_one(self) -> None:
        source = _load_example(_DATA_PATH)
        game_ids = [match.game_id for match in source.matches]
        for match in source.matches:
            game_ids.extend(result.game_id for result in match.home.current_results)
            game_ids.extend(result.game_id for result in match.away.current_results)
            game_ids.extend(result.game_id for result in match.head_to_head)
        self.assertEqual(set(game_ids), {-1})

        raw = _raw_source()
        raw["matches"][0]["game_id"] = 0
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_source(raw)
        self.assertIn("$.matches[0].game_id", str(caught.exception))

        raw = _raw_source()
        raw["matches"][0]["home"]["current_results"][0]["game_id"] = -2
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_source(raw)
        self.assertIn(
            "$.matches[0].home.current_results[0].game_id",
            str(caught.exception),
        )

    def test_weekday_and_competition_configs_change_all_headings(self) -> None:
        cases = (
            ("2026-04-09", "周四", "马杯女足", "马约翰杯女子足球赛"),
            ("2026-04-12", "周日", "马杯五人制", "马约翰杯五人制足球赛"),
        )
        template = load_preview_template(_TEMPLATE_PATH)
        for day, weekday, short_name, full_name in cases:
            with self.subTest(day=day):
                raw = _raw_source()
                raw["preview_date"] = day
                raw["column"] = {
                    "competition_full_name": full_name,
                    "competition_short_name": short_name,
                }
                for match in raw["matches"]:
                    match["kickoff"] = day + match["kickoff"][10:]
                source = _parse_source(raw)
                rendered = _render(template, source)
                self.assertTrue(
                    rendered.title.startswith(f"【{short_name}{weekday}前瞻】")
                )
                self.assertIn(full_name, rendered.body_html)
                self.assertIn(f"{weekday}比赛预告及天气情况", rendered.body_html)

        raw = _raw_source()
        raw["column"] = {
            "competition_full_name": "测试赛事",
            "competition_short_name": "测试",
            "weekday_label_override": "周末",
        }
        rendered = _render(template, _parse_source(raw))
        self.assertTrue(rendered.title.startswith("【测试周末前瞻】"))
        self.assertIn("周末比赛预告及天气情况", rendered.body_html)


class TemplateTests(unittest.TestCase):
    def test_weather_summary_includes_condition_and_hides_calm_wind_level(
        self,
    ) -> None:
        calm = PreviewWeather("多云", 8, 18, "微风", "≤3级")
        gust = PreviewWeather("阵雨", 10, 20, "阵风", "4级")
        normal = PreviewWeather("晴", 9, 21, "南风", "≤3级")

        self.assertEqual(_weather_summary(calm), "多云，8~18℃，微风")
        self.assertEqual(_weather_summary(gust), "阵雨，10~20℃，阵风4级")
        self.assertEqual(_weather_summary(normal), "晴，9~21℃，南风≤3级")

    def test_female_outcome_heading_uses_season_only(self) -> None:
        self.assertEqual(
            _outcome_heading(SeasonOutcome("24-25", "女足", "16强")),
            "24-25",
        )
        self.assertEqual(
            _outcome_heading(SeasonOutcome("24-25", "甲", "16强")),
            "24-25-甲",
        )

    def test_female_head_to_head_prefix_omits_competition_label(self) -> None:
        raw = _raw_source()
        meeting = raw["matches"][0]["head_to_head"][0]
        meeting["season"] = "22-23"
        meeting["competition_label"] = "女足"
        female = _parse_source(raw).matches[0].head_to_head[0]

        female_line = _head_to_head_line(female)
        self.assertTrue(female_line.startswith("（22-23）"))
        self.assertNotIn("女足", female_line)
        self.assertIn(f"{female.home.short_name} ", female_line)
        self.assertTrue(female_line.endswith(f" {female.away.short_name}"))

        meeting["competition_label"] = "甲"
        male = _parse_source(raw).matches[0].head_to_head[0]
        self.assertTrue(_head_to_head_line(male).startswith("（22-23-甲）"))

    def test_venue_short_names_cover_configured_fields_and_fallback(self) -> None:
        expected = {
            "紫荆足球场": "紫操",
            "西区足球场": "西操",
            "东区足球场": "东操",
            "紫荆足球场北侧场地": "紫北",
            "紫荆足球场南侧场地": "紫南",
            "西区足球场北侧场地": "西北",
            "西区足球场南侧场地": "西南",
        }
        for full_name, short_name in expected.items():
            with self.subTest(full_name=full_name):
                self.assertEqual(VENUE_SHORT_NAMES[full_name], short_name)
                self.assertEqual(_venue_short_name(full_name), short_name)

        self.assertEqual(_venue_short_name("综合体育馆"), "综合体育馆")

    def test_schedule_uses_short_venue_but_match_detail_keeps_full_name(
        self,
    ) -> None:
        raw = _raw_source()
        _keep_first_match(raw)
        raw["matches"][0]["venue"] = "紫荆足球场北侧场地"

        rendered = _render(
            load_preview_template(_TEMPLATE_PATH),
            _parse_source(raw),
        )

        self.assertIn(">紫北</td>", rendered.body_html)
        self.assertIn("紫荆足球场北侧场地</p>", rendered.body_html)

    def test_missing_outcome_and_head_to_head_have_explicit_fallbacks(self) -> None:
        raw = _raw_source()
        _keep_first_match(raw)
        match = raw["matches"][0]
        match["home"]["previous_outcomes"] = [{"season": "22-23", "outcome": "未参赛"}]
        match["head_to_head"] = []

        rendered = _render(
            load_preview_template(_TEMPLATE_PATH),
            _parse_source(raw),
        )

        self.assertIn("（22-23）", rendered.body_html)
        self.assertIn("未参赛", rendered.body_html)
        document = lxml_html.fromstring(rendered.body_html)
        cells = [
            cell
            for cell in document.xpath(".//td")
            if "两队最近三年交手战绩" in cell.text_content()
        ]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].xpath("./p")[-1].text_content(), "无")

    def test_repeated_nested_lists_empty_fallback_and_text_escaping(self) -> None:
        raw = _raw_source()
        for match in raw["matches"]:
            match["head_to_head"] = []
        raw["matches"][0]["home"]["name"] = "<社科 & 心理>"
        source = _parse_source(raw)
        first = replace(
            source.matches[0],
            preview_paragraphs=("<strong>纯文本</strong>",),
        )
        source = replace(source, matches=(first, *source.matches[1:]))
        template = PreviewTemplate.compile(
            """
            <section>
              <!-- wx:each source.matches as match -->
              <h1>{{match.home.name}}</h1>
              <!-- wx:each match.head_to_head as meeting -->
              <p>{{meeting|result_line}}</p>
              <!-- wx:empty --><p>暂无数据</p><!-- wx:endeach -->
              <!-- wx:each match.preview_paragraphs as paragraph -->
              <p>{{paragraph}}</p>
              <!-- wx:endeach -->
              <!-- wx:endeach -->
              <!-- wx:each source.matches as match --><span>{{match.home.team_id}}</span><!-- wx:endeach -->
            </section>
            """,
            version="test-preview-v1",
        )
        rendered = _render(template, source)

        self.assertIn("&lt;社科 &amp; 心理&gt;", rendered.body_html)
        self.assertIn("&lt;strong&gt;纯文本&lt;/strong&gt;", rendered.body_html)
        self.assertNotIn("<strong>纯文本</strong>", rendered.body_html)
        self.assertEqual(rendered.body_html.count("暂无数据"), 2)
        self.assertEqual(rendered.body_html.count("254"), 1)
        self.assertEqual(rendered.body_html.count("47"), 1)

    def test_penalty_and_special_results_use_finite_formatter(self) -> None:
        raw = _raw_source()
        first = raw["matches"][0]["home"]["current_results"][0]
        first["home_penalty"] = 5
        first["away_penalty"] = 4
        first["result_text"] = "4:0（点球 5:4）"
        second = raw["matches"][0]["home"]["current_results"][1]
        del second["home_score"]
        del second["away_score"]
        second["result_text"] = "对手退赛"
        source = _parse_source(raw)
        template = PreviewTemplate.compile(
            "<!-- wx:each source.matches as match -->"
            "<!-- wx:each match.home.current_results as result -->"
            "<p>{{result|result_line}}</p>"
            "<!-- wx:endeach -->"
            "<!-- wx:endeach -->"
        )
        rendered = _render(template, source)
        self.assertIn("社科-心理 4(5):0(4) 工物-安全", rendered.body_html)
        self.assertNotIn("点球 5:4", rendered.body_html)
        self.assertIn("社科-心理 对手退赛 法学", rendered.body_html)

    def test_invalid_marker_filter_and_path_fail(self) -> None:
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("{{{source.headline}}}")
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("{{source.headline|unknown}}")
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("<!-- wx:each source.matches as match -->")

        template = PreviewTemplate.compile("<p>{{source.not_a_field}}</p>")
        with self.assertRaises(TemplateContractError):
            _render(template, _load_example(_DATA_PATH))

    def test_weather_fallback_and_content_fingerprint(self) -> None:
        raw = _raw_source()
        source = replace(_parse_source(raw), weather=None)
        template = load_preview_template(_TEMPLATE_PATH)
        first = _render(template, source)
        raw["headline"] = "新的标题"
        second = _render(template, _parse_source(raw))
        self.assertIn("待更新", first.body_html)
        self.assertNotEqual(first.content_fingerprint, second.content_fingerprint)


class QhlyPreviewV1AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_preview_template(_TEMPLATE_PATH)
        cls.source = _load_example(_DATA_PATH)
        cls.rendered = _render(cls.template, cls.source)
        cls.document = lxml_html.fromstring(cls.rendered.body_html)

    def test_header_background_changes_only_for_female_preview(self) -> None:
        cases = (
            (_DATA_PATH, DEFAULT_HEADER_BACKGROUND_URL),
            (_WOMEN_DATA_PATH, FEMALE_HEADER_BACKGROUND_URL),
            (_FUTSAL_DATA_PATH, DEFAULT_HEADER_BACKGROUND_URL),
        )
        for source_path, expected_url in cases:
            with self.subTest(source_path=source_path.name):
                rendered = _render(self.template, _load_example(source_path))
                images = lxml_html.fromstring(rendered.body_html).xpath(".//img")
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0].get("src"), expected_url)
                self.assertIn("aspect-ratio:32/9", images[0].get("style", ""))
                self.assertIn("object-fit:cover", images[0].get("style", ""))

    def test_schedule_table_has_compact_columns_and_light_border_style(
        self,
    ) -> None:
        schedule_table = self.document.xpath(".//table")[0]
        weather_panel = schedule_table.getprevious()
        rows = schedule_table.xpath("./tbody/tr")
        header_cells = rows[0].xpath("./td")

        self.assertIn("table-layout:fixed", schedule_table.get("style", ""))
        self.assertIsNotNone(weather_panel)
        self.assertEqual(weather_panel.tag, "section")
        self.assertIn("border-bottom:0", weather_panel.get("style", ""))
        self.assertIn("color:#000", weather_panel.get("style", ""))
        self.assertEqual(weather_panel.xpath(".//br"), [])
        self.assertEqual(len(weather_panel.xpath("./p")), 2)
        self.assertTrue(
            all(
                "margin:0" in item.get("style", "")
                for item in weather_panel.xpath("./p")
            )
        )
        self.assertFalse(any("height:0" in row.get("style", "") for row in rows))
        self.assertEqual(rows[0].get("style"), "background:#fff;color:#5a0383")
        self.assertEqual(
            [cell.get("width") for cell in header_cells],
            ["27.27%", "9.09%", "27.27%", "18.18%", "18.18%"],
        )
        self.assertTrue(
            all(
                "border:1px solid #d6d6d6" in cell.get("style", "")
                for row in rows
                for cell in row.xpath("./td")
            )
        )
        self.assertTrue(
            all(
                "background:#fff" in cell.get("style", "")
                and "color:#000" in cell.get("style", "")
                for cell in rows[1].xpath("./td")
            )
        )
        self.assertEqual(rows[1].xpath("./td")[3].text_content(), "紫操")
        self.assertIn(
            "white-space:nowrap",
            rows[1].xpath("./td")[4].get("style", ""),
        )

    def test_history_table_text_is_explicitly_black(self) -> None:
        history_tables = [
            table
            for table in self.document.xpath(".//table")
            if "过往三届战绩" in table.text_content()
        ]

        self.assertGreater(len(history_tables), 0)
        for table in history_tables:
            rows = table.xpath("./tbody/tr")
            self.assertIn("color:#000", table.get("style", ""))
            self.assertTrue(
                all(
                    "color:#000" in cell.get("style", "")
                    for row in rows[1:]
                    for cell in row.xpath("./td")
                )
            )
            for cell in rows[3].xpath("./td[@colspan='2']"):
                paragraphs = cell.xpath("./p")
                self.assertGreater(len(paragraphs), 0)
                margins = [item.get("style", "") for item in paragraphs]
                self.assertTrue(
                    all("margin:0 0 3px" in style for style in margins[:-1])
                )
                self.assertIn("margin:0", margins[-1])
            for cell in rows[2].xpath("./td[@colspan='2']"):
                paragraphs = cell.xpath("./p")
                headings = paragraphs[0::2]
                results = paragraphs[1::2]
                self.assertTrue(
                    all("margin:0 0 2px" in item.get("style", "") for item in headings)
                )
                self.assertTrue(
                    all(
                        "margin:0 0 3px" in item.get("style", "")
                        for item in results[:-1]
                    )
                )
                self.assertIn("margin:0", results[-1].get("style", ""))

    def test_each_match_card_starts_with_a_dark_purple_accent(self) -> None:
        card_accent_style = (
            "background:#5a0383;height:4px;line-height:0;"
            "margin:-10px 0 10px -10px;"
            "overflow:hidden;width:60px"
        )
        match_cards = self.document.xpath(
            './/section[contains(@style,"background:rgba(252,154,255,.08)") '
            'and contains(@style,"padding:10px")]'
        )

        self.assertEqual(len(match_cards), len(self.source.matches))
        for card in match_cards:
            self.assertEqual(
                card.xpath("./section[1]")[0].get("style"),
                card_accent_style,
            )

    def test_full_example_keeps_visual_effects_and_wechat_fallbacks(self) -> None:
        template_source = _TEMPLATE_PATH.read_text(encoding="utf-8")
        source_text = _DATA_PATH.read_text(encoding="utf-8")
        self.assertNotIn('style="<section', template_source)
        self.assertGreater(template_source.count("\n"), 100)
        self.assertNotIn("schedule_rows", source_text)
        self.assertNotIn("_html", source_text)
        PreviewTemplate.compile(template_source, version="qhly-preview-v1")
        rendered = self.rendered

        self.assertEqual(rendered.title, "【马杯男足周日前瞻】|| 测试")

        with tempfile.TemporaryDirectory() as directory:
            output = rendered.save(Path(directory) / "article")
            preview_html = (output / "body.html").read_text(encoding="utf-8")
            self.assertIn('data-wechat-article-body="1"', preview_html)
            self.assertIsInstance(Article.load(output), Article)

        self.assertLess(len(rendered.body_html.encode("utf-8")), 1_000_000)
        self.assertEqual(len(self.document.xpath(".//img")), 1)
        self.assertIn("grid-template-columns:100%", rendered.body_html)
        self.assertIn(
            "aspect-ratio:32/9;border:3px solid #5a0383;display:block;"
            "height:auto;object-fit:cover;opacity:.3;width:100%",
            rendered.body_html,
        )
        self.assertNotIn("background-image:url", rendered.body_html)
        self.assertNotIn("padding-top:31.92%", rendered.body_html)
        self.assertNotIn("transform:translate3d(-25px,0,0)", rendered.body_html)
        self.assertNotIn("transform:translate3d(40px,0,0)", rendered.body_html)
        self.assertNotIn("transform:translate3d(65px,0,0)", rendered.body_html)
        self.assertEqual(
            rendered.body_html.count("margin:10px 24px;text-align:left"), 2
        )
        self.assertIn("transform:scale(.8)", rendered.body_html)
        self.assertEqual(
            rendered.body_html.count("transform:translate3d(6px,-6px,0)"), 4
        )
        self.assertEqual(
            rendered.body_html.count("transform:translate3d(-6px,-6px,0)"), 4
        )
        self.assertEqual(
            rendered.body_html.count("transform:translate3d(6px,6px,0)"), 4
        )
        self.assertEqual(
            rendered.body_html.count("transform:translate3d(-6px,6px,0)"), 4
        )
        self.assertEqual(
            rendered.body_html.count("background:#5a0383;height:2px;margin:5px 0"),
            2,
        )
        self.assertEqual(
            rendered.body_html.count(
                "border:1px solid #d6d6d6;border-collapse:collapse;color:#000;"
                "font-size:14px"
            ),
            2,
        )
        self.assertEqual(rendered.body_html.count("padding:5px;width:40%"), 4)
        self.assertEqual(rendered.body_html.count("padding:5px;width:20%"), 2)
        self.assertEqual(rendered.body_html.count("table-layout:fixed"), 3)
        self.assertNotIn("<colgroup>", rendered.body_html)
        self.assertEqual(rendered.body_html.count('style="height:36px"'), 1)
        self.assertEqual(rendered.body_html.count('style="height:20px"'), 1)
        self.assertEqual(
            rendered.body_html.count(
                'align-items:flex-end;display:flex;flex-flow:row nowrap;margin:10px 0"'
            ),
            2,
        )
        self.assertIn(
            "border-left:2px solid #5a0383;border-right:2px solid #5a0383;"
            "margin:0 0 10px",
            rendered.body_html,
        )
        self.assertIn("border-left:2px solid #a65bcb", rendered.body_html)
        self.assertIn("border-width:21px 0 21px 17px", rendered.body_html)
        self.assertEqual(rendered.body_html.count("比赛前瞻"), 4)
        self.assertEqual(
            rendered.body_html.count("margin:0 0 .6em;text-indent:2em"),
            5,
        )
        self.assertEqual(rendered.body_html.count("width:33%"), 2)
        self.assertEqual(
            rendered.body_html.count(
                '<section style="width:100%"><p style="margin:0;word-break:break-all"'
            ),
            1,
        )
        self.assertEqual(
            rendered.body_html.count(
                "color:#000;margin-left:auto;text-align:right;width:100%"
            ),
            1,
        )

        history_tables = [
            table
            for table in self.document.xpath(".//table")
            if "过往三届战绩" in table.text_content()
        ]
        self.assertEqual(len(history_tables), 2)
        for table in history_tables:
            rows = table.xpath("./tbody/tr")
            self.assertEqual(len(rows), 5)

            sizing_cells = rows[0].xpath("./td")
            self.assertEqual(len(sizing_cells), 5)
            self.assertEqual([cell.get("width") for cell in sizing_cells], ["20%"] * 5)
            self.assertTrue(all(cell.get("height") == "0" for cell in sizing_cells))

            self.assertEqual(
                [cell.get("colspan", "1") for cell in rows[1].xpath("./td")],
                ["2", "1", "2"],
            )
            self.assertEqual(
                [cell.get("colspan", "1") for cell in rows[2].xpath("./td")],
                ["2", "1", "2"],
            )
            self.assertEqual(
                [cell.get("colspan", "1") for cell in rows[3].xpath("./td")],
                ["2", "1", "2"],
            )

            head_to_head = rows[4].xpath("./td")[0]
            self.assertEqual(head_to_head.get("colspan"), "5")
            self.assertEqual(head_to_head.xpath("./br"), [])
            paragraphs = head_to_head.xpath("./p")
            self.assertEqual(len(paragraphs), 2)
            self.assertIn("margin:0 0 4px", paragraphs[0].get("style", ""))
            self.assertIn("margin:0", paragraphs[1].get("style", ""))


class PreviewBundleTests(unittest.TestCase):
    def _payloads(self) -> tuple[dict, dict, dict]:
        return (
            json.loads(_DATA_PATH.read_text(encoding="utf-8")),
            json.loads(_WEATHER_PATH.read_text(encoding="utf-8")),
            json.loads(_CONFIG_PATH.read_text(encoding="utf-8")),
        )

    def test_inline_article_is_rejected(self) -> None:
        source, weather, config = self._payloads()
        key = next(iter(source["previews"]))
        source["previews"][key] = {
            "article": "  第一段正文  \n\n  第二段正文\n   \n",
            "authors": [" 张三 ", "张三", "李四"],
        }

        with self.assertRaises(PreviewValidationError) as caught:
            _parse_bundle(source, weather, config)
        self.assertIn("article", str(caught.exception))

    def test_markdown_article_supports_direct_multiline_paste(self) -> None:
        source, weather, config = self._payloads()
        key = next(iter(source["previews"]))
        match = source["matches"][0]
        article_file = (
            f"previews/{match['home']['short_name']}vs{match['away']['short_name']}.md"
        )
        source["previews"][key] = {
            "article_file": article_file,
            "authors": [" 张三 ", "张三", "李四"],
        }
        _keep_first_match(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / article_file
            markdown.parent.mkdir(parents=True)
            markdown.write_text(
                "第一段第一行。\n第一段第二行。\n\n第二段正文。\n\n\n第三段正文。\n",
                encoding="utf-8",
            )

            parsed = parse_preview_bundle(
                source,
                weather,
                config,
                source_directory=root,
            )

            self.assertEqual(
                parsed.matches[0].preview_paragraphs,
                (
                    "第一段第一行。\n第一段第二行。",
                    "第二段正文。",
                    "第三段正文。",
                ),
            )
            self.assertEqual(parsed.matches[0].writers, ("张三", "李四"))

            source["previews"][key]["article_file"] = "previews/随意改名.md"
            with self.assertRaises(PreviewValidationError) as renamed:
                parse_preview_document(source, source_directory=root)
            self.assertIn("必须与比赛名称一致", str(renamed.exception))

            source["previews"][key]["article_file"] = article_file
            markdown.unlink()
            with self.assertRaises(PreviewValidationError) as missing:
                parse_preview_document(source, source_directory=root)
            self.assertIn("无法读取 Markdown 文件", str(missing.exception))

    def test_preview_mapping_must_match_matches_exactly(self) -> None:
        source, _, _ = self._payloads()
        missing_key = next(iter(source["previews"]))
        del source["previews"][missing_key]
        with self.assertRaises(PreviewValidationError) as missing:
            parse_preview_document(source)
        self.assertIn(missing_key, str(missing.exception))
        self.assertIn("缺少", str(missing.exception))

        source, _, _ = self._payloads()
        source["previews"]["不存在 vs 球队"] = {
            "article_file": "previews/不存在vs球队.md",
            "authors": ["作者"],
        }
        with self.assertRaises(PreviewValidationError) as extra:
            parse_preview_document(source)
        self.assertIn("不存在 vs 球队", str(extra.exception))
        self.assertIn("多余", str(extra.exception))

        source, _, _ = self._payloads()
        key = next(iter(source["previews"]))
        source["previews"][f" {key}"] = source["previews"].pop(key)
        with self.assertRaises(PreviewValidationError) as spaced:
            parse_preview_document(source)
        self.assertIn("不能包含首尾空白", str(spaced.exception))

    def test_duplicate_matchup_reports_both_game_ids(self) -> None:
        source, _, _ = self._payloads()
        duplicate = copy.deepcopy(source["matches"][0])
        duplicate["game_id"] = 987654
        source["matches"].append(duplicate)

        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_document(source)

        message = str(caught.exception)
        self.assertIn("对阵简称重复", message)
        self.assertIn(str(source["matches"][0]["game_id"]), message)
        self.assertIn("987654", message)

    def test_weather_null_partial_and_complete_states(self) -> None:
        source, _, config = self._payloads()
        day = source["preview_date"]
        empty_weather = {
            day: {
                "condition": None,
                "low_c": None,
                "high_c": None,
                "wind_direction": None,
                "wind_level": None,
            }
        }
        self.assertIsNone(_parse_bundle(source, empty_weather, config).weather)

        partial_weather = copy.deepcopy(empty_weather)
        partial_weather[day]["low_c"] = 8
        with self.assertRaises(PreviewValidationError) as caught:
            _parse_bundle(source, partial_weather, config)
        message = str(caught.exception)
        self.assertIn(f"$weather['{day}']", message)
        self.assertIn("high_c", message)
        self.assertIn("wind_direction", message)
        self.assertIn("wind_level", message)

        complete_weather = {
            day: {
                "condition": "多云",
                "low_c": 8,
                "high_c": 18,
                "wind_direction": "东南风",
                "wind_level": "2级",
            }
        }
        parsed = _parse_bundle(source, complete_weather, config)
        self.assertIsNotNone(parsed.weather)
        self.assertEqual(parsed.weather.condition, "多云")
        self.assertEqual(parsed.weather.low_c, 8)

        legacy_weather = copy.deepcopy(complete_weather)
        del legacy_weather[day]["condition"]
        with self.assertRaises(PreviewValidationError) as legacy:
            _parse_bundle(source, legacy_weather, config)
        self.assertIn("condition", str(legacy.exception))

        complete_weather[day]["forecast_date"] = day
        with self.assertRaises(PreviewValidationError) as extra:
            _parse_bundle(source, complete_weather, config)
        self.assertIn("forecast_date", str(extra.exception))

    def test_config_is_strict(self) -> None:
        source, weather, config = self._payloads()
        config["unknown"] = []
        with self.assertRaises(PreviewValidationError) as config_error:
            _parse_bundle(source, weather, config)
        self.assertIn("$config", str(config_error.exception))
        self.assertIn("unknown", str(config_error.exception))


class PreviewCliTests(unittest.TestCase):
    def test_render_writes_loadable_article_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "article"
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = cli_main(
                    [
                        "render",
                        str(_TEMPLATE_PATH),
                        "--source",
                        str(_DATA_PATH),
                        "--weather",
                        str(_WEATHER_PATH),
                        "--config",
                        str(_CONFIG_PATH),
                        "--cover-media-id",
                        "dry-run-cover",
                        "--output",
                        str(output),
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(output.exists())
            article = Article.load(output)
            self.assertEqual(article.title, payload["title"])
            self.assertEqual(article.author, "清华绿茵")


if __name__ == "__main__":
    unittest.main()
