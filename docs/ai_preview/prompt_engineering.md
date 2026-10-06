# AI 前瞻 Prompt：首版组装方案

## 1. 消息组成

System message 根据 `MATCH_ID` 对应的比赛项目，依次拼接：

1. `src/ai_preview/prompt/system.md`；
2. `src/ai_preview/prompt/competition_rules/` 中对应男足、女足或五人制的一个赛制说明；
3. `src/ai_preview/prompt/writing_rules.md`；
4. `src/ai_preview/prompt/data_rules.md`。

只加入本场项目对应的一个赛制文档，避免无关赛制干扰写作。组装后的 System message 可通过
`ai_preview.build_prompt_bundle(match_id).system_message` 取得。

User message 由程序根据一场比赛动态生成，包含 `match_context`、`manual_context` 和
`automatic_context`。本阶段只组装正文生成 Prompt，不调用模型，也不处理标题生成。

## 2. 输入与接口

业务输入只有 `MATCH_ID`。比赛所属赛事、赛季、主客队、时间和场地均从本地自动资料中读取，
不要求调用方重复传入；这样可以避免调用参数与比赛快照互相冲突。

命令行用法：

```powershell
python -m ai_preview MATCH_ID
```

安装项目后也可以使用：

```powershell
ai-preview-prompt MATCH_ID
```

Python 接口为 `ai_preview.build_user_message(match_id)`；需要检查中间结构时使用
`ai_preview.build_prompt_bundle(match_id)`。命令行另提供 `--config`，用于替换资料选择配置；
足球资料通过 PostgreSQL Repository 读取，不提供旧文件回退。

## 3. 自动资料选择

- 只选择目标比赛开球时间之前、已经结束且有效的比赛。即使本地快照已经包含目标比赛赛果，
  也不会把本场结果注入 Prompt。
- 历史范围由 `history_seasons` 控制，默认包含目标赛季在内的最近三个赛季。
- 同一实际身份、同一项目的多个历史 ID 用于追溯自身历史；本体和前身在 `history_teams` 中
  分别组织，ID 不出现在 Prompt 中。当前赛季单队历史不展开前身，旧前身目标比赛只追溯其自身。
- 每方最近 `recent_matches_with_events` 场非交锋比赛保留比分、首发和完整有效事件，随后归入
  对应身份的 `recent_matches`；更早比赛归入 `earlier_matches`。前身互相比赛不累计成本体总战绩。
- `season_outcomes` 按排名记录选择本体成绩或前身最佳成绩，保留成绩来源；没有排名为“未参赛”。
  每个身份的 `past_seasons` 只统计早于目标赛季的赛事战绩。
- 两队在历史范围内的全部直接交锋进入 `head_to_head`，始终保留事件详情，不占上述近期场次
  额度。
- 直接交锋统一转换为目标比赛的主客顺序；比分、首发、进球、换人、牌和其他事件同步转换。
- 交锋名称保留比赛当时实际身份的配置简称，不将前身名称替换为本场合并球队名称。
- 输出不包含比赛、赛事、球队、球员或事件 ID。姓名暂时保留，以降低首版 Prompt 组装难度；
  正文是否出现姓名仍由写作规则约束。
- 当前赛事战绩和小组积分只按本场之前的比赛现场计算。无法确认同分球队准确顺位时，
  `rank` 为 `null` 并给出 `rank_note`，不读取赛季结束后的排名倒推赛前名次。

## 4. 配置

默认配置位于 `src/ai_preview/config.json`：

```json
{
  "recent_matches_with_events": 3,
  "history_seasons": 3
}
```

`recent_matches_with_events` 可以为 `0`；此时两队的非直接交锋全部使用摘要，但直接交锋仍然
保留完整事件。配置只负责资料选择，不改变 User message 的字段结构。

## 5. 初步验收

后续选择约 5-10 场比赛试写，重点检查事实错误、球员归属、人工资料使用、双方叙述平衡、
文章结构和可编辑性。首轮以暴露问题为主，不设置精确分数或通过比例。
