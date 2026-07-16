# 微信公众号草稿教程

`wechat_official` 接收一篇完整 `Article`，处理正文图片和封面，然后调用微信公众号草稿 API。它不读取前瞻模板或比赛 data。

## 配置

在仓库根目录 `.env` 中配置：

```dotenv
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

不要把 AppSecret 写入命令、聊天、Issue 或 Git。运行机器的公网出口 IP 必须加入公众号后台白名单，账号还需要永久素材、正文图片和草稿箱接口权限。

只读验证：

```powershell
wechat-official auth-probe
wechat-official network-check
```

`network-check --cross-check` 会额外访问公网 IP 服务，只有明确需要交叉核对时再使用。

## Article

`Article` 包含标题、正文 HTML、封面、作者、摘要和“阅读原文”链接。封面必须使用一种形式：

- `CoverFile(path)`：提交时上传本地图片作为永久封面素材；
- `CoverMediaId(media_id)`：复用公众号素材库中的永久图片。

评论不是文章内容，由 `create_draft()` 参数控制，默认关闭。

## CLI 创建草稿

先运行 dry-run：

```powershell
wechat-official create-draft tmp/qhly_preview_v1/article
```

它只调用 `Article.load()` 完成本地校验，不获取 access token、不上传素材、不创建草稿。

确认后执行：

```powershell
wechat-official create-draft tmp/qhly_preview_v1/article --execute
```

需要开放评论时添加 `--open-comments`；`--fans-only-comments` 必须和它一起使用。

## Python 调用

```python
import asyncio

from wechat_official import Article, WechatOfficialService


async def main() -> None:
    article = Article.load("tmp/qhly_preview_v1/article")
    async with WechatOfficialService.from_environment() as service:
        receipt = await service.create_draft(article)
    print(receipt.media_id)


asyncio.run(main())
```

服务会先上传正文图片并替换 HTML 地址，再上传或复用封面，最后新增草稿。`DraftReceipt.content_fingerprint` 与输入 Article 保持一致。

## 图片限制与排错

正文图片当前支持：

- 允许域名中的 HTTPS 图片；
- `data:image/...;base64,...` 内联图片。

不支持本地正文图片路径。封面本地文件支持 JPEG、PNG 和 GIF，大小限制由客户端配置和微信接口共同决定。

常见错误：

| 错误 | 处理 |
| --- | --- |
| `WechatConfigurationError` | 检查 `.env` 变量名和值 |
| `40125` | AppSecret 无效或已重置 |
| `40164` | 把微信报告的出口 IP 加入白名单 |
| `48001` | 当前公众号或账号没有所需接口权限 |
| `MediaUploadError` | 检查图片类型、大小、来源域名和封面文件 |
| `DraftValidationError` | 检查 Article 字段、文章目录和评论参数 |

接口失败不会自动发布或群发内容。草稿创建成功后仍应登录公众号后台检查标题、封面、正文图片和最终排版。
