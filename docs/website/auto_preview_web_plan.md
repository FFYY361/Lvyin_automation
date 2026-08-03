# Auto Preview 网站实施计划

## 目标与原则

把现有 Auto Preview 自动化逐步建设为协会内部使用的网站。实施顺序固定为：

```text
环境与数据库
→ 管理员闭环后端
→ 管理员闭环前端
→ 完整用户与管理员后端
→ 完整前端并投入使用
```

总体原则：

- 先通过 FastAPI `/docs` 和 HTTP 请求跑通后端，再开发对应前端。
- PostgreSQL 是网站唯一业务数据源；`var/artifacts/` 只保存管理员上传的封面
  文件，文章输入快照、HTML 和微信回执全部保存在 PostgreSQL。
- 网站不与 `runs/auto_preview` 双向同步。现有 CLI 暂时继续使用 `runs/`，
  网站稳定后再评估是否迁移 CLI。
- 网站代码使用与 `src/` 平级的 `backend/` 和 `frontend/`；`src/` 保留可复用
  业务能力，依赖方向固定为 frontend → backend → src。
- 尽可能复用现有查询、数据构建、文章渲染、微信草稿和指纹能力，不复制业务
  算法。
- 应用只供协会内部使用，实现密码哈希、登录会话、基本权限、输入校验和秘密
  隔离等基础安全，不建设复杂企业安全体系。
- 优先代码可读性和直接实现；在实际出现扩展需求前，不引入微服务、Redis、
  Celery、Kubernetes 等额外基础设施。
- 第一版数据查询、文章渲染和微信草稿接口同步执行，不引入 Job 表和独立
  worker；只有实际出现超时、断线后继续执行或详细进度需求时再增加后台任务。

## Stage 1：环境和数据库准备

### 目标

准备一套本机可重复启动、方便查看和调试的 PostgreSQL 开发环境。此阶段不开发
前瞻业务 API。

### 工作内容

1. 安装和验证开发环境：
   - Python 3.11 及名为 `lvyin` 的 Conda 环境；
   - Docker Desktop；
   - PostgreSQL 官方镜像；
   - DBeaver 或同类图形化数据库客户端；
   - `psql` 命令行作为排错工具。
2. 新增 `compose.yaml`，只启动 PostgreSQL：
   - 端口仅绑定 `127.0.0.1:5432`；
   - 使用命名 volume 持久化数据；
   - 使用 `pg_isready` 健康检查；
   - 数据库名、用户和密码通过环境变量配置。
3. 在 `.env.example` 增加网站配置占位值：
   - `WEBSITE_DATABASE_URL`；
   - `WEBSITE_ARTIFACT_ROOT`；
   - API host、port 和日志级别；
   - 本地签名 Cookie 配置。
4. 引入 SQLAlchemy、Psycopg 和 Alembic：
   - 建立数据库 Engine 和 Session 工厂；
   - 建立空的首个迁移；
   - 验证 `upgrade`、`downgrade` 和重新 `upgrade`。
5. 写一条数据库调试说明，覆盖：
   - 启动、停止和查看 PostgreSQL 日志；
   - 使用 DBeaver 和 `psql` 连接；
   - 查看表、事务和锁；
   - 导出、恢复开发数据库；
   - 清空并重建仅限本地的开发数据库。

### 验收

- 新环境可以根据文档启动 PostgreSQL。
- DBeaver 和 `psql` 均可连接。
- Alembic 升级、回滚和重建成功。
- 关闭并重新启动容器后数据仍然存在。
- 数据库端口不能从局域网访问。

## Stage 2：管理员闭环后端

### 目标

在没有前端的情况下，管理员可以通过 FastAPI `/docs` 完成从批量创建前瞻到
微信公众号草稿的完整流程。

### 配置和 Auto Preview 兼容改动

网站新增必填配置 `WEBSITE_DEFAULT_COVER_MEDIA_ID`。网站启动时缺失或为空立即
报错；新建 Batch 默认保存为 `cover_kind=media_id`、
`cover_storage_key=<该配置>`、`cover_content_type=NULL`。默认封面不属于完整性
缺项，可以直接创建微信草稿，也不做远程预校验。

