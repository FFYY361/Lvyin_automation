# 绿茵宣传部网站运维手册

生产地址：`https://media.thufootball.tech`；服务器仓库：`/home/xfy/lvyin_media/repo`；Conda 环境：`lvyin`。

网站由 Supervisor 管理两个服务：`lvyin-postgres` 是数据库，`lvyin-web` 是后端。
前端没有单独的进程，构建文件放在 `frontend/dist`，由后端直接提供。

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

先在本地提交并推送代码。如果前端有变化，再构建和上传：

```powershell
pnpm --dir frontend test
pnpm --dir frontend build
tar.exe -czf frontend-dist.tar.gz -C frontend/dist .
scp .\frontend-dist.tar.gz xfy@服务器地址:/home/xfy/lvyin_media/
```

在服务器更新后端：

```bash
cd /home/xfy/lvyin_media/repo
CONF=/home/xfy/lvyin_media/ops/supervisord.conf
supervisorctl -c "$CONF" stop lvyin-web
git pull
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

## 3. 赛季更迭

新赛季开始时，修改 `src/auto_preview/config.py`：

- `current_tournament_ids`：新赛季男足、女足、五人制的赛事 ID；
- `current_tournament_names`：这些赛事在网页上的名称；
- `historical_seasons`：将上一赛季加入历史赛季。

球队、赛事、比赛和最终排名统一维护在 PostgreSQL 的 `institutions`、`tournaments` 和
`games` 表中，不再维护 notes 文件。新赛季赛事先写入 `tournaments` 并保持
`is_finalized = false`，再运行 `scripts/sync_ai_preview_automatic.py` 抓取报名、赛程和比赛。
赛季结束并完成排名、比赛详情和人工修正审计后，更新 `final_rankings`，最后将赛事设置为
`is_finalized = true`；封存赛事不会再参与同步。

修改完成后在本地运行测试和前端构建，再按照第 2 节更新服务器。数据库不需要按赛季重建。
