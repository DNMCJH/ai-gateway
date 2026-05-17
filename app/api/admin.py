from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.auth import require_api_key
from app.providers.registry import registry
from app.storage.database import get_logs, get_stats
from app.api.chat import smart_router
from app.core.router import STRATEGIES
from app.core.cache import cache_stats
from app.core.tenant import upsert_tenant, list_tenants, get_tenant_spend
from app.core.prompt_registry import create_prompt, list_prompts, resolve_prompt_ab, deactivate_prompt

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_api_key)])


@router.get("/stats")
async def stats():
    return await get_stats()


@router.get("/logs")
async def logs(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    model: Optional[str] = None,
    status: Optional[str] = None,
):
    return await get_logs(limit=limit, offset=offset, model=model, status=status)


@router.get("/providers")
async def providers():
    result = []
    for p in registry.available_providers():
        result.append({
            "name": p.name,
            "display_name": p.display_name,
            "models": p.list_models(),
            "available": await p.is_available(),
        })
    return result


@router.post("/config/routing")
async def set_routing(strategy: str):
    if strategy not in STRATEGIES:
        return {"error": f"Unknown strategy. Available: {list(STRATEGIES.keys())}"}
    smart_router.set_strategy(strategy)
    return {"strategy": strategy}


@router.get("/config/routing")
async def get_routing():
    return {"strategy": smart_router.strategy_name}


# --- Cache ---

@router.get("/cache/stats")
async def get_cache_stats():
    return await cache_stats()


# --- Tenants ---

class TenantCreate(BaseModel):
    api_key: str
    name: str = ""
    rpm_limit: int = 60
    daily_budget_usd: float = 0
    monthly_budget_usd: float = 0


@router.get("/tenants")
async def tenants_list():
    return await list_tenants()


@router.post("/tenants")
async def tenants_create(body: TenantCreate):
    await upsert_tenant(
        api_key=body.api_key, name=body.name, rpm_limit=body.rpm_limit,
        daily_budget_usd=body.daily_budget_usd, monthly_budget_usd=body.monthly_budget_usd,
    )
    return {"status": "ok", "api_key": body.api_key}


@router.get("/tenants/{api_key}/spend")
async def tenant_spend(api_key: str):
    return await get_tenant_spend(api_key)


# --- Prompts ---

class PromptCreate(BaseModel):
    name: str
    messages: list[dict]
    model: str = ""
    temperature: float = 1.0
    ab_weight: float = 1.0


@router.get("/prompts")
async def prompts_list(name: Optional[str] = None):
    return await list_prompts(name)


@router.post("/prompts")
async def prompts_create(body: PromptCreate):
    return await create_prompt(
        name=body.name, messages=body.messages,
        model=body.model, temperature=body.temperature, ab_weight=body.ab_weight,
    )


@router.get("/prompts/{name}/resolve")
async def prompts_resolve(name: str):
    result = await resolve_prompt_ab(name)
    if not result:
        return {"error": "Prompt not found"}
    return result


@router.delete("/prompts/{prompt_id}")
async def prompts_delete(prompt_id: str):
    ok = await deactivate_prompt(prompt_id)
    return {"status": "deactivated" if ok else "not_found"}
