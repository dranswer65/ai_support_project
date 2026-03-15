from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import json


async def log_audit_event(
    db: AsyncSession,
    tenant_id: str,
    action: str,
    actor_user_id=None,
    actor_email=None,
    role_code=None,
    entity_type=None,
    entity_id=None,
    metadata=None,
):

    await db.execute(
        text("""
        INSERT INTO audit_logs (
            tenant_id,
            actor_user_id,
            actor_email,
            role_code,
            action,
            entity_type,
            entity_id,
            metadata
        )
        VALUES (
            :tenant_id,
            :actor_user_id,
            :actor_email,
            :role_code,
            :action,
            :entity_type,
            :entity_id,
            :metadata
        )
        """),
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "actor_email": actor_email,
            "role_code": role_code,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": json.dumps(metadata or {}),
        }
    )