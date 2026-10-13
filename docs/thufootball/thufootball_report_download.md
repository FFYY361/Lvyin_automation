# 单场比赛战报下载

## 浏览器流程与接口结论

单场管理页：

```text
https://www.tafa.org.cn/member/game_new.php?game_id=<GAME_ID>
```

页面底部“绘制战报”由 `/member/js/game.js` 的
`onDrawButtonClick()` 实现。以 `game_id=4245` 验证后，调用顺序为：

1. `GET https://api.thufootball.tech/OnReStatGameData`
2. `GET https://api.thufootball.tech/GetGameInfo`
3. 可选读取 `GET https://api.thufootball.tech/GetGamePageCode`
4. 浏览器使用 jCanvas 在 `<canvas id="canvas">` 上绘制 1600px 宽战报

前两个接口需要 `openid`、`session_key` 和 `game_id`。
`GetGamePageCode` 只需要 `game_id`，本地客户端明确不向该公开图片请求附带
登录凭据。

`OnReStatGameData` 会重新计算并修改该场比赛的服务端统计数据。它虽然使用
HTTP GET，却不是读取操作，具体修改范围也没有公开的服务端源码可供审计。
因此本地实现默认不调用；只有显式传入 `--refresh-stats` 时才调用。

网页没有一个“返回完整战报 PNG”的后端接口。旧代码曾使用
`$('canvas').getCanvasImage('png')` 创建下载链接，但这一段已经被注释，因此
网页只能在 Canvas 绘制完成后手工另存。本地实现读取同一份字段白名单，
使用 Pillow 在最终尺寸上直接绘制 RGB PNG，不需要浏览器、HTML 或 JavaScript，
不保存 `GetGameInfo` 原始响应。

本地绘图规则：

- 固定 1600px 宽、纯白背景，高度由测量后的全部内容决定；
- 队名使用数据库机构资料按全局 `team_id` 对应的全名，未配置时返回错误；
- 队名分别以 x=400、1200 为中心，比分位于 x=800；赛事和时间场地居中；
- 队名用 SimHei 50px、1px 描边模拟加粗，在 600px 内逐字符换行；
- 首发按号码排序，用 Noto Sans SC 36px，分别从 x=0、1000 左对齐。
  优先保持单个球员条目完整，每侧最大宽度 600px，超长条目逐字符换行；
- 比赛事件保留 API 顺序，按网站规则合并同一时间、转换乌龙球方向和第二张
  黄牌，并使用相同的事件框、横线、中轴线和图标布局；
- 绘制前按球队校验首发人数、同时间事件、换人顺序和人数、球员在场状态、
  纪律处罚及点球大战事件类型；助攻事件静默过滤，不进入校验或绘制；
- `START`、`END`、图例、小程序码，以及 `G.png`、`YC.png`、`Y2C.png`、
  `SI.png`、`SO.png` 等 15 个静态图标为包内保存的原始 PNG，按透明通道贴图，
  读取和解码后缓存，不在生成战报时联网请求；
- 姓名与备注用 Noto Sans SC 40px，在 550px 内换行；同分钟同侧事件共用
  卡片，左右卡片以相同中心高度连接中轴。补时显示为 `80+7'`；
- 底部仅保留进球、点球、点球罚失和乌龙球四项图例，按实际图标与文字宽度
  计算后整体居中。

绘图先用实际字体测量文字与图片边界，再创建画布并绘制。长队名、名单、
姓名备注、同分钟多事件均会增加相应区域高度，不截断文字、不缩小字号。
关闭名单、时间、场地或二维码后重新排版。无事件时仍保留起止图标与底部图例。
字体栅格化与浏览器会有细微差异，不追求逐像素一致。

### 字体与输出设置

Windows 默认读取系统 Fonts 目录；可通过 `THUFOOTBALL_FONT_DIR` 指定目录。
Linux 等环境必须配置该目录并自行提供以下字体文件，程序不静默替换字体：

| 文件 | 用途与字号 |
| --- | --- |
| `simhei.ttf` | 队名 50、副标题 25、阵容标签 38、图例 24 |
| `NotoSansSC-VF.ttf` | 字重固定 400；首发 36、事件姓名备注 40 |
| `trebucbd.ttf` | 比分 60、点球大战比分 50 |
| `trebuc.ttf` | 事件分钟 36 |
| `STFANGSO.TTF` | 时间场地 22 |

系统字体不包含在项目安装包中。缺失或无法读取时返回具体文件路径。
不需要设置 `THUFOOTBALL_CHROMIUM`。

画布直接按最终尺寸绘制，不放大后缩小；仅二维码需要时调整到 140×140px。

网站在创建批次（包括复用已有批次）和刷新批次后，将有效比赛的小程序码保存到
`var/qrcodes/<game_id>.img`。文件保留接口原始图片字节，可能是 JPEG 或 PNG，
因此采用 `.img` 后缀。缓存长期保留，无需数据库迁移。
已有可解码缓存不会再次请求；缺失或损坏时重新获取并原子替换文件。
预取最多同时请求 4 场，单场失败记录日志，不影响批次操作；网站绘图时优先读缓存，
缺失时再请求并缓存。关闭二维码选项时不读取或请求二维码。
CLI 默认仍直接请求接口，不使用网站缓存。

