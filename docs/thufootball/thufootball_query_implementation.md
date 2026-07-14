# THUFootball 查询能力实现设计

## 1. 文档目的

本文档定义自动化比赛前瞻工具所需的 THUFootball 查询层。实现使用 Python，负责把 THUFootball 原始响应转换为稳定的领域对象，并提供以下能力：

1. 查询指定赛事的全部比赛。
2. 查询指定日期的全部比赛。
3. 查询指定赛事在指定日期的全部比赛。
4. 查询指定球队在一个或多个赛事中的最终成绩。
5. 查询指定球队在指定赛事中的全部比赛结果。
6. 查询两支球队在一个或多个赛事中的交锋历史。

查询层只调用只读 API，不提供新增、编辑或删除赛事数据的入口。赛事 ID、球队 ID 和比赛 ID 是领域对象的稳定身份；队名和赛事名称只用于展示。

## 2. 数据来源与能力边界

| THUFootball API | 查询层用途 | 说明 |
| --- | --- | --- |
| `GetCurrentGames` | 读取指定自然日的比赛 | API 日期范围只用于缩小结果集，返回后仍按业务时区精确过滤 |
| `GetTournInfo` | 读取赛事信息及赛事全部比赛 | 赛事查询、球队赛果、交锋和最终成绩推断的主要数据源 |
| `GetGameInfo` | 补充单场比赛详情 | 只为入选且缺少必要字段的比赛调用，避免无差别逐场请求 |

THUFootball 目前没有已确认的“跨赛事交锋”或“球队最终成绩”专用接口。这两类结果由查询层获取各赛事数据后在本地聚合或推断。

### 2.1 查询路由

| 查询条件 | 数据获取方式 |
| --- | --- |
| 仅赛事 | 对每个赛事调用 `GetTournInfo` |
| 仅日期 | 调用 `GetCurrentGames`，再按上海时区自然日过滤 |
| 赛事和日期 | 调用对应赛事的 `GetTournInfo`，再按日期过滤 |
| 球队在赛事中的赛果 | 调用 `GetTournInfo`，按球队 ID 过滤 |
| 多赛事最终成绩 | 并发读取各赛事的 `GetTournInfo`，逐赛事推断 |
| 多赛事交锋 | 并发读取各赛事的 `GetTournInfo`，按两队 ID 过滤后合并 |

指定赛事时不再额外调用全局日期接口，以避免扩大查询范围。一次公共方法调用内，相同赛事只请求一次，相同比赛 ID 只保留一条记录。

## 3. Python 模块边界

建议将实现拆为以下模块：

```text
thufootball/
  client.py       # HTTP、鉴权、响应校验和错误映射
  models.py       # 查询条件、比赛、赛果和成绩领域模型
  queries.py      # 比赛、球队赛果和交锋查询
  outcomes.py     # 积分、排名和淘汰赛成绩推断
  rules.py        # 赛事规则模型及配置加载
```

`client.py` 不计算排名或胜平负；`outcomes.py` 不直接发送 HTTP 请求。上层管线只依赖本文定义的公共查询接口，不直接处理 THUFootball 原始字段。

## 4. 底层只读客户端

使用 `httpx.AsyncClient` 实现一个可注入、可替换的异步客户端：

```python
from datetime import date


class THUFootballClient:
    async def get_current_games(
        self,
        *,
        start_date: date,
        end_date: date,
        visibility: str = "public",
        field_id: int | None = None,
    ) -> list[dict]: ...

    async def get_tournament_info(self, tournament_id: int) -> dict: ...

    async def get_game_info(self, game_id: int) -> dict: ...
```

`start_date` 和 `end_date` 使用左闭右开的领域语义。适配器负责映射成 `history_bound` 和 `future_bound`，查询层仍须对结果做精确时间过滤，不依赖服务端边界是否包含结束日。

### 4.1 HTTP 与鉴权约束

- 从 `THUFOOTBALL_OPENID`、`THUFOOTBALL_SESSION_KEY` 环境变量读取凭据。
- 使用 `httpx` 的 `params` 参数编码查询参数，不手工拼接 URL。
- 默认超时为：连接 5 秒，读取和写入各 15 秒，连接池等待 5 秒。
- 只读请求遇到连接超时、`502`、`503`、`504` 时最多重试一次；鉴权、权限和 schema 错误不重试。
- 日志不得记录完整 URL 查询串、凭据、Cookie 或完整敏感响应。
- 多赛事请求使用 `asyncio.Semaphore(4)`，默认同时在途请求不超过 4 个。
- 客户端构造函数允许注入 `httpx.AsyncClient`，便于协议测试使用本地桩。

## 5. 领域模型

