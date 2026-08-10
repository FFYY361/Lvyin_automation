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

最终排名整理完成后，更新 `src/thufootball/notes/`：

- `teams.json`：新增球队 ID、院系改名或合并；
- `tourns.json`：加入已经产生最终排名的赛事；
- `ranks/<赛事ID>.json`：记录该赛事各球队的最终名次；
- `identity_audit.json`：球队合并或 ID 共用关系变化时更新。

`tourns.json` 中的每个赛事都必须有同名 ID 的排名文件。新赛季刚开始、还没有最终排名时，
只修改 `current_tournament_ids`，不要提前加入 `tourns.json`。

修改完成后在本地运行测试和前端构建，再按照第 2 节更新服务器。数据库不需要按赛季重建。
