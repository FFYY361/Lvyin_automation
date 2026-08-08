# Stage 7：绿茵宣传部生产化与部署

本阶段着重完成本地开发和可测试的生产配置，不登录或修改服务器，也不制作字节级依赖锁、
Linux 专用环境或完整自动化部署系统。生产部署只记录常规流程，待下一阶段在服务器验证。

## 本阶段已完成

- Python 发行包名改为 `lvyin_media`，现有模块导入路径和 CLI 名称保持不变。
- 注册必须提交共享邀请码 `WEBSITE_INVITE_CODE`；邀请码错误在密码哈希和写库前返回 403。
- `GET /api/health` 检查数据库 `SELECT 1` 以及 Artifact 根目录读写能力。
- `WEBSITE_ALLOWED_HOSTS` 限制 Host；`WEBSITE_DOCS_ENABLED` 控制 API 文档。
- README 和 `docs` 中的仓库正式称呼统一为“绿茵宣传部”。
- 前端测试、类型检查和生产构建均在本地完成；服务器不需要 Node.js 或 pnpm。

健康接口的意义是区分“3001 端口上有进程”和“网站真的可以工作”。只有 FastAPI、数据库、
Artifact 三者都可用才返回 200；它不访问 TAFA、高德或微信，因此第三方故障不会导致网站
被误判为离线。

## 服务器常规安装方案

固定目录仍为 `/home/xfy/lvyin_media`，Miniconda 安装在 `/home/xfy/miniconda3`。
不执行 `conda init`，不修改系统 Python。服务器取得代码后按项目声明一次性创建环境即可，
无需 SHA/hash 级重建：

```bash
git clone git@github-lvyin:FFYY361/THUfootball_automation.git \
  /home/xfy/lvyin_media/repo

/home/xfy/miniconda3/bin/conda create -y -n lvyin_media python=3.11 postgresql=17 supervisor pip
/home/xfy/miniconda3/envs/lvyin_media/bin/pip install '/home/xfy/lvyin_media/repo[website]'
```

本地运行 `pnpm test`、`pnpm typecheck`、`pnpm build` 后，只上传 `frontend/dist` 到服务器
对应仓库目录。生产 `.env` 至少使用：

```dotenv
WEBSITE_ALLOWED_HOSTS=media.thufootball.tech,127.0.0.1,localhost
WEBSITE_DOCS_ENABLED=false
WEBSITE_COOKIE_SECURE=true
WEBSITE_INVITE_CODE=请替换为8到128字符的私密邀请码
THUFOOTBALL_CHROMIUM=/usr/bin/google-chrome-stable
```

FastAPI 计划只监听 `127.0.0.1:3001` 且使用单 worker；PostgreSQL 计划只监听
`127.0.0.1:55432`。Apache 和 HTTPS 继续由服务器现有配置提供。

## 后续 TODO

- 在 Ubuntu 服务器安装 Miniconda、创建环境并验证 Python、PostgreSQL、Supervisor 命令。
- 配置 GitHub SSH Host 别名，clone 仓库并建立权限为 600 的生产 `.env`。
- 初始化空 PostgreSQL，执行 `alembic upgrade head` 和 `python -m backend init-admin`。
- 上传本地 `frontend/dist`，配置 Supervisor、`crontab @reboot` 和日志轮转。
- 配置每天凌晨 4 点的联合备份：最多 7 份、总量不超过 2GB；最新单份超过 2GB 时
  只保留该份。不安排恢复演练或异地副本。
- 检查本机 `/api/health`、公网 HTTPS，以及注册、登录、任务、前瞻、战报和微信草稿。
- 在真实 Linux 环境完成 Shell、依赖安装和启停流程验证；这些验证不属于本阶段。
- 根据首次部署经验再决定是否编写自动发布、回滚、制品校验和依赖锁工具。

暂不实施 PWA。Ubuntu 20.04 升级、Apache、证书和系统 Chrome 由服务器管理员负责。

