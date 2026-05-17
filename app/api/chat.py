import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.providers.registry import registry
from app.core.auth import require_api_key
from app.core.router import SmartRouter
from app.core.retry import with_retry, stream_with_fallback
from app.core.limiter import TokenBucketLimiter
from app.core.cost import calculate_cost
from app.core.cache import cache_get, cache_put, semantic_cache_get
from app.core.prompt_registry import resolve_prompt_ab
from app.storage.database import log_call
from app.schemas.chat import ChatCompletionRequest, ChatMessage

router = APIRouter(dependencies=[Depends(require_api_key)])
smart_router = SmartRouter(settings.default_routing_strategy)
limiter = TokenBucketLimiter(settings.rate_limit_rpm)

ROUTE_ALIASES = {
    "auto": None,
    "best": "capability",
    "cheapest": "cost",
}


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"...[truncated {len(s) - limit} chars]"


def _serialize_request_body(request: ChatCompletionRequest) -> str | None:
    if not settings.log_request_body:
        return None
    body = json.dumps(request.model_dump(), ensure_ascii=False)
    return _truncate(body, settings.body_log_max_chars)


def _serialize_response_body(content: str) -> str | None:
    if not settings.log_response_body:
        return None
    return _truncate(content, settings.body_log_max_chars)


def _resolve_route(request):
    """Return the provider and resolved model id. Rewrites alias models in-place."""
    if request.model in ROUTE_ALIASES:
        providers = registry.available_providers()
        if not providers:
            raise HTTPException(status_code=503, detail="No providers available")
        # Skip rate-limited providers when auto-routing; routing to one that
        # would 429 below is wasteful. Fall back to full list only if all are saturated.
        with_capacity = [p for p in providers if limiter.remaining(p.name) >= 1]
        candidates = with_capacity or providers
        strategy_override = ROUTE_ALIASES[request.model]
        try:
            provider, model_id = smart_router.route(request, candidates, strategy_override)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e))
        request.model = model_id
        return provider

    try:
        return registry.get_provider_for_model(request.model)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Model '{request.model}' not found")


def _get_fallbacks(primary):
    return [p for p in registry.available_providers() if p.name != primary.name]


async def _stream_response(provider, request, fallbacks, request_id):
    input_tokens = 0
    output_tokens = 0
    accumulated_content = []
    request_body = _serialize_request_body(request)
    start = time.monotonic()
    try:
        async for chunk in stream_with_fallback(provider, request, fallbacks):
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens
            if settings.log_response_body:
                for c in chunk.choices:
                    if c.delta.content:
                        accumulated_content.append(c.delta.content)
            yield json.dumps(chunk.model_dump(exclude_none=True), ensure_ascii=False)
        yield "[DONE]"
        latency = int((time.monotonic() - start) * 1000)
        cost = calculate_cost(request.model, input_tokens, output_tokens)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost, latency_ms=latency, status="success",
            request_body=request_body,
            response_body=_serialize_response_body("".join(accumulated_content)),
        )
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            latency_ms=latency, status="error", error_message=str(e),
            request_body=request_body,
        )


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    # Resolve prompt_id if provided
    if request.prompt_id:
        prompt = await resolve_prompt_ab(request.prompt_id)
        if not prompt:
            raise HTTPException(status_code=400, detail=f"Prompt '{request.prompt_id}' not found")
        import json as _json
        msgs = _json.loads(prompt["messages_json"]) if isinstance(prompt.get("messages_json"), str) else prompt.get("messages", [])
        request.messages = [ChatMessage(**m) for m in msgs] + list(request.messages)
        if prompt.get("model") and not request.model:
            request.model = prompt["model"]
        if prompt.get("temperature"):
            request.temperature = prompt["temperature"]

    if not request.messages:
        raise HTTPException(status_code=400, detail="messages is required (or provide prompt_id)")

    provider = _resolve_route(request)

    if not limiter.acquire(provider.name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    fallbacks = _get_fallbacks(provider)
    request_id = f"req-{uuid.uuid4().hex[:12]}"

    if request.stream:
        return EventSourceResponse(
            _stream_response(provider, request, fallbacks, request_id)
        )

    # Cache lookup (non-streaming only)
    cached = await cache_get(request)
    if not cached:
        cached = await semantic_cache_get(request)
    if cached:
        cached["id"] = f"chatcmpl-cache-{uuid.uuid4().hex[:8]}"
        await log_call(
            request_id=request_id, model=request.model, provider="cache",
            input_tokens=0, output_tokens=0, cost_usd=0, latency_ms=0,
            status="cache_hit",
        )
        return cached

    request_body = _serialize_request_body(request)
    start = time.monotonic()
    try:
        response = await with_retry(provider, request, fallbacks)
        latency = int((time.monotonic() - start) * 1000)
        usage = response.usage
        cost = calculate_cost(request.model, usage.prompt_tokens, usage.completion_tokens)
        content = response.choices[0].message.content if response.choices else ""
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
            cost_usd=cost, latency_ms=latency, status="success",
            request_body=request_body,
            response_body=_serialize_response_body(content),
        )
        await cache_put(request, response.model_dump())
        return response
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        await log_call(
            request_id=request_id, model=request.model, provider=provider.name,
            latency_ms=latency, status="error", error_message=str(e),
            request_body=request_body,
        )
        raise HTTPException(status_code=502, detail=str(e))
