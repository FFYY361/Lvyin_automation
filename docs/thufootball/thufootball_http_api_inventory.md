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

- URL：`https://api.thufootball.tech/GetCurrentGames`
- 签名：`GET GetCurrentGames(openid?: str, session_key?: str, history_bound?: date, future_bound?: date, field_id?: int, type?: str) -> JSON`
- 状态：已验证（2026-07-14）

#### 输入参数

| 参数 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `openid` | `str` | 否 | 当前 TAFA 登录态的 `USER_OPENID`；查询公开比赛时可省略；若传入则必须同时传入 `session_key` |
| `session_key` | `str` | 否 | 当前 TAFA 登录态的 `USER_SESSION_KEY`；查询公开比赛时可省略；若传入则必须同时传入 `openid` |
| `history_bound` | `date` | 否 | 查询起始日期，格式为 `YYYY-MM-DD`；默认约为当前日期前 2 周；该边界包含在查询范围内 |
| `future_bound` | `date` | 否 | 查询结束日期，格式为 `YYYY-MM-DD`；默认约为当前日期后 2 周；该边界不包含在查询范围内 |
| `field_id` | `int` | 否 | 只返回指定场地的比赛 |
| `type` | `str` | 否 | 查询范围，可传 `public` 或 `all`，默认为 `public`；当前账号实测传 `all` 仍返回 `current public games` |

日期范围实测采用左闭右开语义。例如 `history_bound=2026-04-19`、`future_bound=2026-04-20` 会返回 4 月 19 日的比赛；起止日期相同时返回空列表。

> **时间与日期筛选时区：** `time` 返回不带 `Z` 或 UTC 偏移量的字符串，已基本确认该字段表示 UTC，使用时应加上 UTC 时区后再转换为北京时间。由于现有样本转换前后均落在同一个自然日，且没有 UTC 16:00–24:00（北京时间次日 00:00–08:00）的边界比赛，暂时仍无法确认 `history_bound`、`future_bound` 是按 UTC 日期还是北京时间日期筛选。

Python 函数使用参数名 `game_type`，发送 HTTP 请求时会映射为接口参数 `type`。

#### 输出参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `success` | `bool` | 请求是否成功 |
| `info` | `str` | 接口返回的结果说明 |
| `current_games` | `array<object>` | 符合查询条件的比赛列表；没有比赛时为空数组 |

`current_games[]` 中每个比赛对象包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 比赛 ID，可用于调用 `GetGameInfo` |
| `association_id` | `int` | 所属协会 ID |
| `tourn_id` | `int` | 所属赛事 ID，可用于调用 `GetTournInfo` |
| `order_number` | `int` | 比赛在赛事中的序号 |
| `home_tourn_team_id` | `int` | 主队的赛事球队 ID |
| `away_tourn_team_id` | `int` | 客队的赛事球队 ID |
| `field_id` | `int` | 场地 ID |
| `time` | `str` | UTC 开赛时间，格式为 `YYYY-MM-DD HH:MM:SS`，但响应字符串本身不含时区标识；转换为北京时间时加 8 小时 |
| `start` | `bool` | 比赛是否已经开始 |
| `end` | `bool` | 比赛是否已经结束 |
| `build_user_id` | `int` | 创建比赛的用户 ID |
| `result` | `str` | 比分文本，例如 `0:1` |
| `need_show` | `int` | 是否需要比赛展示的整数标记，实测为 `0` 或 `1` |
| `need_referee` | `int` | 是否需要裁判的整数标记，实测为 `0` 或 `1` |
| `show_referee` | `bool` | 是否展示裁判信息 |
| `note` | `str` | 比赛备注 |
| `stage` | `str` | 赛事阶段，例如 `小组赛` |
| `group_name` | `str \| null` | 小组名称；未分组时可能为空字符串或 `null` |
| `round` | `int \| null` | 轮次；不适用时为 `null` |
| `home_goal` | `int` | 主队进球数 |
| `away_goal` | `int` | 客队进球数 |
| `penalty_shootout` | `int` | 是否启用“常规比分打平后点球决胜”规则的整数标记；不能单独用于判断是否实际进入点球大战 |
| `home_penalty` | `int \| null` | 主队点球大战进球数；不适用时可能为 `null` |
| `away_penalty` | `int \| null` | 客队点球大战进球数；不适用时可能为 `null` |
| `home_abandon` | `int \| null` | 主队是否弃赛的整数标记；无弃赛信息时为 `null` |
| `away_abandon` | `int \| null` | 客队是否弃赛的整数标记；无弃赛信息时为 `null` |
| `valid` | `int` | 比赛是否有效的整数标记，实测为 `0` 或 `1` |
| `status` | `bool` | 比赛记录状态 |
| `tourn_info` | `object` | 所属赛事信息 |
| `home_tourn_team_info` | `object` | 主队在该赛事中的信息及统计 |
| `away_tourn_team_info` | `object` | 客队在该赛事中的信息及统计 |
| `field_info` | `object` | 比赛场地信息 |
| `minute` | `int` | 当前比赛分钟数；已结束比赛实测也可能为 `0` |
| `stoppage_minute` | `int` | 当前补时分钟数 |