下列签名用于固定接口语义。实现可使用 `dataclass(frozen=True)` 和 `StrEnum`。

### 5.1 枚举

```python
class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class MatchResult(StrEnum):
    WIN = "win"
    DRAW = "draw"
    LOSS = "loss"
    UNKNOWN = "unknown"


class OutcomeState(StrEnum):
    FINAL = "final"
    PROVISIONAL = "provisional"
    UNDETERMINED = "undetermined"


class TournamentFormat(StrEnum):
    LEAGUE = "league"
    GROUP = "group"
    KNOCKOUT = "knockout"
    GROUP_KNOCKOUT = "group_knockout"
```

`PROVISIONAL` 表示赛事尚未完成；`UNDETERMINED` 表示赛事应已完成，但规则或数据不足以唯一确定成绩。两者均不得对外表述为最终成绩。

### 5.2 比赛查询条件

```python
@dataclass(frozen=True)
class GameQuery:
    tournament_ids: tuple[int, ...] = ()
    match_date: date | None = None
    team_ids: tuple[int, ...] = ()
    team_match: Literal["any", "all"] = "any"
    include_unfinished: bool = True
    timezone: str = "Asia/Shanghai"
```

校验规则：

- `tournament_ids` 和 `match_date` 至少提供一项。
- 所有 ID 必须是正整数；重复赛事 ID 在保留首次出现顺序的前提下去重。
- `team_ids` 最多包含两个不同球队。
- `team_match="any"` 表示任一球队参赛即匹配；`all` 表示比赛必须同时包含所有指定球队。
- 当前版本只允许 `timezone="Asia/Shanghai"`，避免调用方产生不同日期口径。

### 5.3 统一比赛对象

```python
@dataclass(frozen=True)
class GameSummary:
    game_id: int
    tournament_id: int
    kickoff: datetime
    status: GameStatus
    stage: str | None
    round: int | None
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    home_score: int | None
    away_score: int | None
    result_text: str | None
    field_name: str | None
```

`kickoff` 必须是带 `Asia/Shanghai` 时区的 `datetime`。只有确定完赛且比分合法时才设置数值比分。

### 5.4 球队赛果

```python
@dataclass(frozen=True)
class TeamGameResult:
    game: GameSummary
    team_id: int
    opponent_id: int
    opponent_name: str
    venue: Literal["home", "away"]
    goals_for: int | None
    goals_against: int | None
    result: MatchResult
```

不论目标球队是主队还是客队，`goals_for`、`goals_against` 和 `result` 都使用目标球队视角。

### 5.5 交锋历史

```python
@dataclass(frozen=True)
class HeadToHeadSummary:
    team_a_wins: int
    draws: int
    team_b_wins: int


@dataclass(frozen=True)
class HeadToHeadHistory:
    team_a_id: int
    team_b_id: int
    tournament_ids: tuple[int, ...]
    matches: tuple[GameSummary, ...]
    summary: HeadToHeadSummary
    by_tournament: Mapping[int, HeadToHeadSummary]
```

### 5.6 赛事成绩规则

```python
@dataclass(frozen=True)
class OutcomeRuleSet:
    tournament_id: int
    version: str
    format: TournamentFormat
    win_points: int = 3
    draw_points: int = 1
    loss_points: int = 0
    tie_breakers: tuple[str, ...] = (
        "points",
        "goal_difference",
        "goals_for",
    )
    stage_aliases: Mapping[str, str] = field(default_factory=dict)
    group_membership: Mapping[int, str] = field(default_factory=dict)
    relegation_positions: Mapping[str, tuple[int, ...]] = field(
        default_factory=dict
    )
    has_third_place_match: bool = False
    completion_mode: Literal[
        "all_listed_games_finished",
        "final_finished",
    ] = "all_listed_games_finished"
```

约束：

- `stage_aliases` 把 API 原始阶段名映射成 `group`、`round_of_16`、`quarterfinal`、`semifinal`、`third_place`、`final` 等规范阶段。
- `group_membership` 在分组赛事中把球队 ID 映射到组名。
- `relegation_positions` 的键使用组名或 `overall`，值为最终降级名次。
- 支持的同分规则为 `points`、`goal_difference`、`goals_for`、`head_to_head_points`、`head_to_head_goal_difference`。
- 禁止把球队 ID、队名或输入顺序当作同分排序依据。

### 5.7 球队赛事成绩

```python
@dataclass(frozen=True)
class OutcomeEvidence:
    kind: str
    game_ids: tuple[int, ...]
    summary: str


@dataclass(frozen=True)
class TeamTournamentOutcome:
    team_id: int
    tournament_id: int
    state: OutcomeState
    labels: tuple[str, ...]
    overall_rank: int | None
    group: str | None
    group_rank: int | None
    stage_reached: str | None
    relegated: bool | None
    evidence: tuple[OutcomeEvidence, ...]
    rules_version: str | None
    warnings: tuple[str, ...]
```