这是对现有 Auto Preview 业务逻辑的修改：`AutoPreviewPipeline` 未显式指定封面
且没有可复用旧封面时，优先把该配置解析成 `CoverMediaId`，避免把默认图片重复
上传到微信；CLI 缺少配置时仍回退到原 `default_cover.png`。除这个小型解析函数
外，不改变 CLI、`runs/` 状态机、日志、文件结构或公开返回值。

网站依赖方向固定为 `backend → src`。直接复用 `Competition`、赛事排序、
`PreviewSourceBuilder`、天气 Service、`PreviewSourceData`、正文分段、
`PreviewService.render()`、`Article`、`CoverFile`、`CoverMediaId`、
`WechatOfficialService.create_draft()` 以及文章和发布指纹。网站 API、数据库、
权限、动态状态和持久化逻辑只放在 `backend/`。

### 数据库模型

#### `users`

| 列 | 类型与约束 |
| --- | --- |
| `id` | `BIGINT` PK identity |
| `username` | `VARCHAR(64)` NOT NULL UNIQUE |
| `display_name` | `VARCHAR(100)` NOT NULL |
| `password_hash` | `TEXT` NOT NULL |
| `role` | `VARCHAR(16)` NOT NULL，`admin` 或 `user` |
| `auth_version` | `INTEGER` NOT NULL DEFAULT 0，必须非负 |
| `is_active` | `BOOLEAN` NOT NULL DEFAULT true |
| `created_at`, `updated_at` | `TIMESTAMPTZ` NOT NULL |

用户名区分大小写，不自动 trim，长度 1–64，且不得包含任何空白字符。Stage 2 的
`python -m backend init-admin` 只创建 admin。

#### `preview_batches`

| 列 | 类型与约束 |
| --- | --- |
| `id` | `BIGINT` PK identity |
| `preview_date` | `DATE` NOT NULL |
| `competition` | `VARCHAR(16)` NOT NULL |
| `headline` | `VARCHAR(200)` NOT NULL DEFAULT `''` |
| `editors`, `reviewers`, `approvers` | `JSONB` NOT NULL DEFAULT `[]` |
| `cover_kind` | `VARCHAR(16)` NOT NULL，`file` 或 `media_id` |
| `cover_storage_key` | `TEXT` NOT NULL，文件 key 或微信 media ID |
| `cover_content_type` | `VARCHAR(64)` NULL |
| `current_article_id` | `BIGINT` NULL FK → `articles.id` |
| `last_error_code` | `VARCHAR(64)` NULL |
| `last_error_message` | `TEXT` NULL |
| `last_error_at` | `TIMESTAMPTZ` NULL |
| `created_at`, `updated_at` | `TIMESTAMPTZ` NOT NULL |

`(preview_date, competition)` 唯一。文件封面 key 为
`covers/<sha256>.<jpg|png|gif>` 且 MIME 非空；media ID 模式 MIME 为 NULL。Batch
不保存封面 SHA、原文件名、文件大小、独立 `cover_media_id`，也不允许无封面。
标题、人员、天气、封面、比赛自动数据、署名或正文实际变化时清空
`current_article_id`；任务开放和错误信息变化不清空。render 成功后指向同 Batch
的 Article，未被清空时重复 render 直接复用。

#### `preview_matches`

| 列 | 类型与约束 |
| --- | --- |
| `game_id` | `BIGINT` PK |
| `batch_id` | `BIGINT` NOT NULL FK |
| `tournament_id` | `BIGINT` NOT NULL |
| `tournament_name`, `competition_name` | `VARCHAR(200)` NOT NULL |
| `stage` | `VARCHAR(100)` NOT NULL |
| `kickoff` | `TIMESTAMPTZ` NOT NULL |
| `venue` | `VARCHAR(200)` NOT NULL |
| `home_snapshot`, `away_snapshot`, `head_to_head_snapshot` | `JSONB` NOT NULL |
| `active` | `BOOLEAN` NOT NULL DEFAULT true |
| `task_open` | `BOOLEAN` NOT NULL DEFAULT false |
| `claimed_by_user_id` | `BIGINT` NULL FK → `users.id` |
| `writers` | `JSONB` NOT NULL DEFAULT `[]` |
| `body` | `TEXT` NOT NULL DEFAULT `''` |
| `body_version` | `INTEGER` NOT NULL DEFAULT 0 |
| `created_at`, `updated_at` | `TIMESTAMPTZ` NOT NULL |

