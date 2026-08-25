# 足球资料数据库

## 数据边界

PostgreSQL 是足球资料的唯一运行时来源：

- `institutions`：院系简称、男足/女足/五人制历史球队 ID、三段球队描述，以及按姓名为键的
  球员描述 JSON。
- `tournaments`：规范赛事名称、项目、最终排名及赛事、报名、赛程和停赛业务数据；
  `is_finalized` 标记已经完整审计并封存的赛事。
- `games`：完整比赛事实、事件、裁判和每队人数，通过 `tournament_id` 关联赛事。
- `matches`：仍是网站写作任务，不与 `games` 合并。

三张资料表不含时间、版本字段。赛事默认按赛季倒序、ID 升序查询。Prompt 和比赛规则留在
`src/ai_preview/prompt`，corpus 留在只读资料目录；迁移使用的 notes、institutions 和 automatic
文件已经删除，也不提供文件回退。

## 数据库迁移

```powershell
python -m alembic upgrade head
```

首次文件迁移已经完成，旧导入器和源文件不再保留。当前基线为 53 个院系、159 段球队描述、
1704 名球员、14 届已封存赛事、344 项最终排名、590 场比赛和 19883 条事件。比赛 `3497`
保留软件学院判负且删除两条错误事件；比赛 `4152` 保留苏世民书院资格问题判负。

## Automatic 同步

```powershell
python scripts/sync_ai_preview_automatic.py
```

同步目标来自 `tournaments` 中 `is_finalized = false` 的赛事，不使用硬编码赛事清单；已经封存
的赛事不会被读取或写入。所有远端赛事和比赛读取成功并通过 ID、外键和完整性校验后，才开启
数据库事务；事务内更新赛事 `data`、upsert 比赛并删除该届远端已不存在的比赛。失败时整次
回滚，不更新规范名称、项目、最终排名、封存状态或人工资料，也不再写 automatic JSON 或
manifest。

## 运行时使用

Repository 位于 `thufootball.database`，接收已有 SQLAlchemy Session。网站复用 backend
Session；CLI 从 `.env` 的 `WEBSITE_DATABASE_URL` 建立短生命周期 Session。所有相关路径在
数据库不可用或记录缺失时直接报错，不回退旧文件。
