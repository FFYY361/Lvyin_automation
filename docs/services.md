# Service 说明

本页面向二次开发，说明四个可组合的中层 Service。普通使用者优先使用 README 中的
`auto_preview` 和 `auto_report`。

## thufootball

`THUFootballQueryService` 查询比赛、球队赛果、赛事成绩和交锋记录；
`THUFootballReportService` 生成单场 PNG 战报。

```powershell
thufootball games --match-date 2026-07-15 --tournament-id 122
thufootball team-matches 48 --tournament-id 122
thufootball team-outcomes 48 --tournament-id 128 --tournament-id 122
thufootball head-to-head 48 163 --tournament-id 122 --tournament-id 123
thufootball report 4245 --output tmp\game-4245.png
```

```python
from datetime import date

from thufootball import GameQuery, THUFootballQueryService

async with THUFootballQueryService.from_environment() as service:
    games = await service.query_games(GameQuery(match_date=date(2026, 7, 15)))
    matches = await service.query_team_matches(48, tournament_id=122)
    outcomes = await service.query_team_outcomes(48, (128, 122))
    history = await service.query_team_to_team_matches(48, 163, (122, 123))
```

受保护查询使用 `THUFOOTBALL_OPENID` 和 `THUFOOTBALL_SESSION_KEY`。单场战报默认
只读；只有显式传入 `--refresh-stats` 才会调用影响未知的服务端统计刷新接口。
详细设计见 [查询实现](thufootball/thufootball_query_implementation.md)、
[HTTP API 清单](thufootball/thufootball_http_api_inventory.md) 和
[战报下载实现](thufootball/thufootball_report_download.md)。

## weather

`WeatherQueryService` 使用高德 Web Service，按六位行政区划代码和日期返回短期预报。

```powershell
weather query --adcode 110108 --date 2026-07-23
```

```python
from datetime import date

from weather import WeatherQueryService

async with WeatherQueryService.from_environment() as service:
    forecast = await service.get_weather("110108", date(2026, 7, 23))
```

配置 `AMAP_WEATHER_API_KEY`。历史日期会在联网前拒绝，超出响应预报范围时返回
`ForecastUnavailable`。

## preview

`PreviewService` 严格校验三份结构化输入，并在本地渲染微信文章。当前唯一模板标签为
`v1 initial`；模板内容 SHA256 单独作为 fingerprint，不对外充当版本号。

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --weather templates/qhly_preview_v1/example_weather.json `
  --config templates/qhly_preview_v1/example_config.json `
  --cover-media-id MEDIA_ID `
  --output tmp/qhly_preview_v1/article
```

```python
from preview import PreviewService, load_preview_bundle
from wechat_official import CoverMediaId

source = load_preview_bundle(
    "templates/qhly_preview_v1/example_data.json",
    "templates/qhly_preview_v1/example_weather.json",
    "templates/qhly_preview_v1/example_config.json",
)
service = PreviewService.from_template(
    "templates/qhly_preview_v1/template.html",
)
article = service.render(source, cover=CoverMediaId("MEDIA_ID"), author="清华绿茵")
article.save("tmp/qhly_preview_v1/article")
```

模板契约和语法见 [前瞻模板教程](preview/preview_template_tutorial.md) 与
[模板字段说明](../templates/qhly_preview_v1/README.md)。

## wechat_official

`WechatOfficialService` 接收 1–8 个完整 `Article`，上传正文图片和封面并创建一个
微信公众号多图文草稿。不会发布或群发。

```powershell
# 默认 dry-run：只做本地加载和校验
wechat-official create-draft tmp/headline tmp/second

# 显式执行远程写入
wechat-official create-draft tmp/headline tmp/second --execute
```

```python
from wechat_official import Article, WechatOfficialService

articles = [Article.load("tmp/headline"), Article.load("tmp/second")]
async with WechatOfficialService.from_environment() as service:
    receipt = await service.create_draft(articles)
```

真实写入需要 `WECHAT_APP_ID`、`WECHAT_APP_SECRET` 和公众号后台 IP 白名单。完整排错
见 [微信公众号草稿教程](wechat_official/wechat_official_draft_tutorial.md)。
