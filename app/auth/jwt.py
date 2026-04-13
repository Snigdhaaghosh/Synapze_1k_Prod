"""
Synapze Enterprise — JWT authentication
Access tokens (short-lived) + Refresh tokens (long-lived).
Token rotation on refresh. Revocation via Redis blocklist.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import redis.asyncio as aioredis

from app.config import settings
from app.core.exceptions import AuthError, TokenExpiredError
from app.core.logging import get_logger, set_user_id

logger = get_logger("auth")
_security = HTTPBearer(auto_error=False)

# Redis key prefix for blocklisted (revoked) tokens
_BLOCKLIST_PREFIX = "jwt:revoked:"
_REFRESH_PREFIX = "jwt:refresh:"


def _get_redis():
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES),
        "type": "access",
        "jti": str(uuid.uuid4()),  # unique token ID for revocation
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": True},
        )
        if payload.get("type") != expected_type:
            raise AuthError(f"Invalid token type — expected {expected_type}")
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except JWTError as e:
        raise AuthError(f"Invalid token: {e}")


async def is_token_revoked(jti: str) -> bool:
    """Check if token has been explicitly revoked (logout, password change, etc.)."""
    try:
        r = _get_redis()
        return bool(await r.exists(f"{_BLOCKLIST_PREFIX}{jti}"))
    except Exception as e:
        logger.warning(f"Blocklist check failed (allowing): {e}")
        return False  # fail-open — don't break auth if Redis is down


async def revoke_token(jti: str, expire_seconds: int = 86400 * 30) -> None:
    """Add token JTI to revocation blocklist."""
    try:
        r = _get_redis()
        await r.setex(f"{_BLOCKLIST_PREFIX}{jti}", expire_seconds, "1")
    except Exception as e:
        logger.error(f"Token revocation failed: {e}")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "AUTH_ERROR", "message": "Authorization header missing"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, "access")
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "TOKEN_EXPIRED", "message": "Access token expired — use refresh token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=e.to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check revocation blocklist
    jti = payload.get("jti", "")
    if jti and await is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "TOKEN_REVOKED", "message": "Token has been revoked"},
        )

    user_id = payload["sub"]
    set_user_id(user_id)

    # Verify user still exists in DB
    from app.db.database import get_user
    from app.core.exceptions import RecordNotFoundError
    try:
        user = await get_user(user_id)
    except RecordNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "USER_NOT_FOUND", "message": "Account not found or deleted"},
        )

    if user.get("is_suspended"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ACCOUNT_SUSPENDED", "message": "Account suspended"},
        )

    return {
        "user_id": user_id,
        "email": payload["email"],
        "user": user,
        "token_jti": jti,
    }


async def require_user(current: dict = Depends(get_current_user)) -> str:
    return current["user_id"]


async def require_user_full(current: dict = Depends(get_current_user)) -> dict:
    return current
