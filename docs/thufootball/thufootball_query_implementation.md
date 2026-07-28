# THUFootball 查询能力实现设计

## 1. 目标与事实基础

查询层使用 Python，把 THUFootball HTTP API 的原始响应转换为稳定领域对象，为比赛前瞻提供六项能力：

1. 查询指定赛事的全部比赛。
2. 查询指定日期的全部比赛。
3. 查询指定赛事在指定日期的全部比赛。
4. 查询指定球队在一个或多个赛事中的最终成绩。
5. 查询指定球队在指定赛事中的全部比赛结果。
6. 查询两支球队在一个或多个赛事中的交锋历史。

查询层只读，不提供任何新增、编辑或删除数据的入口。“全部比赛”指当前 API 和凭据可见范围内的全部比赛，不保证包括无权访问的私有数据。

实现以已验证接口为准：

| API | 用途 |
| --- | --- |
| `GetUserInfo` | 验证凭据是否有效；响应中的用户资料不进入查询领域模型 |
| `GetMyTournaments` | 获取当前凭据可取得的协会赛事目录；不能据此判断用户是否管理或参加赛事 |
| `GetCurrentGames` | 获取日期范围内的比赛；公开查询可不传凭据 |
| `GetTournInfo` | 获取赛事、报名球队、赛事比赛和同系列赛季 ID，是赛事查询的主要数据源 |
| `GetGameInfo` | 按需补充单场详情、事件和裁判等信息 |

`GetTournTypes` 不属于当前六项查询能力的必要依赖。查询服务不调用非只读
接口；战报下载单独由 `THUFootballReportService` 管理，其重新统计步骤不进入
任何查询方法。

实现必须遵守以下事实：

- 比赛的 `time` 是不带时区后缀的 UTC 时间字符串，解析后再转换为 `Asia/Shanghai`。
- `home_tourn_team_id`、`away_tourn_team_id` 是赛事内球队 ID；嵌套球队对象的 `team_id` 才是跨赛事球队 ID。
- `game.status` 表示比赛记录是否有效，不是比赛进行状态；比赛是否开始或结束分别使用 `start`、`end`。
- `GetTournInfo.registered_teams[].rank` 可能为 `0`，不能直接视为最终名次。
- `GetGameInfo.game_time_metadata` 可能残留错误计时状态，不能单独用来判断比赛是否结束。

## 2. 代码结构与核心模型

建议代码结构：

```text
src/thufootball/
  client.py       # HTTP、鉴权、重试、响应校验
  models.py       # 领域对象和枚举
  mappers.py      # API 原始字段到领域对象的白名单映射
  queries.py      # 比赛、最终成绩、球队赛果和交锋查询
  rankings.py     # 人工维护的静态最终成绩加载与校验
  notes/          # 支持赛事、球队别名、身份审计和逐赛事排名
```

`client.py` 不计算业务结果。原始响应只能进入 `mappers.py`，上层不使用未经筛选的 `dict`；最终成绩查询只读取本地静态名单，不发送 HTTP 请求。

### 2.1 比赛状态与结果

```python
class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    STARTED = "started"
    FINISHED = "finished"
    UNKNOWN = "unknown"


class MatchResult(StrEnum):
    WIN = "win"
    DRAW = "draw"
    LOSS = "loss"
    UNKNOWN = "unknown"
```

无效或无法解释的比赛统一映射为 `UNKNOWN`，不额外扩展 `GameStatus`。

### 2.2 查询条件与比赛对象

```python
@dataclass(frozen=True)
class GameQuery:
    tournament_ids: tuple[int, ...] = ()
    match_date: date | None = None
    team_ids: tuple[int, ...] = ()
    team_match: Literal["any", "all"] = "any"
    include_unfinished: bool = True


@dataclass(frozen=True)
class GameSummary:
    game_id: int
    tournament_id: int
    tournament_name: str
    kickoff_utc: datetime
    kickoff_local: datetime
    status: GameStatus
    record_active: bool
    valid: bool
    stage: str | None
    group_name: str | None
    round: int | None
    home_tournament_team_id: int
    home_team_id: int
    home_team_name: str
    away_tournament_team_id: int
    away_team_id: int
    away_team_name: str
    home_score: int | None
    away_score: int | None
    result_text: str | None
    penalty_shootout: bool
    home_penalty: int | None
    away_penalty: int | None
    home_abandon: bool | None
    away_abandon: bool | None
    field_name: str | None
```

