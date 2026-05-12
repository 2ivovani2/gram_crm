"""
JWT authentication with Telegram Login Widget verification.
Users are identified by their Telegram ID — no username/password.
"""

import hashlib
import hmac
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import User, get_db

JWT_SECRET  = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production-please")
BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALGORITHM   = "HS256"
TOKEN_DAYS  = 30

security = HTTPBearer(auto_error=False)

# Sliding-window rate limiter: max 10 attempts per IP per 5 minutes
_attempts: dict[str, list[float]] = defaultdict(list)
_WINDOW = 300
_MAX    = 10


def check_rate_limit(request: Request) -> None:
    ip  = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = [t for t in _attempts[ip] if now - t < _WINDOW]
    _attempts[ip] = bucket
    if len(bucket) >= _MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait 5 minutes.",
        )
    _attempts[ip].append(now)


def verify_telegram_auth(data: dict) -> bool:
    """
    Verify Telegram Login Widget data using HMAC-SHA256.
    See: https://core.telegram.org/widgets/login#checking-authorization
    """
    received_hash = data.get("hash", "")
    # Build data-check-string: sorted key=value pairs (excluding hash), joined by newline
    check_string = "\n".join(
        f"{k}={v}"
        for k, v in sorted(data.items())
        if k != "hash" and v is not None
    )
    token = BOT_TOKEN
    if not token:
        raise HTTPException(500, "Bot token not configured")

    secret_key = hashlib.sha256(token.encode()).digest()
    computed   = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_hash)


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload  = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
        user_id  = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
