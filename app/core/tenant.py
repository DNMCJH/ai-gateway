import time
from typing import Optional

import aiosqlite

from app.config import settings
from app.core.limiter import TokenBucketLimiter

_db_path = settings.db_path
_tenant_limiters: dict[str, TokenBucketLimiter] = {}


class TenantInfo:
    __slots__ = ("api_key", "name", "rpm_limit", "daily_budget_usd", "monthly_budget_usd", "enabled")

    def __init__(self, api_key: str, name: str, rpm_limit: int,
                 daily_budget_usd: float, monthly_budget_usd: float, enabled: bool):
        self.api_key = api_key
        self.name = name
        self.rpm_limit = rpm_limit
        self.daily_budget_usd = daily_budget_usd
        self.monthly_budget_usd = monthly_budget_usd
        self.enabled = enabled


async def get_tenant(api_key: str) -> Optional[TenantInfo]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tenants WHERE api_key = ?", (api_key,))
        row = await cursor.fetchone()
        if not row:
            return None
        return TenantInfo(
            api_key=row["api_key"], name=row["name"],
            rpm_limit=row["rpm_limit"],
            daily_budget_usd=row["daily_budget_usd"],
            monthly_budget_usd=row["monthly_budget_usd"],
            enabled=bool(row["enabled"]),
        )


async def get_tenant_spend(api_key: str) -> dict:
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM call_logs "
            "WHERE tenant_key = ? AND date(timestamp) = date('now') AND status = 'success'",
            (api_key,),
        )
        daily = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM call_logs "
            "WHERE tenant_key = ? AND timestamp >= date('now', 'start of month') AND status = 'success'",
            (api_key,),
        )
        monthly = (await cursor.fetchone())[0]
        return {"daily_spend_usd": daily, "monthly_spend_usd": monthly}


def get_tenant_limiter(tenant: TenantInfo) -> TokenBucketLimiter:
    if tenant.api_key not in _tenant_limiters:
        _tenant_limiters[tenant.api_key] = TokenBucketLimiter(tenant.rpm_limit)
    return _tenant_limiters[tenant.api_key]


async def check_tenant_budget(tenant: TenantInfo) -> Optional[str]:
    if tenant.daily_budget_usd <= 0 and tenant.monthly_budget_usd <= 0:
        return None
    spend = await get_tenant_spend(tenant.api_key)
    if tenant.daily_budget_usd > 0 and spend["daily_spend_usd"] >= tenant.daily_budget_usd:
        return f"Daily budget exceeded (${spend['daily_spend_usd']:.4f} / ${tenant.daily_budget_usd:.4f})"
    if tenant.monthly_budget_usd > 0 and spend["monthly_spend_usd"] >= tenant.monthly_budget_usd:
        return f"Monthly budget exceeded (${spend['monthly_spend_usd']:.4f} / ${tenant.monthly_budget_usd:.4f})"
    return None


async def upsert_tenant(api_key: str, name: str = "", rpm_limit: int = 60,
                        daily_budget_usd: float = 0, monthly_budget_usd: float = 0):
    from app.storage.database import enqueue_write
    await enqueue_write(
        "INSERT OR REPLACE INTO tenants (api_key, name, rpm_limit, daily_budget_usd, monthly_budget_usd) "
        "VALUES (?, ?, ?, ?, ?)",
        [api_key, name, rpm_limit, daily_budget_usd, monthly_budget_usd],
    )
    _tenant_limiters.pop(api_key, None)


async def list_tenants() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tenants ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]
