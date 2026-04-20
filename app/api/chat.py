import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.providers.registry import registry
from app.core.router import SmartRouter
from app.core.retry import with_retry, stream_with_fallback
from app.core.limiter import TokenBucketLimiter
from app.core.cost import calculate_cost
from app.storage.database import log_call
from app.schemas.chat import ChatCompletionRequest

router = APIRouter()
smart_router = SmartRouter(settings.default_routing_strategy)
limiter = TokenBucketLimiter(settings.rate_limit_rpm)

AUTO_ROUTE_MODELS = {"auto", "best", "cheapest"}


def _resolve_provider(request):
    if request.model in AUTO_ROUTE_MODELS:
        providers = registry.available_providers()
        if not providers:
            raise HTTPException(status_code=503, detail="No providers available")
        return smart_router.route(request, providers)

    try:
        return registry.get_provider_for_model(request.model)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Model '{request.model}' not found")


def _get_fallbacks(primary):
    return [p for p in registry.available_providers() if p.name != primary.name]


async def _stream_response(provider, request, fallbacks, request_id):
    total_output_tokens = 0
    start = time.monotonic()
    try:
        async for chunk in stream_with_fallback(provider, request, fallbacks):
            for c in chunk.choices:
                if c.delta.content:
                    total_output_tokens += 1
            yield json.dumps(chunk.model_dump(), ensure_ascii=False)
        yield "[DONE]"
        latency = int((time.monotonic() - start) * 1000)
        cost = calculate_cost(request.model, 0, total_output_tokens)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            input_tokens=0, output_tokens=total_output_tokens,
            cost_usd=cost, latency_ms=latency, status="success",
        )
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            latency_ms=latency, status="error", error_message=str(e),
        )


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    provider = _resolve_provider(request)

    if not limiter.acquire(provider.name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    fallbacks = _get_fallbacks(provider)
    request_id = f"req-{uuid.uuid4().hex[:12]}"

    if request.stream:
        return EventSourceResponse(
            _stream_response(provider, request, fallbacks, request_id)
        )

    start = time.monotonic()
    try:
        response = await with_retry(provider, request, fallbacks)
        latency = int((time.monotonic() - start) * 1000)
        usage = response.usage
        cost = calculate_cost(request.model, usage.prompt_tokens, usage.completion_tokens)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
            cost_usd=cost, latency_ms=latency, status="success",
        )
        return response
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            latency_ms=latency, status="error", error_message=str(e),
        )
        raise HTTPException(status_code=502, detail=str(e))
