import hashlib
import json
import time
from typing import Optional

import httpx
import aiosqlite

from app.config import settings
from app.schemas.chat import ChatCompletionRequest

_db_path = settings.db_path


def _cache_key(request: ChatCompletionRequest) -> str:
    normalized = json.dumps(
        {"model": request.model, "messages": [m.model_dump() for m in request.messages]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()


def _messages_text(request: ChatCompletionRequest) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in request.messages)


async def _get_embedding(text: str) -> Optional[list[float]]:
    if not settings.deepseek_api_key:
        return None
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.deepseek_base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={"model": "text-embedding-v1", "input": text[:8000]},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("data", [{}])[0].get("embedding")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Exact-match cache ---

async def cache_get(request: ChatCompletionRequest) -> Optional[dict]:
    if not settings.cache_enabled:
        return None
    key = _cache_key(request)
    cutoff = time.time() - settings.cache_ttl_seconds
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT response_json FROM response_cache WHERE cache_key = ? AND created_at > ?",
            (key, cutoff),
        )
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
    return None


async def cache_put(request: ChatCompletionRequest, response_dict: dict):
    if not settings.cache_enabled:
        return
    from app.storage.database import enqueue_write
    key = _cache_key(request)
    now = time.time()
    response_json = json.dumps(response_dict, ensure_ascii=False)

    embedding = None
    if settings.semantic_cache_enabled:
        text = _messages_text(request)
        emb = await _get_embedding(text)
        if emb:
            embedding = json.dumps(emb)

    await enqueue_write(
        "INSERT OR REPLACE INTO response_cache (cache_key, model, response_json, created_at, embedding) "
        "VALUES (?, ?, ?, ?, ?)",
        [key, request.model, response_json, now, embedding],
    )


# --- Semantic cache ---

async def semantic_cache_get(request: ChatCompletionRequest) -> Optional[dict]:
    if not settings.semantic_cache_enabled:
        return None

    text = _messages_text(request)
    query_emb = await _get_embedding(text)
    if not query_emb:
        return None

    cutoff = time.time() - settings.cache_ttl_seconds
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT response_json, embedding FROM response_cache "
            "WHERE model = ? AND created_at > ? AND embedding IS NOT NULL",
            (request.model, cutoff),
        )
        best_score = 0.0
        best_response = None
        async for row in cursor:
            emb = json.loads(row[1])
            score = _cosine_similarity(query_emb, emb)
            if score > best_score:
                best_score = score
                best_response = row[0]

        if best_score >= settings.semantic_cache_threshold:
            return json.loads(best_response)
    return None


async def cache_stats() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM response_cache")
        total = (await cursor.fetchone())[0]
        cutoff = time.time() - settings.cache_ttl_seconds
        cursor = await db.execute(
            "SELECT COUNT(*) FROM response_cache WHERE created_at > ?", (cutoff,)
        )
        active = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM response_cache WHERE embedding IS NOT NULL AND created_at > ?",
            (cutoff,),
        )
        with_embedding = (await cursor.fetchone())[0]
        return {
            "total_entries": total,
            "active_entries": active,
            "with_embedding": with_embedding,
            "ttl_seconds": settings.cache_ttl_seconds,
            "semantic_enabled": settings.semantic_cache_enabled,
            "semantic_threshold": settings.semantic_cache_threshold,
        }
