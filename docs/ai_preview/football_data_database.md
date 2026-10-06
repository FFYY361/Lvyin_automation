# 足球资料数据库

## 数据边界

PostgreSQL 是足球资料的唯一运行时来源：

- `institutions`：院系简称、本体的男足/女足/五人制历史球队 ID、前身配置 `predecessors`、
  三段球队描述，以及按姓名为键的球员描述 JSON。前身保存自己的名称、简称和项目 ID；
  本体 ID 数组不包含前身 ID，纯更名不建立前身。
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

首次文件迁移已经完成，旧导入器和源文件不再保留。202609 院系调整后为 52 个院系、156 个球队描述字段、
1704 名球员、14 届已封存赛事、344 项最终排名、590 场比赛和 19883 条事件。比赛 `3497`
保留软件学院判负且删除两条错误事件；比赛 `4152` 保留苏世民书院资格问题判负。

202609 院系合并、更名迁移：

```powershell
python scripts/migrate_institutions_202609.py --backup-only
python -m alembic upgrade head
python scripts/migrate_institutions_202609.py
```

迁移每次先备份院系、最终排名和 Prompt 内容到 `data/backups`，打印备份路径与 SHA-256。
资料更新在一个事务内完成，可重复执行；重复执行保留已经补充的土水本体 ID 和人工描述。
土木、水利原始排名保留，纯更名只更新排名键。回滚资料时停止应用，使用
`python scripts/migrate_institutions_202609.py --restore BACKUP_PATH`，并切回兼容代码；保留 v4
字段及全部比赛事实，不对资料库执行破坏性 downgrade。

最终成绩按赛季、项目选择：本体有排名只取本体，否则取前身最好成绩，全部没有则“未参赛”。
同项目的本体、前身身份分别统计比赛，历史展示统一使用配置名称与简称。

## Automatic 同步

2026–2027 接入后的资料为 52 个院系、2417 名人工球员资料、18 届赛事。
新赛事 `139、140、141、142` 保持未封存，最终排名为空；原有 14 届赛事继续封存。
73 支球队、1690 条有效报名与比赛详情通过现有同步流程保存。院系根目录使用全局 `team_id`，
赛事报名使用 `tournament_team_id`，两者不可互换。土水本体男足 `2076`、女足 `2077`，前身 ID 保持独立。

本次不增加结构版本或长期导入入口。生产 v2 先备份全库，再执行既有 `v2 → v4` 结构迁移、
202609 院系资料迁移及一次性新赛季接入。v2 尚未创建 Prompt 表，不能直接运行院系脚本的
`--backup-only`；先使用完整 PostgreSQL 备份。执行材料和校验结果保留在发布资料目录。

人工资料按院系与姓名合并，保留已有描述、往年球员和项目标签。模板配置的当前赛事为空时拒绝
新查询；历史文章仍可查看和渲染。旧批次刷新依据全部比赛的赛事 ID 校验，含停用比赛；不属于
当前配置的批次返回 409，不修改其资料。

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
