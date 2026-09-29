# 1.1 模型候选队列

本清单只记录待评测模型，不代表已经上线。候选模型必须先通过临时配置、API 烟雾测试、
高风险比赛筛选和完整 8 场评分，再由管理员明确确认后，才能写入正式模型配置。

## P0

| 临时 profile | 模型 ID | 当前凭据 | 备注 |
| --- | --- | --- | --- |
| `deepseek_v41_flash_thinking` | `deepseek-flash` | `AI_SERVICE_DEEPSEEK_API_KEY` | DeepSeek 新 Flash 标识；旧 `deepseek-v4-flash` 已路由到该系列，需重新建立独立基线。 |
| `qwen38_max_0902_thinking` | `qwen3.8-max-0902` | `AI_SERVICE_QWEN_API_KEY` | Qwen3.8 Max 快照，优先比较质量上限。 |
| `qwen38_flash_thinking` | `qwen3.8-flash` | `AI_SERVICE_QWEN_API_KEY` | Qwen3.8 效率型候选，比较速度和成本。 |

## P1

| 临时 profile | 模型 ID | 当前凭据 | 备注 |
| --- | --- | --- | --- |
| `mimo_v25_pro_thinking` | `mimo-v2.5-pro` | `AI_SERVICE_QWEN_API_KEY` | 先确认百炼第三方模型权限和接口兼容性。 |
| `glm5_turbo_thinking` | `glm-5-turbo` | `AI_SERVICE_GLM_API_KEY` | 只测过 GLM-5.2，需验证事实边界和中文写作。 |
| `minimax_m3_thinking` | 新增 MiniMax API Key | `MiniMax-M3` | 需要单独凭据和 OpenAI 兼容接口烟雾测试。 |

## P2

| 临时 profile | 模型 ID | 当前凭据 | 备注 |
| --- | --- | --- | --- |
| `mistral_medium_35` | `mistral-medium-latest` | 新增 Mistral API Key | 作为国际模型参照组，暂不作为首轮上线目标。 |

## 测试顺序

1. 单场 4049 烟雾测试，确认模型 ID、思考参数、非流式响应、超时和 Token 统计。
2. 测试 4049、4076、4011 三场高风险样本；出现关键事实红线即淘汰。
3. 对通过者运行固定 8 场完整评分。
4. 对入围模型测试标题 Prompt 的 6 条 JSON 候选输出。
5. 将原始输出、耗时、Token、评分和红线整理为报告，等待人工确认。

候选 profile 不应直接写入 `src/ai_service/config.json`，也不应加入网站的正式模型白名单。
