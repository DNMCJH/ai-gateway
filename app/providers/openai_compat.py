import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.core.key_pool import KeyPool
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

    # Subclasses set False to opt out of sending stream_options.include_usage
    # (some compat backends 400 on unknown fields).
    supports_stream_usage: bool = True

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        keys = [k.strip() for k in (api_key or "").split(",") if k.strip()]
        self.key_pool = KeyPool(keys) if keys else None
        self.api_key = keys[0] if keys else ""
        self.client = httpx.AsyncClient(timeout=60.0)

    def _next_key(self) -> str:
        if self.key_pool:
            return self.key_pool.next_key()
        return self.api_key

    def _headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _maybe_disable_key(self, key: str, status_code: int):
        if self.key_pool and status_code in (401, 403, 429):
            self.key_pool.disable_key(key, seconds=60)

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

        key = self._next_key()
        try:
            resp = await self.client.post(url, json=payload, headers=self._headers(key))
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._maybe_disable_key(key, e.response.status_code)
            raise
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
        if self.supports_stream_usage:
            payload["stream_options"] = {"include_usage": True}

        key = self._next_key()
        async with self.client.stream(
            "POST", url, json=payload, headers=self._headers(key)
        ) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                self._maybe_disable_key(key, e.response.status_code)
                raise
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

                usage = None
                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage = Usage(
                        prompt_tokens=u.get("prompt_tokens", 0),
                        completion_tokens=u.get("completion_tokens", 0),
                        total_tokens=u.get("total_tokens", 0),
                    )

                yield ChatCompletionChunk(
                    id=chunk.get("id", f"chatcmpl-{uuid.uuid4().hex[:8]}"),
                    created=chunk.get("created", int(time.time())),
                    model=chunk.get("model", request.model),
                    choices=choices,
                    usage=usage,
                )

    async def is_available(self) -> bool:
        return bool(self.api_key)
