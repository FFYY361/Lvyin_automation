# 绿茵宣传部网站运维手册

生产地址：`https://media.thufootball.tech`；服务器仓库：`/home/xfy/lvyin_media/repo`；Conda 环境：`lvyin`。

网站由 Supervisor 管理两个服务：`lvyin-postgres` 是数据库，`lvyin-web` 是后端。
前端没有单独的进程，构建文件放在 `frontend/dist`，由后端直接提供。

AI 前瞻正文的本赛季资料按现有赛事配置实时读取 thufootball，往季资料和人工描述读取本地数据库。
正文资料读取失败时不回退到本赛季本地记录，已有正文和 AI 结果保留。
标题使用前瞻批次已保存的战绩、交锋快照和正文；需要更新这些事实时使用现有的刷新批次功能。
命令行 `ai-preview-prompt` 与模型评测脚本使用相同的数据来源和自动凭据刷新能力。

## 1. 启动、停止和重启

登录服务器后设置配置文件位置：

```bash
CONF=/home/xfy/lvyin_media/ops/supervisord.conf
```

```bash
# 查看状态
supervisorctl -c "$CONF" status
# 启动、停止或重启全部服务
supervisorctl -c "$CONF" start all
supervisorctl -c "$CONF" stop all
supervisorctl -c "$CONF" restart all
# 只重启后端，不重启数据库
supervisorctl -c "$CONF" restart lvyin-web
```

如果 Supervisor 本身没有运行：

```bash
conda activate lvyin
supervisord -c "$CONF"
```

```bash
curl http://127.0.0.1:3001/api/health
curl https://media.thufootball.tech/api/health
tail -n 100 /home/xfy/lvyin_media/logs/web.log
```

关闭 SSH 不会停止服务。服务器重启后，crontab 中的 `@reboot` 会启动 Supervisor；用 `crontab -l` 可以查看。

## 2. 更新网站代码

先在本地提交并推送代码，运行后端测试；前端运行测试、类型检查和构建后再上传。
生产使用固定提交的 detached HEAD，更新时显式指定已验证的完整提交号，不直接 `git pull`。
停站前准备好新制品和旧版本安装包；停站后备份数据库、Artifacts、`.env`、旧前端及提交号，
并验证备份。与每日备份共用 `ops/backup.lock`，只停止 `lvyin-web`。
现有 `ops/backup.sh` 会自动启动网站，不直接用它维持更新期间的停站窗口。

```powershell
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend build
tar.exe -czf frontend-dist.tar.gz -C frontend/dist .
scp .\frontend-dist.tar.gz xfy@服务器地址:/home/xfy/lvyin_media/
```

在服务器更新后端：

```bash
cd /home/xfy/lvyin_media/repo
CONF=/home/xfy/lvyin_media/ops/supervisord.conf
supervisorctl -c "$CONF" stop lvyin-web
git fetch origin main
# 将下方占位符替换为本次已验证的完整提交号
git switch --detach <release-commit>
conda activate lvyin
python -m pip install '.[website]'
python -m alembic upgrade head
```

如果上传了新前端，再替换 `frontend/dist`：

```bash
cd /home/xfy/lvyin_media/repo
rm -rf frontend/dist
mkdir frontend/dist
tar -xzf /home/xfy/lvyin_media/frontend-dist.tar.gz -C frontend/dist
```

```bash
supervisorctl -c "$CONF" start lvyin-web
curl https://media.thufootball.tech/api/health
```

如果只改了 `.env`，不需要重新安装，只需重启 `lvyin-web`。

AI 前瞻的网站模型需要 `AI_SERVICE_DEEPSEEK_API_KEY` 和 `AI_SERVICE_QWEN_API_KEY`。
通过 SSH 补充这两项，保留其他生产配置，保持 `.env` 权限为 600；不在命令输出、Git
或前端制品中保存密钥。首次升级到 `v2_football_data` 只会建表，必须另行导入
`institutions`、`tournaments`、`games` 三张资料表。使用一致性快照导出，在单事务中
导入并校验完整内容；不覆盖生产用户、任务、文章，不导入本地 AI 生成记录。

回滚时停止网站，切回备份中的旧提交，用 `--no-deps --force-reinstall` 安装备份的
旧 wheel，恢复旧前端和 `.env`，再启动并检查健康接口。此次 v2 为新增表，回滚应用
时保留资料和 AI 结果表，不运行会删除它们的 downgrade。仅数据库确有损坏时才从
停站备份恢复；恢复前保留失败现场及上线后新增数据。

