from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from ai_service import (
    AIChatService,
    AIServiceAuthenticationError,
    ChatMessage,
    ChatResult,
    TokenUsage,
    load_ai_service_config,
)


class AIServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_openai_compatible_request_and_maps_result(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "model": "qwen-test-revision",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "生成结果"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 4,
                        "total_tokens": 16,
                    },
                },
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with patch.dict(
                os.environ, {"AI_SERVICE_QWEN_API_KEY": "test-secret"}, clear=True
            ):
                service = AIChatService.from_config(http_client=http_client)
                result = await service.chat(
                    [ChatMessage("system", "按要求回答"), ChatMessage("user", "你好")]
                )
        finally:
            await http_client.aclose()

        self.assertEqual(
            result,
            ChatResult(
                content="生成结果",
                profile="qwen",
                model="qwen-test-revision",
                finish_reason="stop",
                usage=TokenUsage(12, 4, 16),
            ),
        )
        self.assertEqual(
            str(requests[0].url),
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-secret")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], "qwen3.5-35b-a3b")
        self.assertEqual(payload["messages"][1]["content"], "你好")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["enable_thinking"])

    async def test_classifies_authentication_failure_without_exposing_key(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with patch.dict(
                os.environ, {"AI_SERVICE_DEEPSEEK_API_KEY": "private-key"}, clear=True
            ):
                service = AIChatService.from_config(
                    "deepseek", http_client=http_client
                )
                with self.assertRaises(AIServiceAuthenticationError) as caught:
                    await service.chat([ChatMessage("user", "test")])
        finally:
            await http_client.aclose()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn("private-key", str(caught.exception))
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_config_defines_selectable_profiles_without_secrets(self) -> None:
        config = load_ai_service_config()
        self.assertEqual(config.default_profile, "qwen")
        self.assertEqual(
            set(config.profiles),
            {
                "qwen",
                "deepseek",
                "deepseek_v4_flash_thinking",
                "qwen38_thinking",
                "deepseek_v4_pro_thinking",
                "glm",
                "doubao_turbo",
                "doubao_pro",
                "kimi_k2_6",
                "kimi_k2_6_no_thinking",
                "kimi_k3",
            },
        )
        self.assertEqual(
            config.get_profile("glm").api_key_env, "AI_SERVICE_GLM_API_KEY"
        )
        self.assertEqual(
            config.get_profile("deepseek_v4_pro_thinking").request_options,
            {"thinking": {"type": "enabled"}},
        )
        self.assertEqual(
            config.get_profile("doubao_turbo").request_options,
            {"thinking": {"type": "enabled"}},
        )
        self.assertEqual(
            config.get_profile("kimi_k3").request_options,
            {"reasoning_effort": "max"},
        )
        self.assertEqual(
            config.get_profile("kimi_k2_6_no_thinking").request_options,
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(
            config.get_profile("kimi_k3").api_key_env,
            "AI_SERVICE_QWEN_API_KEY",
        )
        self.assertNotIn("api_key", config.get_profile("glm").__dict__)


if __name__ == "__main__":
    unittest.main()