`tourn_info` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 赛事 ID |
| `association_id` | `int` | 所属协会 ID |
| `name` | `str` | 赛事完整名称 |
| `report_name` | `str` | 报道中使用的赛事名称 |
| `brief_name` | `str` | 赛事简称 |
| `acronym` | `str` | 赛事缩写 |
| `build_user_id` | `int` | 创建赛事的用户 ID |
| `season` | `str` | 赛季 |
| `logo` | `str` | 赛事徽标 URL |
| `begin` | `str` | 赛事开始日期，格式为 `YYYY-MM-DD` |
| `end` | `str` | 赛事结束日期，格式为 `YYYY-MM-DD` |
| `gender` | `str` | 参赛性别分类，例如 `MAN` |
| `players` | `int` | 比赛制式的上场人数 |
| `rule` | `str` | 竞赛规则名称 |
| `intro` | `str` | 赛事简介 |
| `minonfield` | `int` | 场上最少球员数 |
| `has_kitnum` | `bool` | 是否使用球衣号码 |
| `ordinary_time` | `int` | 常规比赛时长，单位为分钟 |
| `extra_time` | `int` | 加时赛时长，单位为分钟 |
| `penalty_condition` | `str` | 点球大战适用条件 |
| `penalty_round` | `int` | 点球大战初始轮数 |
| `status` | `bool` | 赛事记录状态 |
| `visible` | `bool` | 赛事是否公开可见 |

`home_tourn_team_info` 和 `away_tourn_team_info` 结构相同，均包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 赛事球队 ID |
| `tourn_id` | `int` | 赛事 ID |
| `team_id` | `int` | 球队 ID |
| `group_place` | `str` | 所在小组或分组位置 |
| `name` | `str` | 球队完整名称 |
| `report_name` | `str` | 报道中使用的球队名称 |
| `brief_name` | `str` | 球队简称 |
| `acronym` | `str` | 球队缩写 |
| `intro` | `str \| null` | 球队简介 |
| `logo` | `str` | 球队徽标 URL |
| `color_shirt` | `str \| null` | 球衣颜色 |
| `color_shirt_text` | `str \| null` | 球衣文字颜色 |
| `color_short` | `str \| null` | 球裤颜色 |
| `color_short_text` | `str \| null` | 球裤文字颜色 |
| `color_sock` | `str \| null` | 球袜颜色 |
| `color_sock_text` | `str \| null` | 球袜文字颜色 |
| `op_user_id` | `int` | 最后操作用户 ID |
| `win` | `int` | 胜场数 |
| `draw` | `int` | 平局数 |
| `lose` | `int` | 负场数 |
| `goal` | `int` | 进球数 |
| `assist` | `int` | 助攻数 |
| `concede` | `int` | 失球数 |
| `point` | `int` | 积分 |
| `penalty` | `int` | 点球进球数 |
| `penalty_miss` | `int` | 点球未进数 |
| `own_goal` | `int` | 乌龙球数 |
| `yellow_card` | `int` | 黄牌数 |
| `red_card` | `int` | 红牌数 |
| `rank` | `int` | 排名；实测值为 `0` |
| `status` | `bool` | 赛事球队记录状态 |

`field_info` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 场地 ID |
| `association_id` | `int` | 所属协会 ID |
| `name` | `str` | 场地完整名称 |
| `brief_name` | `str` | 场地简称 |
| `intro` | `str` | 场地简介 |
| `build_user_id` | `int` | 创建场地的用户 ID |
| `picture` | `str` | 场地图片 URL |
| `longitude` | `float \| null` | 经度 |
| `latitude` | `float \| null` | 纬度 |
| `parent_field_id` | `int` | 上级场地 ID；没有上级时为 `0` |
| `status` | `bool` | 场地记录状态 |

#### 示例输出

该区间真实返回 3 场比赛，比赛 ID 分别为 `4245`、`4260`、`4261`。下面保留真实响应结构并展示第一场比赛；其余比赛的字段结构相同。

