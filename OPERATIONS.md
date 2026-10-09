# 绿茵宣传部网站运维手册

生产地址：`https://media.thufootball.tech`；服务器仓库：`/home/xfy/lvyin_media/repo`；Conda 环境：`lvyin`。

各版本上线功能及资料调整见 [版本记录](RELEASES.md)。

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
旧 wheel，恢复旧前端和 `.env`，再启动并检查健康接口。回滚应用时保留资料和 AI
结果表，不运行会删除它们的 downgrade。仅数据库确有损坏时才从
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

模板比赛查询及 AI 正文的本赛季比赛、事件和报名资料走 thufootball 接口，往季和人工资料
读取本地数据库。没有周期性资料同步，应按需要运行上述同步脚本，维护本地资料。

2026–2027 当前男足为 `139、140、141`，女足为 `142`；五人制当前赛事为空。
模板历史范围为此前三个赛季；AI `history_seasons=4` 包含目标赛季及此前三个赛季。
女足从 2026–2027 起采用瑞士轮规则，thufootball 第一阶段的“循环赛”等明确阶段在展示中
归为“瑞士轮”，原始快照保留接口事实。旧女足仍按目标赛季选择原规则。
