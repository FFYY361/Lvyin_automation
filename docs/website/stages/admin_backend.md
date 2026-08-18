# Stage 2 管理员后端操作手册

本手册用于在本机 PostgreSQL 上启动 FastAPI，并通过 `/docs` 完成管理员前瞻
闭环。Stage 2 只创建微信公众号草稿，不发布或群发。

## 1. 配置与安装

在项目根目录复制 `.env.example` 为 `.env`，至少填写：

```dotenv
THUFOOTBALL_OPENID=
THUFOOTBALL_SESSION_KEY=
TAFA_USERNAME=足联网站登录邮箱
TAFA_PASSWORD=足联网站登录密码
AMAP_WEATHER_API_KEY=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
WEBSITE_DATABASE_URL=postgresql+psycopg://lvyin:密码@127.0.0.1:5432/lvyin
WEBSITE_DEFAULT_COVER_MEDIA_ID=
WEBSITE_COOKIE_SECRET=至少32个字符的随机值
```

`WEBSITE_DEFAULT_COVER_MEDIA_ID` 必须是公众号中已经存在的永久图片素材 media
ID。网站不会远程预检它；真正是否有效由微信草稿接口返回。网站缺失该配置会
拒绝启动。默认封面是有效封面，不属于完整性缺项，也不会再次调用封面上传。

本地开发保持 `WEBSITE_COOKIE_SECURE=false`；部署到 HTTPS 后必须改为 `true`。

```powershell
conda activate lvyin
python -m pip install -e ".[dev,website]"
docker compose up -d
python -m alembic upgrade head
```

当前只有 `v1_initial`。回滚验证只在确认数据库没有需保留的数据时执行：

```powershell
python -m alembic downgrade base
python -m alembic upgrade head
```

## 2. 创建管理员与启动 API

用户名区分大小写、不自动去除首尾字符，且不能包含任何空白字符。

```powershell
python -m backend init-admin --username admin --display-name 管理员
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/docs`。先调用 `POST /api/auth/login`；Swagger UI 会
保留签名 Cookie，后续管理员接口可直接调用。Cookie 默认有效 7 天。

## 3. `/docs` 闭环顺序

### 更新 THUFootball 凭据

后端启动时会使用 `TAFA_USERNAME`、`TAFA_PASSWORD` 登录足联网站，从
`ref_db_new.php` 读取并验证 `OPENID`、`SESSION_KEY`，随后更新当前进程环境。
THUFootball 拒绝凭据时会自动获取并重试两次。自动获取只更新当前进程，
不会写入 `.env`；只有下方手动设置接口会同时更新 `.env`。两次均失败时，请使用手动
方式更新；现有设置接口和前端设置页面继续保留。

`THUFOOTBALL_OPENID` 和 `THUFOOTBALL_SESSION_KEY` 会过期。网站运行期间不要只修改
`.env`：运行中的进程不会自动重新读取该文件。请在已经登录 TAFA 的
`https://www.tafa.org.cn/member/ref_db_new.php` 页面打开浏览器开发者工具，在控制台
执行：

```javascript
copy(JSON.stringify({
  openid: USER_OPENID,
  session_key: USER_SESSION_KEY
}))
```

然后在 `/docs` 调用 `PUT /api/settings/thufootball-credentials`，粘贴生成的 JSON。
接口只调用只读 `GetUserInfo` 验证凭据；验证成功后会同时原子更新项目根目录
`.env` 和当前进程环境。`GET /api/settings/thufootball-credentials` 仅显示配置状态
和脱敏值，不返回完整凭据。

批次创建和数据刷新各自只在查询期间持有短生命周期 Client。更新接口会等待正在
执行的查询关闭 Client，并在切换凭据期间阻止新查询开始；因此更新返回后，后续
查询一定使用新凭据。当前 Stage 2 必须运行单个 Uvicorn 进程，不使用 `--workers`。

### 创建和查看 Batch

调用 `POST /api/batches/create`：

```json
{
  "dates": ["2026-04-18", "2026-04-19"],
  "competitions": ["male", "female"]
}
```

日期和赛事先去重，再固定按日期升序以及 male、female、futsal 排序。已经存在的
日期赛事组合直接返回 `reused`，不会查询天气或 THUFootball，也不会修改已有
Batch；重新查询已有 Batch 必须使用 `refresh-data`。只对缺失组合查询上游，同一
赛事只查询一次当前比赛列表，再为缺失日期筛选。没有比赛的缺失组合返回
`skipped/no_games`，新建成功返回 `created`。天气失败作为 warning，不回滚已经
成功的比赛数据。

部分组合查询失败时 HTTP 仍返回 200，由每项的 `status` 表示结果；只有所有组合
都因 THUFootball 查询失败时返回 HTTP 502，原有 `results` 保留在
`error.details.results` 中。

使用 `GET /api/batches` 查看列表，使用
`GET /api/batches/{id}` 查看天气、全部比赛、缺项和当前
`current_preview_article_id`。需要重新查询时调用
`POST /api/batches/{id}/refresh-data`。人工天气不会被普通 refresh 覆盖。

### 默认人员、标题、天气和任务

新建 Batch 会复制当时的 `editorial_defaults`。先设置长期默认人员：

```json
PUT /api/editorial-defaults
{
  "editors": ["编辑甲"],
  "reviewers": ["责编甲"],
  "approvers": ["审核甲"]
}
```

修改已有 Batch：

```json
PATCH /api/batches/{id}
{
  "headline": "本周马杯前瞻",
  "editors": ["编辑甲"],
  "reviewers": ["责编甲"],
  "approvers": ["审核甲"]
}
```

