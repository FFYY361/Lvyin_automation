# thufootball_automation

清华绿茵的赛事查询、前瞻、战报和微信公众号草稿自动化。当前项目版本为
`v1 initial`（机器版本 `1.0.0`）。模块边界见
[项目架构](docs/architecture.md)，底层 Service 用法见
[Service 说明](docs/services.md)。

## 1. 环境准备

项目统一使用 Conda 管理开发环境，要求 Python 3.11 或更高版本。网站前端所需的
Node.js 也安装在同一个 Conda 环境中：

```powershell
conda create -n lvyin -c conda-forge python=3.11 pip nodejs=24 -y
conda activate lvyin
python -m pip install -e ".[website,dev]"
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --dir frontend install --frozen-lockfile
```

以后进入项目前只需执行 `conda activate lvyin`，不要再创建 venv、Poetry 或其他
Python 环境。

复制环境配置模板：

```powershell
Copy-Item .env.example .env
```

主要密钥和配置如下：

| 配置 | 用途 |
| --- | --- |
| `TAFA_USERNAME`、`TAFA_PASSWORD` | 网站自动获取 THUFootball 登录凭据 |
| `THUFOOTBALL_OPENID`、`THUFOOTBALL_SESSION_KEY` | CLI 手动凭据，可作为备用 |
| `AMAP_WEATHER_API_KEY` | 高德天气 Web Service Key |
| `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 微信公众号草稿接口 |
| `WEBSITE_DEFAULT_COVER_MEDIA_ID` | 网站和 Auto Preview CLI 共用的永久封面素材 ID |
| `WEBSITE_POSTGRES_*`、`WEBSITE_DATABASE_URL` | 本地 PostgreSQL |
| `WEBSITE_COOKIE_SECRET` | 网站登录 Cookie 签名密钥，需使用随机长字符串 |

不要提交真实 `.env`。微信公众号接口还要求把运行机器的出口 IP 加入后台白名单。

## 2. 网站说明

网站提供管理员批次管理、普通用户任务协作、前瞻/战报渲染和微信公众号草稿创建。
开发数据库只使用当前唯一迁移 `v1_initial`。

首次启动 PostgreSQL 并初始化数据库：

```powershell
conda activate lvyin
docker compose up -d postgres
python -m alembic upgrade head
python -m backend init-admin --username admin --display-name 管理员
```

开发时分别启动后端和前端：

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
pnpm --dir frontend dev
```

浏览器访问 `http://127.0.0.1:5173`；API 文档位于
`http://127.0.0.1:8000/docs`。

构建后由 FastAPI 同进程提供前端页面：

```powershell
pnpm --dir frontend build
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

数据库、管理员和协作操作详见 [网站文档](docs/website/)。开发数据库如仍使用旧迁移，
应删除项目 `lvyin-website` 的 PostgreSQL 卷后重新执行 `alembic upgrade head`，不做
旧数据库兼容或数据转换。

## 3. CLI 说明

CLI 仅推荐直接使用两条自动化管线。赛事参数支持 `male`、`female`、`futsal`，日期
和赛事均可传多个值并自动展开。`publish` 只创建微信公众号草稿，不会正式发布或
群发。

### Auto Preview

```powershell
python scripts/auto_preview.py --dates 2026-04-11 --competitions female
python scripts/auto_preview.py --dates 2026-04-11 2026-04-12 --competitions male female --stage data
python scripts/auto_preview.py --dates 2026-04-11 --competitions female --stage publish
```

| stage | 行为 |
| --- | --- |
| `data` | 查询比赛和天气，生成待填写的前瞻数据与正文文件 |
| `article` | 渲染文章；默认阶段 |
| `publish` | 完成全部组合后创建一个 1–8 篇的公众号草稿 |

产物位于 `runs/auto_preview/YYYY-MM-DD_赛事/`。首次 data 后填写：

- `runs/auto_preview/config.json`：编辑、责编、审核；
- `runs/auto_preview/weather.json`：按日期缓存的海淀天气；
- 组合目录的 `source.json`：标题和作者；
- 组合目录的 `previews/*.md`：每场纯文本正文。

未显式传封面时必须配置 `WEBSITE_DEFAULT_COVER_MEDIA_ID`，也可使用
`--cover FILE` 或 `--cover-media-id ID`。内容或模板变化会自动重渲染；`--override`
会从 data 阶段重做并尽量保留人工正文。旧 schema 2/3 产物不再兼容，需要使用
`--override` 按 v1 结构重建。

### Auto Report

```powershell
python scripts/auto_report.py --dates 2026-04-11 --competitions male
python scripts/auto_report.py --dates 2026-04-11 2026-04-12 --competitions male female --stage report
python scripts/auto_report.py --dates 2026-04-11 --competitions male --stage publish
```

| stage | 行为 |
| --- | --- |
| `report` | 查询完赛比赛并生成 PNG 战报 |
| `article` | 组装战报文章；默认阶段 |
| `publish` | 完成全部组合后创建一个 1–8 篇的公众号草稿 |

产物位于 `runs/auto_report/YYYY-MM-DD_赛事/`。未完赛比赛会跳过，弃赛比赛生成文字
说明；普通重跑复用通过哈希校验的产物。`--override` 会重新查询和绘制，但始终使用
`refresh_stats=False`，不会调用服务端统计刷新接口。封面可用 `--cover FILE` 或
`--cover-media-id ID` 覆盖默认战报素材。
