# qhly_preview_v1 模板

该目录包含仓库当前正式前瞻模板：

- `template.html`：正文模板；
- `schema.json`：前瞻 data 契约；
- `example_data.json`：男足完整示例；
- `example_data_women_saturday.json`：女足最小示例；
- `example_data_futsal_saturday.json`：五人制最小示例。

渲染示例：

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --cover path/to/cover.png `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article
```

data 必须符合 `schema.json`，未知字段会被拒绝。前瞻文案使用纯文本数组，不允许在 data 中注入 HTML。模板语法、字段校验和文章目录格式见 [前瞻模板与渲染教程](../../docs/preview/preview_template_tutorial.md)。

顶部赛程表通过 `venue_short_name` 格式化器缩写场地：紫荆/西区/东区足球场分别显示为“紫操/西操/东操”，紫荆与西区的南北侧场地显示为“紫南/紫北/西南/西北”。映射表位于 `src/preview/template.py` 的 `VENUE_SHORT_NAMES`；未配置名称原样显示，比赛详情仍保留场地全称。

比分行统一使用 `主队简称 X:Y 客队简称` 格式，球队与比分之间各保留一个英文半角空格。带点球的比分使用 `X(x):Y(y)`，例如常规时间 `0:0`、点球 `2:1` 显示为 `0(2):0(1)`。非点球比赛与“对手退赛”等特殊结果保持原样。

女足的过往三届成绩和交手记录只显示赛季，例如 `24-25`，不追加“女足”标签；男足继续显示 `24-25-甲` 等等级信息。

标题区背景图由 `header_background_url` 格式化器按赛事切换：女足使用女足比赛实景图，男足与五人制继续使用默认背景。图片地址统一配置在 `src/preview/template.py`；所有背景使用响应式 `32:9` 画框居中裁切，保证不同原图的标题框高度一致。
