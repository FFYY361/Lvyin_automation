# 足球资料数据库

PostgreSQL 是足球资料的唯一运行时来源。v6 起，全部业务按赛事报名身份识别球队。

## 数据结构

- `institutions.tournament_teams`：`{male: {赛事ID: 报名ID}, female: {...}, futsal: {...}}`。
  JSON 的赛事键为十进制字符串，报名 ID 为正整数。三个项目始终保留，未报名项目用 `{}`。
- `institutions.predecessors`：前身名称、简称和相同结构的 `tournament_teams`。每条报名只归属一个
  本体或前身，本体不复制前身报名。人工描述仍统一由所属院系维护。
- `tournaments`：赛事事实、项目、封存状态和最终排名。`final_rankings` 使用报名 ID 字符串作键，
  如 `{"1735": "冠军"}`，不再使用球队名称。赛季从赛事资料读取，不在院系重复维护。
- `tournaments.data.registered_teams`：同时保存赛事 ID、报名 ID 和上游全局 ID。
- `games`：完整比赛事实、事件、裁判和人数。主客队两种 ID 都保留，业务只使用报名 ID。
- `matches`：网站写作任务。主客队、赛果和交锋快照中的球队字段为 `tournament_team_id`。
- `articles.input_snapshot`：前瞻输入使用同一字段；战报输入仍引用比赛与战报哈希。

全局 ID 仅供原始事实、旧数据转换和事实一致性核对，不用于身份归属和比赛统计。
`matches` 不与 `games` 合并；Prompt 继续在数据库维护，不提供旧文件回退。

## v5 → v6 迁移

新增版本为 `v6_tournament_team_identity`。先使用既有流程备份全库和运行目录，停止该数据库的
应用写入并等待进行中的 AI 标题任务结束，再准备转换清单：

```powershell
python scripts/migrate_tournament_team_ids.py --output data/backups/tournament-team-v6.json
python -m alembic -x team_identity_manifest=data/backups/tournament-team-v6.json upgrade head
```

准备命令只读数据库；旧快照引用本地没有的比赛时，只读查询 thufootball，把证据存入清单。
Alembic 执行时不联网，复核源内容摘要和完整转换结果后，在一个事务中写入。缺失、歧义、
源内容变化或清单被修改都会使迁移失败并回滚。空数据库直接执行 `alembic upgrade head`。

迁移修改院系配置、排名键、比赛快照和前瞻文章输入，删除三个旧 `*_team_ids` 列。原始比赛、
报名、人工描述、正文、署名、任务、AI 结果、文章 HTML、内容指纹和微信草稿保留。
v6 本身为等价迁移；后续前身配置调整独立执行，见下文。

标题 Prompt 会包含快照中的 ID，因此还会更新 `ai_title_results.prompt_hash`：仅当成功结果的
旧指纹仍匹配迁移前当前输入时，替换为新输入指纹。原本过期的结果保持过期，候选文本、状态和
时间不变；正文 Prompt 不含这些 ID，不修改 `ai_preview_results`。

已有 CLI 运行目录先预检，再转换输入与受影响的缓存校验值，不重新渲染或创建草稿：

```powershell
python scripts/migrate_tournament_team_ids.py --runs-root runs/auto_preview --manifest data/backups/tournament-team-v6.json
python scripts/migrate_tournament_team_ids.py --runs-root runs/auto_preview --manifest data/backups/tournament-team-v6.json --apply
```

转换前备份位于清单旁的 `runs-before`。原有旧版本运行目录的 schema 兼容范围不因此扩大。
回退时恢复迁移前完整数据库备份并切回匹配代码，不运行破坏性 downgrade。
202609 院系修改属于历史版本，旧脚本只保留 `--backup-only`，不再提供当前库的数据修改入口。

### 2026-10 前身配置调整

完成 v6 后执行，默认只预检，加 `--apply` 才备份并在事务中写入：

```powershell
python scripts/update_institution_predecessors_202610.py
python scripts/update_institution_predecessors_202610.py --apply
```

土木水利学院保留土木-建管、水利两个前身，新闻与传播学院-马克思主义学院保留新闻、马院两个前身。
交叉-AI 的 2023～2024、2024～2025 男足与五人制报名转入前身
“交叉信息研究院”（简称“交叉”），2025～2026 起归本体；女足无对应历史报名。
脚本只调整交叉-AI，校验报名唯一性、赛事项目、全部排名引用及报名覆盖不变，可重复执行。
院系原有人工描述保留，已有任务、正文和草稿不重写，新建或刷新批次时读取新归属。

## 运行时规则

单个报名 ID 只查询对应赛事。院系历史通过全名和项目／赛事范围展开配置，不扫描全部远端赛事。
公开前瞻按项目、历史赛季从近到远追溯；同赛季男足甲乙丙级合并判断。遇到两个及以上有报名的前身，
该赛季及更早赛季的排名均显示“未参赛”，交锋不计入；前身数按报名计算，不依赖是否有排名。
截止点之后优先使用本体，否则继承唯一参赛前身；双方交锋分别受各自的截止点约束，保持目标比赛主客顺序。
不再从多个成绩中择优。AI 保留完整前身战绩、成绩来源和交锋，不应用公开表格的合并截止点，
前身资料的正文使用遵循数据库原有 Prompt，代码不额外追加限制。
旧前身目标只追溯自身。新建或刷新批次时应用规则，已有文章、草稿和前身配置不自动改写。

AI 当前赛季资料走实时 thufootball，往季和人工描述走数据库。实时读取不写回数据库；失败直接
报错，不回退本地当前赛季事实。新报名必须先在院系配置中登记，不能通过全局 ID 猜测。

Automatic 同步入口保持：

```powershell
python scripts/sync_ai_preview_automatic.py
```

只同步 `is_finalized=false` 的赛事，远端读取和校验成功后再单事务更新事实。规范名称、排名、
封存状态和人工资料不由 Automatic 覆盖，也不自动修改院系报名归属。
