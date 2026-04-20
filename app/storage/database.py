import os
import aiosqlite
from app.config import settings

_db_path = settings.db_path


async def init_db():
    os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
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
                routing_strategy TEXT
            )
        """)
        await db.commit()


async def log_call(**kwargs):
    async with aiosqlite.connect(_db_path) as db:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        await db.execute(
            f"INSERT INTO call_logs ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        await db.commit()


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