`game_id` 全局唯一并自带主键索引。另建 `(batch_id, active, kickoff, game_id)`、
活动且开放任务的 `(kickoff, game_id)` 部分索引，以及已认领记录的
`(claimed_by_user_id, kickoff, game_id)` 部分索引。不保存归档时间和原因；比赛
消失时设 inactive，重新出现时恢复，改期时更新 `batch_id` 并关闭任务，三种情况
都保留人工正文、版本和认领人。正文保存携带 `expected_version`，冲突返回 409。

#### `weather`

列为：`date DATE PK`、`adcode CHAR(6)`、`region_name VARCHAR(100)`、
`condition VARCHAR(100)`、`low_c SMALLINT`、`high_c SMALLINT`、
`wind_direction VARCHAR(50)`、`wind_level VARCHAR(50)`、`source VARCHAR(16)`、
`report_time TIMESTAMPTZ`，除主键外均 NOT NULL。自动查询采用上游报告时间，
人工修改使用当前时间；普通 refresh 不覆盖人工天气。不另存创建和更新时间。

#### `editorial_defaults`

列为：`id SMALLINT PK`（固定为 1）、`editors JSONB`、`reviewers JSONB`、
`approvers JSONB`、`updated_at TIMESTAMPTZ`，均 NOT NULL，人员列默认 `[]`。
新建 Batch 复制当时的默认人员，后续默认值修改不影响已有 Batch。

#### `articles`

| 列 | 类型与约束 |
| --- | --- |
| `id` | `BIGINT` PK identity |
| `batch_id` | `BIGINT` NOT NULL FK |
| `version_number` | `INTEGER` NOT NULL |
| `input_snapshot` | `JSONB` NOT NULL |
| `title`, `body_html`, `digest` | `TEXT` NOT NULL |
| `author` | `VARCHAR(100)` NOT NULL |
| `source_url` | `TEXT` NOT NULL DEFAULT `''` |
| `template_version` | `VARCHAR(128)` NOT NULL |
| `content_fingerprint` | `CHAR(64)` NOT NULL |
| `cover_kind` | `VARCHAR(16)` NOT NULL，`file` 或 `media_id` |
| `cover_storage_key` | `TEXT` NOT NULL |
| `cover_sha256` | `CHAR(64)` NOT NULL |
| `is_complete` | `BOOLEAN` NOT NULL |
| `missing_fields` | `JSONB` NOT NULL DEFAULT `[]` |
| `created_at` | `TIMESTAMPTZ` NOT NULL |

`(batch_id, version_number)` 唯一，并建相同字段的版本倒序索引。Article 创建后
不可修改。Batch 的有效渲染过期后插入新行；当前有效版本通过
`preview_batches.current_article_id` 查找，历史最新版本也可按版本倒序查询。
media ID 封面的 SHA 为 media ID UTF-8 字节的 SHA-256。创建微信草稿前，文件
封面重新读文件计算 SHA，media ID 重新计算字符串 SHA，防止渲染后封面被替换。

#### `wechat_drafts`

列为：`id BIGINT PK identity`、`articles JSONB NOT NULL`、
`publication_fingerprint CHAR(64) NOT NULL UNIQUE`、`media_id TEXT NOT NULL`、
`wechat_created_at TIMESTAMPTZ NOT NULL`、`created_at TIMESTAMPTZ NOT NULL`。
`articles` 是有序 `{article_id, content_fingerprint, cover_sha256}` 数组。
`confirm=false` 不写表；`confirm=true` 创建或按发布指纹复用回执。

### Artifact、状态和执行模型

`var/artifacts/` 只保存管理员上传的文件封面：

```text
var/artifacts/
└── covers/
    └── <sha256>.<jpg|png|gif>
```

相同内容复用相同 key，默认 media ID 不产生本地文件，Stage 2 不删除历史封面。
PostgreSQL 和 covers 目录都必须备份。

