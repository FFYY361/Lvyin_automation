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

### 每日备份与天气刷新

北京时间每天 `04:00` 执行现有 `ops/backup.sh`。备份完成、网站恢复健康后，脚本在同一把
`ops/backup.lock` 下执行 `python -m backend refresh-weather`；不新增独立定时任务。
天气失败不改变已完成的备份结果，网站未恢复健康时跳过天气并记录原因。

手动检查或刷新时，在服务器仓库和现有 `lvyin` 环境执行：

```bash
cd /home/xfy/lvyin_media/repo
python -m backend refresh-weather --dry-run
python -m backend refresh-weather
tail -n 100 /home/xfy/lvyin_media/logs/weather-refresh.log
```

按上海时区刷新今天及未来三天内已创建批次的自动天气，保留人工天气。天气内容变化后同步更新
同日期前瞻文章；仅预报发布时间变化时不重新生成文章。无预报正常跳过，真实失败退出码为 `1`。
每个日期独立提交，失败日期保留原数据；微信公众号已有草稿不自动改写。
自动刷新成功或已有人工天气时，清除同日期批次的 `weather_query_failed`；人工保存天气也会清除。
天气内容未变化时同样清除，其他错误保留；重新查询批次沿用原有错误处理逻辑。

天气日志由系统 `logrotate` 在备份后轮转，保留七份并压缩旧日志；配置为
`ops/weather-logrotate.conf`，状态为 `ops/weather-logrotate.state`。需要暂停自动天气时，
移除 `ops/backup.sh` 中 `BEGIN daily weather refresh` 到 `END daily weather refresh` 的附加块，
保留原备份任务及时间。每天备份的退出清理逻辑仍会自动恢复网站，发布停站时继续使用独立回退备份。

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

院系合并通过 `institutions.predecessors` 保存前身全称、简称和报名归属。v6 起，本体和前身均使用
`tournament_teams = {male: {赛事ID: 报名ID}, female: {...}, futsal: {...}}`。新赛季报名后填写对应
赛事与报名 ID，不把前身报名复制给本体。排名按报名 ID 保存；纯更名修改配置名称和简称。
v6 已随 `1.5.0` 上线；迁移操作见 `docs/ai_preview/football_data_database.md`，发布与回退记录见 `RELEASES.md`。

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
女足从 2026–2027 起采用类似欧冠联赛阶段的分档对阵、统一积分排名机制。
AI Prompt 中的第一阶段统一称为“联赛阶段”，原始快照保留接口事实；旧赛季按当年赛制处理。

## 4. 数据库 Prompt 与战报字体

固定 Prompt 仅在数据库 `prompt_templates` / 管理页面维护，Git 不保存规则正文。
发布若涉及规则改写，需按当次 `need_release.md` 将本地已确认的九个分区导入线上，
先备份线上内容，再单事务更新；修改人置空，不复制本地用户 ID。

Linux 战报使用 Pillow 渲染，必须配置 `THUFOOTBALL_FONT_DIR`。生产字体目录为
`/home/xfy/lvyin_media/data/report-fonts`，包含 `simhei.ttf`、`NotoSansSC-VF.ttf`、
`trebucbd.ttf`、`trebuc.ttf`、`STFANGSO.TTF`。字体不进入 Git 或前端制品；
部署前验证可加载并在隔离环境生成 PNG。二维码缓存由现有配置管理。

## 5. 2026-10-21 本地赛事球队 ID 迁移（演练记录）

- 本地数据库由 `v5_super_admin` 升至 `v6_tournament_team_identity`。52 个院系及前身改用按项目、
  赛事组织的报名映射；347 项排名、39 场写作任务快照和 12 篇前瞻输入完成转换。
- 原始 702 场比赛、人工描述、正文、署名、28 篇文章内容及草稿保持不变；交叉仍保留原有归属。
  标题缓存仅在旧输入仍匹配时重算指纹；本地三个已有结果无需重算，有效／过期状态均保持一致。
- 15 个 CLI 运行目录完成源数据及元数据转换，原文件逐个备份，未重新渲染或创建微信草稿。
- 隔离副本及真实迁移均通过 67 组前瞻资料、702 场比赛队名、11 个批次 HTML／标题／状态的
  等价检查；错误清单完整回滚、空库升级通过。Python 347 项及 121 项子测试通过，1 项真实接口
  冒烟默认跳过；前端 38 项测试、类型检查和构建通过。
- 本地完整备份、转换清单和核验记录位于 `data/backups/tournament-identity-20261021/`，完整备份为
  `before.dump`，CLI 原文件位于 `runs-before/`。操作方法见 `docs/ai_preview/football_data_database.md`。
  当次为本地演练；后续 `1.5.0` 生产迁移、验证及备份记录见 `RELEASES.md`。

### 同日前身配置调整（本地演练）

- 土水保留土木-建管、水利两个前身，新闻-马院保留新闻、马院两个前身；历史报名和排名不变。
- 交叉-AI 添加前身“交叉信息研究院”（简称“交叉”）：男足 `88→988`、`100→1523`，
  五人制 `93→1425`、`111→1630`；2025～2026 起的报名仍属本体，女足无历史报名。
- 使用 `scripts/update_institution_predecessors_202610.py --apply` 更新交叉-AI，院系仍为 52 条。
  写入前备份为 `data/backups/institution-predecessors-20261021T074137450065.json`；
  校验全部报名覆盖和排名引用不变。未重写任务快照、文章或草稿；生产需在 v6 后另行执行该脚本。
- 土水、新闻-马院的解除归属操作已撤销，四条临时独立记录已撤回，交叉-AI 保持不变。
  撤销前备份为 `data/backups/restore-predecessors-20261021T074940864250.json`。
- 重复预检无待修改项；已有成绩与数据库测试 12 项通过，覆盖三项目前身归属和交叉分界。

### 同日前瞻历史继承规则调整

- 历史赛季从近到远追溯，同项目有多个参赛前身时，该赛季及更早赛季不再继承排名、交锋；
  男足甲乙丙级一起判断，缺少排名仍计入参赛前身数。单前身正常继承，删除排名择优代码。
- 模板排名、公开交锋共用同一筛选函数。AI 仍接收完整前身背景、原始成绩及交锋，不应用表格截止点。
  代码里新增的正文禁用指令及交锋标记已撤回；数据库 Prompt、数据库结构、前身配置保持不变。
- 前瞻、AI、成绩和网站回归 143 项及 7 项子测试通过；最终 AI 上下文调整另通过 15 项及 3 项子测试。
  三项目真实配置只读核验通过：土水往季不继承，新闻-马院男足／五人制从 2024～2025 起截断，
  女足单前身继续继承，交叉-AI 单前身继续继承。新建或刷新批次时生效，未批量重写已有文章或草稿。