网站整批次渲染逐场调用单场战报流程，每场成功后独立提交数据库，失败则只回滚
该场并继续尝试后面的比赛。最终返回所有比赛的诊断；有任一场失败时不生成新的
整批次文章，但已成功的单场战报及其数据库状态保留，重试时可以复用。
全部成功后再单独组装并保存文章，文章组装失败也不会撤销已提交的单场战报。
PNG 最后编码一次，使用 `optimize=False, compress_level=6, dpi=(96, 96)`。

### 性能与缓存

分别测量比赛查询与校验、二维码请求、内容测量与排版、绘制、PNG 编码。
字体和静态资源加载后，普通战报的排版、绘制和编码目标合计小于 1 秒；
网络耗时另计，首次资源加载和极长战报另行报告，不以 CI 时间断言验收。

网站图片输入指纹包含模板版本 `pillow-v1`，下次实际渲染会更新旧模板产物。
历史文章可继续读取。CLI 保留已有文件复用规则，需要重绘时使用 `--override`。

## CLI

```powershell
thufootball report 4245 --output tmp\game-4245.png
```

默认行为：

- 不调用 `OnReStatGameData`，不修改服务端比赛统计；
- 下载比赛详情和小程序码；
- 显示比赛时间、场地、首发名单和事件时间线；
- 目标文件已存在时拒绝覆盖。

常用选项：

```powershell
# 显式重新统计；警告：该 API 会修改服务端比赛统计
thufootball report 4245 --refresh-stats

# 不显示二维码、时间、场地或首发名单
thufootball report 4245 --no-qrcode --no-time --no-field --no-lineup

# 覆盖已有文件
thufootball report 4245 --output tmp\game-4245.png --override
```

成功后 CLI 输出 JSON，其中包含绝对路径、媒体类型、像素尺寸、本次是否
执行了重新统计，以及结构化 `warnings`。该字段保留原名称，内容可以包含
`info` 和 `warning`。只有信息时 stderr 输出 `status=info`；有警告时输出
`status=warning`。两者均继续生成文件并成功退出；技术异常才导致单场失败。

## 业务诊断与批次文章

无效事件和助攻静默过滤。原业务 warning 降为 info，原业务 error 降为 warning，
其余有效事件的内容和顺序不变。网站、CLI 和自动战报使用相同的中文诊断。
网站按两行展示，例如：

```text
[信息] lineup_under_capacity
新雅 · 0 分钟 · 仅10人首发，预期11人

[警告] duplicate_start
求真 · 0 分钟 · 球员 8号张三在首发中重复出现
```

球队使用机构简称，球员使用号码和姓名，不展示数据库编号。
首发统一为 `0 分钟`，补时为 `80+7 分钟`，点球大战为 `点球大战`；
普通事件缺少时间时省略时间。

网站批次逐场尝试并独立提交，某场技术失败不撤销其他场的成功结果。
只有 info 时允许生成文章；任一 warning 都阻止批次文章，但单场战报仍生成并保存。
网站返回 HTTP 409、`report_batch_article_blocked` 及全部逐场诊断，
清空当前有效文章指针，历史文章仍可查看。命中 PNG 缓存也重新校验，不能绕过文章门禁。
技术失败仍按错误流程汇总，前端自动重新读取已提交的比赛状态。

自动战报先完成所有可生成的单场文件，再判断文章门禁；存在 warning 或技术失败时，
不组装该批次文章、不创建微信草稿。`report.json` 版本为 2，支持 info、
展示字段、`article_blocked` / `render_failed` 状态和逐场 failures。
旧版本缓存不可作为新规则下的合格产物，需使用 `--override` 重新执行 report 阶段。

## Python

```python
import asyncio

from thufootball import (
    ReportSettings,
    THUFootballClient,
    THUFootballReportService,
)


async def main() -> None:
    async with THUFootballClient() as client:
        reports = THUFootballReportService(client)
        result = await reports.download_game_report(
            4245,
            "tmp/game-4245.png",
            settings=ReportSettings(include_qr_code=True),
            # 默认 False。只有明确接受服务端数据修改风险时才传 True。
            refresh_stats=False,
            overwrite=False,
        )
        print(result.path)
        print(result.warnings)


asyncio.run(main())
```

## 安全边界

- OpenID 和 `session_key` 只从现有配置加载，不写入战报、日志或返回对象。
- `GameDetail` 继续使用字段白名单；评论、手机号、工作人员会话等原始响应
  内容不会进入报告模型。
- 二维码图片请求保持匿名。
- 战报静态图标从安装包读取，无网络请求。
- 输出采用同目录临时文件后原子替换；默认禁止覆盖已有文件。
- 默认 `refresh_stats=False`，不会调用有副作用的 `OnReStatGameData`。
- 只有明确接受服务端比赛统计可能被修改时，才使用 `--refresh-stats` 或
  `refresh_stats=True`。
