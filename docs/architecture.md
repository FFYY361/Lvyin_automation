# thufootball_automation 项目架构

## 当前边界

仓库当前提供三个中层模块，但不提供自动编排管线：

```text
thufootball             preview                         wechat_official
赛事领域查询             本地 data + template 渲染       Article + 微信草稿 API
```

- `thufootball` 不依赖另两个模块，只负责只读领域查询。
- `preview` 只依赖 `wechat_official` 导出的 `Article`、`CoverFile` 和 `CoverMediaId` 数据契约，不读取凭据、不访问网络。
- `wechat_official` 不导入 `preview`，只接收完整文章。

## 中层服务

- `THUFootballQueryService`：比赛、球队赛果、最终成绩与交锋查询。
- `PreviewService`：校验 `PreviewSourceData`，渲染模板并返回 `Article`。
- `WechatOfficialService`：处理 Article 正文图片和封面，创建公众号草稿。

文章可以在 Python 内存中直接传递，也可以通过版本化文章目录落盘：

```text
article/
├── article.json
├── body.html
└── cover.png
```

当前已在 `src/auto_preview` 实现第一条高层自动化：按日期与男足、女足或五人制固定赛事范围读取数据，生成前瞻 Article，并按显式 stage 创建微信公众号草稿。`auto_preview` 只编排公开中层 Service；任务状态、严格断点复用和运行记录保存在仓库根目录的 `runs/auto_preview`，不反向改变三个中层模块的依赖边界。批量并发等通用调度能力留待后续单独设计。

## 安全边界

- THUFootball 能力只读。
- Preview 完全本地运行。
- 微信能力止于草稿箱，不包含发布或群发。
- 微信 CLI 默认 dry-run，只有显式 `--execute` 才产生外部写入。