Batch 不保存状态列，动态计算为：`incomplete` 表示标题、天气、人员、活动比赛、
署名或正文有缺项；`ready` 表示当前数据完整但当前 Article 未进入草稿；`drafted`
表示当前 Article 已包含在成功草稿中。封面不参与完整性判断。incomplete 也允许
render，缺项用现有提示；零活动比赛由后端加入只用于预览的占位对阵。

第一版同步执行查询、render 和微信草稿创建，不建立 Job、worker 或 Session
表。日期和赛事用事务、唯一约束与 advisory lock 防止重复写入；批量创建复用
同赛事查询缓存，草稿通过有序发布指纹幂等。

批量 create 先查询 PostgreSQL：已有日期赛事组合直接返回 `reused`，不访问天气或
THUFootball，也不修改 Batch；只有缺失组合才查询并创建，已有 Batch 的重新查询
统一由 `refresh-data` 完成。部分查询失败时返回 HTTP 200 和逐项结果；全部缺失
组合都查询失败时返回 HTTP 502，并在 `error.details.results` 保留逐项错误。

THUFootball 查询 Service 和 Client 不跨 HTTP 请求复用。管理员可在网站运行期间
通过设置接口更新一对完整凭据：接口只调用只读 `GetUserInfo` 验证，随后原子写回
项目 `.env` 并更新当前进程环境。查询与凭据更新使用同一应用级互斥锁；更新返回
后不再存在使用旧凭据的 Client。Stage 2 限定单 Uvicorn 进程，不启用多 worker。

### 公共 API

- 认证：`POST /api/auth/login`、`POST /api/auth/logout`、
  `GET /api/auth/me`、`python -m backend init-admin`。
- 设置：`GET /api/settings/thufootball-credentials`、
  `PUT /api/settings/thufootball-credentials`；只返回配置状态和脱敏凭据。
- 批次：`POST /api/preview-batches/create`、`GET /api/preview-batches`、
  `GET /api/preview-batches/{id}`、
  `POST /api/preview-batches/{id}/refresh-data`、
  `PATCH /api/preview-batches/{id}`。
- 默认人员：`GET /api/editorial-defaults`、`PUT /api/editorial-defaults`。
- 任务与内容：`POST /api/preview-batches/{id}/open-tasks`、
  `POST /api/preview-batches/{id}/close-tasks`、
  `GET /api/preview-matches`、`PATCH /api/preview-matches/{game_id}`、
  `PUT /api/weather/{date}`。
  open/close 不接收请求体，分别开放或关闭该 Batch 当前全部 active 比赛。
  match 列表只返回 `active AND task_open` 的比赛，并附所属 Batch 的日期和赛事。
- 封面：`POST /api/preview-batches/{id}/cover`、
  `PUT /api/preview-batches/{id}/cover-media-id`。
- 文章：`POST /api/preview-batches/{id}/render`、`GET /api/articles/{id}`、
  `GET /api/articles/{id}/preview`。
- 微信草稿：`POST /api/wechat-drafts`、`GET /api/wechat-drafts/{id}`。

不提供 `/bulk`、`/article-versions` 或 `default` cover kind。本阶段只创建微信
草稿，不正式发布或群发。

### 后端验收

先执行迁移 `upgrade → downgrade → upgrade` 和
`python scripts/smoke_website_api.py`。随后通过 `/docs` 完成“初始化管理员并登录
→ 创建多个日期赛事 Batch → refresh → 开放任务 → 编辑标题、天气、人员、封面、
署名和正文 → 渲染 incomplete/ready 预览 → 使用 `confirm=false` 核对 1–8 篇顺序
→ 明确确认后执行一次真实微信草稿创建 → 核对 media ID 与 drafted 状态”。具体
命令和请求体见 [Stage 2 操作手册](stages/admin_backend.md)。

## Stage 3：管理员闭环前端

### 目标

建立 React + TypeScript + Vite 管理网站，使管理员无需使用 `/docs` 即可完成
Stage 2 的全部操作。

### 页面与功能

