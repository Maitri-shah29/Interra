"""Optional hosted planner. JSON mode plus the runtime's strict Proposal validation.

Arbitrary manifest argument maps cannot use OpenAI's closed strict-schema subset.
JSON mode is deliberate; invalid proposals still fail safely at the planner boundary.
"""
import json
import httpx
from ..models import Proposal
from .ollama import SYSTEM


class OpenAIChatProvider:
    def __init__(self, model, api_key, seed=0, client=None):
        if not model or not api_key:
            raise ValueError("an explicit model and OPENAI_API_KEY are required")
        self.model, self.seed = model, seed
        self.client = client or httpx.AsyncClient(base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"}, timeout=60.)
        self.owns_client = client is None

    async def plan(self, context):
        response = await self.client.post("/chat/completions", json={
            "model": self.model, "temperature": 0, "seed": self.seed,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM +
                " Return JSON matching this schema: " + json.dumps(Proposal.model_json_schema())},
                {"role": "user", "content": context.model_dump_json()}]})
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        if message.get("refusal") or not message.get("content"):
            raise ValueError("model returned no usable proposal")
        return message["content"]

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()
