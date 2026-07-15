# 清华绿茵多场比赛前瞻模板 v1

唯一母版来源：[《【马杯男足周六前瞻】|| 落日熔金，危崖试翼》](https://mp.weixin.qq.com/s/JBOIyY6f679Tg1DDb76Cgw)。当前 V1 是已完成公众号草稿实测的正式模板。

## 文件

- `template.html`：公众号正文 HTML 模板；
- `example_data.json`：男足周六双场完整示例；
- `example_data_women_saturday.json`：女足周六最小示例；
- `example_data_futsal_saturday.json`：五人制周六最小示例；
- `schema.json`：包含栏目配置在内的完整前瞻源数据 JSON Schema。

源数据只包含栏目、日期、天气、球队、结构化赛果、纯文本前瞻和人员信息，不包含 `schedule_rows`、`*_html` 或预拼接日期/比分。赛程表和比赛卡片直接使用同一份 `matches`。男足、女足和五人制通过 `column.competition_full_name` 与 `column.competition_short_name` 切换，不需要额外配置文件。

`game_id` 已知时填写 THUFootball 返回的正整数；无法确认时统一填写 `-1`。`0` 和小于 `-1` 的值不合法。`team_id` 不使用该哨兵规则，仍必须是真实正整数。

## 本地生成前瞻

```powershell
python tools/wechat_preview.py render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article.html
```

渲染不会连接微信，也不会创建草稿。输入会被严格解析为不可变数据类；未知字段、错误类型、跨日比赛、非 `+08:00` 开球时间和半组比分都会给出包含完整字段路径的错误。

每场比赛通过 `writers` 数组声明作者。文末作者按比赛顺序和场内顺序汇总，去除姓名首尾空白并按首次出现稳定去重，再用半角空格连接。

## 模板语法

- 普通文本：`{{match.home.name}}`，始终进行 HTML 转义；
- 有限格式化器：`{{match.kickoff|time_hm}}`；
- 可嵌套列表：`<!-- wx:each source.matches as match --> ... <!-- wx:endeach -->`；
- 空列表回退：在列表块中加入 `<!-- wx:empty -->暂无数据`；
- 循环状态：`loop.index`、`loop.first`、`loop.last`。

模板不支持三花括号、任意表达式、函数调用或由源数据注入 HTML。

## 公众号兼容约束

- 主视觉使用真实图片撑开高度，避免微信把占位换行计算成额外空白；
- 章节标题到内容默认间距为 `10px`；
- 比赛信息使用 `margin:10px 24px;text-align:left`，避免窄屏截断；
- 历史表格使用 5 个等宽底层列和 `colspan="2" / 1 / colspan="2"`，结构上锁定 `40% / 20% / 40%`；
- 交手战绩标题与正文以 `4px` 段落间距连接，不使用不可控的 `<br>` 空行；
- 正文图片必须使用微信允许的图片地址；封面必须是永久图片素材 ID。

## 草稿接口行为

客户端不在本地预判微信的标题、摘要或正文长度限制。执行真实写入时会提交完整 JSON，并将微信返回的 `errcode` 和 `errmsg` 作为 `DraftWriteError` 上报。模板能力只负责生成和写入草稿，不包含发布或群发操作。
