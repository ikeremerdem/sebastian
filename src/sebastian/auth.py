"""Bearer-key auth for /api and /mcp; password + signed session for the UI."""

import hmac

from fastapi import HTTPException, Request

from .config import get_config


def check_api_key(header: str | None) -> bool:
    key = get_config().api_key
    if not key or not header or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), key.encode())


def require_api_key(request: Request) -> None:
    if not get_config().api_key:
        raise HTTPException(503, "API key is not configured (SEBASTIAN_API_KEY)")
    if not check_api_key(request.headers.get("authorization")):
        raise HTTPException(
            401, "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