人工天气：

```json
PUT /api/weather/2026-08-08
{
  "condition": "晴",
  "low_c": 20,
  "high_c": 31,
  "wind_direction": "南风",
  "wind_level": "2级"
}
```

调用 `POST /api/batches/{id}/open-tasks` 开放该 Batch 当前全部 active
比赛，调用 `POST /api/batches/{id}/close-tasks` 关闭当前全部 active 比赛。
两个接口都不接收请求体，并返回实际处理的 `game_ids`；没有 active 比赛时返回
空数组并保持 HTTP 200。任务开放状态变化不会让已渲染 Article 过期。

调用 `GET /api/matches` 可跨 Batch 查询当前全部已开放且有效的比赛，即
`active=true AND task_open=true`。结果按 `kickoff, game_id` 排序；每项除完整 match
字段外，还包含所属 Batch 的 `batch_id`、`batch_date` 和 `competition`。

调用 `GET /api/matches` 可跨 Batch 查询当前全部已开放且有效的比赛，即
`active=true AND task_open=true`。结果按 `kickoff, game_id` 排序；每项除完整 match
字段外，还包含所属 Batch 的 `batch_id`、`batch_date` 和 `competition`。

### 保存署名和正文

从 Batch 详情读取每场 `body_version`，保存时作为 `expected_version`：

```json
PATCH /api/matches/123456
{
  "expected_version": 0,
  "writers": ["作者甲", "作者乙"],
  "body": "第一段正文。\n\n第二段正文。"
}
```

一个或多个换行都用于分段，段前空格自动去除，不解析 Markdown。成功后版本号加一；其他请求已经保存过时返回
HTTP 409，并附当前版本、署名和正文，管理员应重新读取后决定如何合并。

### 替换封面

- `POST /api/batches/{id}/cover`：multipart 上传 JPEG、PNG 或 GIF，最大
  10 MiB；相同内容复用 `covers/<sha256>.<ext>`。
- `PUT /api/batches/{id}/cover-media-id`：请求体
  `{"media_id":"永久素材ID"}`，直接使用已有微信永久素材。

数据库只支持 `file` 和 `media_id`，不支持 `default` 类型，也不提供清空封面。
Batch 不保存 SHA；render 时 Article 固化 SHA，创建草稿前再次校验。

### 渲染和预览

调用 `POST /api/batches/{id}/render-preview`。即使状态为 `incomplete` 也会生成
带占位提示的 Article，并返回 `missing_fields`；零活动比赛时会加入只用于预览
的占位对阵。缺项 Article 不能创建微信草稿。

没有内容变化时再次 render 返回相同 Article 且 `reused=true`。标题、人员、
天气、封面、比赛数据、署名或正文变化后 `current_preview_article_id` 被清空，下次 render
插入版本号更大的新 Article；历史 Article 不修改。

- `GET /api/articles/{id}`：读取输入快照、最终 HTML、封面指纹和完整性。
- `GET /api/articles/{id}/preview`：在浏览器中查看最终 HTML。

### 创建微信草稿

先进行不产生外部写入的确认预览：

```json
POST /api/wechat-drafts
{
  "article_ids": [101, 102],
  "confirm": false
}
```

顺序就是微信公众号头条、次条顺序，仅允许 1–8 篇。服务会检查每个 Article
仍是所属 Batch 的 `current_preview_article_id`、内容完整且封面 SHA 未变化，并返回有序
发布指纹。

管理员核对无误并明确同意真实创建后，把同一请求改为 `confirm=true`。成功会
保存微信 `media_id` 和回执时间；相同 Article、顺序和封面指纹的重复请求直接
返回已有回执，不重复调用微信。默认或指定 media ID 封面不会调用 `upload_cover`。

## 4. 自动验证

完整测试：

```powershell
python -m ruff check backend src test scripts
python -m pytest
```

真实 PostgreSQL、假微信服务的可重复 API smoke test：

```powershell
python scripts/smoke_website_api.py
```

Smoke test 创建随机 PostgreSQL schema，验证登录、render、HTML 预览、
`confirm=false`、一次假草稿创建和 drafted 状态，结束后删除该 schema，不修改
正式业务表。

## 5. 备份与故障定位

PostgreSQL 保存业务数据、不可变 Article 和微信回执；
`var/artifacts/covers/` 保存管理员上传且不一定可重建的原始封面。两者必须一起
备份。Stage 2 不自动删除历史封面。

常见错误：

- 启动时报 `WEBSITE_DEFAULT_COVER_MEDIA_ID is required`：补齐默认永久素材 ID。
- 409 `body_version_conflict`：正文版本过期，重新读取比赛后合并。
- 409 `article_stale`：Batch 内容已变化，重新 render 后使用新 Article ID。
- 409 `cover_changed` 或 `cover_missing`：恢复对应上传封面，或为 Batch 设置新
  封面并重新 render。
- 409 `article_incomplete`：根据 `missing_fields` 补齐业务内容后重新 render。
- 502 `query_failed`、`weather_query_failed` 或 `wechat_failed`：检查对应凭据、
  网络、上游额度或微信 IP 白名单，然后明确重试。
- 502 `batch_queries_failed`：本次 create 的所有缺失组合都查询失败；逐项
  错误位于 `error.details.results`。
- 400 `invalid_thufootball_credentials`：重新从已登录的 TAFA 页面复制完整凭据。
- 502 `thufootball_validation_failed`：THUFootball 验证请求超时或返回异常，稍后重试。
- 500 `credential_persistence_failed`：检查项目根目录 `.env` 的写权限；原进程凭据
  未被替换。
