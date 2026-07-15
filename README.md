# THUFootball 前瞻自动化工具

## 说明

这个项目提供一条只读、安全的赛事前瞻工作流：从 THUFootball 读取比赛、赛事和球队数据，整理为模板所需的 data，生成可本地预览的 HTML，再将文章写入微信公众号草稿箱。

项目包含两个主要 Python 包：

- `thufootball`：封装 THUFootball 的只读查询接口，返回经过校验的结构化领域模型。
- `wechat_official`：校验前瞻 data、渲染 HTML、处理正文图片并创建微信公众号草稿。

整体数据流为 `THUFootball → data(JSON) → HTML → 微信公众号草稿`。THUFootball 客户端不会修改服务端数据；微信公众号能力止于创建草稿，不会自动发布或群发。总体设计见 [自动化比赛前瞻工具实现计划](docs/preview_automation_implementation_plan.md)。

## 环境

项目要求 Python 3.11 或更高版本。安装后会提供 `thufootball` 和 `wechat-preview` 两个命令。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

macOS 或 Linux 可分别使用 `source .venv/bin/activate` 和 `cp .env.example .env`。在 `.env` 中按需填写：

```dotenv
THUFOOTBALL_OPENID=
THUFOOTBALL_SESSION_KEY=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

不要提交真实凭据。`THUFOOTBALL_OPENID` 和 `THUFOOTBALL_SESSION_KEY` 用于需要认证的赛事查询；`WECHAT_APP_ID` 和 `WECHAT_APP_SECRET` 用于公众号草稿接口。调用微信服务前，还需要在公众号后台把运行机器的出口 IP 加入白名单。

安装完成后可先检查命令入口：

```powershell
thufootball --help
wechat-preview --help
```

## thufootball

`THUFootballQueryService` 是面向上游任务的领域查询入口。它在底层只读客户端之上完成赛事发现、并发查询、北京时间过滤、球队视角赛果换算、交锋汇总和静态最终成绩读取；`THUFootballClient` 只是它的传输依赖，不是本节主要接口。

| Python 接口 | 用途 | 返回类型 | CLI |
| --- | --- | --- | --- |
| `query_games(query)` | 按赛事、北京时间日期和球队组合查询比赛 | `list[GameSummary]` | `thufootball games [选项]` |
| `query_team_matches(team_id, tournament_id=None, *, include_unfinished=False)` | 查询一支球队的全部赛果，并统一为该球队视角 | `list[TeamGameResult]` | `thufootball team-matches TEAM_ID [选项]` |
| `query_team_outcomes(team_id, tournament_ids=None)` | 从仓库静态数据读取球队在支持赛事中的最终成绩 | `list[TeamTournamentOutcome]` | `thufootball team-outcomes TEAM_ID [选项]` |
| `query_team_to_team_matches(team_a_id, team_b_id, tournament_ids=None, *, include_unfinished=False)` | 查询两队跨赛事交锋和胜平负汇总 | `HeadToHeadHistory` | `thufootball head-to-head TEAM_A_ID TEAM_B_ID [选项]` |

`query_games` 使用 `GameQuery` 描述通用筛选条件：`tournament_ids` 可指定多个赛事，`match_date` 是北京时间自然日，`team_ids` 最多指定两个全局球队 ID，`team_match` 决定匹配任一球队还是全部球队，`include_unfinished` 决定是否保留未完赛比赛。

命令行与这四个 Python 接口一一对应，并把领域模型输出为格式化 JSON：

```powershell
thufootball games --match-date 2026-07-15 --team-id 48
thufootball team-matches 48 --tournament-id 122
thufootball team-outcomes 48 --tournament-id 128 --tournament-id 122
thufootball head-to-head 48 163 --tournament-id 122 --tournament-id 123
```

`games` 省略赛事和日期时会查询当前凭据可访问的全部赛事；可重复传入 `--tournament-id` 和最多两个 `--team-id`，增加 `--finished-only` 可排除未完赛比赛。`team-matches` 省略赛事时查询全部可访问赛事，默认只返回已完赛比赛。`team-outcomes` 完全读取本地静态数据，不需要凭据、不访问 HTTP。`head-to-head` 可跨多个赛事汇总，默认只统计已完赛比赛；后两个比赛查询可用 `--include-unfinished` 保留有效未完赛记录。

在 Python 中复用同一个查询服务即可继续搭建上游任务：

```python
import asyncio
from datetime import date

