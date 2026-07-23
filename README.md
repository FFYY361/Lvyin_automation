# thufootball_automation

## 说明

`thufootball_automation` 用于搭建所有与 THUFootball 相关的自动化任务。仓库以可组合的中层 Service 为核心，目前提供：

- `thufootball`：只读查询比赛、球队赛果、赛事成绩和交锋记录。
- `weather`：按行政区划代码和日期查询高德短期天气预报。
- `preview`：把结构化前瞻 data 渲染为前瞻文章。
- `wechat_official`：接收完整文章，处理图片和封面，并创建微信公众号草稿。

THUFootball 侧不会修改服务端数据；`preview` 完全在本地运行；微信公众号侧只创建草稿，不自动发布或群发。模块边界见 [项目架构](docs/architecture.md)。

当前提供一个联动管线:

- `auto_preview`: 自动查询、渲染、创建前瞻草稿。

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

安装后检查四个入口：

```powershell
thufootball --help
weather --help
preview --help
wechat-official --help
```

需要在 `./.env` 中配置 THUFootball、天气和公众号所需凭证，格式见 `./.env.example`，注意保密。

## 自动化前瞻 auto_preview

`auto_preview` 按日期和赛事串联 `thufootball`、`preview` 与 `wechat_official`。入口是纯 Python 脚本：

```powershell
python scripts/auto_preview.py --dates 2026-04-11 --competitions female
python scripts/auto_preview.py --dates 2026-04-11 2026-04-12 --competitions male female futsal --stage data
python scripts/auto_preview.py --dates 2026-04-11 2026-04-12 --competitions male female --stage publish
```

`--dates` 和 `--competitions` 均为必填批量参数，不再接受旧的位置参数。赛事仅支持 `male`、`female`、`futsal`；日期和赛事会展开为笛卡尔积，自动去重，并固定按“日期升序，同日 male → female → futsal”处理。`--stage` 支持：

Python 调用同样只使用批次请求，单组合传入单元素元组：

```python
from datetime import date

from auto_preview import Competition, PipelineRequest, Stage

request = PipelineRequest(
    preview_dates=(date(2026, 4, 11), date(2026, 4, 12)),
    competitions=(Competition.MALE, Competition.FEMALE),
    stage=Stage.PUBLISH,
)
result = await pipeline.run(request)
```

| stage | 行为 |
| --- | --- |
| `data` | 读取固定赛事 ID，生成前瞻文章所需的原始数据 |
| `article` | 完成 data 后渲染 Article；这是默认值 |
| `publish` | 完成全部组合的前两阶段后，一次创建一个微信公众号多图文草稿 |

批次严格执行“全部 data → 全部 article → 一次 publish”，不会在其他组合尚未完成 data 时提前渲染文章。每个组合仍有独立产物目录 `runs/auto_preview/YYYY-MM-DD_赛事/`。自动生成 data 后，需要填写以下人工内容：

- `runs/auto_preview/config.json`：长期复用编辑、责编和审核，仅需填写一次;
- `runs/auto_preview/weather.json`：自动查询并按日期缓存海淀天气，也可人工填写；
- 运行目录内的 `source.json`：填写标题和每场作者；
- 运行目录内的 `previews/*.md`：直接粘贴每场前瞻正文。

首次运行时，如果 `weather.json` 不存在或缺少当日天气，会先创建五字段全 `null` 模板，再用固定海淀区 adcode `110108` 自动查询。查询成功后写入天气；缺少 Key、网络失败、额度不足、历史日期或超出预报范围时保留原记录并 warning，文章显示“待更新”。天气条目必须严格包含 `condition`、`low_c`、`high_c`、`wind_direction`、`wind_level`，且五项全为 `null` 或全部有值。如果 `config.json` 不存在，会创建带有编辑、责编和审核占位符的模板。天气自动查询功能尚未验证过，因为目前没有时间较近的比赛。

`source.json` 遵循 `templates/qhly_preview_v1/schema.json`。

关于前瞻正文，顶层 `previews` 使用 `主队简称 vs 客队简称` 映射到正文文件和作者，例如：