`labels` 允许组合结果，例如 `("小组第三", "降级")`。证据至少包含用于排名或淘汰赛判断的比赛 ID 及安全摘要。

## 6. 公共查询接口

公共接口由 `THUFootballQueryService` 提供。服务构造时注入 `THUFootballClient`、可冻结的 `Clock` 和并发配置。

### 6.1 通用比赛查询

```python
async def query_games(self, query: GameQuery) -> list[GameSummary]: ...
```

- 仅赛事：返回指定赛事全部比赛。
- 仅日期：返回该自然日全部比赛。
- 赛事和日期：返回两个条件的交集。
- 可选球队条件用于缩小结果集。
- 默认保留未完赛赛程，按 `kickoff`、`tournament_id`、`game_id` 升序。
- 空结果返回空列表，不抛出“无比赛”异常。
- 同一比赛 ID 对应不同核心字段时返回 `DataConflict`，不得静默选择其中一条。

### 6.2 球队最终成绩

```python
async def query_team_outcomes(
    self,
    team_id: int,
    tournament_ids: Sequence[int],
    rules: Mapping[int, OutcomeRuleSet],
) -> list[TeamTournamentOutcome]: ...
```

- 每个赛事返回一个独立结果，顺序与去重后的输入赛事 ID 一致。
- 赛事未结束时返回 `PROVISIONAL`；规则缺失、球队不在赛事中或无法打破同分时返回 `UNDETERMINED` 并写明原因。
- 一个赛事无法读取时，整个调用抛出 `BatchQueryError`，错误中列出失败赛事 ID；不返回容易被误当完整数据的部分列表。
- 不计算跨赛事总排名，也不把多个赛事成绩合并为一个标签。

### 6.3 球队在单项赛事中的全部赛果

```python
async def query_team_results(
    self,
    team_id: int,
    tournament_id: int,
    *,
    include_unfinished: bool = False,
) -> list[TeamGameResult]: ...
```

- 默认只返回已完成且比分合法的比赛结果。
- `include_unfinished=True` 时保留赛程，但结果为 `UNKNOWN`，进失球为空。
- 输出按时间倒序，时间相同时按比赛 ID 倒序。

### 6.4 两队多赛事交锋

```python
async def query_head_to_head(
    self,
    team_a_id: int,
    team_b_id: int,
    tournament_ids: Sequence[int],
    *,
    include_unfinished: bool = False,
) -> HeadToHeadHistory: ...
```

- 两个球队 ID 必须不同。
- 正反主客场均计入交锋，名称变化不影响匹配。
- 默认只统计已完赛比赛；未完赛比赛即使被包含，也不进入胜平负汇总。
- 比赛按时间倒序，同时提供跨赛事总计和按赛事分组的统计。
- 没有交锋时返回空 `matches` 和全零汇总。

## 7. 六类查询调用示例

```python
# 1. 指定赛事全部比赛
await service.query_games(GameQuery(tournament_ids=(136,)))

# 2. 指定日期全部比赛
await service.query_games(GameQuery(match_date=date(2026, 6, 15)))

# 3. 指定赛事、指定日期全部比赛
await service.query_games(
    GameQuery(tournament_ids=(136,), match_date=date(2026, 6, 15))
)

# 4. 球队在多个赛事中的最终成绩
await service.query_team_outcomes(
    team_id=1913,
    tournament_ids=(136, 140),
    rules={136: rules_136, 140: rules_140},
)

# 5. 球队在指定赛事中的全部比赛结果
await service.query_team_results(team_id=1913, tournament_id=136)

# 6. 两队在多个赛事中的交锋历史
await service.query_head_to_head(
    team_a_id=1913,
    team_b_id=1916,
    tournament_ids=(136, 140),
)
```

## 8. 日期、状态与比分规则

### 8.1 自然日

指定日期按 `Asia/Shanghai` 解释，区间固定为：

```text
[当天 00:00:00, 次日 00:00:00)
```

API 返回无时区时间字符串时，将其解释为上海本地时间。不得直接使用字符串比较日期。

### 8.2 比赛状态

优先使用 API 的明确状态字段；没有状态字段时按以下规则降级：

1. 比分可成功解析且主客队进球均存在，视为 `FINISHED`。
2. 比分为空且开球时间在未来，视为 `SCHEDULED`。
3. 有明确延期或取消标记时分别映射为 `POSTPONED`、`CANCELLED`。
4. 其余情况为 `UNKNOWN`，不进入胜平负和积分计算。