1. 登录页：管理员用户名密码登录和退出。
2. 管理首页：
   - 选择多个 dates；
   - 勾选 male、female、futsal；
   - 提交前展示即将创建的日期赛事笛卡尔积；
   - 一次批量创建前瞻任务。
3. 批次列表页：显示日期、赛事、当前状态、最近错误和重新查询入口。
4. 批次详情页：
   - 查看只读比赛数据；
   - 发布或关闭待领取任务；
   - 编辑标题、天气、编辑/责编/审核；
   - 上传或替换封面；
   - 查看完整性缺项。
5. 比赛写作区：
   - 管理员填写一个或多个署名；
   - 使用纯文本多段正文；
   - 点击按钮手动保存；
   - 有未保存内容时离开页面给予提示；
   - HTTP 409 时提示数据已更新，不静默覆盖。
6. 文章预览页：渲染最新文章版本，显示缺项和是否可发布。
7. 微信草稿页：
   - 勾选 1–8 篇最新完整文章；
   - 拖动调整头条和次条顺序；
   - 二次确认后创建微信草稿；
   - 展示 `media_id` 和重复请求复用结果。

### 前端约束

- 第一版优先桌面浏览器和清晰可用，不先做复杂视觉设计。
- 所有业务状态以 API 返回为准，前端不复制完整性或排序算法。
- 耗时请求显示加载状态并防止重复提交；失败后由管理员明确重试。
- 不引入 Job 轮询、WebSocket 或后台任务框架。
- 前后端本机同源运行，不增加 CORS 复杂度。
- 管理员闭环验收后再开发普通用户页面。

### 验收

管理员仅使用网站即可完成“批量创建 → 发布任务 → 编辑/写作 → 渲染
→ 创建微信草稿”，不需要手动修改数据库、`runs/` 或调用 `/docs`。

## Stage 4：完整用户与管理员后端

### 目标

在管理员闭环稳定后，加入普通用户账号、权限和单场比赛认领，使多人可以安全协作。管理员继承全部普通用户权限。

### 数据库

- 不新增表；任务继续使用 `preview_matches`。
- `users` 新增非负的 `auth_version INTEGER NOT NULL DEFAULT 0`，角色约束改为
  `role IN ('user', 'admin')`。当前数据库没有普通用户数据，迁移不转换角色值。
- `preview_matches` 已有 `claimed_by_user_id`、`writers`、`body` 和
  `body_version`，不新增列或索引。

### 账号与权限

- `require_user` 校验签名 Cookie 中的 `user_id`、`auth_version`、启用状态以及
  `user/admin` 角色；`require_admin` 在其上继续校验管理员角色。
- `POST /api/auth/register` 公开注册固定角色为 `user` 的账号并自动登录；用户名
  不 trim、区分大小写、禁止空白，重复用户名返回 409。
- `PATCH /api/auth/me` 允许所有有效用户修改自己的显示名称；不回写已有署名。
- `POST /api/auth/change-password` 修改本人密码并使其他旧会话失效。
- 管理员通过 `GET /api/admin/users`、`PATCH /api/admin/users/{id}` 和
  `POST /api/admin/users/{id}/reset-password` 管理普通用户。列表不返回密码哈希或
  会话版本。
- `GET /api/admin/users/{id}` 对所有有效用户开放，只返回 `id` 和
  `display_name`，用于按认领人 ID 获取展示名称。
- `GET /api/preview-batches`、`GET /api/preview-batches/{id}`、文章 JSON 和 HTML
  预览对所有有效用户只读开放；批次写操作、渲染和微信草稿仍只限管理员。

### 任务 API

- `GET /api/tasks/open` 只限管理员，返回全部 `active AND task_open` 比赛。
- `GET /api/tasks/wait_claim` 对所有有效用户开放，返回
  `active AND task_open AND claimed_by_user_id IS NULL` 比赛。
- `GET /api/me/tasks` 返回本人全部认领，包括已关闭或失效的比赛。
- 新任务列表复用比赛 payload，只额外返回 Batch 的稳定 `competition` 值；不返回
  `preview_date`、`current_article_id` 或重复的认领人对象。
- `POST /api/preview-matches/{game_id}/claim` 原子认领；成功时署名替换为当前用户
  显示名称，正文保留，版本递增并使 Article 过期。
