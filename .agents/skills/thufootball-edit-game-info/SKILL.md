---
name: thufootball-edit-game-info
description: 调用 THUFootball API 修改现有比赛信息，主要用于单场或批量调整比赛时间、场地。说明如何复用 thufootball service 获取 TAFA 令牌，以及修改接口的参数格式。
---

# 修改 THUFootball 比赛信息

仅依赖本仓库的 `src/thufootball` service 及其已有依赖。在项目的 Conda `lvyin` 环境中运行，按用户指定的比赛和字段修改。

## 获取令牌

复用 [credentials.py](../../../src/thufootball/credentials.py) 的 `fetch_tafa_credentials`，不要重写登录流程。它登录 TAFA 网站，再取得 `openid` 和 `session_key`。

以下为异步函数内的调用片段，账号密码从仓库 `.env` 的 `TAFA_USERNAME`、`TAFA_PASSWORD` 读取：

```python
import os
from thufootball.config import load_env_file
from thufootball.credentials import fetch_tafa_credentials

load_env_file()
openid, session_key = await fetch_tafa_credentials(
    os.environ["TAFA_USERNAME"], os.environ["TAFA_PASSWORD"]
)
```

如果已有 `THUFOOTBALL_OPENID`、`THUFOOTBALL_SESSION_KEY`，也可直接通过 `thufootball.config.load_credentials()` 获取。凭据失效时重新调用上述登录方法，不打印凭据或含凭据的完整 URL。

## 修改接口

**`GET https://api.thufootball.tech/OnEditGameInfo`**

| 查询参数 | 内容 |
| --- | --- |
| `openid` | 当前令牌中的 OpenID |
| `session_key` | 当前会话密钥 |
| `game_id` | 比赛 ID，不是赛事 ID 或场序 |
| `data` | 要修改的字段组成的 JSON **字符串** |

已验证字段：

- `time`：UTC 时间，格式 `YYYY-MM-DD HH:MM:SS`。北京时间需减 8 小时，注意跨日。
- `field_id`：场地整数 ID，不能传场地名称。

调用示例（接在上面的异步片段后）：

```python
import json
import httpx

# 北京时间 2026-11-21 08:00；改场地时换成 {"field_id": 场地ID}
changes = {"time": "2026-11-21 00:00:00"}
async with httpx.AsyncClient(timeout=30) as client:
    response = await client.get(
        "https://api.thufootball.tech/OnEditGameInfo",
        params={
            "openid": openid,
            "session_key": session_key,
            "game_id": game_id,
            "data": json.dumps(changes),
        },
    )
    response.raise_for_status()
    result = response.json()
```

`game_id` 替换成目标比赛的整数 ID。使用 `params` 传参，由 `httpx` 编码；不要传 JSON 请求体。成功响应包含 `success: true`、`info` 和更新后的 `game_info`，不能只根据 HTTP 200 判断成功。

## 查询比赛和场地

以下均为同一 API 域名下的 GET，携带 `openid`、`session_key`：

| 接口 | 额外参数 | 返回内容 |
| --- | --- | --- |
| `GetMyTournaments` | 无 | `tourns[]`：赛事 ID、名称、赛季 |
| `GetTournInfo` | `tourn_id` | `games[]`：比赛 ID、时间、对阵等 |
| `GetGameInfo` | `game_id` | `game_info`：指定比赛当前信息 |
| `GetAllFields` | `show_subfields=true` | `fields[]`：场地 ID、全称、简称 |

批量修改时遍历明确的 `game_id`，每次只提交一个字段，跳过用户指定的排除项；保留修改前的值，供需要时恢复。其他字段先确认接口支持的名称和值类型，不要把完整 `game_info` 回传。

这是有副作用的 GET。仓库 `THUFootballClient` 的通用读取方法会自动重试，写入时使用上面的直接请求方式，不自动重试。网站“赛程导入”调用的是 `OnNewGame`，会新增比赛，不用于修改已有比赛。