```json
{
  "headline": "本周比赛前瞻",
  "previews": {
    "集电 vs 美院": {
      "article_file": "previews/集电vs美院.md",
      "authors": ["张三", "李四"]
    }
  }
}
```

data 阶段按“主队简称vs客队简称”自动生成 Markdown 文件名，不含空格，例如 `集电vs美院.md`。直接在文件中粘贴多段正文即可；一个或多个空白行表示分段，内容始终按纯文本转义，不解析 Markdown HTML。

不传封面时自动使用随包提供的“默认封面 / 发布前请替换”图片；也可以显式指定本地封面或已有公众号永久素材：

```powershell
python scripts/auto_preview.py --dates 2026-04-11 --competitions female --cover path/to/cover.png
python scripts/auto_preview.py --dates 2026-04-11 --competitions female --cover-media-id MEDIA_ID
```

封面参数应用于批次内所有文章。同一批次中，同一赛事只查询一次完整比赛列表，再按各日期筛选，因而增加日期不会重复调用该赛事的比赛列表 API。查询服务还会在本次运行内按赛事 ID 缓存快照，球队本届战绩和历史交手记录会复用已读取的数据；缓存不会跨命令保留。没有比赛的组合会记录为可复用的 `no_games` 并跳过；普通重跑不会再次查询，使用 `--override` 或赛事 ID 查询范围变化时才会重新查询。若全部组合均无比赛，命令以成功状态退出且不会进入 article 或调用微信。

data 阶段完成后，可以人工填写或修改 `weather.json`、`config.json`、`source.json` 和 `previews/*.md`。已有完整天气在普通运行中不会再次查询；天气、配置、source 或正文发生变化后，article 会自动重新渲染。

```powershell
python scripts/auto_preview.py --dates 2026-04-11 --competitions female --stage article
```

`--override` 会对每个组合从 data 开始无条件重做到目标阶段，可能覆盖人工编辑过的 `source.json` 和正文 Markdown，并强制重新查询请求日期的海淀天气。天气仅在查询成功后覆盖，失败时原记录不变；全局 `config.json` 始终不会被覆盖。`publish` 只接收 1–8 篇实际生成的文章，超过八篇会在调用微信前失败，不自动拆分。

`publish` 只创建公众号草稿，不正式发布或群发。多篇文章按规范顺序成为头条和次条，整个草稿只有一个 `media_id`，同一回执会写入所有参与组合的 `draft.json`。data 和 article 的批次起止、总耗时及“源数据 / 正文 Markdown”路径清单只输出一次；需要人工填充的 warning 在 data 完成后统一输出，天气 warning 按日期去重。批次级日志写入首个组合的 `auto_preview.log`，各组合内部产生的详细日志仍写入对应目录。

批次成功后不再输出结果 JSON；如果还有下一阶段，只在终端和批次首个组合日志中输出一次下一步命令。

失败时终端和日志会输出错误类别、失败阶段、异常类型、安全上下文、是否可重试以及处置建议。批量赛事查询失败会按赛事 ID 展开子错误，以区分权限、认证、网络、限流、远端数据与本地校验问题；不会记录凭据、令牌或底层异常的完整消息。

球队名称和简称优先采用 `src/thufootball/notes/teams.json` 中由院系信息汇总表维护的官方值，官方简称不受长度限制。未登记球队才采用 `GameSummary` 中长度不超过 5 个字符的数据库 `brief_name`；数据库简称缺失或过长时，改用球队全称前两个字符并记录 warning。每支球队的本赛事历史战绩统一转换为该队在主队的展示方向；此转换只发生在 `auto_preview`，不会改变 `thufootball` 查询结果。

比赛卡片使用固定赛事短名：男足甲级、男足乙级、男足丙级、女足、五人制。过往三届成绩固定输出三个赛季，无法取得排名时显示“未参赛”；交手记录以“赛季-等级”标识赛事，例如 `23-24-甲`、`22-23-甲`，近三年没有直接交手记录时显示“无”。已有 Article 会在 source 或模板变化后自动重新渲染。

