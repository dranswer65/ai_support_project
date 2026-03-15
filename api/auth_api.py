from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from database import AsyncSessionLocal
from core.auth_schema import ensure_auth_tables, seed_auth_defaults
from core.auth_rbac import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user_context,
)

router = APIRouter()


@router.post("/admin/auth/seed")
async def seed_auth_system():
    async with AsyncSessionLocal() as db:
        await ensure_auth_tables(db)
        await seed_auth_defaults(db)

    return {"ok": True}


@router.post("/admin/auth/users/create")
async def create_user(payload: Dict[str, Any]):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "").strip()
    full_name = str(payload.get("full_name") or "").strip() or None
    mobile = str(payload.get("mobile") or "").strip() or None
    is_active = bool(payload.get("is_active", True))
    is_platform_admin = bool(payload.get("is_platform_admin", False))

    if not email:
        raise HTTPException(status_code=400, detail="email_required")
    if not password:
        raise HTTPException(status_code=400, detail="password_required")

    pw_hash = hash_password(password)

    async with AsyncSessionLocal() as db:
        await ensure_auth_tables(db)

        exists_res = await db.execute(
            text(
                """
                SELECT id
                FROM users
                WHERE email = :email
                LIMIT 1
                """
            ),
            {"email": email},
        )
        if exists_res.mappings().first():
            raise HTTPException(status_code=409, detail="email_already_exists")

        res = await db.execute(
            text(
                """
                INSERT INTO users (
                    email,
                    password_hash,
                    full_name,
                    mobile,
                    is_active,
                    is_platform_admin,
                    updated_at
                )
                VALUES (
                    :email,
                    :password_hash,
                    :full_name,
                    :mobile,
                    :is_active,
                    :is_platform_admin,
                    NOW()
                )
                RETURNING id
                """
            ),
            {
                "email": email,
                "password_hash": pw_hash,
                "full_name": full_name,
                "mobile": mobile,
                "is_active": is_active,
                "is_platform_admin": is_platform_admin,
            },
        )
        row = res.mappings().first()
        await db.commit()

    return {"ok": True, "user_id": int(row["id"]), "email": email}


@router.post("/admin/auth/tenant-users/assign")
async def assign_tenant_user(payload: Dict[str, Any]):
    tenant_id = str(payload.get("tenant_id") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    role_code = str(payload.get("role_code") or "").strip().upper()
    doctor_key = str(payload.get("doctor_key") or "").strip() or None
    is_active = bool(payload.get("is_active", True))

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id_required")
    if not email:
        raise HTTPException(status_code=400, detail="email_required")
    if not role_code:
        raise HTTPException(status_code=400, detail="role_code_required")

    async with AsyncSessionLocal() as db:
        await ensure_auth_tables(db)

        user_res = await db.execute(
            text(
                """
                SELECT id
                FROM users
                WHERE email = :email
                LIMIT 1
                """
            ),
            {"email": email},
        )
        user = user_res.mappings().first()
        if not user:
            raise HTTPException(status_code=404, detail="user_not_found")

        role_res = await db.execute(
            text(
                """
                SELECT id
                FROM roles
                WHERE role_code = :role_code
                LIMIT 1
                """
            ),
            {"role_code": role_code},
        )
        role = role_res.mappings().first()
        if not role:
            raise HTTPException(status_code=404, detail="role_not_found")

        await db.execute(
            text(
                """
                INSERT INTO tenant_users (
                    tenant_id,
                    user_id,
                    role_id,
                    doctor_key,
                    is_active,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :user_id,
                    :role_id,
                    :doctor_key,
                    :is_active,
                    NOW()
                )
                ON CONFLICT (tenant_id, user_id, role_id)
                DO UPDATE SET
                    doctor_key = EXCLUDED.doctor_key,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "user_id": int(user["id"]),
                "role_id": int(role["id"]),
                "doctor_key": doctor_key,
                "is_active": is_active,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "email": email,
        "role_code": role_code,
    }


@router.post("/admin/login")
async def admin_login(payload: Dict[str, Any]):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "").strip()
    tenant_id = str(payload.get("tenant_id") or "").strip() or None

    if not email:
        raise HTTPException(status_code=400, detail="email_required")
    if not password:
        raise HTTPException(status_code=400, detail="password_required")

    async with AsyncSessionLocal() as db:
        await ensure_auth_tables(db)

        user_res = await db.execute(
            text(
                """
                SELECT id, email, password_hash, full_name, is_active, is_platform_admin
                FROM users
                WHERE email = :email
                LIMIT 1
                """
            ),
            {"email": email},
        )
        user = user_res.mappings().first()

        if not user:
            raise HTTPException(status_code=401, detail="invalid_credentials")

        if not bool(user["is_active"]):
            raise HTTPException(status_code=403, detail="user_inactive")

        if not verify_password(password, str(user["password_hash"])):
            raise HTTPException(status_code=401, detail="invalid_credentials")

        role_code = "PLATFORM_ADMIN"

        if not bool(user["is_platform_admin"]):
            if not tenant_id:
                raise HTTPException(status_code=400, detail="tenant_id_required_for_clinic_login")

            tu_res = await db.execute(
                text(
                    """
                    SELECT r.role_code
                    FROM tenant_users tu
                    JOIN roles r
                      ON r.id = tu.role_id
                    WHERE tu.user_id = :user_id
                      AND tu.tenant_id = :tenant_id
                      AND tu.is_active = TRUE
                    ORDER BY tu.id ASC
                    LIMIT 1
                    """
                ),
                {
                    "user_id": int(user["id"]),
                    "tenant_id": tenant_id,
                },
            )
            tu = tu_res.mappings().first()
            if not tu:
                raise HTTPException(status_code=403, detail="tenant_role_not_found")

            role_code = str(tu["role_code"]).upper()

        token = create_access_token(
            user_id=int(user["id"]),
            email=str(user["email"]),
            tenant_id=tenant_id,
            role_code=role_code,
            is_platform_admin=bool(user["is_platform_admin"]),
        )

        await db.execute(
            text(
                """
                UPDATE users
                SET last_login_at = NOW(),
                    updated_at = NOW()
                WHERE id = :user_id
                """
            ),
            {"user_id": int(user["id"])},
        )
        await db.commit()

    response = JSONResponse(
        {
            "ok": True,
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": int(user["id"]),
                "email": str(user["email"]),
                "full_name": str(user.get("full_name") or ""),
            },
            "tenant_id": tenant_id,
            "role_code": role_code,
            "is_platform_admin": bool(user["is_platform_admin"]),
        }
    )

    response.set_cookie(
        key="sp_access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,   # keep False for local http://127.0.0.1
        path="/",
        max_age=60 * 60 * 8,
    )

    return response


@router.post("/admin/logout")
async def admin_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        key="sp_access_token",
        path="/",
    )
    return response


@router.get("/admin/me")
async def admin_me(request: Request):
    async with AsyncSessionLocal() as db:
        ctx = await get_current_user_context(request, db)
    return {"ok": True, "me": ctx}