比分只接受约定的两个非负整数格式。异常比分返回结构化数据问题，不自行修复或猜测。

## 9. 最终成绩推断

### 9.1 完成状态

- `all_listed_games_finished`：赛事结束日期已早于当前日期，且已列出的比赛没有未完成项，才进入最终排名计算。
- `final_finished`：规范阶段为 `final` 的比赛已完成，才判定淘汰赛结束。
- 结束条件尚未满足时返回 `PROVISIONAL`。
- 配置要求的决赛或分组信息不存在时返回 `UNDETERMINED`。

当前时间必须来自可注入的 `Clock`，不能在推断函数中直接读取系统时间。

### 9.2 联赛和小组排名

1. 只使用已完赛且比分合法的对应阶段比赛。
2. 按规则配置计算积分、净胜球、进球数及相互战绩小表。
3. 依次应用 `tie_breakers`；相互战绩只在当前同分球队子集内计算。
4. 所有配置规则用尽后仍并列时，不使用任意稳定排序冒充名次；目标球队结果为 `UNDETERMINED`。
5. 联赛输出“第 N 名”，小组赛输出“X 组第 N 名”。
6. 目标球队名次命中对应组或 `overall` 的降级名次时，追加“降级”标签。

### 9.3 淘汰赛

- 决赛胜者为“冠军”，负者为“亚军”。
- 存在三四名决赛时，胜者为“第三名”，负者为“第四名”。
- 不设三四名决赛时，半决赛负者统一为“四强”。
- 四分之一决赛负者为“八强”；八分之一决赛负者为“十六强”。
- 同一阶段存在多场比赛时，通过球队 ID 定位目标球队参与的比赛。
- 阶段字段无法通过 `stage_aliases` 规范化时，不根据轮次数字猜测阶段。

### 9.4 混合赛制

小组加淘汰赛赛事先计算小组排名，再检查球队是否进入淘汰阶段：

- 进入淘汰赛的球队以淘汰赛最终成绩作为主要标签，小组排名保留在证据中。
- 未进入淘汰赛的球队使用小组名次作为结果。
- 降级标签在主要成绩确定后独立追加。

## 10. 错误与可观测性

查询层沿用项目测试计划中的错误分类，并补充查询参数和批量请求错误：

```text
QueryValidationError
ConfigurationError
AuthenticationError
PermissionError
Timeout
RateLimited
InvalidResponse
SchemaError
DataConflict
BatchQueryError
```

`PROVISIONAL` 和 `UNDETERMINED` 是有效领域结果，不作为异常抛出。每个异常至少包含阶段、可重试性和相关的赛事或比赛 ID；错误摘要必须脱敏。

最小日志记录：

- 查询类型和脱敏后的 ID 集合。
- 命中的比赛数量、去重数量和未完赛数量。
- 成绩推断使用的规则版本及结果状态。
- 请求阶段、耗时、重试次数和稳定错误类型。

## 11. 验收场景

### 11.1 比赛查询

- 单赛事查询返回该赛事全部比赛且按时间升序。
- 日期查询包含当天零点，排除次日零点。
- 赛事与日期组合查询只返回交集。
- 空比赛日返回空列表。
- 相同比赛 ID 的完全重复记录被去重；核心字段冲突返回 `DataConflict`。

### 11.2 球队赛果与交锋

- 目标球队作为主队和客队时，进失球和胜平负均按球队视角计算。
- 同一对手的正反主客场都进入交锋历史。
- 多赛事交锋按时间合并，并保留按赛事统计。
- 无交锋返回空历史和零汇总。
- 未完赛比赛默认不进入赛果和胜平负统计。

### 11.3 最终成绩

- 可分别推断冠军、亚军、四强、八强、小组第三和联赛名次。
- 配置降级名次后可输出组合标签，例如“小组第三、降级”。
- 存在三四名决赛时区分第三和第四。
- 赛事未结束时返回 `PROVISIONAL`。
- 缺少赛事规则、阶段映射或无法打破同分时返回 `UNDETERMINED`，并包含原因。

### 11.4 适配与错误

- 凭据含 `+`、`/`、`=` 时参数值保持不变。
- 鉴权失败不重试，日志不出现完整凭据。
- 多赛事中任一请求失败时返回包含失败赛事 ID 的 `BatchQueryError`。
- API 缺少核心身份字段、返回非 JSON 或异常比分时产生对应结构化错误。

## 12. 非目标

- 不按队名或赛事名称自动猜测 ID。
- 不跨数据源补全 THUFootball 未提供的历史比赛。
- 不在缺少赛事规则时猜测降级名额或同分排名。
- 不提供任何赛事、球队、球员或比赛数据的写入接口。
- 不在本层生成公众号文案、HTML 或草稿。
