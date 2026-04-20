import asyncio
import logging
from typing import AsyncIterator, Optional

from app.providers.base import ProviderBase
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChunk,
)

logger = logging.getLogger(__name__)

FIRST_CHUNK_TIMEOUT = 10.0
MAX_RETRIES = 2


async def with_retry(
    provider: ProviderBase,
    request: ChatCompletionRequest,
    fallback_providers: Optional[list[ProviderBase]] = None,
) -> ChatCompletionResponse:
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await provider.chat(request)
        except Exception as e:
            last_error = e
            logger.warning(f"Retry {attempt + 1}/{MAX_RETRIES} for {provider.name}: {e}")
            await asyncio.sleep(0.5 * (attempt + 1))

    for fb in fallback_providers or []:
        try:
            logger.info(f"Falling back to {fb.name}")
            return await fb.chat(request)
        except Exception as e:
            logger.warning(f"Fallback {fb.name} failed: {e}")

    raise last_error or RuntimeError("All providers failed")


async def stream_with_fallback(
    provider: ProviderBase,
    request: ChatCompletionRequest,
    fallback_providers: Optional[list[ProviderBase]] = None,
) -> AsyncIterator[ChatCompletionChunk]:
    try:
        stream = provider.chat_stream(request)
        first_chunk = await asyncio.wait_for(
            stream.__anext__(), timeout=FIRST_CHUNK_TIMEOUT
        )
        yield first_chunk
        async for chunk in stream:
            yield chunk
        return
    except Exception as e:
        logger.warning(f"Stream from {provider.name} failed: {e}")

    for fb in fallback_providers or []:
        try:
            logger.info(f"Stream fallback to {fb.name}")
            async for chunk in fb.chat_stream(request):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Stream fallback {fb.name} failed: {e}")

    raise RuntimeError("All providers failed for streaming")
