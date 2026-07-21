# 前瞻模板与渲染教程

`preview` 负责纯本地的前瞻三文件校验、数据合成和 HTML 渲染。它不会读取 `.env`、访问 THUFootball 或连接微信公众号。

## 输入

正式模板位于 `templates/qhly_preview_v1/template.html`，示例 source、weather、config 和 JSON Schema 位于同一目录。

三份 JSON 使用严格解码：缺失必填字段、未知字段、非法日期、非 `+08:00` 开球时间、不完整比分和非法比赛 ID 都会在渲染前报错。`qhly_preview_v1` 模板的 source 由同目录 `schema.json` 唯一定义；`previews` 以 `主队简称 vs 客队简称` 为键，并使用 `article_file` 引用同目录下的正文 Markdown，例如 `previews/集电vs美院.md`。正文可以直接粘贴，以空白行分段，模板负责生成 HTML 标签和转义内容。

模板支持：

- `{{path.to.value}}`：输出并转义标量；
- `{{path|filter}}`：使用受支持的有限格式化器；
- `<!-- wx:each ... -->`：遍历数组；
- `<!-- wx:empty -->`：空数组回退内容。

不支持三花括号、任意表达式、动态函数调用或 source 中的 HTML。

## CLI 渲染

本地封面：

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --weather templates/qhly_preview_v1/example_weather.json `
  --config templates/qhly_preview_v1/example_config.json `
  --cover path/to/cover.png `
  --author "清华绿茵" `
  --digest "本期比赛前瞻" `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article
```

复用已有永久封面素材时，把 `--cover` 替换为：

```powershell
--cover-media-id MEDIA_ID
```

两个参数互斥且必须选择一个。整个命令只进行本地读写。

## Python 渲染

```python
from pathlib import Path

from preview import PreviewService, load_preview_bundle
from wechat_official import Article, CoverFile

source = load_preview_bundle(
    "templates/qhly_preview_v1/example_data.json",
    "templates/qhly_preview_v1/example_weather.json",
    "templates/qhly_preview_v1/example_config.json",
)
service = PreviewService.from_template(
    "templates/qhly_preview_v1/template.html",
    version="qhly-preview-v1",
)
article = service.render(
    source,
    cover=CoverFile(Path("path/to/cover.png")),
    author="清华绿茵",
    digest="本期比赛前瞻",
)
assert isinstance(article, Article)
article.save("tmp/qhly_preview_v1/article")
```

## 文章目录

`Article.save()` 把内存中的 `body_html` 写入独立的 `body.html`，其他字段写入 UTF-8 的 `article.json`。本地封面会复制进目录；已有素材只记录 `media_id`。

`Article.load()` 会拒绝未知字段、路径越界、缺失正文或封面、未知协议版本和内容指纹不一致。可以直接在浏览器打开 `body.html` 检查正文结构，但微信公众号后台预览仍是最终排版依据。