- `POST /api/preview-matches/{game_id}/release` 普通用户只能释放本人任务，管理员
  可释放任意任务；释放清空认领人和署名，保留正文并递增版本。
- `POST /api/preview-matches/{game_id}/assign` 由管理员转交给任意启用账号或释放；
  自动更新署名、保留正文并递增版本。
- `PATCH /api/preview-matches/{game_id}/body` 由认领人或管理员保存正文并携带
  `expected_version`；认领人保存时不要求比赛仍有效或开放。

### 认领规则

- 一场比赛只能有一个认领用户；并发认领只能一人成功，其他请求返回 409。
- 同一用户重复认领按幂等成功；管理员可以调用全部普通用户接口。
- 认领时署名替换为用户 display name；释放时清空署名；转交时替换为目标用户
  display name。三者均保留正文、递增版本并使当前 Article 过期。
- 正文手动保存并带 `expected_version`。
- 不增加提交、审核或锁稿状态；正文非空即可视为已填写，管理员随时可以渲染。
- 关闭待领取状态后禁止新的认领，但不删除已有认领和正文。

### 基础安全和测试

- 服务端执行角色和资源归属校验，不能只靠前端隐藏按钮。
- 密码哈希、签名 HttpOnly Cookie、输入校验、上传类型和大小限制继续保留。
- 自助注册不允许指定 role，管理员账号仍只通过本地初始化方式创建。密码重置、
  本人改密或启用状态切换会递增 `auth_version`，立即失效旧 Cookie。
- 不增加邀请码、邮箱验证、验证码、复杂组织架构、审批流、OAuth 或企业级审计
  平台。
- 测试重点覆盖并发重复注册、用户名大小写与空白规范化、权限矩阵、并发认领、
  越权修改、正文版本冲突和管理员转交。

## Stage 5：完整前端与投入使用

### 用户前端

- 注册普通用户账号、登录和修改密码。
- 查看可领取任务，并按日期、赛事筛选。
- 原子认领并看到失败原因。
- 查看自己的任务和比赛只读数据。
- 编辑纯文本多段正文并手动保存。
- 显示保存版本、最后保存时间和冲突提示。
- 查看包含本人正文的文章预览。

### 管理员增强

- 用户账号管理和密码重置。
- 查看所有任务的认领人和正文完成情况。
- 转交、释放认领和修改多作者署名。
- 批量查看缺项，快速定位未认领或未填写比赛。
- 保留 Stage 3 的批量创建、编辑、渲染和微信草稿能力。

### 使用体验与部署

- 完成手机和桌面响应式布局。
- 增加 PWA manifest 和“添加到主屏幕”能力；不做复杂离线编辑。
- 完成空状态、加载状态、错误恢复和二次确认。
- 用真实协会工作流完成一轮试运行。
- 部署到云服务器时使用 PostgreSQL、HTTPS、持久化 Artifact Volume 和每日备份。
- 配置微信 API 所需的固定出口 IP 白名单。
- 凭据只保存在服务器环境变量中，不进入浏览器、数据库内容或日志。

### 最终验收

- 管理员可以批量创建日期赛事组合、发布任务、编辑和生成微信草稿。
- 多位普通用户可以自行注册、登录、领取不同比赛并保存正文。
- 并发认领、越权修改和正文版本冲突均得到明确处理。
- PostgreSQL 是唯一业务数据源；管理员上传的 covers 不是全部可重建数据，必须
  和 PostgreSQL 一起备份。
- 现有 Auto Preview CLI 和测试没有被网站开发破坏。
- 协会成员能够在手机或电脑浏览器中完成日常前瞻工作。

## 阶段推进规则

- 每个 Stage 必须先完成其自动化测试和手动验收，再进入下一 Stage。
- Stage 2 后端 API 的输入输出一旦供前端使用，应尽量保持 `/api` 路径和字段兼容；
  确实出现无法兼容的重大变化时再引入新版本路径。
- 若复用现有逻辑需要小规模重构，先补充覆盖当前行为的测试，再移动代码。
- 不为了未来可能出现的规模提前增加分布式组件；出现真实瓶颈后再演进。
