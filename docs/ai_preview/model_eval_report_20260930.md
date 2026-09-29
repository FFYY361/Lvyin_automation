# 1.1 候选模型评测报告（2026-09-30）

## 测试范围

使用固定正文 Prompt、固定资料快照和临时模型配置，先对 7 个候选进行 4049 烟雾测试，再对通过者执行 4049/4076/4011 高风险测试，最终对 4 个通过者执行固定 8 场集：4237、4246、4245、4065、4024、4049、4076、4011。没有修改 `src/ai_service/config.json`、默认模型或网站白名单。

## 结果

| 候选 | 烟雾 | 高风险 | 8 场 | 平均耗时 | 平均总 Token | 结论 |
|---|---:|---:|---:|---:|---:|---|
| `deepseek_v41_flash_thinking` / `deepseek-flash` | 1/1 | 3/3 | 7/8（4011 网络错误） | 28.1s（成功样本） | 18,890 | 质量候选；先解决稳定性并重跑 4011 |
| `qwen38_max_0902_thinking` / `qwen3.8-max-0902` | 1/1 | 3/3 | 8/8 | 154.1s | 21,850 | 质量上限候选；延迟和成本最高 |
| `qwen38_flash_thinking` / `qwen3.8-flash` | 1/1 | 3/3 | 8/8 | 86.3s | 20,301 | 综合候选；需人工评分确认质量 |
| `glm5_turbo_thinking` / `glm-5-turbo` | 1/1 | 3/3 | 8/8 | 86.7s | 16,111 | 综合候选；Token 较低，需人工评分确认事实约束 |
| `mimo_v25_pro_thinking` / `mimo-v2.5-pro` | 0/1（HTTP 400） | 未进入 | 未进入 | — | — | 淘汰/待确认正确的百炼模型 ID 或接入方式 |
| `minimax_m3_thinking` / `MiniMax-M3` | 未调用 | 未进入 | 未进入 | — | — | 缺少 `AI_SERVICE_MINIMAX_API_KEY` |
| `mistral_medium_35` / `mistral-medium-latest` | 未调用 | 未进入 | 未进入 | — | — | 缺少 `AI_SERVICE_MISTRAL_API_KEY` |

所有成功样本均返回非空正文，`finish_reason=stop`，未出现截断。自动检查只覆盖接口成功、非空和输出长度；事实准确性、匿名规则、文采和结构仍需人工按五项评分表复核。

## 建议

1. 暂不把任何新模型写入正式配置。优先人工盲评 Qwen 3.8 Flash 与 GLM-5 Turbo，二者延迟接近；若质量达标，再作为低风险候选。
2. Qwen 3.8 Max 保留为高质量备选，只有在质量显著领先时才值得接受约 154 秒平均延迟。
3. DeepSeek Flash 继续作为基线候选，但必须重跑 4011 并确认网络错误不是持续性问题。
4. MiMo 先核对百炼实际可用模型 ID；MiniMax、Mistral 补充密钥后再测试。
5. 标题 AI 的 6 候选、四字词、重复和姓名命中测试应在正文盲评结束后单独执行，不能用正文通过替代标题验收。

## 原始材料

- `data/ai_preview/runs/candidate_smoke_20260930/manifest.json`
- `data/ai_preview/runs/candidate_highrisk_20260930/manifest.json`
- `data/ai_preview/runs/candidate_full_deepseek_20260930/manifest.json`
- `data/ai_preview/runs/candidate_full_qwenmax_20260930/manifest.json`
- `data/ai_preview/runs/candidate_full_qwenflash_20260930/manifest.json`
- `data/ai_preview/runs/candidate_full_glm5_20260930/manifest.json`

## DeepSeek 重跑

4011 于 2026-09-30 重跑成功，模型 `deepseek-flash`，耗时 33.372 秒，返回完整正文，`finish_reason=stop`。此前 8 场中的网络错误暂按偶发网络故障处理，但正式上线前仍建议增加一次重试策略并观察稳定性。

## 四个可用模型人工初评

按事实准确性 30、结构完整性 20、匿名与规则遵循 20、文采与贴合度 20、可发布性 10 初评：

| 模型 | 分数 | 主要判断 |
|---|---:|---|
| `qwen3.8-max-0902` | 91/100 | 事实覆盖最完整，攻守对位清楚，三段结构稳定；代价是平均 154 秒，内容略偏长。 |
| `deepseek-flash` | 90/100 | 事实表达最稳，少做无依据扩写，重跑 4011 成功；文采略收敛，仍需观察偶发网络错误。 |
| `qwen3.8-flash` | 88/100 | 结构和可读性较好，速度明显优于 Max；部分判断略模板化，个别历史信息需要人工核对。 |
| `glm-5-turbo` | 82/100 | 语言表现有冲击力，但出现“交叉信息尚未破门的防线”等语义错误，把零失球误写成未破门；“复仇与正名”等表达也需要编辑把关。 |

这四个分数是基于固定 8 场输出的人工初评，不是最终上线评分；最终评分仍需两人盲评和事实逐项核对。

## 暂不可用模型的接入方式

### MiMo

本次 HTTP 400 的原因是把 MiMo 请求发到了百炼兼容端点。MiMo 官方使用独立服务：

- Base URL：`https://api.xiaomimimo.com/v1`
- API Key：MiMo 控制台生成，环境变量建议 `MIMO_API_KEY`
- 请求：标准 OpenAI Chat Completions
- 当前建议模型：`mimo-v2.6-pro`；官方列出的旧 `mimo-v2.5-pro` 将于 2026-10-21 下线，暂不建议新接入。
- 思考参数：`extra_body: {"thinking": {"type": "enabled"}}`

官方文档给出的模型列表和端点见 [MiMo 模型列表](https://mimo.mi.com/docs/en-US/api/model/list-models)、[MiMo Chat API](https://mimo.mi.com/docs/en-US/api/chat)。

### MiniMax

MiniMax 官方控制台示例使用：

- API Key：MiniMax 开放平台或 Token Plan 生成，环境变量建议 `MINIMAX_API_KEY`
- 中国大陆文本端点：`https://api.minimaxi.com/v1/text/chatcompletion_v2`
- 模型：`MiniMax-M3`
- 鉴权：`Authorization: Bearer <key>`
- 请求字段：`messages`、`temperature`、`max_completion_tokens`、`stream`
- M3 思考模式按官方接口参数开启；需要先做单场连通性测试，不直接复用当前 OpenAI `/chat/completions` 假设。

官方控制台的可复制请求示例见 [MiniMax 文本生成控制台](https://solutions.minimax.cn/debug/text)。

### Mistral

Mistral 官方 OpenAI 兼容接口可以直接接入：

- API Key：Mistral 控制台生成，环境变量建议 `MISTRAL_API_KEY`
- Base URL：`https://api.mistral.ai/v1`
- 模型：`mistral-medium-latest`
- 请求：`POST /chat/completions`，标准 `messages`、`temperature`、`max_tokens`、`stream=false`
- 如需严格 JSON，可使用 `response_format: {"type": "json_object"}`；正文测试仍使用普通文本。

参考 [Mistral Chat Completion](https://docs.mistral.ai/studio/conversations/chat-completion) 和 [Mistral API 规范](https://docs.mistral.ai/api)。
