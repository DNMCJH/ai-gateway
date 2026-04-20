import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.providers.base import ProviderBase
from app.config import settings
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChunk,
    Choice,
    ChoiceMessage,
    Usage,
    ChunkChoice,
    ChunkDelta,
)

ANTHROPIC_MODELS = [
    {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "owned_by": "anthropic"},
    {"id": "claude-haiku-3-5-20241022", "name": "Claude 3.5 Haiku", "owned_by": "anthropic"},
]

ANTHROPIC_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
}

FINISH_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


class AnthropicProvider(ProviderBase):
    name = "anthropic"
    display_name = "Anthropic (Claude)"

    def __init__(self):
        self.base_url = settings.anthropic_base_url.rstrip("/")
        self.api_key = settings.anthropic_api_key
        self.client = httpx.AsyncClient(timeout=60.0)

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        system = None
        messages = []
        for m in request.messages:
            if m.role == "system":
                system = m.content
            else:
                messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }
        if system:
            payload["system"] = system
        return payload

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        url = f"{self.base_url}/v1/messages"
        payload = self._build_payload(request)

        resp = await self.client.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()

        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage_data = data.get("usage", {})
        return ChatCompletionResponse(
            id=f"chatcmpl-{data.get('id', uuid.uuid4().hex[:8])}",
            created=int(time.time()),
            model=data.get("model", request.model),
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(role="assistant", content=content),
                    finish_reason=FINISH_REASON_MAP.get(data.get("stop_reason"), "stop"),
                )
            ],
            usage=Usage(
                prompt_tokens=usage_data.get("input_tokens", 0),
                completion_tokens=usage_data.get("output_tokens", 0),
                total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
            ),
        )

    async def chat_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self.base_url}/v1/messages"
        payload = self._build_payload(request)
        payload["stream"] = True

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        async with self.client.stream(
            "POST", url, json=payload, headers=self._headers()
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                event_type = data.get("type")

                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield ChatCompletionChunk(
                            id=completion_id,
                            created=created,
                            model=request.model,
                            choices=[
                                ChunkChoice(
                                    index=0,
                                    delta=ChunkDelta(content=delta.get("text", "")),
                                )
                            ],
                        )
                elif event_type == "message_stop":
                    yield ChatCompletionChunk(
                        id=completion_id,
                        created=created,
                        model=request.model,
                        choices=[
                            ChunkChoice(
                                index=0,
                                delta=ChunkDelta(),
                                finish_reason="stop",
                            )
                        ],
                    )

    def list_models(self) -> list[dict]:
        return ANTHROPIC_MODELS

    def get_cost_per_token(self, model: str) -> dict:
        return ANTHROPIC_PRICING.get(model, {"input": 0.0, "output": 0.0})

    async def is_available(self) -> bool:
        return bool(self.api_key)
