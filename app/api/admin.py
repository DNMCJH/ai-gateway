from typing import Optional

from fastapi import APIRouter, Query

from app.providers.registry import registry
from app.storage.database import get_logs, get_stats
from app.api.chat import smart_router
from app.core.router import STRATEGIES

router = APIRouter(prefix="/api/admin")


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
