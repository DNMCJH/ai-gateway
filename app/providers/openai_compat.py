import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.providers.base import ProviderBase
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


class OpenAICompatibleProvider(ProviderBase):
    """Base for any provider with an OpenAI-compatible API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=60.0)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        payload = {
            "model": request.model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": request.stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        url = f"{self.base_url}/v1/chat/completions"
        payload = self._build_payload(request)
        payload["stream"] = False

        resp = await self.client.post(url, json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()

        return ChatCompletionResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:8]}"),
            created=data.get("created", int(time.time())),
            model=data.get("model", request.model),
            choices=[
                Choice(
                    index=c.get("index", 0),
                    message=ChoiceMessage(
                        role=c["message"]["role"],
                        content=c["message"].get("content", ""),
                    ),
                    finish_reason=c.get("finish_reason"),
                )
                for c in data.get("choices", [])
            ],
            usage=Usage(
                prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
                total_tokens=data.get("usage", {}).get("total_tokens", 0),
            ),
        )

    async def chat_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self.base_url}/v1/chat/completions"
        payload = self._build_payload(request)
        payload["stream"] = True

        async with self.client.stream(
            "POST", url, json=payload, headers=self._headers()
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                chunk = json.loads(data)
                choices = []
                for c in chunk.get("choices", []):
                    delta = c.get("delta", {})
                    choices.append(
                        ChunkChoice(
                            index=c.get("index", 0),
                            delta=ChunkDelta(
                                role=delta.get("role"),
                                content=delta.get("content"),
                            ),
                            finish_reason=c.get("finish_reason"),
                        )
                    )
                yield ChatCompletionChunk(
                    id=chunk.get("id", f"chatcmpl-{uuid.uuid4().hex[:8]}"),
                    created=chunk.get("created", int(time.time())),
                    model=chunk.get("model", request.model),
                    choices=choices,
                )

    async def is_available(self) -> bool:
        return bool(self.api_key)
