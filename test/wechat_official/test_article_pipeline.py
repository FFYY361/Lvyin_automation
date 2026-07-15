from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import httpx
from lxml import html as lxml_html

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from wechat_official import (
    PreviewTemplate,
    PreviewValidationError,
    PublishedArticleReader,
    SourceAccessBlocked,
    SourceValidationError,
    TemplateContractError,
    extract_article,
    load_preview_source,
    load_preview_template,
    parse_preview_source,
    save_article_source,
    save_rendered_article,
)
from wechat_official.cli import main as cli_main
from wechat_official.html_tools import sanitise_html


_FIXTURE = _PROJECT_ROOT / "test" / "fixtures" / "article_source" / "wechat_article.html"


class ArticleSourceTests(unittest.TestCase):
    def test_extracts_only_article_and_metadata(self) -> None:
        article = extract_article(
            _FIXTURE.read_text(encoding="utf-8"),
            source_url="https://mp.weixin.qq.com/s/authorised-sample",
        )

        self.assertEqual(article.title, "马杯前瞻｜计算机系 vs 自动化系")
        self.assertEqual(article.author, "清华绿茵")
        self.assertNotIn("公众号导航", article.body_html)
        self.assertNotIn("评论与推荐", article.body_html)
        self.assertNotIn("script", article.body_html)
        self.assertNotIn("onclick", article.body_html)
        self.assertNotIn("javascript:", article.body_html)
        self.assertNotIn("运行时音频", article.body_html)
        self.assertIn("color:#123456", article.body_html)
        self.assertNotIn("position:fixed", article.body_html)
        self.assertEqual(len(article.media), 1)
        self.assertEqual(article.media[0].url, "https://mmbiz.qpic.cn/test/preview.png")

    def test_recognises_verification_page(self) -> None:
        with self.assertRaises(SourceAccessBlocked):
            extract_article(
                "<html><body><h1>请完成验证</h1><p>环境异常</p></body></html>",
                source_url="https://mp.weixin.qq.com/s/blocked",
            )

    def test_normalisation_is_deterministic_and_idempotent(self) -> None:
        raw = '<div onclick="x()" style="padding: 2px; color: red"><p>A &amp; B</p></div>'
        first = sanitise_html(raw)
        second = sanitise_html(raw)
        third = sanitise_html(first)
        self.assertEqual(first, second)
        self.assertEqual(first, third)

    def test_saves_preview_body_and_metadata(self) -> None:
        article = extract_article(
            _FIXTURE.read_text(encoding="utf-8"),
            source_url="https://mp.weixin.qq.com/s/authorised-sample",
        )
        with tempfile.TemporaryDirectory() as directory:
            preview, metadata = save_article_source(article, directory)
            body = Path(directory) / "body.html"
            self.assertTrue(preview.exists())
            self.assertTrue(body.exists())
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], article.title)
            self.assertEqual(len(payload["media"]), 1)


class PublishedReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_follows_only_allowlisted_redirects(self) -> None:
        raw = _FIXTURE.read_text(encoding="utf-8")

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/s/start":
                return httpx.Response(
                    302,
                    headers={"location": "/s/final"},
                    request=request,
                )
            return httpx.Response(200, text=raw, request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reader = PublishedArticleReader(http_client=http)
        try:
            article = await reader.read("https://mp.weixin.qq.com/s/start")
        finally:
            await http.aclose()
        self.assertEqual(article.source_url, "https://mp.weixin.qq.com/s/final")

    async def test_rejects_redirect_to_unlisted_host(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "https://example.com/internal"},
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reader = PublishedArticleReader(http_client=http)
        try:
            with self.assertRaises(SourceValidationError):
                await reader.read("https://mp.weixin.qq.com/s/start")
        finally:
            await http.aclose()


_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "template.html"
_DATA_PATH = _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_data.json"
_WOMEN_DATA_PATH = (
    _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_data_women_saturday.json"
)
_FUTSAL_DATA_PATH = (
    _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "example_data_futsal_saturday.json"
)


def _raw_source() -> dict[str, object]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


class PreviewSourceTests(unittest.TestCase):
    def test_women_and_futsal_saturday_examples_render(self) -> None:
        cases = (
            (_WOMEN_DATA_PATH, "【马杯女足周六前瞻】", "马约翰杯女子足球赛"),
            (_FUTSAL_DATA_PATH, "【马杯五人制周六前瞻】", "马约翰杯五人制足球赛"),
        )
        template = load_preview_template(_TEMPLATE_PATH)
        for path, title_prefix, full_name in cases:
            with self.subTest(path=path.name):
                source = load_preview_source(path)
                rendered = template.render(source)
                self.assertEqual(source.preview_date.weekday(), 5)
                self.assertEqual(source.matches[0].game_id, -1)
                self.assertEqual(source.matches[0].preview_paragraphs, ("前瞻文章",))
                self.assertTrue(rendered.title.startswith(title_prefix))
                self.assertIn(full_name, rendered.body_html)
                self.assertIn("前瞻文章", rendered.body_html)
                self.assertGreaterEqual(rendered.body_html.count("暂无数据"), 5)

    def test_strict_decoder_rejects_unknown_field_with_path(self) -> None:
        raw = _raw_source()
        raw["legacy_html"] = "<p>旧字段</p>"
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
        self.assertIn("$", str(caught.exception))
        self.assertIn("legacy_html", str(caught.exception))

    def test_writer_credit_is_trimmed_stable_and_deduplicated(self) -> None:
        raw = _raw_source()
        matches = raw["matches"]
        self.assertIsInstance(matches, list)
        matches[0]["writers"] = [" 唐伟 ", "唐伟", "王镜尧"]
        matches[1]["writers"] = ["王镜尧", "赵六"]

        source = parse_preview_source(raw)

        self.assertEqual(source.ordered_writers, ("唐伟", "王镜尧", "赵六"))
        rendered = load_preview_template(_TEMPLATE_PATH).render(source)
        self.assertIn("前瞻作者 | 唐伟 王镜尧 赵六", rendered.body_html)

    def test_blank_writer_reports_exact_path(self) -> None:
        raw = _raw_source()
        raw["matches"][0]["writers"] = [" "]
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
        self.assertIn("$.matches[0].writers[0]", str(caught.exception))

    def test_match_date_and_score_pairs_are_validated(self) -> None:
        raw = _raw_source()
        raw["matches"][1]["kickoff"] = "2026-04-12T19:00:00+08:00"
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
        self.assertIn("$.matches[1].kickoff", str(caught.exception))

        raw = _raw_source()
        del raw["matches"][0]["home"]["current_results"][0]["away_score"]
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
        self.assertIn("$.matches[0].home.current_results[0]", str(caught.exception))

    def test_unknown_game_id_uses_only_minus_one(self) -> None:
        source = load_preview_source(_DATA_PATH)
        game_ids = [match.game_id for match in source.matches]
        for match in source.matches:
            game_ids.extend(result.game_id for result in match.home.current_results)
            game_ids.extend(result.game_id for result in match.away.current_results)
            game_ids.extend(result.game_id for result in match.head_to_head)
        self.assertEqual(set(game_ids), {-1})

        raw = _raw_source()
        raw["matches"][0]["game_id"] = 0
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
        self.assertIn("$.matches[0].game_id", str(caught.exception))

        raw = _raw_source()
        raw["matches"][0]["home"]["current_results"][0]["game_id"] = -2
        with self.assertRaises(PreviewValidationError) as caught:
            parse_preview_source(raw)
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
                raw["weather"]["forecast_date"] = day
                raw["column"] = {
                    "competition_full_name": full_name,
                    "competition_short_name": short_name,
                }
                for match in raw["matches"]:
                    match["kickoff"] = day + match["kickoff"][10:]
                source = parse_preview_source(raw)
                rendered = template.render(source)
                self.assertTrue(rendered.title.startswith(f"【{short_name}{weekday}前瞻】"))
                self.assertIn(full_name, rendered.body_html)
                self.assertIn(f"{weekday}比赛预告及天气情况", rendered.body_html)

        raw = _raw_source()
        raw["column"] = {
            "competition_full_name": "测试赛事",
            "competition_short_name": "测试",
            "weekday_label_override": "周末",
        }
        rendered = template.render(parse_preview_source(raw))
        self.assertTrue(rendered.title.startswith("【测试周末前瞻】"))
        self.assertIn("周末比赛预告及天气情况", rendered.body_html)


class TemplateTests(unittest.TestCase):
    def test_repeated_nested_lists_empty_fallback_and_text_escaping(self) -> None:
        raw = _raw_source()
        for match in raw["matches"]:
            match["head_to_head"] = []
        raw["matches"][0]["home"]["name"] = "<社科 & 心理>"
        raw["matches"][0]["preview_paragraphs"] = ["<strong>纯文本</strong>"]
        source = parse_preview_source(raw)
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
        rendered = template.render(source)

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
        second = raw["matches"][0]["home"]["current_results"][1]
        del second["home_score"]
        del second["away_score"]
        second["result_text"] = "对手退赛"
        source = parse_preview_source(raw)
        template = PreviewTemplate.compile(
            "<!-- wx:each source.matches as match -->"
            "<!-- wx:each match.home.current_results as result -->"
            "<p>{{result|result_line}}</p>"
            "<!-- wx:endeach -->"
            "<!-- wx:endeach -->"
        )
        rendered = template.render(source)
        self.assertIn("4:0（点球 5:4）", rendered.body_html)
        self.assertIn("社科-心理对手退赛法学", rendered.body_html)

    def test_invalid_marker_filter_and_path_fail(self) -> None:
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("{{{source.headline}}}")
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("{{source.headline|unknown}}")
        with self.assertRaises(TemplateContractError):
            PreviewTemplate.compile("<!-- wx:each source.matches as match -->")

        template = PreviewTemplate.compile("<p>{{source.not_a_field}}</p>")
        with self.assertRaises(TemplateContractError):
            template.render(load_preview_source(_DATA_PATH))

    def test_weather_fallback_and_content_fingerprint(self) -> None:
        raw = _raw_source()
        raw.pop("weather")
        source = parse_preview_source(raw)
        template = load_preview_template(_TEMPLATE_PATH)
        first = template.render(source)
        raw["headline"] = "新的标题"
        second = template.render(parse_preview_source(raw))
        self.assertIn("待更新", first.body_html)
        self.assertNotEqual(first.content_fingerprint, second.content_fingerprint)


class QhlyPreviewV1AssetTests(unittest.TestCase):
    def test_full_example_keeps_visual_effects_and_wechat_fallbacks(self) -> None:
        template_source = _TEMPLATE_PATH.read_text(encoding="utf-8")
        source_text = _DATA_PATH.read_text(encoding="utf-8")
        self.assertNotIn('style="<section', template_source)
        self.assertGreater(template_source.count("\n"), 100)
        self.assertNotIn("schedule_rows", source_text)
        self.assertNotIn("_html", source_text)
        template = PreviewTemplate.compile(template_source, version="qhly-preview-v1")
        rendered = template.render(load_preview_source(_DATA_PATH))

        self.assertEqual(rendered.title, "【马杯男足周六前瞻】|| 落日熔金，危崖试翼")

        with tempfile.TemporaryDirectory() as directory:
            preview_path = save_rendered_article(rendered, Path(directory) / "preview.html")
            preview_html = preview_path.read_text(encoding="utf-8")
            self.assertIn("box-sizing:border-box", preview_html)
            self.assertIn("max-width:100%!important", preview_html)
            self.assertIn("margin-block-start:0", preview_html)

        self.assertLess(len(rendered.body_html.encode("utf-8")), 1_000_000)
        self.assertEqual(len(rendered.media), 1)
        self.assertIn("grid-template-columns:100%", rendered.body_html)
        self.assertIn("display:block;height:auto;opacity:.3;width:100%", rendered.body_html)
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
                "border:1px solid #d6d6d6;border-collapse:collapse;font-size:14px"
            ),
            2,
        )
        self.assertEqual(rendered.body_html.count("padding:5px;width:40%"), 4)
        self.assertEqual(rendered.body_html.count("padding:5px;width:20%"), 2)
        self.assertEqual(rendered.body_html.count("table-layout:fixed"), 2)
        self.assertNotIn("<colgroup>", rendered.body_html)
        self.assertEqual(rendered.body_html.count('style="height:36px"'), 1)
        self.assertEqual(rendered.body_html.count('style="height:20px"'), 1)
        self.assertEqual(
            rendered.body_html.count(
                "align-items:flex-end;display:flex;flex-flow:row nowrap;"
                "margin:10px 0\""
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

        document = lxml_html.fromstring(rendered.body_html)
        history_tables = [
            table
            for table in document.xpath(".//table")
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


class PreviewCliTests(unittest.TestCase):
    def test_render_and_create_draft_dry_run_use_typed_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "article.html"
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = cli_main(
                    [
                        "render",
                        str(_TEMPLATE_PATH),
                        "--source",
                        str(_DATA_PATH),
                        "--output",
                        str(output),
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(output.exists())

        stdout = StringIO()
        with redirect_stdout(stdout):
            status = cli_main(
                [
                    "create-draft",
                    str(_TEMPLATE_PATH),
                    "--source",
                    str(_DATA_PATH),
                    "--cover-media-id",
                    "dry-run-cover",
                ]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "dry-run")
        self.assertIn("preview", payload)

if __name__ == "__main__":
    unittest.main()
