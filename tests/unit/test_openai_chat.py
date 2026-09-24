import json
from types import SimpleNamespace
import unittest
import httpx
from agent.providers.openai_chat import OpenAIChatProvider


class HostedProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_model_seed_and_json_mode(self):
        def respond(request):
            body = json.loads(request.content)
            self.assertEqual(body["model"], "declared-model")
            self.assertEqual(body["seed"], 17)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"final_response":"Hello"}'}}]})
        async with httpx.AsyncClient(base_url="https://unit.test", transport=httpx.MockTransport(respond)) as client:
            provider = OpenAIChatProvider("declared-model", "test-only", seed=17, client=client)
            result = await provider.plan(SimpleNamespace(model_dump_json=lambda: "{}"))
            self.assertEqual(json.loads(result)["final_response"], "Hello")

    async def test_refusal_fails_safely(self):
        async with httpx.AsyncClient(base_url="https://unit.test", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"refusal": "refused"}}]}))) as client:
            provider = OpenAIChatProvider("declared-model", "test-only", client=client)
            with self.assertRaisesRegex(ValueError, "no usable proposal"):
                await provider.plan(SimpleNamespace(model_dump_json=lambda: "{}"))
