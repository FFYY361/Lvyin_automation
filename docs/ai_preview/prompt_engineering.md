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
`ai_preview.build_prompt_bundle(match_id)`。命令行另提供 `--config`、`--data-root` 和
`--teams-path`，只用于开发和测试时替换默认本地文件，不属于业务输入。

## 3. 自动资料选择

- 只选择目标比赛开球时间之前、已经结束且有效的比赛。即使本地快照已经包含目标比赛赛果，
  也不会把本场结果注入 Prompt。
- 历史范围由 `history_seasons` 控制，默认包含目标赛季在内的最近三个赛季。
- 同一院系、同一项目在 `teams.json` 中登记的多个历史球队 ID 会合并使用，避免球队换 ID 后
  漏掉旧赛季资料；这些 ID 不会出现在 Prompt 中。
- 每支球队最近 `recent_matches_with_events` 场非直接交锋进入 `recent_matches`，包含比分、
  首发和完整有效事件；更早比赛进入只有赛果摘要的 `earlier_matches`。
- 两队在历史范围内的全部直接交锋进入 `head_to_head`，始终保留事件详情，不占上述近期场次
  额度。
- 直接交锋统一转换为目标比赛的主客顺序；比分、首发、进球、换人、牌和其他事件同步转换。
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
