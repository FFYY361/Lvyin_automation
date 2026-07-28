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
网页只能在 Canvas 绘制完成后手工另存。本地实现读取同一份安全字段白名单，
再由本机 Edge/Chromium 运行官网同版本的 jCanvas 绘制代码并直接导出 Canvas
PNG，不保存 `GetGameInfo` 原始响应。

本地绘制逐项复现 `game.js` 的 jCanvas 逻辑：

- 固定 1600px 宽、纯白背景和动态 `hy` 高度；
- 使用网站相同的队名、比分、副标题、时间和场地坐标与字号；
- 首发按号码排序，以网站相同的 600px 最大宽度和 36px 行高换行；
- 比赛事件保留 API 顺序，按网站规则合并同一时间、转换乌龙球方向和第二张
  黄牌，并使用相同的事件框、横线、中轴线和图标布局；
- `START`、`END`、图例、小程序码，以及 `G.png`、`YC.png`、`Y2C.png`、
  `SI.png`、`SO.png` 等事件图标，均直接读取网站同款公开图片资源，不再用
  Pillow 或矢量代码近似复刻。

比赛 `4245` 的默认输出已与参考图核对，尺寸同为 `1600×1646`。

像素级字体栅格依赖浏览器。程序会自动寻找 Microsoft Edge、Google Chrome
或 Chromium；也可将 `THUFOOTBALL_CHROMIUM` 设置为浏览器可执行文件路径。
渲染时只把字段白名单和匿名静态图片写入临时 HTML，不会把 OpenID 或
`session_key` 交给浏览器。

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

成功后 CLI 输出 JSON，其中包含绝对路径、媒体类型、像素尺寸，以及本次是否
执行了重新统计。

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


asyncio.run(main())
```

## 安全边界

- OpenID 和 `session_key` 只从现有配置加载，不写入战报、日志或返回对象。
- `GameDetail` 继续使用字段白名单；评论、手机号、工作人员会话等原始响应
  内容不会进入报告模型。
- 二维码图片请求保持匿名。
- 战报静态图标请求保持匿名，不携带 OpenID 或 `session_key`。
- 输出采用同目录临时文件后原子替换；默认禁止覆盖已有文件。
- 默认 `refresh_stats=False`，不会调用有副作用的 `OnReStatGameData`。
- 只有明确接受服务端比赛统计可能被修改时，才使用 `--refresh-stats` 或
  `refresh_stats=True`。
