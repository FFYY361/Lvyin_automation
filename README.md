# thufootball_automation

## 说明

`thufootball_automation` 用于搭建所有与 THUFootball 相关的自动化任务。仓库以可组合的中层 Service 为核心，目前提供：

- `thufootball`：只读查询比赛、球队赛果、赛事成绩和交锋记录。
- `preview`：把结构化 data 渲染为文章，是当前实现的一项具体自动化能力，并不限定整个库的用途。
- `wechat_official`：接收完整文章，处理图片和封面，并创建微信公众号草稿。

当前不提供自动串联三者的管线。THUFootball 侧不会修改服务端数据；`preview` 完全在本地运行；微信公众号侧只创建草稿，不自动发布或群发。模块边界见 [项目架构](docs/architecture.md)。

## 环境

项目要求 Python 3.11 或更高版本。推荐使用标准库 `venv`：

```powershell
python -m venv .venv --prompt lvyin
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

也可以使用 Conda：

```powershell
conda create -n thufootball_automation python=3.11
conda activate thufootball_automation
python -m pip install -e .
```

安装后检查三个入口：

```powershell
thufootball --help
preview --help
wechat-official --help
```

各模块的凭据要求分别写在对应章节。`preview` 不需要 `.env`。

## thufootball

`thufootball` 提供通用的 THUFootball 领域查询，不包含文章或公众号逻辑。

### CLI

| 命令 | 用途 |
| --- | --- |
| `thufootball games` | 按赛事、北京日期和球队查询比赛 |
| `thufootball team-matches TEAM_ID` | 查询一支球队的比赛并换算为该队视角 |
| `thufootball team-outcomes TEAM_ID` | 读取球队在支持赛事中的最终成绩 |
| `thufootball head-to-head A B` | 汇总两队交锋和胜平负 |

命令成功时输出格式化 JSON：

```powershell
thufootball games --match-date 2026-07-15 --tournament-id 122
thufootball team-matches 48 --tournament-id 122
thufootball team-outcomes 48 --tournament-id 128 --tournament-id 122
thufootball head-to-head 48 163 --tournament-id 122 --tournament-id 123
```

需要访问受保护数据时，在仓库根目录 `.env` 中配置：

```dotenv
THUFOOTBALL_OPENID=
THUFOOTBALL_SESSION_KEY=
```

`team-outcomes` 只读取仓库静态数据，不需要凭据，也不访问 HTTP。

### Python

`THUFootballQueryService` 是供其他自动化任务调用的中层入口：

| 接口 | 返回类型 |
| --- | --- |
| `query_games(query)` | `list[GameSummary]` |
| `query_team_matches(team_id, ...)` | `list[TeamGameResult]` |
| `query_team_outcomes(team_id, ...)` | `list[TeamTournamentOutcome]` |
| `query_team_to_team_matches(a, b, ...)` | `HeadToHeadHistory` |

```python
import asyncio
from datetime import date

from thufootball import GameQuery, THUFootballQueryService


async def main() -> None:
    async with THUFootballQueryService.from_environment() as service:
        games = await service.query_games(
            GameQuery(match_date=date(2026, 7, 15), team_ids=(48,))
        )
        matches = await service.query_team_matches(48, tournament_id=122)
        outcomes = await service.query_team_outcomes(48, (128, 122))
        history = await service.query_team_to_team_matches(48, 163, (122, 123))


asyncio.run(main())
```

完整查询规则见 [THUFootball 查询能力实现设计](docs/thufootball/thufootball_query_implementation.md)，底层接口字段见 [THUFootball HTTP API 清单](docs/thufootball/thufootball_http_api_inventory.md)。

## preview

`PreviewService` 校验结构化 data、渲染模板并生成统一 `Article`。该过程纯本地运行。

### CLI

使用本地封面生成文章目录：

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --cover tmp\wechat-test-cover.png `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article
```

也可以用 `--cover-media-id MEDIA_ID` 记录已有的公众号永久封面素材。输出目录包含 `article.json`、`body.html`，使用本地封面时还会复制一份 `cover.*`。

`preview` 不读取 `.env`、不访问 THUFootball，也不连接微信公众号。

### Python

```python
from pathlib import Path

from preview import PreviewService, load_preview_source
from wechat_official import CoverFile

source = load_preview_source("templates/qhly_preview_v1/example_data.json")
service = PreviewService.from_template(
    "templates/qhly_preview_v1/template.html",
    version="qhly-preview-v1",
)
article = service.render(
    source,
    cover=CoverFile(Path("path/to/cover.png")),
    author="清华绿茵",
    digest="本期比赛前瞻",
)
article.save("tmp/qhly_preview_v1/article")
```

渲染结果使用统一文章字段：

| 字段 | 含义 |
| --- | --- |
| `title` | 图文标题 |
| `body_html` | 正文 HTML |
| `cover` | `CoverFile` 本地封面或 `CoverMediaId` 已有永久素材 |
| `author` | 作者署名，可为空 |
| `digest` | 文章摘要，可为空 |
| `source_url` | “阅读原文”链接，可为空 |

data 契约、模板语法和文章目录说明见 [前瞻模板与渲染教程](docs/preview/preview_template_tutorial.md)，当前模板字段见 [qhly_preview_v1 模板说明](templates/qhly_preview_v1/README.md)。

## wechat_official

`WechatOfficialService` 不接收模板或前瞻 data，只接收完整 `Article` 或由 `Article.save()` 生成的文章目录。

### CLI

先执行默认 dry-run；它只在本地加载、校验文章，不读取公众号凭据，也不会上传图片：

```powershell
wechat-official create-draft tmp/qhly_preview_v1/article
```

确认后显式增加 `--execute` 才会上传正文图片、处理封面并创建草稿：

```powershell
wechat-official create-draft tmp/qhly_preview_v1/article --execute
```

真实写入前，在仓库根目录 `.env` 中配置公众号凭据，并把运行机器的出口 IP 加入公众号后台白名单：

```dotenv
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

### Python

```python
import asyncio

from wechat_official import Article, WechatOfficialService


async def main() -> None:
    article = Article.load("tmp/qhly_preview_v1/article")
    async with WechatOfficialService.from_environment() as service:
        receipt = await service.create_draft(article)
    print(receipt.media_id, receipt.content_fingerprint)


asyncio.run(main())
```

评论默认关闭；需要时使用 `create_draft(article, open_comments=True)`，仅粉丝评论还需同时传入 `fans_only_comments=True`。正文图片当前支持允许域名的 HTTPS 图片和 `data:` 图片，不读取本地正文资源路径。

凭据、IP 白名单、图片与草稿排错见 [微信公众号草稿教程](docs/wechat_official/wechat_official_draft_tutorial.md)。