`penalty_shootout` 保留远端字段的真实语义：表示该场是否启用“打平后点球决胜”的规则，不表示比赛实际进入了点球大战。`GameSummary.decided_by_penalty_shootout` 要求该标记为真，并同时满足完赛、常规比分打平和有效点球比分不相等；标记为真不能单独证明实际点球决胜，标记为假则直接否定。

字段映射固定为：

```python
home_tournament_team_id = raw["home_tourn_team_id"]
home_team_id = raw["home_tourn_team_info"]["team_id"]
away_tournament_team_id = raw["away_tourn_team_id"]
away_team_id = raw["away_tourn_team_info"]["team_id"]
```

跨赛事识别和公共查询参数使用 `team_id`；比赛事件、赛事报名球员等赛事内关联使用 `tournament_team_id`。

### 2.3 赛事与赛事球队

```python
@dataclass(frozen=True)
class TournamentTeam:
    tournament_team_id: int
    team_id: int
    name: str
    brief_name: str
    group_place: str | None
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    reported_rank: int | None


@dataclass(frozen=True)
class TournamentSnapshot:
    tournament_id: int
    name: str
    season: str
    begin_date: date
    end_date: date
    players_per_side: int
    season_ids: Mapping[str, int]
    teams: tuple[TournamentTeam, ...]
    games: tuple[GameSummary, ...]
```

API 返回的 `rank <= 0` 映射为 `reported_rank=None`。非零排名也只作为人工核对证据；运行时最终成绩以已提交的逐赛事静态排名文件为准。

### 2.4 查询结果与成绩规则

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
    penalty_goals_for: int | None
    penalty_goals_against: int | None
    score_text: str | None
    result: MatchResult


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


@dataclass(frozen=True)
class TeamTournamentOutcome:
    team_name: str
    tournament_id: int
    tournament_name: str
    season: str
    rank: str
```

复杂赛制不在运行时代码中推断。`TeamTournamentOutcome` 来自人工核对快照后提交的逐赛事排名文件。

## 3. 功能与公共接口

### 3.1 底层客户端

```python
class THUFootballClient:
    async def get_user_info(self) -> UserProbe: ...

    async def get_accessible_tournaments(self) -> list[TournamentRef]: ...

    async def get_current_games(
        self,
        *,
        history_bound: date,
        future_bound: date,
        game_type: Literal["public", "all"] = "public",
        field_id: int | None = None,
    ) -> list[GameSummary]: ...

    async def get_tournament_info(
        self,
        tournament_id: int,
    ) -> TournamentSnapshot: ...

    async def get_game_info(self, game_id: int) -> GameDetail: ...
