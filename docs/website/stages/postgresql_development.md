# 网站 PostgreSQL 开发环境

本文用于完成网站实施计划的 Stage 1。当前阶段只启动 PostgreSQL，并验证
SQLAlchemy 和 Alembic；不启动业务 API。

## 1. 安装本机工具

需要以下工具：

- Miniconda 或 Anaconda（`lvyin` 环境固定使用 Python 3.11）；
- Docker Desktop，使用 Linux containers 和 WSL 2 后端；
- DBeaver Community 或其他 PostgreSQL 图形客户端；
- `psql`。本机未安装时，可以先使用 PostgreSQL 容器内自带的 `psql`。

安装后在新的 PowerShell 中检查：

```powershell
conda --version
docker --version
docker compose version
```

首次准备项目环境时创建名为 `lvyin` 的 Conda 环境并安装依赖：

```powershell
conda create -n lvyin python=3.11 pip -y
conda activate lvyin
python -m pip install --upgrade pip
python -m pip install -e ".[dev,website]"
python --version
alembic --version
```

`python --version` 应显示 `Python 3.11.x`。以后每次打开新的 PowerShell，先进入
仓库并执行：

```powershell
conda activate lvyin
```

如果环境已经存在，不要再次执行 `conda create`，直接激活并安装或更新依赖。

## 2. 配置本地环境变量

编辑 `.env`，至少替换以下两项中的占位值：

```dotenv
WEBSITE_POSTGRES_PASSWORD=替换为本机开发密码
WEBSITE_DATABASE_URL=postgresql+psycopg://lvyin:同一个密码@127.0.0.1:5432/lvyin
```

如果密码含有 `@`、`:`、`/`、`#` 等 URL 保留字符，必须在
`WEBSITE_DATABASE_URL` 中进行百分号编码。为减少本地排错，推荐先使用只包含
字母、数字、连字符和下划线的开发密码。

`.env` 已被 Git 忽略，不要把真实凭据写入 `.env.example`。

## 3. 启动、查看和停止 PostgreSQL

确保 Docker Desktop 已启动，然后在仓库根目录执行：

```powershell
docker compose config
docker compose up -d
docker compose ps
docker compose logs -f postgres
```

看到容器状态为 `healthy` 后按 `Ctrl+C` 退出日志跟踪。停止容器但保留数据：

```powershell
docker compose stop
```

重新启动：

```powershell
docker compose start
```

停止并移除容器但保留命名卷：

```powershell
docker compose down
```

不要在日常停止时增加 `--volumes`；该选项会删除本地数据库卷。

## 4. 使用 psql 和 DBeaver 连接

不依赖本机安装的 `psql`，直接进入容器：

```powershell
docker compose exec postgres psql -U lvyin -d lvyin
```

如果本机已安装 `psql`：

```powershell
psql -h 127.0.0.1 -p 5432 -U lvyin -d lvyin
```

DBeaver 新建 PostgreSQL 连接时使用：

| 字段 | 值 |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `5432` |
| Database | `.env` 中的 `WEBSITE_POSTGRES_DB` |
| Username | `.env` 中的 `WEBSITE_POSTGRES_USER` |
| Password | `.env` 中的 `WEBSITE_POSTGRES_PASSWORD` |

首次连接时 DBeaver 可能提示下载 PostgreSQL JDBC 驱动，允许下载即可。

## 5. 运行和验证 Alembic

执行 `conda activate lvyin`，确认 PostgreSQL 为 `healthy`，然后依次执行：

```powershell
alembic upgrade head
alembic current
alembic downgrade base
alembic current
alembic upgrade head
alembic current
```

最终的 `current` 应显示唯一迁移 `v1_initial (head)`。

在 `psql` 中可检查迁移表：

```sql
\dt
SELECT * FROM alembic_version;
```

## 6. 查看表、事务和锁

常用 `psql` 命令：

```sql
\l
\dt
\d alembic_version
SELECT version();
```

查看当前事务和等待事件：

```sql
SELECT pid, usename, state, xact_start, wait_event_type, wait_event, query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY xact_start NULLS LAST;
```

查看尚未授予的锁：

```sql
SELECT locktype, relation::regclass, mode, pid
FROM pg_locks
WHERE NOT granted;
```

DBeaver 中可以在数据库连接上打开 SQL Editor 执行相同 SQL。

## 7. 导出和恢复开发数据库

导出为容器内工具生成的自定义格式备份，再复制到仓库 `tmp` 目录：

```powershell
docker compose exec postgres sh -c 'pg_dump -U lvyin -d lvyin -Fc -f /tmp/lvyin.dump'
docker compose cp postgres:/tmp/lvyin.dump tmp\lvyin.dump
```

恢复前先确认目标是本机开发数据库，然后执行：

```powershell
docker compose cp tmp\lvyin.dump postgres:/tmp/lvyin.dump
docker compose exec postgres pg_restore -U lvyin -d lvyin --clean --if-exists /tmp/lvyin.dump
```

安装本机 PostgreSQL 客户端后，也可以直接执行：

```powershell
pg_dump -h 127.0.0.1 -p 5432 -U lvyin -d lvyin -Fc -f tmp\lvyin.dump
pg_restore -h 127.0.0.1 -p 5432 -U lvyin -d lvyin --clean --if-exists tmp\lvyin.dump
```

## 8. 仅限本机：清空并重建

以下操作会删除此 Compose 项目的全部 PostgreSQL 数据。执行前先用
`docker compose ls` 和 `docker volume ls` 确认当前目录及项目名为
`lvyin-website`：

```powershell
docker compose down --volumes
docker compose up -d
alembic upgrade head
```

不要在共享或生产环境执行这一节。

## 9. Stage 1 验收清单

- `docker compose ps` 显示 PostgreSQL 为 `healthy`；
- DBeaver 与 `psql` 均可连接；
- `upgrade -> downgrade -> upgrade` 成功；
- 创建一条临时数据，执行 `docker compose restart postgres` 后数据仍存在；
- `docker compose port postgres 5432` 显示绑定到 `127.0.0.1:5432`，局域网地址无法访问；
- 现有项目测试仍通过。

完成以上验收后再进入 Stage 2。
