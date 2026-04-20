import time

from fastapi import APIRouter

from app.providers.registry import registry

router = APIRouter()


@router.get("/v1/models")
async def list_models():
    models = registry.list_all_models()
    return {
        "object": "list",
        "data": [
            {
                "id": m["id"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": m.get("owned_by", "unknown"),
            }
            for m in models
        ],
    }
