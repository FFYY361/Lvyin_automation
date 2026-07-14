# THUFootball HTTP API 清单

## 1. 文档说明

本文档记录待验证的 THUFootball HTTP API。现阶段每个 API 只记录名称和状态，后续通过测试验证能力后，再在对应小节补充详细信息。

## 2. APIs

### `GetUserInfo`

- URL：`https://api.thufootball.tech/GetUserInfo`
- 签名：`GET GetUserInfo(openid: str, session_key: str) -> JSON`
- 状态：已验证（2026-07-14）

#### 输入参数

| 参数 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `openid` | `str` | 是 | 当前 TAFA 登录态的 `USER_OPENID` |
| `session_key` | `str` | 是 | 当前 TAFA 登录态的 `USER_SESSION_KEY` |

#### 输出参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `success` | `bool` | 请求是否成功 |
| `info` | `str` | 接口执行结果说明；实测成功值为 `Successfully get!` |
| `user_registered` | `bool` | 当前微信用户是否已注册 THUFootball 账号 |
| `user_info` | `object` | 当前用户的账号资料，字段见下表 |
| `player_info` | `object` | 当前用户关联的球员资料，字段见下表 |
| `official_info` | `object` | 当前用户关联的官员资料，字段见下表 |
| `referee_info` | `object` | 当前用户关联的裁判资料，字段见下表 |

`user_info`：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 用户 ID |
| `association_id` | `int` | 所属协会 ID |
| `wx_openid` | `str` | 微信 OpenID |
| `session_key` | `str` | 当前登录会话密钥 |
| `name` | `str` | 用户姓名 |
| `gender` | `str` | 性别 |
| `birth` | `str` | 出生日期，格式为 `YYYY-MM-DD` |
| `head` | `str` | 用户头像 URL |
| `mobile` | `str` | 手机号 |
| `region` | `str` | 所在地区 |
| `region_domestic` | `bool` | 所在地区是否为国内地区 |
| `intro` | `str \| null` | 个人简介 |
| `register_time` | `str` | 注册时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `login_time` | `str` | 最近登录时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `player_id` | `int \| null` | 关联的球员 ID |
| `official_id` | `int \| null` | 关联的官员 ID |
| `referee_id` | `int \| null` | 关联的裁判 ID |
| `tafa_binded` | `bool` | 是否已绑定 TAFA 账号 |
| `tafa_login_token` | `str \| null` | TAFA 登录令牌 |
| `web_login_code` | `str \| null` | Web 登录验证码；实测为 `null` |
| `web_login_status` | `str \| int \| null` | Web 登录状态；实测为 `null`，非空值类型和枚举尚未验证 |
| `association_name` | `str` | 所属协会名称 |
| `association_city` | `str` | 所属协会城市 |
| `system_authority` | `int` | 系统权限等级；实测为 `0`，枚举含义尚未验证 |
| `ongoing_apps` | `array` | 当前进行中的申请；实测为空数组，元素结构尚未验证 |

`player_info`：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 球员 ID |
| `association_id` | `int` | 所属协会 ID |
| `user_id` | `int` | 关联的用户 ID |
| `name` | `str` | 球员姓名 |
| `id_number` | `str` | 球员记录中的证件号或账号标识；本次实测值为邮箱 |
| `head` | `str` | 球员头像 URL |
| `birth` | `str` | 出生日期，格式为 `YYYY-MM-DD` |
| `gender` | `str` | 性别 |
| `join_time` | `str` | 加入时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `position` | `str \| null` | 场上位置 |
| `note` | `str \| null` | 备注 |
| `intro` | `str \| null` | 球员简介 |
| `old_member_id` | `int \| null` | 旧系统成员 ID |

`official_info`：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 官员 ID |
| `association_id` | `int` | 所属协会 ID |
| `user_id` | `int` | 关联的用户 ID |
| `name` | `str` | 官员姓名 |
| `id_number` | `str` | 官员记录中的证件号或账号标识；本次实测值为邮箱 |
| `head` | `str` | 官员头像 URL |
| `birth` | `str` | 出生日期，格式为 `YYYY-MM-DD` |
| `gender` | `str` | 性别 |
| `join_time` | `str` | 加入时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `intro` | `str \| null` | 官员简介 |