```json
{
  "success": true,
  "info": "Successfully get current public games!",
  "current_games": [
    {
      "id": 4245,
      "association_id": 1,
      "tourn_id": 122,
      "order_number": 35,
      "home_tourn_team_id": 1735,
      "away_tourn_team_id": 1745,
      "field_id": 20,
      "time": "2026-04-19 05:00:00",
      "start": true,
      "end": true,
      "build_user_id": 149,
      "result": "1:0",
      "need_show": 1,
      "need_referee": 1,
      "show_referee": false,
      "note": "",
      "stage": "决赛",
      "group_name": null,
      "round": null,
      "home_goal": 1,
      "away_goal": 0,
      "penalty_shootout": 1,
      "home_penalty": 0,
      "away_penalty": 0,
      "home_abandon": null,
      "away_abandon": null,
      "valid": 1,
      "status": true,
      "tourn_info": {
        "id": 122,
        "association_id": 1,
        "name": "2025~2026马杯男足甲级",
        "report_name": "马甲",
        "brief_name": "马甲",
        "acronym": "MJ",
        "build_user_id": 149,
        "season": "2025~2026",
        "logo": "https://api.thufootball.tech/img_static/tourn_logo.png",
        "begin": "2025-09-24",
        "end": "2026-05-24",
        "gender": "MAN",
        "players": 11,
        "rule": "足球(十一人制)",
        "intro": "",
        "minonfield": 7,
        "has_kitnum": true,
        "ordinary_time": 80,
        "extra_time": 30,
        "penalty_condition": "KNOCKOUT",
        "penalty_round": 5,
        "status": true,
        "visible": true
      },
      "home_tourn_team_info": {
        "id": 1735,
        "tourn_id": 122,
        "team_id": 48,
        "group_place": "B1",
        "name": "车辆与运载学院男子足球队",
        "report_name": "车辆与运载学院",
        "brief_name": "汽车",
        "acronym": "QC",
        "intro": "                                                                        ",
        "logo": "https://api.thufootball.tech/img_static/team_logo.png",
        "color_shirt": "white",
        "color_shirt_text": "白色",
        "color_short": "blue",
        "color_short_text": "蓝色",
        "color_sock": null,
        "color_sock_text": null,
        "op_user_id": 149,
        "win": 6,
        "draw": 0,
        "lose": 0,
        "goal": 25,
        "assist": 0,
        "concede": 2,
        "point": 9,
        "penalty": 3,
        "penalty_miss": 0,
        "own_goal": 0,
        "yellow_card": 6,
        "red_card": 1,
        "rank": 1,
        "status": true
      },
      "away_tourn_team_info": {
        "id": 1745,
        "tourn_id": 122,
        "team_id": 163,
        "group_place": "A2",
        "name": "未央书院男子足球队",
        "report_name": "未央书院",
        "brief_name": "未央",
        "acronym": "WY",
        "intro": "                                                                ",
        "logo": "https://api.thufootball.tech/img_static/team_logo.png",
        "color_shirt": "blue",
        "color_shirt_text": "蓝",
        "color_short": "blue",
        "color_short_text": "蓝",
        "color_sock": null,
        "color_sock_text": null,
        "op_user_id": 149,
        "win": 5,
        "draw": 0,
        "lose": 1,
        "goal": 19,
        "assist": 0,
        "concede": 4,
        "point": 9,
        "penalty": 1,
        "penalty_miss": 0,
        "own_goal": 0,
        "yellow_card": 7,
        "red_card": 0,
        "rank": 0,
        "status": true
      },
      "field_info": {
        "id": 20,
        "association_id": 1,
        "name": "东大操场",
        "brief_name": "东操",
        "intro": "",
        "build_user_id": 0,
        "picture": "https://api.thufootball.tech/img_field_picture/field_picture_20_1686663069.jpg",
        "longitude": 116.33227651012751,
        "latitude": 40.00573364494456,
        "parent_field_id": 0,
        "status": true
      },
      "minute": 0,
      "stoppage_minute": 0
    }
  ]
}
```

### `GetTournInfo`

- URL：`https://api.thufootball.tech/GetTournInfo`
- 签名：`GET GetTournInfo(openid: str, session_key: str, tourn_id: int) -> JSON`
- 状态：已验证（2026-07-14）

#### 输入参数

| 参数 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `openid` | `str` | 是 | 当前 TAFA 登录态的 `USER_OPENID` |
| `session_key` | `str` | 是 | 当前 TAFA 登录态的 `USER_SESSION_KEY` |
| `tourn_id` | `int` | 是 | 赛事 ID，可从 `GetCurrentGames.current_games[].tourn_id` 获取 |

#### 输出参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `success` | `bool` | 请求是否成功 |
| `info` | `str` | 接口返回的结果说明 |
| `tourn_info` | `object` | 赛事基本信息和竞赛配置 |
| `authority` | `int` | 当前用户对该赛事的权限代码；赛事 `122`、`129`、`136` 实测均为 `1`，代码枚举含义待确认 |
| `season_ids` | `object<str, int>` | 同系列赛事的“赛季名称 → 赛事 ID”映射 |
| `registered_teams` | `array<object>` | 报名该赛事的球队及赛事内统计 |
| `registered_players` | `array<object>` | 报名该赛事的球员及赛事内统计 |
| `games` | `array<object>` | 该赛事的全部比赛 |
| `suspensions` | `array<object>` | 停赛记录；没有记录时为空数组 |
| `head_person` | `object` | 赛事负责人用户信息 |
| `officials` | `array<object>` | 赛事工作人员用户信息；赛事 `122` 实测为空数组，赛事 `136` 已确认元素结构 |

