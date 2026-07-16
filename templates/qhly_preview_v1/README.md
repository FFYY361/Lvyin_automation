# qhly_preview_v1 模板

该目录包含仓库当前正式前瞻模板：

- `template.html`：正文模板；
- `schema.json`：前瞻 data 契约；
- `example_data.json`：男足完整示例；
- `example_data_women_saturday.json`：女足最小示例；
- `example_data_futsal_saturday.json`：五人制最小示例。

渲染示例：

```powershell
preview render templates/qhly_preview_v1/template.html `
  --source templates/qhly_preview_v1/example_data.json `
  --cover path/to/cover.png `
  --version qhly-preview-v1 `
  --output tmp/qhly_preview_v1/article
```

data 必须符合 `schema.json`，未知字段会被拒绝。前瞻文案使用纯文本数组，不允许在 data 中注入 HTML。模板语法、字段校验和文章目录格式见 [前瞻模板与渲染教程](../../docs/preview/preview_template_tutorial.md)。