## 3. 赛季更迭

新赛季开始时，修改 `src/auto_preview/config.py`：

- `current_tournament_ids`：新赛季男足、女足、五人制的赛事 ID；
- `current_tournament_names`：这些赛事在网页上的名称；
- `historical_seasons`：将上一赛季加入历史赛季。

院系合并通过 `institutions.predecessors` 保存前身全称、简称和项目 ID。新赛季合并球队报名后，
将其新 ID 填入院系根目录的对应数组；根目录只记录本体 ID，不能复制前身 ID。纯更名直接修改
院系名称／简称及相关排名键。202609 资料迁移操作见 `docs/ai_preview/football_data_database.md`。

球队、赛事、比赛和最终排名统一维护在 PostgreSQL 的 `institutions`、`tournaments` 和
`games` 表中，不再维护 notes 文件。新赛季赛事先写入 `tournaments` 并保持
`is_finalized = false`，再运行 `scripts/sync_ai_preview_automatic.py` 抓取报名、赛程和比赛。
赛季结束并完成排名、比赛详情和人工修正审计后，更新 `final_rankings`，最后将赛事设置为
`is_finalized = true`；封存赛事不会再参与同步。

修改完成后在本地运行测试和前端构建，再按照第 2 节更新服务器。数据库不需要按赛季重建。

模板比赛查询继续走 thufootball 接口，AI Prompt 的比赛、事件和报名资料继续使用本地数据库。
本版本没有周期性资料同步，新赛季应按需要运行上述同步脚本，避免 AI 使用过期资料。

2026–2027 当前男足为 `139、140、141`，女足为 `142`；五人制当前赛事为空。
模板历史范围为此前三个赛季；AI `history_seasons=4` 包含目标赛季及此前三个赛季。
女足从 2026–2027 起采用瑞士轮规则，thufootball 第一阶段的“循环赛”等明确阶段在展示中
归为“瑞士轮”，原始快照保留接口事实。旧女足仍按目标赛季选择原规则。

本次接入不用新增 Alembic 版本，生产从 v2 升到既有 v4。完整升级链路已在生产备份恢复的
隔离数据库中预跑：结构迁移、院系资料迁移、一次性新赛季事务写入及既有同步流程。
原有 590 场比赛及事件、344 项排名值、1704 名球员描述和业务表按内容校验保留。
一次性执行材料保存在 `releases/20261006-season`，停站回滚备份位于
`deploy-backups/20261006-season`；本次不维护长期导入脚本。
新赛季导入 73 支球队、1690 条报名，新增 713 名球员资料，院系总数 52、球员资料总数 2417。
2026-10-06 校验时男足共 76 场、女足新增 36 场，赛事和比赛后续更新仍使用既有同步脚本。

## 4. 2026-09-09 AI 功能上线记录

- 发布提交：`dde9a33d3f330ff0d44ef7e30fc2abc76c6463f9`；上一版本：`bdfaf08`。
- 数据库：`v1_initial` → `v2_football_data`；导入 53 个院系、14 届赛事、590 场比赛。
- 资料快照 SHA-256：`0d3a716b3dc312a5d61dea91d0573008e04acb4c9ce021b475e87fbe6822eedd`。
- 独立回滚备份：`/home/xfy/lvyin_media/deploy-backups/20260909-dde9a33`，各文件 SHA-256 校验通过。
- 发布与验收记录：`/home/xfy/lvyin_media/releases/20260909-dde9a33`。
- 停站备份至健康恢复约 10 秒；PostgreSQL 全程保持运行。
- 本地验证：后端 273 项及 115 个子测试通过，1 项真实外部接口测试按设计跳过；前端 32 项测试、类型检查和构建通过。
- 上线后原有业务表逐行内容与停站备份一致；公网 HTTPS、前端资源、管理员会话、资料页、任务列表及现有 18 篇文章预览通过。
- 普通用户权限、真实密码登录流程及生成错误分支由本地集成测试覆盖；线上验证了无效登录及未登录访问受限。
- 正式比赛 `4257`：DeepSeek 生成成功（846 字符，验收约 89 秒），Qwen 生成成功（894 字符，验收约 179 秒），重复请求均复用缓存；结果保留在各自 AI 槽位，人工正文未改动。
- 生产环境重新渲染前瞻 HTML 及 Chrome 战报 PNG（1600 × 1670）成功，验证产物保存在发布目录；未写入业务文章、未创建微信草稿。
- 当前赛事配置为 `122、123、124、126、128`；未删除生产历史任务，未新增资料同步定时任务。
