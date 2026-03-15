from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)

JWT_SECRET = (os.getenv("JWT_SECRET") or "change-me-now").strip()
JWT_ALG = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES") or "480")


def hash_password(password: str) -> str:
    password = str(password or "")
    if not password.strip():
        raise HTTPException(status_code=400, detail="password_required")
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(str(password or ""), str(password_hash or ""))
    except Exception:
        return False


def create_access_token(
    *,
    user_id: int,
    email: str,
    tenant_id: Optional[str],
    role_code: str,
    is_platform_admin: bool,
) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=JWT_EXPIRE_MINUTES)

    payload = {
        "sub": str(user_id),
        "email": email,
        "tenant_id": tenant_id,
        "role_code": role_code,
        "is_platform_admin": bool(is_platform_admin),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_token")


def get_bearer_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    cookie_token = (request.cookies.get("sp_access_token") or "").strip()
    if cookie_token:
        return cookie_token

    query_token = (
        (request.query_params.get("access_token") or "").strip()
        or (request.query_params.get("token") or "").strip()
    )
    if query_token:
        return query_token

    raise HTTPException(status_code=401, detail="missing_access_token")


async def get_current_user_context(request: Request, db: AsyncSession) -> Dict[str, Any]:
    token = get_bearer_token_from_request(request)
    claims = decode_access_token(token)

    user_id = int(claims.get("sub"))
    tenant_id = (claims.get("tenant_id") or "").strip() or None
    role_code = str(claims.get("role_code") or "").strip().upper()
    is_platform_admin = bool(claims.get("is_platform_admin"))

    user_res = await db.execute(
        text(
            """
            SELECT id, email, full_name, mobile, is_active, is_platform_admin
            FROM users
            WHERE id = :user_id
            LIMIT 1
            """
        ),
        {"user_id": user_id},
    )
    user = user_res.mappings().first()

    if not user:
        raise HTTPException(status_code=401, detail="user_not_found")

    if not bool(user["is_active"]):
        raise HTTPException(status_code=403, detail="user_inactive")

    permissions: List[str] = []

    if is_platform_admin:
        perm_res = await db.execute(
            text(
                """
                SELECT permission_code
                FROM permissions
                ORDER BY permission_code ASC
                """
            )
        )
        permissions = [str(r["permission_code"]) for r in perm_res.mappings().all()]
    else:
        if not tenant_id:
            raise HTTPException(status_code=403, detail="tenant_missing_in_token")

        perm_res = await db.execute(
            text(
                """
                SELECT DISTINCT p.permission_code
                FROM tenant_users tu
                JOIN roles r
                  ON r.id = tu.role_id
                JOIN role_permissions rp
                  ON rp.role_id = r.id
                JOIN permissions p
                  ON p.id = rp.permission_id
                WHERE tu.user_id = :user_id
                  AND tu.tenant_id = :tenant_id
                  AND tu.is_active = TRUE
                  AND r.role_code = :role_code
                ORDER BY p.permission_code ASC
                """
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role_code": role_code,
            },
        )
        permissions = [str(r["permission_code"]) for r in perm_res.mappings().all()]

    return {
        "user_id": user_id,
        "email": str(user["email"]),
        "full_name": str(user.get("full_name") or ""),
        "mobile": str(user.get("mobile") or ""),
        "tenant_id": tenant_id,
        "role_code": role_code,
        "is_platform_admin": is_platform_admin,
        "permissions": permissions,
    }


def require_role(ctx: Dict[str, Any], allowed_roles: List[str]) -> None:
    if ctx.get("is_platform_admin"):
        return

    role_code = str(ctx.get("role_code") or "").upper()
    allowed = {str(x).upper() for x in allowed_roles}

    if role_code not in allowed:
        raise HTTPException(status_code=403, detail="role_forbidden")


def require_permission(ctx: Dict[str, Any], permission_code: str) -> None:
    if ctx.get("is_platform_admin"):
        return

    perms = set(ctx.get("permissions") or [])
    if permission_code not in perms:
        raise HTTPException(status_code=403, detail="permission_forbidden")