`referee_info`：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 裁判 ID |
| `association_id` | `int` | 所属协会 ID |
| `user_id` | `int` | 关联的用户 ID |
| `name` | `str` | 裁判姓名 |
| `avoid_team_ids` | `str` | 需要回避的球队 ID，多个 ID 以逗号分隔 |
| `uniform_colors` | `str` | 可用裁判服颜色，多个颜色以逗号分隔 |
| `level` | `str \| int \| null` | 裁判等级；实测为 `null`，非空值类型和枚举尚未验证 |
| `join_time` | `str` | 加入时间，格式为 `YYYY-MM-DD HH:MM:SS` |
| `op_user_id` | `int` | 操作用户 ID |
| `old_member_id` | `int \| null` | 旧系统成员 ID |

#### 示例输出

```json
{
  "success": true,
  "info": "Successfully get!",
  "user_registered": true,
  "user_info": {
    "id": 905,
    "association_id": 1,
    "wx_openid": "<masked-openid>",
    "session_key": "<masked-session-key>",
    "name": "薛飞阳",
    "gender": "男",
    "birth": "2006-07-24",
    "head": "https://api.thufootball.tech/img_avatar/avatar_<masked-openid>.jpg",
    "mobile": "18011599166",
    "region": "四川省 成都市 金牛区",
    "region_domestic": true,
    "intro": null,
    "register_time": "2023-11-11 10:17:45",
    "login_time": "2026-06-30 09:17:17",
    "player_id": 30603,
    "official_id": 399,
    "referee_id": 7503,
    "tafa_binded": true,
    "tafa_login_token": "<masked-tafa-login-token>",
    "web_login_code": null,
    "web_login_status": null,
    "association_name": "清华绿茵",
    "association_city": "北京",
    "system_authority": 0,
    "ongoing_apps": []
  },
  "player_info": {
    "id": 30603,
    "association_id": 1,
    "user_id": 905,
    "name": "薛飞阳",
    "id_number": "xfy24@mails.tsinghua.edu.cn",
    "head": "https://api.thufootball.tech/img_static/user_default_head.png",
    "birth": "2006-07-24",
    "gender": "男",
    "join_time": "2023-09-27 18:47:14",
    "position": null,
    "note": "",
    "intro": "",
    "old_member_id": 6024
  },
  "official_info": {
    "id": 399,
    "association_id": 1,
    "user_id": 905,
    "name": "薛飞阳",
    "id_number": "xfy24@mails.tsinghua.edu.cn",
    "head": "https://mmbiz.qpic.cn/mmbiz/example/0",
    "birth": "2006-07-24",
    "gender": "男",
    "join_time": "2024-03-18 07:57:38",
    "intro": null
  },
  "referee_info": {
    "id": 7503,
    "association_id": 1,
    "user_id": 905,
    "name": "薛飞阳",
    "avoid_team_ids": "60,181,1947,2015",
    "uniform_colors": "black,yellow",
    "level": null,
    "join_time": "2023-09-27 18:47:14",
    "op_user_id": 0,
    "old_member_id": 6024
  }
}
```

### `GetCurrentGames`

- 签名：`GetCurrentGames`
- 状态：待验证

### `GetTournInfo`

- 签名：`GetTournInfo`
- 状态：待验证

### `GetGameInfo`

- 签名：`GetGameInfo`
- 状态：待验证

### `GetMyTournaments`

- URL：`https://api.thufootball.tech/GetMyTournaments`
- 签名：`GET GetMyTournaments(openid: str, session_key: str) -> JSON`
- 状态：待验证

### `GetTournTypes`

- URL：`https://api.thufootball.tech/GetTournTypes`
- 签名：`GET GetTournTypes(openid: str, session_key: str) -> JSON`
- 状态：待验证

### `OnCreateTournament`

- URL：`https://api.thufootball.tech/OnCreateTournament`
- 签名：`GET OnCreateTournament(openid: str, session_key: str, data: str) -> JSON`
- 状态：暂不处理非只读API

### `OnEditTournInfo`

- URL：`https://api.thufootball.tech/OnEditTournInfo`
- 签名：`GET OnEditTournInfo(openid: str, session_key: str, tourn_id: int, data: str) -> JSON`
- 状态：暂不处理非只读API
