import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.core.key_pool import KeyPool
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
    ToolCall,
    FunctionCall,
)

EMPTY_DELTA = ChunkDelta()

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
        keys = [k.strip() for k in (settings.anthropic_api_key or "").split(",") if k.strip()]
        self.key_pool = KeyPool(keys) if keys else None
        self.api_key = keys[0] if keys else ""
        self.client = httpx.AsyncClient(timeout=60.0)

    def _next_key(self) -> str:
        if self.key_pool:
            return self.key_pool.next_key()
        return self.api_key

    def _headers(self, api_key: str) -> dict:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _maybe_disable_key(self, key: str, status_code: int):
        if self.key_pool and status_code in (401, 403, 429):
            self.key_pool.disable_key(key, seconds=60)

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        system = None
        messages = []
        for m in request.messages:
            if m.role == "system":
                system = m.content
            elif m.role == "tool":
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or ""}],
                })
            elif m.role == "assistant" and m.tool_calls:
                content = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments,
                    })
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": m.role, "content": m.content or ""})

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {"name": t.function.name, "description": t.function.description, "input_schema": t.function.parameters}
                for t in request.tools
            ]
        if request.tool_choice is not None:
            tc = request.tool_choice
            if tc == "auto":
                payload["tool_choice"] = {"type": "auto"}
            elif tc == "none":
                payload["tool_choice"] = {"type": "none"}
            elif isinstance(tc, dict) and tc.get("function"):
                payload["tool_choice"] = {"type": "tool", "name": tc["function"]["name"]}
        return payload

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        url = f"{self.base_url}/v1/messages"
        payload = self._build_payload(request)

        key = self._next_key()
        try:
            resp = await self.client.post(url, json=payload, headers=self._headers(key))
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._maybe_disable_key(key, e.response.status_code)
            raise
        data = resp.json()

        content = ""
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        type="function",
                        function=FunctionCall(
                            name=block["name"],
                            arguments=json.dumps(block.get("input", {})),
                        ),
                    )
                )

        usage_data = data.get("usage", {})
        finish_reason = FINISH_REASON_MAP.get(data.get("stop_reason"), "stop")
        if data.get("stop_reason") == "tool_use":
            finish_reason = "tool_calls"

        return ChatCompletionResponse(
            id=f"chatcmpl-{data.get('id', uuid.uuid4().hex[:8])}",
            created=int(time.time()),
            model=data.get("model", request.model),
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(
                        role="assistant",
                        content=content or None,
                        tool_calls=tool_calls or None,
                    ),
                    finish_reason=finish_reason,
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
        input_tokens = 0
        output_tokens = 0
        finish_reason = "stop"
        tool_call_index = -1
        current_tool_id = None
        current_tool_name = None
        current_tool_input = ""

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
                data = json.loads(line[6:])
                event_type = data.get("type")

                if event_type == "message_start":
                    usage = data.get("message", {}).get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                elif event_type == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        tool_call_index += 1
                        current_tool_id = block.get("id", "")
                        current_tool_name = block.get("name", "")
                        current_tool_input = ""
                        yield ChatCompletionChunk(
                            id=completion_id, created=created, model=request.model,
                            choices=[ChunkChoice(index=0, delta=ChunkDelta(
                                tool_calls=[{"index": tool_call_index, "id": current_tool_id, "type": "function", "function": {"name": current_tool_name, "arguments": ""}}]
                            ))],
                        )
                elif event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield ChatCompletionChunk(
                            id=completion_id, created=created, model=request.model,
                            choices=[ChunkChoice(index=0, delta=ChunkDelta(content=delta.get("text", "")))],
                        )
                    elif delta.get("type") == "input_json_delta":
                        partial = delta.get("partial_json", "")
                        yield ChatCompletionChunk(
                            id=completion_id, created=created, model=request.model,
                            choices=[ChunkChoice(index=0, delta=ChunkDelta(
                                tool_calls=[{"index": tool_call_index, "function": {"arguments": partial}}]
                            ))],
                        )
                elif event_type == "message_delta":
                    delta = data.get("delta", {})
                    stop_reason = delta.get("stop_reason")
                    if stop_reason:
                        finish_reason = "tool_calls" if stop_reason == "tool_use" else FINISH_REASON_MAP.get(stop_reason, "stop")
                    usage = data.get("usage", {})
                    if "output_tokens" in usage:
                        output_tokens = usage["output_tokens"]
                elif event_type == "message_stop":
                    yield ChatCompletionChunk(
                        id=completion_id, created=created, model=request.model,
                        choices=[ChunkChoice(index=0, delta=EMPTY_DELTA, finish_reason=finish_reason)],
                        usage=Usage(prompt_tokens=input_tokens, completion_tokens=output_tokens, total_tokens=input_tokens + output_tokens),
                    )

    def list_models(self) -> list[dict]:
        return ANTHROPIC_MODELS

    def get_cost_per_token(self, model: str) -> dict:
        return ANTHROPIC_PRICING.get(model, {"input": 0.0, "output": 0.0})

    async def is_available(self) -> bool:
        return bool(self.api_key)