`tourn_info` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 赛事 ID |
| `association_id` | `int` | 所属协会 ID |
| `name` | `str` | 赛事完整名称 |
| `report_name` | `str` | 报道中使用的赛事名称 |
| `brief_name` | `str` | 赛事简称 |
| `acronym` | `str` | 赛事缩写 |
| `build_user_id` | `int` | 创建赛事的用户 ID |
| `season` | `str` | 赛季名称 |
| `logo` | `str` | 赛事徽标 URL |
| `begin` | `str` | 赛事开始日期，格式为 `YYYY-MM-DD` |
| `end` | `str` | 赛事结束日期，格式为 `YYYY-MM-DD` |
| `gender` | `str` | 参赛性别分类，例如 `MAN`、`BOTH` |
| `players` | `int` | 赛事配置的上场人数 |
| `rule` | `str` | 竞赛规则名称 |
| `intro` | `str` | 赛事简介 |
| `minonfield` | `int` | 场上最少球员数 |
| `has_kitnum` | `bool` | 是否使用球衣号码 |
| `ordinary_time` | `int` | 常规比赛时长，单位为分钟 |
| `extra_time` | `int` | 加时赛时长，单位为分钟 |
| `penalty_condition` | `str` | 点球大战适用条件，例如 `KNOCKOUT`、`ALWAYS` |
| `penalty_round` | `int` | 点球大战初始轮数 |
| `status` | `bool` | 赛事记录状态 |
| `visible` | `bool` | 赛事是否公开可见 |
| `build_user_name` | `str` | 赛事创建者名称 |

`season_ids` 的键是动态赛季名称，值是该赛季对应的赛事 ID。例如 `"2024~2025": 99`、`"2025~2026": 122`。

`registered_teams[]` 与 `GetCurrentGames` 的 `home_tourn_team_info`、`away_tourn_team_info` 结构相同，包含赛事球队 ID、原始球队 ID、名称、队徽、球衣颜色、胜平负、进失球、积分、牌数和排名等字段。

