# Stage 3 管理员前端操作手册

Stage 3 提供 React 管理网站，管理员可以在页面中完成批量创建、任务开放、
内容编辑、文章渲染和微信公众号草稿创建。网站仍然只创建草稿，不正式发布或群发。

## 1. 前端环境

前端要求 Node.js 22 或更高版本，推荐使用 Node.js 24 LTS，并使用 pnpm 11 管理依赖。首次安装：

```powershell
cd frontend
corepack enable
pnpm install
```

`pnpm-lock.yaml` 固定实际依赖版本，不要同时生成 npm 或 Yarn 锁文件。

## 2. 本地开发

先按 [管理员后端操作手册](admin_backend.md) 启动 PostgreSQL、完成迁移并创建管理员，
随后在两个终端分别运行：

```powershell
conda activate lvyin
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
pnpm dev
```

打开 `http://127.0.0.1:5173`。开发服务器会把同源 `/api` 请求代理到 FastAPI，
浏览器只保存现有的签名 Session Cookie，不保存 THUFootball 或微信凭据。

## 3. 单进程运行

构建前端后，FastAPI 会自动从 `frontend/dist/` 提供网站：

```powershell
cd frontend
pnpm build
cd ..
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

此时打开 `http://127.0.0.1:8000`。`/api`、`/docs` 和 `/openapi.json` 仍保持原路径。
如果未生成 `frontend/dist/index.html`，后端 API 仍可启动，但根路径不会提供管理页面。

## 4. 页面闭环

1. 在“设置”中检查 THUFootball 凭据并配置新建批次的默认人员。
2. 在“创建批次”中添加多个日期、选择赛事并核对笛卡尔积后提交。
3. 在“批次管理”中查看状态、缺项和最近错误；需要时执行重新查询。
4. 打开批次详情，填写标题、人员、天气和封面，并在“比赛”卡片区统一开放或关闭任务。
5. 点击比赛卡片进入独立比赛页，查看双方过往三届战绩、本届赛果和近三届交锋，再保存署名与正文；可以使用上一场、下一场连续编辑。
6. 正文出现版本冲突时，比较本地与服务器内容，明确加载服务器版本或基于最新版本人工合并。
7. 打开“文章预览”，执行渲染并核对最终 HTML 和完整性。批次数据变化后仍可查看最近一次渲染结果，但过期版本只能对照，不能用于发布。
8. 在“微信草稿”中选择 1–8 篇当前完整文章并调整顺序。先核对发布指纹，二次确认后才会真实创建草稿。

## 5. 验证

```powershell
cd frontend
pnpm typecheck
pnpm test
pnpm build
```

后端继续使用 Stage 2 的验证命令：

```powershell
python -m ruff check backend src test scripts
python -m pytest
python scripts/smoke_website_api.py
```

最后一条使用测试数据库和假微信服务，不会向真实公众号写入草稿。
