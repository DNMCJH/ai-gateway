import json
import random
import uuid
from typing import Optional

import aiosqlite

from app.config import settings

_db_path = settings.db_path


async def create_prompt(name: str, messages: list[dict], model: str = "",
                        temperature: float = 1.0, ab_weight: float = 1.0) -> dict:
    from app.storage.database import enqueue_write
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompts WHERE name = ?", (name,)
        )
        max_ver = (await cursor.fetchone())[0]
    new_ver = max_ver + 1
    prompt_id = f"prompt-{uuid.uuid4().hex[:8]}"
    messages_json = json.dumps(messages, ensure_ascii=False)
    await enqueue_write(
        "INSERT INTO prompts (id, name, version, messages_json, model, temperature, ab_weight) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [prompt_id, name, new_ver, messages_json, model, temperature, ab_weight],
    )
    return {"id": prompt_id, "name": name, "version": new_ver}


async def get_prompt(name: str, version: Optional[int] = None) -> Optional[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if version:
            cursor = await db.execute(
                "SELECT * FROM prompts WHERE name = ? AND version = ? AND is_active = 1",
                (name, version),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM prompts WHERE name = ? AND is_active = 1 ORDER BY version DESC",
                (name,),
            )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)


async def resolve_prompt_ab(name: str) -> Optional[dict]:
    """Select a prompt version using weighted A/B split."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM prompts WHERE name = ? AND is_active = 1", (name,)
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        weights = [r["ab_weight"] for r in rows]
        selected = random.choices(rows, weights=weights, k=1)[0]
        return selected


async def list_prompts(name: Optional[str] = None) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if name:
            cursor = await db.execute(
                "SELECT * FROM prompts WHERE name = ? ORDER BY version DESC", (name,)
            )
        else:
            cursor = await db.execute("SELECT * FROM prompts ORDER BY name, version DESC")
        rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            r["messages"] = json.loads(r.pop("messages_json"))
        return rows


async def deactivate_prompt(prompt_id: str) -> bool:
    from app.storage.database import enqueue_write
    await enqueue_write("UPDATE prompts SET is_active = 0 WHERE id = ?", [prompt_id])
    return True
