# 绿茵宣传部项目架构

## 当前边界

仓库当前提供四个中层模块：

```text
thufootball       weather          preview                    wechat_official
赛事领域查询       高德短期天气      本地 data + template 渲染  Article + 微信草稿 API
```

- `thufootball` 和 `weather` 是互不依赖的外部适配器；前者的查询层只读，
  战报服务可在显式下载命令中重新统计一场比赛。
- `preview` 只依赖 `wechat_official` 导出的 `Article`、`CoverFile` 和 `CoverMediaId` 数据契约，不读取凭据、不访问网络。
- `wechat_official` 不导入 `preview`，只接收完整文章。

## 中层服务

- `THUFootballQueryService`：比赛、球队赛果、最终成绩与交锋查询。
- `WeatherQueryService`：按 adcode 和日期查询高德短期天气预报。
- `PreviewService`：校验 `PreviewSourceData`，渲染模板并返回 `Article`。
- `WechatOfficialService`：处理 Article 正文图片和封面，创建公众号草稿。

文章可以在 Python 内存中直接传递，也可以通过版本化文章目录落盘：

```text
article/
├── article.json
├── body.html
└── cover.png
```

当前有两条高层自动化：

- `src/auto_preview` 接收一组日期和赛事，按日期 × 赛事展开组合，依次完成全部 data、全部 article，并将最多八篇文章一次创建为微信公众号多图文草稿。data 屏障完成后，它按不同日期调用天气 Service，并固定使用海淀区 `110108`。每个组合的状态、`no_games` 负结果缓存、严格断点复用和运行记录保存在 `runs/auto_preview`。
- `src/auto_report` 复用 `auto_preview` 的当前赛事 ID 配置，执行全部 report、全部 article、一次 publish。每个赛事在同一批次只查询一次比赛列表；完赛场次交给 `THUFootballReportService`，弃赛转换为正文文字，未完赛跳过。每个组合的清单、PNG 哈希、skipped 负结果、Article 和共享草稿回执保存在 `runs/auto_report`。

两条管线都只编排公开中层 Service，不反向改变四个中层模块的依赖边界。它们的 publish 都是批次屏障：任何前序组合失败时，不会提前创建部分草稿。

## 安全边界

- THUFootball 查询能力只读；战报下载默认也不重新统计。只有显式 opt-in
  才调用会修改服务端比赛统计的 `OnReStatGameData`。
- `auto_report` 不暴露刷新统计选项，包含 `--override` 的所有路径都固定使用
  `refresh_stats=False`。
- 天气能力只读取高德短期预报，API Key 不写入日志或产物。
- Preview 完全本地运行。
- 微信能力止于草稿箱，不包含发布或群发。
- 微信 CLI 默认 dry-run，只有显式 `--execute` 才产生外部写入。
