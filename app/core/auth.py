from fastapi import Header, HTTPException, status

from app.config import settings


async def require_api_key(authorization: str | None = Header(default=None)):
    keys = settings.gateway_api_keys
    if not keys:
        return  # auth disabled when no keys configured

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
