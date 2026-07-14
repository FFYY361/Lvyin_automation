# THUFootball 比赛 API 使用说明

本文档说明如何获取 API 凭据，并按“近期比赛 -> 本场详情 -> 同赛事历史比赛”的流程获取撰写单场比赛预览所需的数据。

## 1. 获取 API 凭据

### 方式：通过 TAFA 网站登录态读取

1. 打开 TAFA 网站：

   ```text
   https://www.tafa.org.cn/member/
   ```

2. 使用自己的账号正常登录。

3. 登录后访问裁判数据库页面：

   ```text
   https://www.tafa.org.cn/member/ref_db_new.php
   ```

4. 页面加载完成后，打开浏览器开发者工具：

   - Windows/Linux：按 `F12`
   - macOS：通常为 `Option + Command + I`
   - 也可以右键页面，选择“检查”

5. 切换到 `Console` / “控制台”，分别输入：

   ```js
   USER_OPENID
   ```

   ```js
   USER_SESSION_KEY
   ```

   也可以一次性输出：

   ```js
   console.log({
     openid: USER_OPENID,
     session_key: USER_SESSION_KEY
   })
   ```

### 安全提醒

`USER_OPENID` 和 `USER_SESSION_KEY` 等同于当前用户的 API 登录凭据。不要发送给他人，也不要提交到代码仓库。

`session_key` 可能包含 `+`、`/`、`=` 等特殊字符。命令行调用接口时，建议使用 `--data-urlencode` 或等价方式编码参数，避免手动拼接 URL 导致参数解析错误。

建议在本机 shell 中设置环境变量，而不是把真实凭据写进脚本或文档：

```bash
export THUFOOTBALL_OPENID='你的 USER_OPENID'
export THUFOOTBALL_SESSION_KEY='你的 USER_SESSION_KEY'
```

可先用 `GetUserInfo` 验证登录态是否可用：

```bash
curl -sS --get 'https://api.thufootball.tech/GetUserInfo' \
  --data-urlencode "openid=${THUFOOTBALL_OPENID}" \
  --data-urlencode "session_key=${THUFOOTBALL_SESSION_KEY}"
```

成功时会返回类似：

```json
{
  "success": true,
  "info": "Successfully get!",
  "user_registered": true
}
```

如果返回 `Session key mismatch.`，说明 `session_key` 已失效或不是当前 `openid` 对应的登录态，需要重新从浏览器控制台读取。

## 2. API 调用流程

撰写单场比赛预览通常需要三类数据：

1. 近期比赛列表，用于选择目标比赛并获取 `game_id`
2. 本场比赛详情
3. 两队在同一赛事中的过往比赛信息

### 第一步：获取近期比赛列表

用于获取最近一段时间内的比赛，并从返回结果中选择目标比赛的 `game_id`。

```text
GET https://api.thufootball.tech/GetCurrentGames
```

#### 参数

| 参数 | 是否必需 | 说明 |
| --- | --- | --- |
| `openid` | 可选 | 有登录态时传入 |
| `session_key` | 可选 | 有登录态时传入 |
| `history_bound` | 可选 | 查询起始日期，格式 `YYYY-MM-DD`；默认当前时间前 2 周 |
| `future_bound` | 可选 | 查询结束日期，格式 `YYYY-MM-DD`；默认当前时间后 2 周 |
| `field_id` | 可选 | 按场地过滤 |
| `type` | 可选 | `public` 或 `all`，默认 `public` |

#### curl 示例

当前日期附近没有比赛，因此示例把 `history_bound` 设到 `2026-06-15`，以便返回可用比赛。

```bash
curl -sS --get 'https://api.thufootball.tech/GetCurrentGames' \
  --data-urlencode "openid=${THUFOOTBALL_OPENID}" \
  --data-urlencode "session_key=${THUFOOTBALL_SESSION_KEY}" \
  --data-urlencode 'history_bound=2026-06-15' \
  --data-urlencode 'future_bound=2026-07-28'
```

实测响应摘要：

```json
{
  "success": true,
  "info": "Successfully get current public games!",
  "current_games": [
    {
      "id": 4367,
      "time": "2026-06-15 08:45:00",
      "tourn_id": 136,
      "home_tourn_team_id": 1913,
      "away_tourn_team_id": 1916,
      "result": "0:1",
      "home_tourn_team_info": { "brief_name": "A队" },
      "away_tourn_team_info": { "brief_name": "D队" },
      "field_info": { "brief_name": "红场" }
    },
    {
      "id": 4368,
      "time": "2026-06-16 11:00:00",
      "tourn_id": 136,
      "home_tourn_team_info": { "brief_name": "B队" },
      "away_tourn_team_info": { "brief_name": "C队" },
      "result": "0:2"
    }
  ]
}
```