```

`get_accessible_tournaments` 封装 `GetMyTournaments`，名称明确表达“当前凭据可取得的赛事”，不表达所有权或参赛关系。`game_type` 发送请求时映射为 HTTP 参数 `type`。

### 3.2 通用比赛查询

```python
async def query_games(self, query: GameQuery) -> list[GameSummary]: ...
```

| 条件 | 获取方式 |
| --- | --- |
| 仅赛事 | 并发调用对应赛事的 `GetTournInfo` |
| 仅日期 | 调用 `GetCurrentGames` 后按北京时间精确过滤 |
| 赛事和日期 | 调用对应赛事的 `GetTournInfo` 后按北京时间过滤 |
| 赛事和日期均省略 | 读取 `GetMyTournaments`，并发查询全部可访问赛事 |

`tournament_ids` 和 `match_date` 均省略时查询当前凭据可访问的全部赛事。`team_ids` 最多包含两个不同的全局球队 ID；`team_match="any"` 表示任一球队参赛，`all` 表示所有指定球队同时参赛。输出默认包含未完赛比赛并按本地开球时间升序。

### 3.3 球队最终成绩

```python
async def query_team_outcomes(
    self,
    team_id: int,
    tournament_ids: Sequence[int] | None = None,
) -> list[TeamTournamentOutcome]: ...
```

3.3 只支持 `notes/tourns.json` 中列出的 14 项近三年马杯男足、女足和五人制赛事。省略 `tournament_ids` 时按文件顺序查询全部支持赛事；显式列表不能为空，重复 ID 按首次出现顺序去重，不支持的赛事返回 `QueryValidationError`。

`team_id` 通过 `notes/teams.json` 解析为一个或多个规范球队名称。同一 ID 可以属于同院系不同项目球队；查询返回所选赛事中全部命中成绩，并由 `team_name` 区分。已知球队未参加所选赛事时返回空列表。该方法不访问 HTTP、不计算积分或淘汰赛结果，也不产生比赛数据 warning。

### 3.4 球队全部赛果

```python
async def query_team_matches(
    self,
    team_id: int,
    tournament_id: int | None = None,
    *,
    include_unfinished: bool = False,
) -> list[TeamGameResult]: ...
```

`tournament_id=None` 时先读取赛事目录，再查询全部可访问赛事；显式传入 ID 时只查询该赛事。默认只返回 `FINISHED` 比赛。目标球队无论主客场均转换为球队视角的进球、失球、点球比分、规范比分文本和胜平负；结果按 `(kickoff_local, game_id)` 倒序。`include_unfinished=True` 时保留有效未完赛比赛，其比分、点球比分和比分文本均为 `None`，结果为 `UNKNOWN`。

### 3.5 多赛事交锋

```python
async def query_team_to_team_matches(
    self,
    team_a_id: int,
    team_b_id: int,
    tournament_ids: Sequence[int] | None = None,
    *,
    include_unfinished: bool = False,
) -> HeadToHeadHistory: ...
```

两个球队 ID 必须不同，且均为全局 `team_id`。每个 ID 会通过 `notes/teams.json` 展开为其规范球队名称下登记的全部历史 ID；例如查询 `254` 对 `48` 时，`254` 的历史 ID `80` 也参与匹配。未登记的 ID 保持精确匹配；两边展开后存在重叠则返回 `QueryValidationError`，避免无法判定比赛归属。省略 `tournament_ids` 时查询全部可访问赛事；显式赛事列表不能为空，重复 ID 按首次出现顺序去重。正反主客场、点球大战和弃赛赛果都计入；默认仅返回 `FINISHED` 比赛，按 `(kickoff_local, game_id)` 倒序，并返回跨赛事和分赛事汇总。`by_tournament` 包含实际查询的每一项赛事，无交锋赛事使用全零汇总；完全没有交锋时返回空比赛集合和全零总汇总。

## 4. 功能细节澄清

### 4.1 日期与时区

业务日期固定使用 `Asia/Shanghai` 自然日：

```text
[当天 00:00:00, 次日 00:00:00)
```

API 的 `time` 按 UTC 解析：

```python
kickoff_utc = datetime.strptime(raw_time, FORMAT).replace(tzinfo=UTC)
kickoff_local = kickoff_utc.astimezone(ZoneInfo("Asia/Shanghai"))
```

`GetCurrentGames` 的日期边界已经确认左闭右开，但服务端按 UTC 日期还是北京时间日期筛选尚未确认。查询北京时间日期 `D` 时，先请求 `history_bound=D-1`、`future_bound=D+1`，再使用 `kickoff_local` 精确保留日期 `D` 的比赛，避免漏掉北京时间凌晨比赛。

### 4.2 状态、有效性与比分

状态映射顺序固定为：

1. `raw.status` 为假或 `raw.valid` 不为有效值：`UNKNOWN`，默认排除出统计。
2. `raw.end is True`：`FINISHED`。
3. `raw.start is True`：`STARTED`。
4. 尚未开始且本地开球时间在当前时间之后：`SCHEDULED`。
5. 其余情况：`UNKNOWN`。

判断是否完赛不得依赖 `game_time_metadata`、`minute` 或 `stoppage_minute`。只有 `FINISHED` 且能够按下列顺序归一化的比赛才进入球队赛果和交锋汇总：

1. 单方弃赛：五人制将未弃赛方判为 `5:0`，其他人数判为 `3:0`；返回的 `GameSummary` 副本覆盖比分和 `result_text`，保留弃赛标记，并清除点球字段。
2. 双方弃赛或完赛比分缺失：映射时直接返回带字段路径的 `SchemaError`。
3. 常规比分不同：无论是否启用点球决胜规则，都按常规比分判断胜负，使用普通 `主队比分:客队比分` 文本，并清除无实际意义的点球比分；规则开关本身保持不变。
4. 常规比分相同，但 `penalty_shootout` 为假，或者点球比分缺失、相等：判为平局并清除点球比分；服务端常用 `0:0` 表示启用了规则但没有实际进行点球大战。即使标记为假时出现不相等的点球比分，也以标记的明确否定为准。
5. 常规比分相同、`penalty_shootout` 为真，且双方点球比分均为合法非负整数并不相等：`decided_by_penalty_shootout` 为真，按点球判断胜负，主客视角文本规范为 `2(3):2(4)`；`TeamGameResult` 再将比分和点球字段转换为目标球队视角。
6. 点球比分字段为负数或错误类型：映射时返回 `SchemaError`；不会因为 `penalty_shootout=true` 而把缺失或相等的点球比分视为错误。

`include_unfinished=True` 时，有效未完赛比赛可进入返回列表，但不进入交锋汇总。`valid=false` 或 `status=false` 的记录继续按无效领域状态过滤；`valid=null`、`penalty_shootout=null`、缺失嵌套球队对象、负数计数和其他不符合当前响应契约的数据直接返回 `SchemaError`，不修复、不回填也不跳过。

### 4.3 球队身份与赛事范围

- 跨赛事球队身份只使用嵌套球队对象的 `team_id`。
- `tourn_team_id` 只在单项赛事内关联比赛、报名球员和事件。
- `GetMyTournaments` 只用于发现可访问赛事，不代表“我的赛事”。
- 本地黑名单 `BLACKLISTED_TOURNAMENT_IDS={6, 28}` 优先于 API 目录：自动全赛事查询静默排除这些赛事；显式查询其中任一 ID 时在发送赛事请求前抛出 `QueryValidationError`。`GetCurrentGames` 的结果也会过滤这些赛事，按比赛 ID 读取到黑名单赛事详情时不向调用方返回领域对象。
- `GetCurrentGames(type="all")` 是否扩大数据范围未得到当前账号验证，调用方不得把 `all` 理解为所有私有比赛。
- `season_ids` 可以帮助发现同系列其他赛季，但跨赛事查询仍必须显式形成赛事 ID 列表。
- 最终成绩身份表以官方院系为顶层、以男足/女足/五人制为项目；运行时展开为“院系+项目”的规范名称，同院系不同项目允许共享 API `team_id`。
- 院系全称和简称来自 `notes/院系信息汇总表.xlsx`；电子工程系是源表缺项的显式补充，简称为“电子”。新闻与传播学院和马克思主义学院按联合院系合并。
- `notes/identity_audit.json` 完整记录规范化后的历史名称/ID 合并及所有跨项目重号。未登记重号、跨院系重号、未知排名球队或损坏静态文件返回 `ConfigurationError`。

### 4.4 最终成绩

- `notes/tourns.json` 是 3.3 的唯一赛事范围，当前包含 14 项赛事。
- `notes/teams.json` 使用 `官方院系名 -> {男足, 女足, 五人制, 简称}`；没有参赛记录的项目保留空列表，运行时只为非空项目生成规范球队身份。同一项目的历史 ID 合并，不同项目仍是不同球队身份。
- `notes/ranks/<tournament_id>.json` 使用 `规范球队名 -> rank 字符串`，是运行时唯一最终成绩来源。
- 多个旧队名归入同一规范球队且同届存在多个成绩时只保留较高名次；新闻—马院在赛事 `101` 保留“小组第三”，赛事 `93` 的相同“44强”和赛事 `111` 的相同“48强”分别合并为一条。
- 排名由白名单 `TournamentSnapshot` 人工核对后维护。无三四名赛时半决赛负者记为“四强”；五人制首轮按实际赛事规模记为 `N强`；升降级附加队可记为“升级”“保级”或“降级”。
- API 中从未出场的占位记录（测试球队、AC 米兰、曼联）不进入规范球队或排名文件。
- 运行时代码不包含赛制规则、积分排序、同分比较、淘汰赛推断或 provisional 状态。

## 5. 实现约束与验收

### 5.1 HTTP、缓存和错误

- 使用可注入的 `httpx.AsyncClient`；凭据来自 `THUFOOTBALL_OPENID`、`THUFOOTBALL_SESSION_KEY`。
- `GetCurrentGames` 允许匿名公开查询；其他已采用的接口要求完整凭据。
- 参数通过 `httpx` 的 `params` 编码，不手工拼接 URL。
- 默认超时：连接 5 秒，读取和写入各 15 秒，连接池等待 5 秒。
- 连接失败、超时、`502`、`503`、`504` 的只读请求最多重试一次；鉴权和 schema 错误不重试。
- 多赛事读取使用 `asyncio.Semaphore(4)`；一次公共调用内缓存赛事响应，并按比赛 ID 去重。
- 同一比赛 ID 的核心字段冲突时返回 `DataConflict`，不静默覆盖。

统一错误类型：

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

损坏比赛直接返回带字段路径的 `SchemaError`，错误不携带原始响应。

最终成绩静态文件损坏或审计不一致时返回 `ConfigurationError`，不回退到运行时猜测。

### 5.2 数据安全

- 使用字段白名单映射响应，领域对象不得包含 `session_key`、OpenID、登录令牌、手机号、证件标识或无关人员资料。
- 不缓存或持久化完整 `GetUserInfo`、`GetTournInfo`、`GetGameInfo` 原始响应。
- 人工排名阶段只把白名单 `TournamentSnapshot` 保存到被 Git 忽略的 `tmp/thufootball/snapshots/`，不保存原始响应。
- 日志不记录完整查询串、Cookie、凭据、人员对象或完整敏感响应。
- `GetGameInfo` 只为已入选且缺少所需字段的比赛调用，不批量读取无关评论、人员和事件。

### 5.3 实现顺序

1. 建立枚举、领域模型和脱敏白名单映射。
2. 使用已验证响应建立固定样本，完成 UTC 时间、两级球队 ID、状态和比分映射测试。
3. 实现只读客户端、鉴权探针、超时和错误映射。
4. 实现 `query_games`、球队赛果和交锋查询。
5. 人工核对近三年 14 项赛事快照，维护球队身份审计和逐赛事静态排名，并实现只读加载查询。
6. 最后执行真实只读探针，不调用任何写 API。

### 5.4 最小验收

- 赛事、日期及组合查询得到正确交集；北京时间零点边界不漏比赛。
- 全局球队 ID 在多个赛事中能够关联同一球队，赛事内 ID 不被误用。
- `SCHEDULED`、`STARTED`、`FINISHED`、`UNKNOWN` 四种状态映射正确。
- 无效比赛、未结束比赛和异常比分不进入积分统计。
- 主客场球队视角、点球大战和交锋汇总正确。
- 五人制单方弃赛按 `5:0`、其他人数按 `3:0` 归一化；损坏记录按一次调用一个 warning 聚合报告。
- 3.3 的 14 份排名文件完整覆盖真实参赛球队；历史别名和跨项目重号得到稳定、可审计的查询结果。
- 冠军、亚军、季军、第四名、四强、八强、各轮淘汰、小组名次、升级、保级和降级均有真实样例验收。
- 多赛事请求部分失败时返回包含失败赛事 ID 的 `BatchQueryError`。
- 测试日志和领域结果不包含凭据或人员敏感信息。

### 5.5 真实只读冒烟

直接运行测试文件时，默认只执行一项真实只读冒烟；自动发现和 `--unit-tests` 不访问真实 API：

```powershell
# 省略赛事：查询全部可访问赛事中的球队比赛
python test\thufootball\test_client_queries.py --team-id 48

