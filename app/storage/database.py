import asyncio
import logging
import os
from typing import Optional

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_db_path = settings.db_path

_writer_conn: Optional[aiosqlite.Connection] = None
_writer_queue: Optional[asyncio.Queue] = None
_writer_task: Optional[asyncio.Task] = None
_SHUTDOWN = object()


async def init_db():
    global _writer_conn, _writer_queue, _writer_task

    os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)

    _writer_conn = await aiosqlite.connect(_db_path)
    # WAL allows readers to proceed concurrently with the single writer.
    await _writer_conn.execute("PRAGMA journal_mode=WAL")
    await _writer_conn.execute("PRAGMA synchronous=NORMAL")
    await _writer_conn.execute("""
        CREATE TABLE IF NOT EXISTS call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            request_id TEXT UNIQUE,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_message TEXT,
            routing_strategy TEXT,
            request_body TEXT,
            response_body TEXT
        )
    """)
    # Idempotent migration for tables created before body columns existed.
    for col in ("request_body", "response_body", "tenant_key"):
        try:
            await _writer_conn.execute(f"ALTER TABLE call_logs ADD COLUMN {col} TEXT")
        except Exception:
            pass

    await _writer_conn.execute("""
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            embedding TEXT
        )
    """)

    await _writer_conn.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            api_key TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            rpm_limit INTEGER DEFAULT 60,
            daily_budget_usd REAL DEFAULT 0,
            monthly_budget_usd REAL DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    await _writer_conn.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            messages_json TEXT NOT NULL,
            model TEXT DEFAULT '',
            temperature REAL DEFAULT 1.0,
            is_active INTEGER DEFAULT 1,
            ab_weight REAL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(name, version)
        )
    """)

    await _writer_conn.commit()

    _writer_queue = asyncio.Queue()
    _writer_task = asyncio.create_task(_writer_loop(), name="db-writer")
    asyncio.create_task(_cache_eviction_loop(), name="cache-evictor")


async def shutdown_db():
    global _SHUTDOWN_FLAG
    _SHUTDOWN_FLAG = True
    if _writer_queue is not None:
        await _writer_queue.put(_SHUTDOWN)
    if _writer_task is not None:
        await _writer_task
    if _writer_conn is not None:
        await _writer_conn.close()


_SHUTDOWN_FLAG = False


async def _cache_eviction_loop():
    import time as _time
    while not _SHUTDOWN_FLAG:
        await asyncio.sleep(settings.cache_eviction_interval)
        if _SHUTDOWN_FLAG:
            break
        cutoff = _time.time() - settings.cache_ttl_seconds
        if _writer_queue:
            await _writer_queue.put((
                "DELETE FROM response_cache WHERE created_at <= ?", [cutoff]
            ))
            logger.info("cache eviction: removed entries older than %s", cutoff)


async def _writer_loop():
    assert _writer_conn is not None and _writer_queue is not None
    while True:
        item = await _writer_queue.get()
        if item is _SHUTDOWN:
            return
        sql, params = item
        try:
            await _writer_conn.execute(sql, params)
            await _writer_conn.commit()
        except Exception:
            logger.exception("db writer failed for sql=%s", sql)


async def log_call(**kwargs):
    if _writer_queue is None:
        logger.warning("log_call before init_db: %s", kwargs.get("request_id"))
        return
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    sql = f"INSERT INTO call_logs ({cols}) VALUES ({placeholders})"
    await _writer_queue.put((sql, list(kwargs.values())))


async def enqueue_write(sql: str, params: list):
    """Route arbitrary writes through the single-writer queue."""
    if _writer_queue is None:
        logger.warning("enqueue_write before init_db")
        return
    await _writer_queue.put((sql, params))


async def get_logs(limit: int = 50, offset: int = 0, model: str = None, status: str = None):
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        where, params = [], []
        if model:
            where.append("model = ?")
            params.append(model)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        cursor = await db.execute(
            f"SELECT * FROM call_logs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_stats():
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT COUNT(*) as total_calls, "
            "SUM(cost_usd) as total_cost, "
            "SUM(input_tokens) as total_input_tokens, "
            "SUM(output_tokens) as total_output_tokens, "
            "AVG(latency_ms) as avg_latency "
            "FROM call_logs"
        )
        overall = dict(await cursor.fetchone())

        cursor = await db.execute(
            "SELECT model, provider, COUNT(*) as calls, "
            "SUM(cost_usd) as cost, "
            "SUM(input_tokens) as input_tokens, "
            "SUM(output_tokens) as output_tokens, "
            "AVG(latency_ms) as avg_latency "
            "FROM call_logs GROUP BY model, provider ORDER BY calls DESC"
        )
        by_model = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT date(timestamp) as day, COUNT(*) as calls, SUM(cost_usd) as cost "
            "FROM call_logs GROUP BY day ORDER BY day DESC LIMIT 30"
        )
        daily = [dict(r) for r in await cursor.fetchall()]

        return {"overall": overall, "by_model": by_model, "daily": daily}