后续示例使用 `game_id=4367`。

### 第二步：获取本场比赛信息

选择目标比赛后，用 `game_id` 获取本场比赛的完整信息。

```text
GET https://api.thufootball.tech/GetGameInfo
```

#### 参数

| 参数 | 是否必需 | 说明 |
| --- | --- | --- |
| `openid` | 必需 | API 凭据 |
| `session_key` | 必需 | API 凭据 |
| `game_id` | 必需 | 来自第一步 `current_games[].id` |

#### curl 示例

```bash
curl -sS --get 'https://api.thufootball.tech/GetGameInfo' \
  --data-urlencode "openid=${THUFOOTBALL_OPENID}" \
  --data-urlencode "session_key=${THUFOOTBALL_SESSION_KEY}" \
  --data-urlencode 'game_id=4367'
```

实测响应摘要：

```json
{
  "success": true,
  "info": "Successfully get game info 4367!",
  "game_info": {
    "id": 4367,
    "tourn_id": 136,
    "time": "2026-06-15 08:45:00",
    "home_tourn_team_id": 1913,
    "away_tourn_team_id": 1916,
    "result": "0:1",
    "stage": "小组赛",
    "round": 3,
    "home_goal": 0,
    "away_goal": 1,
    "home_tourn_team_info": {
      "brief_name": "A队",
      "win": 1,
      "draw": 1,
      "lose": 1,
      "goal": 9,
      "concede": 8,
      "point": 4
    },
    "away_tourn_team_info": {
      "brief_name": "D队",
      "win": 1,
      "draw": 0,
      "lose": 2,
      "goal": 4,
      "concede": 13,
      "point": 3
    },
    "field_info": { "brief_name": "红场" }
  },
  "tourn_info": {
    "id": 136,
    "brief_name": "材子杯",
    "season": "2025-2026",
    "rule": "五人制足球"
  },
  "home_tourn_team_players": "... 12 items",
  "away_tourn_team_players": "... 12 items",
  "events": "... 16 items",
  "durations": "... 2 items"
}
```

第三步需要重点使用：

```text
game_info.tourn_id
game_info.home_tourn_team_id
game_info.away_tourn_team_id
game_info.time
```

### 第三步：获取同赛事全部比赛

本项目没有看到专门按两队查询历史比赛的接口。可通过赛事详情接口获取同一赛事下的所有比赛，再在客户端筛选两队之前的比赛。

```text
GET https://api.thufootball.tech/GetTournInfo
```

#### 参数

| 参数 | 是否必需 | 说明 |
| --- | --- | --- |
| `openid` | 必需 | API 凭据 |
| `session_key` | 必需 | API 凭据 |
| `tourn_id` | 必需 | 来自第二步 `game_info.tourn_id` |

#### curl 示例

```bash
curl -sS --get 'https://api.thufootball.tech/GetTournInfo' \
  --data-urlencode "openid=${THUFOOTBALL_OPENID}" \
  --data-urlencode "session_key=${THUFOOTBALL_SESSION_KEY}" \
  --data-urlencode 'tourn_id=136'
```

实测响应摘要：

```json
{
  "success": true,
  "info": "Successfully get tourn info for 2025-2026材子杯!",
  "tourn_info": {
    "id": 136,
    "brief_name": "材子杯",
    "season": "2025-2026",
    "rule": "五人制足球"
  },
  "games": [
    { "id": 4357, "time": "2026-06-02 03:30:00", "home": "A队", "away": "B队", "result": "5:5" },
    { "id": 4358, "time": "2026-06-02 04:20:00", "home": "C队", "away": "D队", "result": "9:2" },
    { "id": 4365, "time": "2026-06-09 04:20:00", "home": "B队", "away": "D队", "result": "4:1" },
    { "id": 4366, "time": "2026-06-09 13:00:00", "home": "A队", "away": "C队", "result": "4:2" },
    { "id": 4367, "time": "2026-06-15 08:45:00", "home": "A队", "away": "D队", "result": "0:1" },
    { "id": 4368, "time": "2026-06-16 11:00:00", "home": "B队", "away": "C队", "result": "0:2" }
  ]
}
```

实际返回的 `games[]` 中仍然包含完整字段，例如 `home_tourn_team_id`、`away_tourn_team_id`、`home_tourn_team_info`、`away_tourn_team_info`、`field_info`、`stage`、`round` 等。上面的摘要只保留了预览常用字段。

## 3. 筛选历史比赛

### 筛选两队各自此前的比赛

适用于撰写两队近期状态、上一场表现、进失球趋势等内容。

