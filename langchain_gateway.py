"""LangChain ChatModel integration for AI Gateway.

Usage:
    from langchain_gateway import ChatGateway

    llm = ChatGateway(
        gateway_url="http://localhost:8000",
        api_key="sk-your-gateway-key",
        model="auto",  # or "best", "cheapest", or specific model id
    )

    # Standard LangChain usage
    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content="Hello!")])

    # With tools
    from langchain_core.tools import tool

    @tool
    def get_weather(city: str) -> str:
        '''Get weather for a city.'''
        return f"Sunny in {city}"

    llm_with_tools = llm.bind_tools([get_weather])
    response = llm_with_tools.invoke([HumanMessage(content="What's the weather in Beijing?")])

    # Streaming
    for chunk in llm.stream([HumanMessage(content="Tell me a story")]):
        print(chunk.content, end="")
"""

from typing import Any, Iterator, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall as LCToolCall
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

import httpx


def _message_to_dict(msg: BaseMessage) -> dict:
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    elif isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    elif isinstance(msg, AIMessage):
        d = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["args"] if isinstance(tc["args"], str) else __import__("json").dumps(tc["args"])},
                }
                for tc in msg.tool_calls
            ]
        return d
    elif isinstance(msg, ToolMessage):
        return {"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id}
    return {"role": "user", "content": str(msg.content)}


class ChatGateway(BaseChatModel):
    """LangChain ChatModel that routes through AI Gateway."""

    gateway_url: str = "http://localhost:8000"
    api_key: str = ""
    model: str = "auto"
    temperature: float = 1.0
    max_tokens: Optional[int] = None
    timeout: float = 60.0

    _client: Any = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "ai-gateway"

    @property
    def _identifying_params(self) -> dict:
        return {"gateway_url": self.gateway_url, "model": self.model}

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _build_payload(self, messages: List[BaseMessage], **kwargs) -> dict:
        payload = {
            "model": self.model,
            "messages": [_message_to_dict(m) for m in messages],
            "temperature": self.temperature,
            "stream": False,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        if kwargs.get("tool_choice") is not None:
            payload["tool_choice"] = kwargs["tool_choice"]
        return payload

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> ChatResult:
        payload = self._build_payload(messages, **kwargs)
        client = self._get_client()
        resp = client.post(
            f"{self.gateway_url}/v1/chat/completions",
            json=payload,
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]

        tool_calls = []
        if msg.get("tool_calls"):
            import json as _json
            for tc in msg["tool_calls"]:
                args = tc["function"]["arguments"]
                tool_calls.append(LCToolCall(
                    name=tc["function"]["name"],
                    args=_json.loads(args) if isinstance(args, str) else args,
                    id=tc["id"],
                ))

        ai_msg = AIMessage(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
        )

        usage = data.get("usage", {})
        return ChatResult(
            generations=[ChatGeneration(message=ai_msg)],
            llm_output={
                "token_usage": usage,
                "model": data.get("model"),
            },
        )

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> Iterator[ChatGenerationChunk]:
        payload = self._build_payload(messages, **kwargs)
        payload["stream"] = True
        client = self._get_client()

        with client.stream(
            "POST",
            f"{self.gateway_url}/v1/chat/completions",
            json=payload,
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                import json as _json
                chunk = _json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content") or ""
                if content:
                    msg_chunk = AIMessageChunk(content=content)
                    yield ChatGenerationChunk(message=msg_chunk)
                    if run_manager:
                        run_manager.on_llm_new_token(content)
