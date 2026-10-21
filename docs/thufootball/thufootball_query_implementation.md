# THUFootball 查询实现

## ID 与身份

业务球队 ID 统一为 `tournament_team_id`，标识一届赛事中的一次球队报名。业务参数不再接受
`team_id`，不将旧字段静默解释为新 ID。跨届、跨项目的报名通过 `institutions` 显式关联。

`GameSummary` 保留上游 `home_team_id`、`away_team_id` 作为原始事实，保证战报输入哈希和事实
冲突检测稳定；筛选、名称、战绩及归属使用 `home_tournament_team_id`、`away_tournament_team_id`。
上游 HTTP 协议见 `thufootball_http_api_inventory.md`，不改写上游字段。

院系和前身均保存 `tournament_teams = {male: {赛事ID: 报名ID}, female: {...}, futsal: {...}}`。
配置校验报名唯一归属、赛事和项目，排名以报名 ID 为键。赛季从赛事资料读取。

## 精确报名查询

```python
await service.query_games(GameQuery(
    tournament_ids=(122,), tournament_team_ids=(1735,), include_unfinished=True,
))
await service.query_team_matches(1735, 122, include_unfinished=False)
await service.query_team_outcomes(1735)
await service.query_team_to_team_matches(1735, 1745, (122,))
```

- `GameQuery` 按报名 ID 精确匹配，最多指定两支队，`team_match` 为 `any` 或 `all`。
- 必须提供赛事、日期或报名 ID，不再默认枚举全部可访问赛事。
- 单队赛果只查该次报名。省略赛事时从配置推导；未配置的报名需要显式提供赛事。
- 单队成绩只读取该报名在本届的人工排名，不联网、不展开历史。
- 两支报名队的精确交锋限定一届赛事；已知报名与指定赛事不一致时返回校验错误。
- `TeamGameResult` 使用 `tournament_team_id`、`opponent_tournament_team_id`。
  `HeadToHeadHistory` 使用双方的 `team_a_tournament_team_ids`、`team_b_tournament_team_ids`。

## 院系历史查询

```python
await service.query_institution_games(
    "车辆与运载学院", competition="male", tournament_ids=(122, 99, 89),
)
await service.query_institution_head_to_head(
    "车辆与运载学院", "未央书院", competition="male", tournament_ids=(122, 99, 89),
)
```

院系全名精确匹配。必须提供项目或赛事范围，同时提供时校验一致；只展开院系配置，不通过
全局 ID 推断历史。项目为 `male`、`female`、`futsal`。

本体可展开同项目的前身，前身只展开自身。比赛按 `game_id` 去重，前身内部交锋保留分别归属的
统计规则。底层 `query_team_sets_matches` 接收显式报名 ID 集合和赛事范围，不展开别名。

网站继续使用原有赛季范围、开球时间截止、排名选择和前身规则。历史交锋中每支队使用该场
赛事的报名 ID，展示时对齐目标比赛主客方向，包括比分和点球比分。

## 赛果与读取规则

排序、并发读取、请求复用、失败处理、黑名单 `6、28`、比赛有效性、弃赛和点球判定保持原样。
报名球员、停赛和事件继续使用赛事内关联。事件按 `side` 归类，乌龙球等处理不变。

真实比赛必须有正整数报名 ID；纯展示占位 `game_id=-1` 可使用 `null`，不得进入查询。
CLI 是接口的薄封装，筛选参数为 `--tournament-team-id`；旧全远端历史语义不再维护。

升级见 `../ai_preview/football_data_database.md`，战报用法见 `thufootball_report_download.md`。