```js
const homeId = gameInfo.game_info.home_tourn_team_id
const awayId = gameInfo.game_info.away_tourn_team_id
const currentGameId = gameInfo.game_info.id
const currentTime = gameInfo.game_info.time

const previousGames = tournInfo.games.filter(g => {
  const teams = [g.home_tourn_team_id, g.away_tourn_team_id]
  return (
    g.id !== currentGameId &&
    g.time < currentTime &&
    (teams.includes(homeId) || teams.includes(awayId))
  )
})
```

对示例比赛 `4367`，筛选结果为：

```json
[
  { "id": 4357, "time": "2026-06-02 03:30:00", "home": "A队", "away": "B队", "result": "5:5" },
  { "id": 4358, "time": "2026-06-02 04:20:00", "home": "C队", "away": "D队", "result": "9:2" },
  { "id": 4365, "time": "2026-06-09 04:20:00", "home": "B队", "away": "D队", "result": "4:1" },
  { "id": 4366, "time": "2026-06-09 13:00:00", "home": "A队", "away": "C队", "result": "4:2" }
]
```

### 只筛选双方历史交手

适用于撰写两队交锋记录。

```js
const headToHead = tournInfo.games.filter(g => {
  const teams = [g.home_tourn_team_id, g.away_tourn_team_id]
  return (
    g.id !== currentGameId &&
    g.time < currentTime &&
    teams.includes(homeId) &&
    teams.includes(awayId)
  )
})
```

对示例比赛 `4367`，同赛事内此前没有 A队 vs D队 的直接交手，因此 `headToHead` 是空数组。

### 获取历史比赛详情

`GetTournInfo` 返回的 `games` 更适合做比赛摘要。如果需要历史比赛的进球、红黄牌、评论、裁判等详细信息，可以对筛选出的历史比赛逐场调用：

```text
GET https://api.thufootball.tech/GetGameInfo
```

参数：

| 参数 | 是否必需 | 说明 |
| --- | --- | --- |
| `openid` | 必需 | API 凭据 |
| `session_key` | 必需 | API 凭据 |
| `game_id` | 必需 | 来自 `previousGames[].id` 或 `headToHead[].id` |

## 4. 可直接运行的 Node.js 示例

下面脚本会：

1. 获取近期比赛
2. 选择第一场比赛
3. 获取本场详情
4. 获取同赛事全部比赛
5. 输出两队此前比赛和双方历史交手

```js
const BASE_URL = 'https://api.thufootball.tech'

const credential = {
  openid: process.env.THUFOOTBALL_OPENID,
  session_key: process.env.THUFOOTBALL_SESSION_KEY
}

if (!credential.openid || !credential.session_key) {
  throw new Error('Please set THUFOOTBALL_OPENID and THUFOOTBALL_SESSION_KEY')
}

async function getJson(path, params) {
  const url = new URL(path, BASE_URL)
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value)
  }

  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`${path} HTTP ${res.status}`)
  }

  const data = await res.json()
  if (!data.success) {
    throw new Error(`${path} failed: ${data.info}`)
  }
  return data
}

function briefGame(g) {
  return {
    id: g.id,
    time: g.time,
    home: g.home_tourn_team_info?.brief_name,
    away: g.away_tourn_team_info?.brief_name,
    result: g.result
  }
}

async function main() {
  const current = await getJson('/GetCurrentGames', {
    ...credential,
    history_bound: '2026-06-15',
    future_bound: '2026-07-28'
  })

  const selected = current.current_games[0]
  if (!selected) {
    throw new Error('No games found. Move history_bound earlier.')
  }

  const gameInfo = await getJson('/GetGameInfo', {
    ...credential,
    game_id: selected.id
  })

  const tournInfo = await getJson('/GetTournInfo', {
    ...credential,
    tourn_id: gameInfo.game_info.tourn_id
  })

  const homeId = gameInfo.game_info.home_tourn_team_id
  const awayId = gameInfo.game_info.away_tourn_team_id
  const currentGameId = gameInfo.game_info.id
  const currentTime = gameInfo.game_info.time

  const previousGames = tournInfo.games.filter(g => {
    const teams = [g.home_tourn_team_id, g.away_tourn_team_id]
    return (
      g.id !== currentGameId &&
      g.time < currentTime &&
      (teams.includes(homeId) || teams.includes(awayId))
    )
  })

  const headToHead = tournInfo.games.filter(g => {
    const teams = [g.home_tourn_team_id, g.away_tourn_team_id]
    return (
      g.id !== currentGameId &&
      g.time < currentTime &&
      teams.includes(homeId) &&
      teams.includes(awayId)
    )
  })

  console.log({
    selected: briefGame(gameInfo.game_info),
    previousGames: previousGames.map(briefGame),
    headToHead: headToHead.map(briefGame)
  })
}

main().catch(err => {
  console.error(err.message)
  process.exit(1)
})
```