# 单赛事球队比赛
python test\thufootball\test_client_queries.py --tournament-id 122 --team-id 48

# 静态最终成绩；省略赛事时查询全部 14 项支持赛事，不访问 API
python test\thufootball\test_client_queries.py --outcomes --team-id 48

# 多项指定赛事的静态最终成绩
python test\thufootball\test_client_queries.py `
  --outcomes `
  --team-id 48 `
  --tournament-id 128 `
  --tournament-id 122

# 省略赛事：查询全部可访问赛事中的两队交锋
python test\thufootball\test_client_queries.py --team-id 48 --opponent-id 163

# 多赛事两队交锋；--tournament-id 可重复
python test\thufootball\test_client_queries.py `
  --tournament-id 122 `
  --tournament-id 123 `
  --team-id 48 `
  --opponent-id 163 `
  --include-unfinished
```

球队比赛查询模式不能与 `--match-date` 同时使用。省略 `--tournament-id` 且未指定日期时查询 `GetMyTournaments` 返回且不在本地黑名单中的全部赛事；显式日期仍走全局日期查询。单队比赛模式最多指定一个赛事，两队交锋模式可以重复传入多个赛事。

`--outcomes` 必须与 `--team-id` 一起使用，可以重复指定赛事，并且不能与 `--opponent-id`、`--match-date`、`--include-unfinished` 或 `--game-id` 同时使用。该模式完全读取本地静态文件；摘要输出命中的规范球队、赛事和排名，`--full-output` 输出完整 `TeamTournamentOutcome` 对象。
