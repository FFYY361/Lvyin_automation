# Stage 4 用户协作后端操作手册

Stage 4 在管理员闭环上增加普通用户注册、账号管理、比赛认领和正文协作。角色只有
`user` 和 `admin`；管理员继承全部普通用户权限。本阶段不修改前端，使用 FastAPI
`/docs` 或 HTTP 客户端验收。

## 1. 迁移和启动

```powershell
conda activate lvyin
python -m alembic upgrade head
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

当前唯一迁移 `v1_initial` 已包含 `auth_version` 和 `user/admin` 角色约束，不转换
任何旧数据库。只有确认测试数据库没有需保留的数据时才验证回滚：

```powershell
python -m alembic downgrade base
python -m alembic upgrade head
```

## 2. 账号和会话

使用共享邀请码注册会创建并自动登录普通用户：

```json
POST /api/auth/register
{
  "username": "member1",
  "display_name": "用户甲",
  "password": "至少八个字符",
  "invite_code": "由管理员提供的邀请码"
}
```

用户名不自动 trim、区分大小写且不能包含任何空白。显示名称会 trim。注册请求不能
指定 `role` 或 `is_active`。

所有有效用户可调用：

```json
PATCH /api/auth/me
{
  "display_name": "新显示名称"
}
```

```json
POST /api/auth/change-password
{
  "current_password": "原密码",
  "new_password": "新的至少八字符密码"
}
```

Cookie 同时保存 `user_id` 和 `auth_version`。本人改密后当前会话继续有效，其他旧
会话失效；管理员重置密码或切换启用状态后，该用户所有旧会话失效。修改显示名称
不会回写已有署名。

## 3. 用户管理

管理员读取完整管理列表：

```text
GET /api/admin/users
```

每项包含账号、角色、启用状态、创建/更新时间和 `claimed_task_count`，不包含密码
哈希或会话版本。管理员只能修改普通用户：

```json
PATCH /api/admin/users/123
{
  "display_name": "用户甲",
  "is_active": false
}
```

```json
POST /api/admin/users/123/reset-password
{
  "new_password": "新的至少八字符密码"
}
```

所有有效用户可以按需读取认领人的展示名称：

```text
GET /api/admin/users/123
```

该接口只返回 `id` 和 `display_name`。禁用用户不会自动释放其任务，也不会修改署名
或正文。

## 4. 批次和文章只读访问

`GET /api/batches`、`GET /api/batches/{id}`、
`GET /api/articles/{id}` 和 `GET /api/articles/{id}/preview` 对全部有效用户开放。
普通用户可查看所有批次和当前或历史文章，但不能创建、刷新或编辑批次，不能修改
天气、封面和人员，也不能渲染文章或创建微信草稿。

## 5. 任务查询

管理员批量检查全部有效开放任务：

```text
GET /api/tasks/open
```

普通用户和管理员查看尚未认领的有效开放任务：

```text
GET /api/tasks/wait_claim
```

任何用户查看本人全部认领：

```text
GET /api/me/tasks
```

本人任务不按 `active` 或 `task_open` 过滤。任务响应复用比赛字段，额外包含 Batch 的
`competition`；按 `kickoff` 筛选日期。需要认领人名称时，用
`claimed_by_user_id` 请求用户展示接口。

## 6. 认领、释放和转交

普通用户和管理员都可以原子认领有效开放比赛：

```text
POST /api/matches/{game_id}/claim
```

成功后认领人改为当前用户、署名替换为当前显示名称、正文保留、版本递增，已渲染
Article 过期。同一用户重复请求按幂等成功；其他并发请求返回 409 `task_claimed`。

释放接口不要求比赛仍有效或开放：

```text
POST /api/matches/{game_id}/release
```

普通用户只能释放本人任务，管理员可以释放任意任务。释放清空认领人和署名，保留
正文并递增版本。

管理员转交或释放：

```json
POST /api/matches/{game_id}/assign
{
  "user_id": 123
}
```

`user_id` 可为 `null`。非空目标必须是启用账号；转交会把署名替换为目标用户当前
显示名称，保留正文并递增版本。

## 7. 保存正文

```json
PATCH /api/matches/{game_id}/body
{
  "expected_version": 2,
  "body": "第一段。\n\n第二段。"
}
```

普通用户必须是当前认领人；管理员可以修改任意比赛。保存不要求比赛仍有效或开放，
且该接口不能修改署名。版本过期返回 409 `body_version_conflict`，详情中包含最新
版本、署名和正文。

## 8. 常见错误

- 401 `authentication required`：未登录、账号停用或会话版本已过期。
- 403 `administrator role required`：普通用户调用管理员接口。
- 403 `not_claim_owner`：普通用户修改或释放他人任务。
- 409 `username_exists`：用户名完全重复。
- 409 `task_unavailable`：比赛未开放或已失效，不能新认领。
- 409 `task_claimed`：比赛已被其他用户认领。
- 409 `body_version_conflict`：正文或署名版本已变化。
- 409 `invalid_assignment_target`：管理员指定的目标不存在或已停用。

## 9. 验证

```powershell
python -m ruff check backend src test scripts
python -m pytest
python scripts/smoke_website_api.py
```

Smoke test 使用随机 PostgreSQL schema 和假微信服务，覆盖注册、开放任务、认领、
保存正文、普通用户只读批次和文章、管理员渲染及草稿闭环，不向真实公众号写入。
