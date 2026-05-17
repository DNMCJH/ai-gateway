from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.core.tenant import get_tenant, check_tenant_budget, get_tenant_limiter


async def require_api_key(request: Request, authorization: str | None = Header(default=None)):
    keys = settings.gateway_api_keys
    if not keys:
        request.state.tenant = None
        return

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    if token not in keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Attach tenant info for downstream use
    tenant = await get_tenant(token)
    request.state.tenant = tenant

    if tenant:
        if not tenant.enabled:
            raise HTTPException(status_code=403, detail="Tenant disabled")

        # Per-tenant rate limit
        limiter = get_tenant_limiter(tenant)
        if not limiter.acquire(token):
            raise HTTPException(status_code=429, detail="Tenant rate limit exceeded")

        # Budget check
        budget_msg = await check_tenant_budget(tenant)
        if budget_msg:
            raise HTTPException(status_code=429, detail=budget_msg)
