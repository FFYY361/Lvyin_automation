# 清华绿茵公众号：已发表文章模板提取与草稿箱写入教程

本教程用于验收 `docs/preview_automation_implementation_plan.md` 中的能力 4、5、6：

1. 从“清华绿茵”已经发表的图文中提取正文、排版和图片引用；
2. 把文章正文改造成可复用模板，并在本地填充、预览；
3. 使用公众号官方接口，将一篇带测试标记的最小文章写入草稿箱。

自动化边界严格停在草稿箱。当前实现没有自动发布或群发入口。

## 0. 开始前必须确认的条件

下面从已经扫码登录 [微信公众平台](https://mp.weixin.qq.com) 后开始。这个阶段只查看，不点击“重置”“启用”“修改”“保存”等按钮。

### 0.1 确认登录的是“清华绿茵”

1. 查看后台首页左上角或账号头像附近显示的公众号名称；
2. 确认名称为“清华绿茵”，而不是同一微信号管理的其他公众号或小程序；
3. 如果登录后出现多个账号，先选择“清华绿茵”再进入后台；
4. 记录首页显示的账号类型和认证状态，例如订阅号/服务号、已认证/未认证，后面排查接口权限时会用到。

不要复制或发送原始 ID、管理员微信号等无关账号信息。

### 0.2 确认自己的后台角色

1. 在左侧菜单向下找到“设置与开发”；
2. 展开后点击“人员设置”；
3. 找到“管理员”或“运营者管理”区域；
4. 在长期运营者列表中找到自己的微信号；
5. 记录页面对你的身份描述：`管理员`、`长期运营者` 或其他角色。

判断：

- 如果自己出现在“管理员”位置，通常可以继续检查全部开发设置；
- 如果自己只出现在“长期运营者”，仍继续下面的只读检查，不要提前假定一定有或一定没有开发权限；
- 如果看不到“人员设置”，或者只能看到内容编辑菜单，直接记为“角色权限不足”，需要管理员协助。

这里的“长期运营者”不是公众号所有者，也不等于开发接口管理员。最终以能否完成 0.3–0.6 为准。

### 0.3 确认能看到开发配置入口

1. 回到左侧“设置与开发”；
2. 寻找以下任一入口：
   - “开发接口管理”；
   - “基本配置”；
   - “开发者中心”；
3. 如果点击“开发接口管理”后还有二级菜单，选择“基本配置”；
4. 如果页面提示相关能力已迁移到微信开发者平台，使用页面提供的官方跳转入口，不要通过搜索结果中的第三方登录页进入；
5. 到达页面后，寻找“开发者 ID（AppID）”“开发者密码（AppSecret）”和“IP 白名单”等区域。

判断：

- 能看到完整 AppID：开发配置至少具有查看权限；
- 完全没有上述入口：当前角色不能自行完成接口接入；
- 有入口但页面提示无权限：截图时遮住 AppID 等账号信息，只把提示文字发给管理员处理。

### 0.4 确认 AppID/AppSecret 操作权限

在基本配置页面只做以下观察：

1. 确认“开发者 ID（AppID）”后面有一串以 `wx` 开头的值；
2. 确认页面存在“开发者密码（AppSecret）”区域；
3. 观察该区域是否提供“启用”“查看”或“重置”之类的按钮；
4. **本阶段不要点击这些按钮**；
5. 如果页面写明需要管理员扫码确认，也记录为“可操作，但需要管理员一次性确认”。

结果分三类：

| 页面情况 | 结论 |
| --- | --- |
| 能看到 AppID，且 AppSecret 区域有可操作按钮 | 可以继续，真实取密钥时可能仍需管理员扫码 |
| 能看到 AppID，但 AppSecret 区域显示无权限 | 需要管理员完成一次性取密钥操作 |
| AppID 和 AppSecret 都看不到 | 当前登录角色不具备开发配置权限 |

AppSecret 通常不能反复显示。若只有“重置”按钮，不要直接重置：重置会使其他正在使用旧密钥的程序失效。

### 0.5 确认 IP 白名单权限

1. 在当前基本配置页面寻找“IP 白名单”；
2. 如果当前页面没有，在“设置与开发”下检查“安全中心”中的 IP 白名单；
3. 确认能够看到当前白名单状态；
4. 观察是否存在“查看”“修改”或“配置”按钮；
5. 本阶段不要点击保存，也不要删除已有 IP。

判断：

- 能看到白名单且有修改入口：通过；
- 能查看但不能修改：需要管理员代为加入脚本运行机器的公网出口 IP；
- 完全找不到：先确认是否进入的是公众号而不是小程序；仍找不到则需要管理员检查开发设置。

### 0.6 确认草稿和素材接口权限

1. 在“设置与开发”→“开发接口管理”中寻找“接口权限”或“接口调用权限”；
2. 在页面内分别搜索或查找以下能力：
   - 素材管理/新增永久素材；
   - 上传图文消息内的图片并获取 URL；
   - 草稿箱－新建草稿；
   - 草稿箱－获取草稿总数；
3. 确认这些行没有显示“未获得”“无权限”或申请失败；
4. 如果页面展示每日调用上限，确认上限不是 0；
5. 不需要申请自动发布或群发权限，本项目只写入草稿箱。

如果后台没有独立的“接口权限”页面，不能仅凭菜单判断成功。先完成 AppID/AppSecret 和 IP 白名单配置，再运行第 5 节的只读 `auth-probe`；能够成功查询草稿总数，就是最终的机器验证。

### 0.7 确认日常草稿箱可见

1. 在左侧进入“内容与互动”；
2. 点击“草稿箱”或“草稿”；
3. 确认可以打开草稿列表；
4. 本阶段不要新建、修改或删除草稿。

这只证明你有后台运营权限，不等于 API 权限已经通过；API 权限仍以 0.6 和 `auth-probe` 为准。

### 0.8 汇总检查结果

完成后按下面格式记录即可，不要记录或发送 AppSecret：

```text
公众号：清华绿茵
账号类型：订阅号/服务号
认证状态：已认证/未认证/页面未显示
我的角色：管理员/长期运营者/其他
AppID：看得到/看不到（不要粘贴具体值）
AppSecret 操作：可操作/需管理员确认/无权限
IP 白名单：可修改/仅查看/找不到
素材接口：有权限/无权限/找不到权限页
草稿接口：有权限/无权限/找不到权限页
后台草稿箱：可打开/不可打开
```

### 0.9 如果自己是长期运营者，且开发者平台看不到该公众号

如果同时满足以下两点：

- 在“人员设置”中属于运营者管理/长期运营者；
- 登录微信开发者平台后无法选择目标公众号，只能看到自己的其他公众号；

则可以判定：当前微信号只有目标公众号的运营权限，没有目标公众号的开发配置权限。这不是浏览器或代码故障，无法通过公开文章链接补齐；AppSecret 也不能从文章源码中获取。

此时请选择下面一种方案：

**方案 A：管理员给当前微信号增加开发者权限（推荐）**

1. 目标公众号管理员登录微信公众平台或由后台跳转到微信开发者平台；
2. 选择目标公众号；
3. 进入开发者/成员与权限相关页面；
4. 将当前微信号添加为该公众号开发者，或开启查看开发接口配置所需权限；
5. 当前微信号接受邀请后重新登录开发者平台；
6. 确认现在能够选择目标公众号并看到 AppID、AppSecret 区域和 IP 白名单。

菜单名称可能随平台版本显示为“开发者权限”“成员管理”或“成员与权限”，以管理员页面中的实际名称为准。

**方案 B：管理员代做一次性接口配置**

1. 管理员取得现有 AppID/AppSecret；
2. 管理员确认重置 AppSecret 不会影响其他接入系统；
3. 管理员把脚本运行机器的公网出口 IP 加入白名单；
4. 管理员在脚本运行机器上直接把凭据写入 `.env` 或安全密钥存储，不通过聊天转发；
5. 运营者运行只读 `auth-probe` 验证凭据和草稿权限。

两种方案都不需要转移公众号所有权。方案 A 方便以后自行维护；方案 B 权限暴露最小，但每次出口 IP 或密钥变化都需要管理员再次处理。

满足以下条件才算第 0 步通过：

- 能看到“设置与开发”下的“基本配置”或“开发接口管理”；
- 能查看 AppID；
- 能启用、查看或重置 AppSecret；
- 能修改 IP 白名单；
- 素材和草稿相关接口拥有权限，或稍后能通过只读 `auth-probe` 验证；
- 能打开后台草稿箱。

“长期管理员/长期运营者”这个名称本身不能保证拥有开发配置权限，以后台实际可见菜单为准。如果看不到 AppSecret 或 IP 白名单，需要公众号管理员或所有者完成一次性配置；之后脚本运行不需要每次扫码。

> 重置 AppSecret 会使旧 AppSecret 失效。如果公众号已经连接了其他程序，先确认它们是否依赖当前 AppSecret。不要为了测试直接重置。

所有命令都在仓库根目录执行：

```powershell
Set-Location "E:\绿茵\绿茵agent"
```

建议先确认命令入口可用：

```powershell
python tools/wechat_preview.py --help
```

## 1. 获取一篇已发表文章的链接

优先选择“清华绿茵”自己发表、且有权复用的普通图文文章。第一次测试建议选择：

- 排版结构接近以后要自动生成的赛前前瞻；
- 文章仍能公开访问；
- 主要由文字、分隔线和静态图片组成；
- 暂时不要选视频号卡片、投票、小程序卡片或复杂交互很多的文章。

在微信手机客户端中获取链接：

1. 打开“清华绿茵”公众号；
2. 打开目标已发表文章；
3. 点击右上角 `…`；
4. 点击“复制链接”；
5. 把链接粘贴到记事本，确认它类似 `https://mp.weixin.qq.com/s/...`。

也可以从公众号后台的已发表内容中打开文章，再复制浏览器地址。

下文用 `$ArticleUrl` 保存链接，避免在命令中反复粘贴：

```powershell
$ArticleUrl = "https://mp.weixin.qq.com/s/这里替换为真实文章参数"
```

## 2. 提取已发表文章（能力 4）

执行：

```powershell
python tools/wechat_preview.py extract $ArticleUrl `
  --output-dir tmp/qhly-template-source
```

成功时会输出 JSON，其中应包含：

```json
{
  "status": "ok",
  "title": "原文章标题",
  "author": "清华绿茵",
  "media_count": 5,
  "content_fingerprint": "...",
  "preview": "...\\source.html",
  "body": "...\\body.html",
  "metadata": "...\\source.json"
}
```

生成的三个关键文件：

| 文件 | 用途 |
| --- | --- |
| `tmp/qhly-template-source/source.html` | 带标题和基本预览样式的完整本地样本，供人工核对 |
| `tmp/qhly-template-source/body.html` | 清理后的正文 HTML，是模板化的起点 |
| `tmp/qhly-template-source/source.json` | 原链接、标题、作者、图片引用和内容指纹 |

打开本地预览：

```powershell
Start-Process (Resolve-Path tmp/qhly-template-source/source.html)
```

检查以下项目：

- 标题和作者正确；
- 段落、字号、颜色、对齐、留白和分隔线与原文大体一致；
- `media_count` 不是意外的 0；
- 正文没有混入公众号菜单、推荐阅读或页面脚本；
- 图片即使暂时仍是微信 CDN 地址，也应保留在正确位置。

提取器只读取公开页面，不会绕过登录、验证码或访问控制。如果出现访问频率验证，应停止批量请求，稍后重试或换一篇可公开访问的授权文章。

### 2.1 先做离线自检（可选）

即使尚未拿到真实文章链接，也可以验证提取器：

```powershell
python tools/wechat_preview.py extract-file test/fixtures/article_source/wechat_article.html `
  --source-url https://mp.weixin.qq.com/s/authorised-sample `
  --output-dir tmp/wechat-source-self-test
```

这个命令不会访问网络，也不会连接公众号后台。

## 3. 使用唯一的前瞻模板（能力 5）

仓库只保留一套正式模板：

- `templates/qhly_preview_v1/template.html`：正文排版；
- `templates/qhly_preview_v1/example_data.json`：男足周六完整样例；
- `templates/qhly_preview_v1/example_data_women_saturday.json`：女足周六最小样例；
- `templates/qhly_preview_v1/example_data_futsal_saturday.json`：五人制周六最小样例；
- `templates/qhly_preview_v1/schema.json`：包含栏目配置在内的完整前瞻源数据契约。

模板中的普通文本使用双花括号，渲染时自动 HTML 转义：

```html
<p>比赛地点：{{match.venue}}</p>
```

源数据不允许提供 HTML。前瞻正文使用纯文本段落数组，由模板生成段落标签：

```html
<!-- wx:each match.preview_paragraphs as paragraph -->
<p>{{paragraph}}</p>
<!-- wx:endeach -->
```

赛程和比赛卡片使用列表块；最终写入微信前会展开成完整 HTML：

```html
<!-- wx:each source.matches as match -->
<tr>
  <td>{{match.home.short_name}}</td>
  <td>{{match.away.short_name}}</td>
</tr>
<!-- wx:endeach -->
```

本地生成前瞻：

```powershell
python tools/wechat_preview.py render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article.html
```

打开结果：

```powershell
Start-Process (Resolve-Path tmp/qhly_preview_v1/article.html)
```

渲染成功的 JSON 会返回 `content_fingerprint`。相同模板、版本与数据会产生相同指纹，便于追踪草稿对应的输入。

## 4. 公众号后台的一次性接口配置（能力 6 前置）

### 4.1 获取 AppID 和 AppSecret

1. 登录 [微信公众平台](https://mp.weixin.qq.com)；
2. 进入“设置与开发”→“基本配置”或“开发接口管理”；
3. 记录公众号 AppID；
4. 如果现有 AppSecret 可以安全取得，使用现有值；
5. 只有确认不会影响其他系统时，才重置 AppSecret。

不要把 AppSecret 发到聊天、截图、Issue 或 Git 中。它只应写入本机仓库根目录的 `.env`。

如果 `.env` 已包含 THUFootball 配置，只追加下面两行，不要覆盖原文件：

```dotenv
WECHAT_APP_ID=这里填写AppID
WECHAT_APP_SECRET=这里填写AppSecret
```

`.gitignore` 应排除 `.env`；可以额外检查：

```powershell
git check-ignore .env
```

### 4.2 配置 IP 白名单

公众号接口会检查发起请求的公网出口 IP。在准备运行脚本的同一台电脑或服务器上查看出口 IP：

```powershell
(Invoke-RestMethod "https://api.ipify.org").Trim()
```

把结果加入公众号后台同一开发配置页面的 IP 白名单。

注意：家庭宽带、校园网、手机热点和部分代理的公网 IP 会变化。若今天能用、之后出现 `40164`，先重新检查出口 IP。正式自动化建议使用具有固定出口 IP 的服务器。

如果使用 VPN/代理，不要只依赖普通“我的 IP”网站。使用与草稿写入完全相同的运行路径询问微信：

```powershell
python tools/wechat_preview.py network-check
```

该命令默认只调用微信且不写入任何外部状态。微信返回 `40164` 时，结果中的
`wechat.observed_source_ip` 和 `whitelist_candidate` 是微信实际看到的出口 IP，应优先于第三方网站结果。

只有需要额外交叉验证，并接受向三个公网 IP 服务发起请求时，才运行：

```powershell
python tools/wechat_preview.py network-check --cross-check
```

它会通过与草稿工具相同的 Python HTTP 客户端和代理环境访问 Cloudflare、AWS 与 ipify。
若 `cross_check.consistent` 为 `false`，说明存在分流或出口不稳定，不要据此修改白名单；以微信报告值或固定出口服务器为准。

## 5. 只读验证凭据和草稿权限

先执行只读探针：

```powershell
python tools/wechat_preview.py auth-probe
```

它只会：

1. 使用 AppID/AppSecret 获取 access token；
2. 调用草稿数量查询接口；
3. 对输出中的 token 做隐藏处理。

它不会上传图片、创建草稿、修改草稿或发布文章。

期望输出：

```json
{
  "status": "ok",
  "credential": "accepted",
  "draft_permission": "accepted",
  "draft_count": 12,
  "token": "<redacted>"
}
```

只有这一步成功后才继续。常见错误见第 8 节。

## 6. 测试写入草稿箱

这一步会产生两类真实副作用：

- 封面会上传到公众号永久素材；
- 会新增一篇草稿。

不会发表或群发文章。

### 6.1 准备最小测试封面

选择一张“清华绿茵”有权使用的 JPG、PNG 或 GIF 图片，建议使用现有公众号素材或专门制作的测试封面。把它保存为：

```text
tmp/wechat-test-cover.png
```

测试文章标题务必保留 `【自动化测试】` 前缀，便于在草稿箱中识别和手工删除。

### 6.2 先 dry-run（不会联网写入）

先用仓库样例排除模板问题：

```powershell
python tools/wechat_preview.py create-draft templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --version qhly-preview-v1 `
  --cover tmp/wechat-test-cover.png `
  --digest "【自动化测试】公众号草稿接口连通性测试"
```

因为没有 `--execute`，预期状态为 `dry-run`，并生成：

```text
tmp/wechat_draft_preview.html
```

打开并最后检查标题、正文和排版：

```powershell
Start-Process (Resolve-Path tmp/wechat_draft_preview.html)
```

### 6.3 显式执行真实草稿写入

确认 `auth-probe` 成功、封面正确、本地预览正确后，运行：

```powershell
python tools/wechat_preview.py create-draft templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --version qhly-preview-v1 `
  --cover tmp/wechat-test-cover.png `
  --digest "【自动化测试】公众号草稿接口连通性测试" `
  --execute
```

只有最后的 `--execute` 会授权真实外部写入。成功输出类似：

```json
{
  "status": "ok",
  "draft_media_id": "MEDIA_ID_FROM_WECHAT",
  "content_fingerprint": "...",
  "created_at": "2026-07-14T...+08:00"
}
```

立即复制保存 `draft_media_id`。然后登录公众号后台：

1. 打开“内容与互动”下的“草稿箱”（后台菜单名称可能略有变化）；
2. 找到标题带 `【自动化测试】` 的文章；
3. 打开草稿，核对标题、作者、摘要、封面、正文和图片；
4. 确认后手工删除测试草稿；
5. 如不再需要测试封面，也在素材库中手工删除。

出现 `status: ok` 且后台能看到内容正确的测试草稿，即完成能力 6 的最小验收。

## 7. 使用正式 V1 做端到端测试

本地预览和接口探针成功后，继续使用仓库唯一的正式 V1：

```powershell
python tools/wechat_preview.py create-draft templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --version qhly-preview-v1 `
  --cover tmp/wechat-test-cover.png `
  --author "清华绿茵" `
  --digest "【自动化测试】真实模板端到端验证" `
  --source-url $ArticleUrl
```

先打开 `tmp/wechat_draft_preview.html` 检查；确认后在完全相同的命令末尾加入：

```powershell
--execute
```

正文中的微信图片会先下载，再上传到公众号正文图片接口，随后将 HTML 中的地址替换为新地址。实现只允许预期的微信图片域名，遇到任意外部图片域名会停止并报错，避免把未知资源静默带入草稿。

## 8. 常见错误与处理

| 表现或错误码 | 含义 | 处理方法 |
| --- | --- | --- |
| `WechatConfigurationError` | 本机未读取到 AppID/AppSecret | 检查仓库根目录 `.env` 中的变量名和值，不要加多余空格 |
| `40013` | AppID 无效 | 确认使用的是“清华绿茵”公众号 AppID，而不是小程序或开放平台 AppID |
| `40125` | AppSecret 无效 | 确认密钥没有复制缺失；若刚重置，更新本机 `.env` |
| `40164` | 调用 IP 不在白名单 | 在运行脚本的机器上重新查询公网出口 IP，并更新后台白名单 |
| `48001` | 当前账号/接口没有所需权限 | 在后台查看接口权限；需要管理员或所有者处理账号认证或权限问题 |
| 文章提取提示访问受限 | 微信公开页触发访问验证或 URL 已失效 | 浏览器确认链接可打开，稍后重试；不要绕过验证码或批量抓取 |
| 模板提示缺少字段 | JSON 与模板占位符不一致 | 按错误信息补字段，或删除不再需要的占位符 |
| 图片域名不允许 | 正文引用了非微信外部图片 | 下载并确认授权后改为本地/受支持素材，再执行上传 |
| 草稿成功但排版有差异 | 微信编辑器会对部分 HTML/CSS 再规范化 | 以后台草稿预览为最终准绳，调整模板的内联样式 |

接口返回的 `errcode` 和 `errmsg` 会被保留在错误 JSON 中；日志不会打印 AppSecret 或完整 access token。

## 9. 最小验收清单

- [ ] 从一篇“清华绿茵”已发表文章生成了 `source.html`、`body.html`、`source.json`；
- [ ] 本地预览与原文章的主要正文结构、排版和图片位置一致；
- [ ] 唯一的 `qhly_preview_v1` 模板已包含显式占位符并通过回归测试；
- [ ] 使用 JSON 数据成功渲染出本地文章 HTML；
- [ ] AppSecret 只保存在本机 `.env`，未进入 Git 或聊天；
- [ ] 当前运行机器的公网出口 IP 已加入白名单；
- [ ] `auth-probe` 返回 `credential: accepted` 和 `draft_permission: accepted`；
- [ ] dry-run 本地预览正确；
- [ ] 加 `--execute` 后得到 `draft_media_id`；
- [ ] 后台草稿箱中能看到并正确预览 `【自动化测试】` 草稿；
- [ ] 测试草稿和不再需要的测试素材已手工清理。

## 10. 相关官方入口

- [微信公众平台后台](https://mp.weixin.qq.com)
- [微信开发者文档：草稿管理](https://developers.weixin.qq.com/doc/service/guide/product/draft.html)