`registered_players[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 赛事报名球员记录 ID |
| `tourn_team_id` | `int` | 所属赛事球队 ID |
| `player_id` | `int` | 球员 ID |
| `position` | `str \| null` | 场上位置 |
| `class_name` | `str \| null` | 班级名称 |
| `department` | `str \| null` | 院系名称 |
| `mobile` | `str \| null` | 手机号 |
| `kitnum` | `int` | 球衣号码 |
| `op_user_id` | `int` | 最后操作用户 ID |
| `start` | `int` | 首发次数 |
| `appearance` | `int` | 出场次数 |
| `minute` | `int` | 累计出场分钟数 |
| `goal` | `int` | 进球数 |
| `assist` | `int` | 助攻数 |
| `yellow_card` | `int` | 黄牌数 |
| `red_card` | `int` | 红牌数 |
| `begin` | `str` | 报名时间，格式为 `YYYY-MM-DD HH:MM:SS`，字符串不含时区标识 |
| `end` | `str \| null` | 报名结束时间；仍有效时通常为 `null` |
| `valid` | `int` | 报名记录是否有效的整数标记 |
| `suspension` | `int` | 停赛相关计数或标记 |
| `penalty` | `int` | 点球进球数 |
| `penalty_miss` | `int` | 点球未进数 |
| `own_goal` | `int` | 乌龙球数 |
| `note` | `str \| null` | 备注 |
| `name` | `str` | 球员姓名 |
| `head` | `str` | 球员头像 URL |
| `team_name` | `str` | 所属赛事球队简称 |

`games[]` 的基础字段与 `GetCurrentGames.current_games[]` 相同，但不重复包含 `tourn_info`，并额外包含 `referees`。其中 `time` 同样是无时区后缀的 UTC 时间字符串。

`games[].referees[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 比赛裁判安排记录 ID |
| `association_id` | `int` | 所属协会 ID |
| `game_id` | `int` | 比赛 ID |
| `referee_id` | `int` | 裁判 ID |
| `position` | `str` | 裁判位置代码，例如 `R` |
| `uniform_color` | `str` | 裁判服颜色 |
| `fee` | `float` | 裁判费用 |
| `released` | `int` | 安排是否发布的整数标记 |
| `accepted` | `int` | 裁判是否接受安排的整数标记 |
| `rate` | `number \| null` | 评分；未评分时为 `null` |
| `status` | `bool` | 安排记录状态 |
| `op_user_id` | `int` | 最后操作用户 ID |
| `name` | `str` | 裁判姓名 |

`suspensions[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 停赛记录 ID |
| `tourn_id` | `int` | 赛事 ID |
| `tourn_team_player_id` | `int` | 赛事报名球员记录 ID |
| `is_additional` | `int` | 是否为追加停赛的整数标记 |
| `begin` | `str` | 停赛记录开始时间，字符串不含时区标识 |
| `reason` | `str` | 停赛原因 |
| `number` | `int` | 停赛场次数 |
| `op_user_id` | `int` | 最后操作用户 ID |
| `valid` | `bool` | 停赛记录是否有效 |
| `tourn_team_player_info` | `object` | 对应报名球员记录；字段是 `registered_players[]` 的赛事统计字段，不包含派生的姓名、头像和球队简称 |
| `player_info` | `object` | 球员基本信息，结构与 `GetUserInfo.player_info` 相同 |
| `tourn_team_info` | `object` | 球员所属赛事球队，结构与 `registered_teams[]` 相同 |

`head_person` 与 `officials[]` 使用相同的用户对象结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 用户 ID |
| `association_id` | `int` | 所属协会 ID |
| `wx_openid` | `str` | 微信 OpenID |
| `session_key` | `str` | 该用户的会话密钥 |
| `name` | `str` | 姓名 |
| `gender` | `str \| null` | 性别 |
| `birth` | `str \| null` | 出生日期 |
| `head` | `str` | 头像 URL |
| `mobile` | `str \| null` | 手机号 |
| `region` | `str \| null` | 地区 |
| `region_domestic` | `bool` | 是否为国内地区 |
| `intro` | `str \| null` | 简介 |
| `register_time` | `str` | 注册时间，字符串不含时区标识 |
| `login_time` | `str` | 最近登录时间，字符串不含时区标识 |
| `player_id` | `int \| null` | 关联球员 ID |
| `official_id` | `int \| null` | 关联工作人员 ID |
| `referee_id` | `int \| null` | 关联裁判 ID |
| `tafa_binded` | `bool` | 是否绑定 TAFA 账号 |
| `tafa_login_token` | `str \| null` | TAFA 登录令牌 |
| `web_login_code` | `str \| null` | Web 登录码 |
| `web_login_status` | `str \| null` | Web 登录状态 |

#### 示例输出

赛事 `122` 的真实响应包含 16 支球队、468 名报名球员、35 场比赛、5 条停赛记录和 0 名赛事工作人员。以下示例保留真实顶层结构，各大型数组仅展示第一项，嵌套对象只保留代表性字段。

```json
{
  "success": true,
  "info": "Successfully get tourn info for 2025~2026马杯男足甲级!",
  "tourn_info": {
    "id": 122,
    "association_id": 1,
    "name": "2025~2026马杯男足甲级",
    "report_name": "马甲",
    "brief_name": "马甲",
    "acronym": "MJ",
    "build_user_id": 149,
    "season": "2025~2026",
    "logo": "https://api.thufootball.tech/img_static/tourn_logo.png",
    "begin": "2025-09-24",
    "end": "2026-05-24",
    "gender": "MAN",
    "players": 11,
    "rule": "足球(十一人制)",
    "intro": "",
    "minonfield": 7,
    "has_kitnum": true,
    "ordinary_time": 80,
    "extra_time": 30,
    "penalty_condition": "KNOCKOUT",
    "penalty_round": 5,
    "status": true,
    "visible": true,
    "build_user_name": "小仓鼠"
  },
  "authority": 1,
  "season_ids": {
    "2014～2015": 1,
    "2015～2016": 17,
    "2016～2017": 26,
    "2017～2018": 32,
    "2018~2019": 40,
    "2019~2020": 48,
    "2020~2021": 50,
    "2021~2022": 57,
    "2022~2023": 72,
    "2023~2024": 89,
    "2024~2025": 99,
    "2025~2026": 122
  },
  "registered_teams": [
    {
      "id": 1733,
      "tourn_id": 122,
      "team_id": 254,
      "group_place": "C3",
      "name": "社会科学学院",
      "report_name": "社会科学学院- 心理与认知科学系",
      "brief_name": "社科-心理",
      "acronym": "SK",
      "intro": "                            ",
      "logo": "https://api.thufootball.tech/img_static/team_logo.png",
      "color_shirt": "#aN",
      "color_shirt_text": "白色",
      "color_short": null,
      "color_short_text": null,
      "color_sock": null,
      "color_sock_text": null,
      "op_user_id": 149,
      "win": 4,
      "draw": 0,
      "lose": 2,
      "goal": 13,
      "assist": 0,
      "concede": 9,
      "point": 9,
      "penalty": 0,
      "penalty_miss": 1,
      "own_goal": 0,
      "yellow_card": 7,
      "red_card": 0,
      "rank": 0,
      "status": true
    }
  ],
  "registered_players": [
    {
      "id": 48001,
      "tourn_team_id": 1733,
      "player_id": 32534,
      "position": null,
      "class_name": "社科硕51",
      "department": null,
      "mobile": "18801317226",
      "kitnum": 1,
      "op_user_id": 149,
      "start": 3,
      "appearance": 3,
      "minute": 270,
      "goal": 0,
      "assist": 0,
      "yellow_card": 1,
      "red_card": 0,
      "begin": "2025-10-05 09:23:15",
      "end": null,
      "valid": 1,
      "suspension": 0,
      "penalty": 0,
      "penalty_miss": 0,
      "own_goal": 0,
      "note": "",
      "name": "Severino Bonvini",
      "head": "https://api.thufootball.tech/img_static/user_default_head.png",
      "team_name": "社科-心理"
    }
  ],
  "games": [
    {
      "id": 3979,
      "association_id": 1,
      "tourn_id": 122,
      "order_number": 1,
      "home_tourn_team_id": 1739,
      "away_tourn_team_id": 1744,
      "field_id": 19,
      "time": "2025-11-01 04:00:00",
      "start": true,
      "end": true,
      "result": "0:9",
      "stage": "小组赛",
      "group_name": "B",
      "round": 1,
      "home_goal": 0,
      "away_goal": 9,
      "valid": 1,
      "status": true,
      "home_tourn_team_info": {
        "id": 1739,
        "team_id": 41,
        "brief_name": "能动"
      },
      "away_tourn_team_info": {
        "id": 1744,
        "team_id": 33,
        "brief_name": "计算机-GIX"
      },
      "field_info": {
        "id": 19,
        "name": "西区足球场",
        "brief_name": "西操"
      },
      "referees": [
        {
          "id": 10105,
          "association_id": 1,
          "game_id": 3979,
          "referee_id": 2777,
          "position": "R",
          "uniform_color": "yellow",
          "fee": 192.5,
          "released": 1,
          "accepted": 1,
          "rate": null,
          "status": true,
          "op_user_id": 149,
          "name": "刘哲贤"
        }
      ]
    }
  ],
  "suspensions": [
    {
      "id": 171,
      "tourn_id": 122,
      "tourn_team_player_id": 49653,
      "is_additional": 0,
      "begin": "2025-11-04 06:34:04",
      "reason": "",
      "number": 1,
      "op_user_id": 126,
      "valid": true,
      "tourn_team_player_info": {
        "id": 49653,
        "tourn_team_id": 1805,
        "player_id": 31193,
        "kitnum": 82
      },
      "player_info": {
        "id": 31193,
        "name": "Bashir",
        "gender": null
      },
      "tourn_team_info": {
        "id": 1805,
        "team_id": 53,
        "brief_name": "建筑"
      }
    }
  ],
  "head_person": {
    "id": 149,
    "association_id": 1,
    "name": "小仓鼠",
    "gender": null,
    "mobile": "",
    "player_id": 10951,
    "official_id": 268,
    "referee_id": 3263
  },
  "officials": []
}
```

### `GetGameInfo`

- URL：`https://api.thufootball.tech/GetGameInfo`
- 签名：`GET GetGameInfo(openid: str, session_key: str, game_id: int) -> JSON`
- 状态：已验证（2026-07-14）

#### 输入参数

| 参数 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `openid` | `str` | 是 | 当前 TAFA 登录态的 `USER_OPENID` |
| `session_key` | `str` | 是 | 当前 TAFA 登录态的 `USER_SESSION_KEY` |
| `game_id` | `int` | 是 | 比赛 ID，可从 `GetCurrentGames.current_games[].id` 或 `GetTournInfo.games[].id` 获取 |

#### 输出参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `success` | `bool` | 请求是否成功 |
| `info` | `str` | 接口返回的结果说明 |
| `game_info` | `object` | 比赛基本信息、比分、球队和场地 |
| `home_tourn_team_players` | `array<object>` | 主队报名球员及赛事累计统计 |
| `away_tourn_team_players` | `array<object>` | 客队报名球员及赛事累计统计 |
| `tourn_info` | `object` | 所属赛事信息和竞赛配置 |
| `events` | `array<object>` | 本场比赛事件，包括首发、换人、进球和牌等 |
| `comments` | `array<object>` | 比赛评论；没有评论时为空数组 |
| `referees` | `array<object>` | 本场裁判安排；未展示或未安排时为空数组 |
| `tourn_authority` | `int` | 当前用户的赛事权限代码，枚举含义待确认 |
| `game_authority` | `int` | 当前用户的比赛权限代码，枚举含义待确认 |
| `home_team_authority` | `int` | 当前用户的主队权限代码，枚举含义待确认 |
| `away_team_authority` | `int` | 当前用户的客队权限代码，枚举含义待确认 |
| `officials` | `array<object>` | 可用于本场执法或管理的工作人员用户列表 |
| `durations` | `array<object>` | 比赛时段，例如上下半场和加时赛 |
| `minute` | `int \| float` | 接口计算的比赛分钟数 |
| `game_time_metadata` | `object` | 当前计时状态和当前比赛时段 |
| `stoppage_minute` | `int` | 接口计算的补时相关原始值；实测可能异常增长，不宜直接用于最终赛况 |

`game_info` 的基础字段与 `GetCurrentGames.current_games[]` 相同，包含比赛 ID、赛事 ID、UTC 开赛时间、比赛状态、比分、阶段、轮次、主客队信息和场地信息，但不在内部重复返回 `tourn_info`。

`home_tourn_team_players[]` 与 `away_tourn_team_players[]` 结构相同，也与 `GetTournInfo.registered_players[]` 基本一致；本接口的球员对象不包含派生字段 `team_name`，其余包括报名记录 ID、球员 ID、姓名、号码、班级、手机、赛事出场和进球等累计统计。

`tourn_info` 与 `GetTournInfo.tourn_info` 结构相同，但本接口实测不包含派生字段 `build_user_name`。

`events[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 比赛事件 ID |
| `game_id` | `int` | 比赛 ID |
| `tourn_team_id` | `int` | 事件所属赛事球队 ID |
| `side` | `str` | 主客队方向，例如 `HOME`、`AWAY` |
| `type` | `str` | 事件类型；实测包括 `START`、`ON`、`OFF`、`GOAL`、`OWNGOAL`、`PENALTY`、`YELLOWCARD` |
| `during_penalty_shootout` | `int` | 是否发生在点球大战的整数标记 |
| `time` | `int` | 事件发生的比赛分钟 |
| `stoppage_time` | `int` | 事件发生的补时分钟 |
| `tourn_team_player_id` | `int` | 对应赛事报名球员记录 ID |
| `position_id` | `int \| null` | 对应位置 ID |
| `sequence` | `int` | 事件顺序值 |
| `valid` | `bool` | 事件是否有效 |
| `time_ordering` | `int` | 同一分钟内的排序值 |
| `name` | `str` | 球员姓名 |
| `kitnum` | `int` | 球衣号码 |
| `note` | `str` | 事件备注 |
| `player_id` | `int` | 球员 ID |

`comments[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 评论 ID |
| `game_id` | `int` | 比赛 ID |
| `user_id` | `int` | 评论用户 ID |
| `time` | `str` | 评论时间，字符串不含时区标识 |
| `content` | `str` | 评论内容 |
| `blocked` | `bool` | 评论是否被屏蔽 |
| `name` | `str` | 评论用户名称 |
| `head` | `str` | 评论用户头像 URL |

`referees[]` 与 `GetTournInfo.games[].referees[]` 结构相同，包含裁判安排 ID、裁判 ID、位置、服装颜色、费用、发布/接受状态、评分和姓名。比赛 `4245` 该数组为空；比赛 `3979` 实测返回 4 条记录。

`officials[]` 与 `GetTournInfo.officials[]` 的用户对象结构相同，响应中包含用户会话字段；文档示例不展示这些鉴权值。

`durations[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 比赛时段 ID |
| `game_id` | `int` | 比赛 ID |
| `name` | `str` | 时段名称，例如 `上半场`、`下半场` |
| `span` | `float` | 配置的时段长度，单位为分钟 |
| `is_extra` | `int` | 是否为加时赛时段的整数标记 |
| `sequence` | `int` | 时段顺序 |
| `status` | `int` | 时段记录状态代码 |
| `times` | `array<object>` | 该时段的计时操作记录 |

`durations[].times[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 计时操作记录 ID |
| `game_duration_id` | `int` | 所属比赛时段 ID |
| `type` | `str` | 操作类型；实测包括 `START`、`END`、`INTERRUPTED`、`RESTART`、`OUT_OF_PLAY`、`IN_PLAY` |
| `time` | `str` | 操作发生时间，字符串不含时区标识 |

`game_time_metadata` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | `str` | 当前计时状态，例如 `START`、`END`、`INTERRUPTED` |
| `current_duration` | `object` | 当前比赛时段，结构与 `durations[]` 相同 |

> **计时状态注意：** 比赛 `4245` 的 `game_info.end=true` 且已有最终比分，但 `game_time_metadata.status` 仍为 `START`，`stoppage_minute` 还会随请求时间增长。这说明计时元数据可能因现场操作未正常结束而残留。判断比赛是否完赛时应优先使用 `game_info.end`、`game_info.result` 和进球字段，不应单独依赖 `game_time_metadata` 或 `stoppage_minute`。

#### 示例输出

比赛 `4245` 的真实响应包含 30 名主队球员、31 名客队球员、33 个比赛事件、0 条评论、0 条裁判安排、18 名工作人员和 4 个比赛时段。以下示例保留全部顶层字段，大型数组只展示第一项，嵌套对象只保留代表性字段。

```json
{
  "success": true,
  "info": "Successfully get game info 4245!",
  "game_info": {
    "id": 4245,
    "association_id": 1,
    "tourn_id": 122,
    "order_number": 35,
    "home_tourn_team_id": 1735,
    "away_tourn_team_id": 1745,
    "field_id": 20,
    "time": "2026-04-19 05:00:00",
    "start": true,
    "end": true,
    "result": "1:0",
    "stage": "决赛",
    "group_name": null,
    "round": null,
    "home_goal": 1,
    "away_goal": 0,
    "penalty_shootout": 1,
    "home_penalty": 0,
    "away_penalty": 0,
    "valid": 1,
    "status": true,
    "home_tourn_team_info": {
      "id": 1735,
      "team_id": 48,
      "brief_name": "汽车"
    },
    "away_tourn_team_info": {
      "id": 1745,
      "team_id": 163,
      "brief_name": "未央"
    },
    "field_info": {
      "id": 20,
      "name": "东大操场",
      "brief_name": "东操"
    }
  },
  "home_tourn_team_players": [
    {
      "id": 48092,
      "tourn_team_id": 1735,
      "player_id": 31754,
      "name": "冀明泽",
      "kitnum": 1,
      "start": 5,
      "appearance": 5,
      "minute": 400,
      "goal": 0,
      "assist": 0
    }
  ],
  "away_tourn_team_players": [
    {
      "id": 48319,
      "tourn_team_id": 1745,
      "player_id": 30369,
      "name": "蒋天源",
      "kitnum": 3,
      "start": 0,
      "appearance": 0,
      "minute": 0,
      "goal": 0,
      "assist": 0
    }
  ],
  "tourn_info": {
    "id": 122,
    "name": "2025~2026马杯男足甲级",
    "brief_name": "马甲",
    "season": "2025~2026",
    "players": 11,
    "rule": "足球(十一人制)",
    "ordinary_time": 80,
    "extra_time": 30
  },
  "events": [
    {
      "id": 139680,
      "game_id": 4245,
      "tourn_team_id": 1745,
      "side": "AWAY",
      "type": "START",
      "during_penalty_shootout": 0,
      "time": 0,
      "stoppage_time": 0,
      "tourn_team_player_id": 48321,
      "position_id": null,
      "sequence": 87164,
      "valid": true,
      "time_ordering": 0,
      "name": "李为峰",
      "kitnum": 5,
      "note": "",
      "player_id": 30368
    }
  ],
  "comments": [],
  "referees": [],
  "tourn_authority": 1,
  "game_authority": 1,
  "home_team_authority": 0,
  "away_team_authority": 0,
  "officials": [
    {
      "id": 5155,
      "association_id": 1,
      "name": "哈哈",
      "gender": null,
      "player_id": null,
      "official_id": null,
      "referee_id": 7626
    }
  ],
  "durations": [
    {
      "id": 2319,
      "game_id": 4245,
      "name": "上半场",
      "span": 40.0,
      "is_extra": 0,
      "sequence": 1,
      "status": 1,
      "times": [
        {
          "id": 3469,
          "game_duration_id": 2319,
          "type": "START",
          "time": "2026-04-19 06:03:37"
        },
        {
          "id": 3470,
          "game_duration_id": 2319,
          "type": "END",
          "time": "2026-04-19 06:44:58"
        }
      ]
    }
  ],
  "minute": 80.0,
  "game_time_metadata": {
    "status": "START",
    "current_duration": {
      "id": 2316,
      "game_id": 4245,
      "name": "下半场",
      "span": 40.0,
      "is_extra": 0,
      "sequence": 2,
      "status": 1,
      "times": [
        {
          "id": 3471,
          "game_duration_id": 2316,
          "type": "START",
          "time": "2026-04-19 07:00:34"
        }
      ]
    }
  },
  "stoppage_minute": 123781
}
```

### `GetMyTournaments`

- URL：`https://api.thufootball.tech/GetMyTournaments`
- 签名：`GET GetMyTournaments(openid: str, session_key: str) -> JSON`
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
| `info` | `str` | 接口返回的结果说明 |
| `tourns` | `array<object>` | 当前登录用户可取得的赛事列表 |

`tourns[]` 包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `int` | 赛事 ID |
| `association_id` | `int` | 所属协会 ID |
| `name` | `str` | 赛事完整名称 |
| `report_name` | `str` | 报道中使用的赛事名称 |
| `brief_name` | `str` | 赛事简称 |
| `acronym` | `str` | 赛事缩写 |
| `build_user_id` | `int` | 创建赛事的用户 ID |
| `season` | `str` | 赛季名称 |
| `logo` | `str` | 赛事徽标 URL |
| `begin` | `str` | 赛事开始日期，格式为 `YYYY-MM-DD` |
| `end` | `str` | 赛事结束日期，格式为 `YYYY-MM-DD` |
| `gender` | `str` | 参赛性别分类，例如 `MAN`、`BOTH` |
| `players` | `int` | 赛事配置的上场人数 |
| `rule` | `str` | 竞赛规则名称 |
| `intro` | `str \| null` | 赛事简介 |
| `minonfield` | `int` | 场上最少球员数 |
| `has_kitnum` | `bool` | 是否使用球衣号码 |
| `ordinary_time` | `int` | 常规比赛时长，单位为分钟 |
| `extra_time` | `int` | 加时赛时长，单位为分钟 |
| `penalty_condition` | `str` | 点球大战适用条件 |
| `penalty_round` | `int` | 点球大战初始轮数 |
| `status` | `bool` | 赛事记录状态 |
| `visible` | `bool` | 赛事是否公开可见 |

> **接口范围注意：** 当前账号实测返回 121 个跨多个赛季、由不同用户创建的赛事，其中 `visible=true` 110 个、`visible=false` 11 个，且 `tourns[]` 不包含当前用户的参赛角色或管理权限。因此接口名称中的 “My” 不等于“我创建/管理/参加的赛事”，它更接近当前登录用户可取得的协会赛事列表。若要判断用户与赛事的具体关系，不能只依赖此接口。

#### 示例输出

当前账号真实返回 121 个赛事。以下示例只展示其中的赛事 `122`。

```json
{
  "success": true,
  "info": "Successfully get my tournaments!",
  "tourns": [
    {
      "id": 122,
      "association_id": 1,
      "name": "2025~2026马杯男足甲级",
      "report_name": "马甲",
      "brief_name": "马甲",
      "acronym": "MJ",
      "build_user_id": 149,
      "season": "2025~2026",
      "logo": "https://api.thufootball.tech/img_static/tourn_logo.png",
      "begin": "2025-09-24",
      "end": "2026-05-24",
      "gender": "MAN",
      "players": 11,
      "rule": "足球(十一人制)",
      "intro": "",
      "minonfield": 7,
      "has_kitnum": true,
      "ordinary_time": 80,
      "extra_time": 30,
      "penalty_condition": "KNOCKOUT",
      "penalty_round": 5,
      "status": true,
      "visible": true
    }
  ]
}
```

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
