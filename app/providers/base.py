from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChunk,
)


class ProviderBase(ABC):
    name: str
    display_name: str

    @abstractmethod
    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        ...

    @abstractmethod
    async def chat_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        ...

    @abstractmethod
    def list_models(self) -> list[dict]:
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        ...

    def get_cost_per_token(self, model: str) -> dict:
        return {"input": 0.0, "output": 0.0}