from thufootball import GameQuery, THUFootballClient, THUFootballQueryService


async def main() -> None:
    async with THUFootballClient() as client:
        queries = THUFootballQueryService(client)
        games = await queries.query_games(
            GameQuery(match_date=date(2026, 7, 15), team_ids=(48,))
        )
        team_matches = await queries.query_team_matches(48, tournament_id=122)
        outcomes = await queries.query_team_outcomes(48, (128, 122))
        head_to_head = await queries.query_team_to_team_matches(
            48,
            163,
            (122, 123),
        )


asyncio.run(main())
```

需要访问服务端的查询会从环境变量或项目 `.env` 读取凭据；只按北京时间日期调用 `games` 时可以走匿名公开比赛查询。所有公开错误都继承自 `THUFootballError`，并携带错误阶段和是否可重试等信息。完整查询规则、静态赛事范围和异常边界见 [THUFootball 查询能力实现设计](docs/thufootball/thufootball_query_implementation.md)；底层 HTTP 参数和响应字段仅供调试，见 [THUFootball HTTP API 清单](docs/thufootball/thufootball_http_api_inventory.md)。

## wechat_official

`wechat_official` 对外提供两个主要工作流：从 data 生成 HTML，以及把渲染后的文章写入微信公众号草稿箱。命令行适合本地试用，Python 接口适合接入自动化上游任务。

### 从 data 生成 HTML

CLI 会读取 JSON data、校验模板契约并生成本地 HTML；这个过程不会连接微信，也不会产生外部写入。

```powershell
wechat-preview render templates/qhly_preview_v1/template.html --source templates/qhly_preview_v1/example_data.json --version qhly-preview-v1 --output tmp/qhly_preview_v1/article.html
```

对应的 Python 调用为：

```python
from wechat_official import (
    load_preview_source,
    load_preview_template,
    save_rendered_article,
)

source = load_preview_source("templates/qhly_preview_v1/example_data.json")
template = load_preview_template(
    "templates/qhly_preview_v1/template.html",
    version="qhly-preview-v1",
)
rendered = template.render(source)
save_rendered_article(rendered, "tmp/qhly_preview_v1/article.html")
```

### 创建微信公众号草稿

`create-draft` 同样接收模板和 data，并在提交前重新渲染文章；它目前不接受任意 HTML 文件路径作为直接输入。省略 `--execute` 时只生成本地预览，不会上传图片或创建草稿：

```powershell
wechat-preview create-draft templates/qhly_preview_v1/template.html --source templates/qhly_preview_v1/example_data.json --cover-media-id EXISTING_COVER_MEDIA_ID --version qhly-preview-v1
```

确认预览后，增加 `--execute` 才会产生真实外部写入。封面必须且只能使用以下一种方式：`--cover path/to/cover.jpg` 上传新的永久封面素材，或 `--cover-media-id MEDIA_ID` 复用素材库中已有的永久图片。封面不是正文图片。

```powershell
wechat-preview create-draft templates/qhly_preview_v1/template.html --source templates/qhly_preview_v1/example_data.json --cover path/to/cover.jpg --version qhly-preview-v1 --execute
```

Python 侧沿用上一步得到的 `rendered`：

```python
from wechat_official import DraftService, MediaPublisher, WechatOfficialClient


async def create_draft(rendered):
    async with WechatOfficialClient.from_environment() as client:
        async with MediaPublisher(client) as media:
            return await DraftService(client, media).create_draft(
                rendered,
                cover_path="path/to/cover.jpg",
            )
```

返回的 `DraftReceipt` 包含草稿 `media_id`、内容指纹和创建时间。模板数据结构、正文图片处理、草稿约束及排错方式见 [微信公众号模板与草稿教程](docs/wechat_official/wechat_official_template_and_draft_tutorial.md)，当前模板字段说明和示例见 [qhly_preview_v1 模板说明](templates/qhly_preview_v1/README.md)。