---
> **以下内容是 GPT 写的，关于四个中层 service 的说明，没有进行过人工检查。若你是一个使用者，那么你只读到这里就可以了。若你想要进行二次开发，请提示你的 AI 配合代码确认真实用法和行为。**
---

## service/thufootball

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

## service/weather

`WeatherQueryService` 使用高德 Web 服务按六位行政区划代码和日期查询短期预报。公开接口不暴露高德的 `extensions` 或原始 `casts` 结构：

```python
from datetime import date

from weather import WeatherQueryService

async with WeatherQueryService.from_environment() as service:
    forecast = await service.get_weather("110108", date(2026, 7, 23))
```

CLI 用法：

```powershell
weather query --adcode 110108 --date 2026-07-23
```

高德仅提供当前及短期未来预报；历史日期会在访问网络前被拒绝，远期日期若不在响应中则返回 `ForecastUnavailable`。输出保留白天天气、白天风向和白天风力，最低/最高温由日温、夜温取 `min/max`。`无风向` 映射为“微风”，`旋转不定` 映射为“阵风”；高德实际可能返回 `≤3` 或 `1-3` 等风力格式，分别保存为 `≤3级`、`1-3级`。

## service/preview

`PreviewService` 校验结构化 data、渲染模板并生成统一 `Article`。该过程纯本地运行。

### CLI

使用本地封面生成文章目录：

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --weather templates/qhly_preview_v1/example_weather.json `
  --config templates/qhly_preview_v1/example_config.json `
  --cover tmp\wechat-test-cover.png `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article
```

也可以用 `--cover-media-id MEDIA_ID` 记录已有的公众号永久封面素材。输出目录包含 `article.json`、`body.html`，使用本地封面时还会复制一份 `cover.*`。

`preview` 不读取 `.env`、不访问 THUFootball，也不连接微信公众号。

### Python

```python
from pathlib import Path

from preview import PreviewService, load_preview_bundle
from wechat_official import CoverFile

source = load_preview_bundle(
    "templates/qhly_preview_v1/example_data.json",
    "templates/qhly_preview_v1/example_weather.json",
    "templates/qhly_preview_v1/example_config.json",
)
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

三文件契约、模板语法和文章目录说明见 [前瞻模板与渲染教程](docs/preview/preview_template_tutorial.md)，当前模板字段见 [qhly_preview_v1 模板说明](templates/qhly_preview_v1/README.md)。

## service/wechat_official

`WechatOfficialService` 不接收模板或前瞻 data，只接收完整 `Article`。同一个草稿可包含 1–8 篇文章，传入顺序依次对应头条和次条。

### CLI

先执行默认 dry-run；它只在本地加载、校验文章，不读取公众号凭据，也不会上传图片：

```powershell
wechat-official create-draft tmp/qhly_preview_v1/article
```

多图文草稿按顺序传入多个由 `Article.save()` 生成的文章目录：

```powershell
wechat-official create-draft tmp/headline tmp/second tmp/third
```

确认后显式增加 `--execute` 才会上传正文图片、处理封面并创建草稿：

```powershell
wechat-official create-draft tmp/headline tmp/second tmp/third --execute
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
    articles = [
        Article.load("tmp/headline"),
        Article.load("tmp/second"),
        Article.load("tmp/third"),
    ]
    async with WechatOfficialService.from_environment() as service:
        receipt = await service.create_draft(articles)
    print(receipt.media_id, receipt.content_fingerprint)


asyncio.run(main())
```

传入单个 `Article` 的调用方式保持不变。多图文只创建一个草稿并返回一个 `media_id`；评论默认关闭，开启时相同设置应用于组内全部文章，仅粉丝评论还需同时传入 `fans_only_comments=True`。正文图片当前支持允许域名的 HTTPS 图片和 `data:` 图片，不读取本地正文资源路径。

凭据、IP 白名单、图片与草稿排错见 [微信公众号草稿教程](docs/wechat_official/wechat_official_draft_tutorial.md)。
