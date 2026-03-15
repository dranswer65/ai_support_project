from __future__ import annotations

import re
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _make_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


async def ensure_tenant_tables(db: AsyncSession) -> None:
    # Base tenants table
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                clinic_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    # Safe migrations for older tenants tables
    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ACTIVE';
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS default_language TEXT NOT NULL DEFAULT 'en';
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS default_tone TEXT NOT NULL DEFAULT 'formal';
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Riyadh';
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS country_code TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS whatsapp_phone_id TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS whatsapp_verify_token TEXT;
            """
        )
    )

    # tenant tokens table
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_tokens (
                tenant_id TEXT PRIMARY KEY
                    REFERENCES tenants(tenant_id)
                    ON DELETE CASCADE,
                admin_token TEXT NOT NULL UNIQUE,
                reception_token TEXT NOT NULL UNIQUE,
                public_api_key TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    # Correct index
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_tenants_status
            ON tenants(status);
            """
        )
    )

    await ensure_tenant_settings_table(db)
    await ensure_message_templates_table(db)
    await ensure_billing_tables(db)


async def ensure_tenant_settings_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_settings (
                tenant_id TEXT PRIMARY KEY
                    REFERENCES tenants(tenant_id)
                    ON DELETE CASCADE,

                clinic_name TEXT NOT NULL,
                logo_url TEXT,
                brand_color TEXT,
                timezone TEXT NOT NULL DEFAULT 'Asia/Riyadh',
                language TEXT NOT NULL DEFAULT 'en',
                whatsapp_greeting TEXT,
                ai_tone TEXT NOT NULL DEFAULT 'formal',
                reminder_hours_before INT NOT NULL DEFAULT 24,
                enable_ai_booking BOOLEAN NOT NULL DEFAULT TRUE,
                enable_reception_direct_booking BOOLEAN NOT NULL DEFAULT FALSE,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenant_settings
            ADD COLUMN IF NOT EXISTS enable_ai_booking BOOLEAN NOT NULL DEFAULT TRUE;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenant_settings
            ADD COLUMN IF NOT EXISTS enable_reception_direct_booking BOOLEAN NOT NULL DEFAULT FALSE;
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_tenant_settings_timezone
            ON tenant_settings(timezone);
            """
        )
    )


async def ensure_message_templates_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS message_templates (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL
                    REFERENCES tenants(tenant_id)
                    ON DELETE CASCADE,
                template_key TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                template_text TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (tenant_id, template_key, language)
            );
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_message_templates_tenant
            ON message_templates(tenant_id);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_message_templates_key
            ON message_templates(template_key);
            """
        )
    )


async def ensure_billing_tables(db: AsyncSession) -> None:
    # plans
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                monthly_price NUMERIC(10,2) NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                max_doctors INT NOT NULL DEFAULT 0,
                max_monthly_appointments INT NOT NULL DEFAULT 0,
                max_ai_conversations INT NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    # subscriptions
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL
                    REFERENCES tenants(tenant_id)
                    ON DELETE CASCADE,
                plan_code TEXT NOT NULL
                    REFERENCES plans(code),
                status TEXT NOT NULL DEFAULT 'TRIAL',
                started_at TIMESTAMPTZ,
                ends_at TIMESTAMPTZ,
                trial_ends_at TIMESTAMPTZ,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (tenant_id)
            );
            """
        )
    )

    # usage events
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL
                    REFERENCES tenants(tenant_id)
                    ON DELETE CASCADE,
                metric_code TEXT NOT NULL,
                quantity INT NOT NULL DEFAULT 1,
                event_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_id
            ON subscriptions(tenant_id);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_subscriptions_status
            ON subscriptions(status);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_metric_date
            ON usage_events(tenant_id, metric_code, event_date);
            """
        )
    )

    await seed_default_plans(db)


async def seed_default_plans(db: AsyncSession) -> None:
    plans = [
        {
            "code": "starter",
            "name": "Starter",
            "monthly_price": 49,
            "currency": "USD",
            "max_doctors": 5,
            "max_monthly_appointments": 300,
            "max_ai_conversations": 1000,
        },
        {
            "code": "growth",
            "name": "Growth",
            "monthly_price": 149,
            "currency": "USD",
            "max_doctors": 20,
            "max_monthly_appointments": 2000,
            "max_ai_conversations": 8000,
        },
        {
            "code": "enterprise",
            "name": "Enterprise",
            "monthly_price": 0,
            "currency": "USD",
            "max_doctors": 999999,
            "max_monthly_appointments": 999999,
            "max_ai_conversations": 999999,
        },
    ]

    for p in plans:
        await db.execute(
            text(
                """
                INSERT INTO plans (
                    code,
                    name,
                    monthly_price,
                    currency,
                    max_doctors,
                    max_monthly_appointments,
                    max_ai_conversations,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (
                    :code,
                    :name,
                    :monthly_price,
                    :currency,
                    :max_doctors,
                    :max_monthly_appointments,
                    :max_ai_conversations,
                    TRUE,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    monthly_price = EXCLUDED.monthly_price,
                    currency = EXCLUDED.currency,
                    max_doctors = EXCLUDED.max_doctors,
                    max_monthly_appointments = EXCLUDED.max_monthly_appointments,
                    max_ai_conversations = EXCLUDED.max_ai_conversations,
                    is_active = TRUE,
                    updated_at = NOW();
                """
            ),
            p,
        )


async def seed_default_message_templates(
    db: AsyncSession,
    *,
    tenant_id: str,
    clinic_name: str,
) -> None:
    templates = [
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_confirmed",
            "language": "en",
            "template_text": (
                "Hello {patient_name}\n\n"
                "Your appointment has been confirmed at {clinic_name}.\n\n"
                "Doctor: {doctor_name}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Please arrive 15 minutes early."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_confirmed",
            "language": "ar",
            "template_text": (
                "مرحبًا {patient_name}\n\n"
                "تم تأكيد موعدكم في {clinic_name} ✅\n\n"
                "الطبيب: {doctor_name}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "يرجى الحضور قبل الموعد بـ 15 دقيقة."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_reminder",
            "language": "en",
            "template_text": (
                "Hello {patient_name}\n\n"
                "Reminder from {clinic_name}.\n"
                "You have an appointment soon.\n\n"
                "Doctor: {doctor_name}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Please reply if you need help."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_reminder",
            "language": "ar",
            "template_text": (
                "مرحبًا {patient_name}\n\n"
                "تذكير من {clinic_name} 🏥\n"
                "لديكم موعد قريبًا.\n\n"
                "الطبيب: {doctor_name}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "يرجى الرد إذا احتجتم المساعدة."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_cancelled",
            "language": "en",
            "template_text": (
                "Hello {patient_name}\n\n"
                "Your appointment at {clinic_name} has been cancelled.\n\n"
                "Doctor: {doctor_name}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Please reply if you would like to reschedule."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "appointment_cancelled",
            "language": "ar",
            "template_text": (
                "مرحبًا {patient_name}\n\n"
                "تم إلغاء موعدكم في {clinic_name}.\n\n"
                "الطبيب: {doctor_name}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "يرجى الرد إذا رغبتم في إعادة جدولة الموعد."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "insurance_pending",
            "language": "en",
            "template_text": (
                "Hello {patient_name}\n\n"
                "Your appointment request at {clinic_name} is pending insurance review.\n\n"
                "Doctor: {doctor_name}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Our team may contact you if more insurance details are needed."
            ),
        },
        {
            "tenant_id": tenant_id,
            "template_key": "insurance_pending",
            "language": "ar",
            "template_text": (
                "مرحبًا {patient_name}\n\n"
                "طلب موعدكم في {clinic_name} قيد مراجعة التأمين.\n\n"
                "الطبيب: {doctor_name}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "قد يتواصل معكم فريقنا إذا احتجنا إلى معلومات تأمين إضافية."
            ),
        },
    ]

    for tpl in templates:
        await db.execute(
            text(
                """
                INSERT INTO message_templates (
                    tenant_id,
                    template_key,
                    language,
                    template_text,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :template_key,
                    :language,
                    :template_text,
                    TRUE,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tenant_id, template_key, language)
                DO NOTHING;
                """
            ),
            tpl,
        )


async def create_tenant_with_tokens(
    db: AsyncSession,
    *,
    tenant_id: str,
    clinic_name: str,
    default_language: str = "en",
    default_tone: str = "formal",
    timezone: str = "Asia/Riyadh",
    country_code: str | None = None,
    whatsapp_phone_id: str | None = None,
    whatsapp_verify_token: str | None = None,
) -> dict:
    tenant_id = (tenant_id or "").strip()
    clinic_name = (clinic_name or "").strip()
    default_language = (default_language or "en").strip().lower()
    default_tone = (default_tone or "formal").strip().lower()
    timezone = (timezone or "Asia/Riyadh").strip()

    if not tenant_id:
        return {"ok": False, "reason": "tenant_id_required"}

    if not clinic_name:
        return {"ok": False, "reason": "clinic_name_required"}

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,49}", tenant_id):
        return {
            "ok": False,
            "reason": "invalid_tenant_id",
            "hint": "Use 3-50 chars: lowercase letters, digits, _ or -",
        }

    if default_language not in {"en", "ar"}:
        return {"ok": False, "reason": "invalid_default_language"}

    if default_tone not in {"formal", "friendly"}:
        return {"ok": False, "reason": "invalid_default_tone"}

    chk = await db.execute(
        text(
            """
            SELECT 1
            FROM tenants
            WHERE tenant_id = :tenant_id
            LIMIT 1;
            """
        ),
        {"tenant_id": tenant_id},
    )
    if chk.first():
        return {"ok": False, "reason": "tenant_exists"}

    admin_token = _make_token("adm")
    reception_token = _make_token("rcp")
    public_api_key = _make_token("pk")

    await db.execute(
        text(
            """
            INSERT INTO tenants (
                tenant_id,
                clinic_name,
                status,
                default_language,
                default_tone,
                timezone,
                country_code,
                whatsapp_phone_id,
                whatsapp_verify_token,
                created_at,
                updated_at
            )
            VALUES (
                :tenant_id,
                :clinic_name,
                'ACTIVE',
                :default_language,
                :default_tone,
                :timezone,
                :country_code,
                :whatsapp_phone_id,
                :whatsapp_verify_token,
                NOW(),
                NOW()
            );
            """
        ),
        {
            "tenant_id": tenant_id,
            "clinic_name": clinic_name,
            "default_language": default_language,
            "default_tone": default_tone,
            "timezone": timezone,
            "country_code": country_code,
            "whatsapp_phone_id": whatsapp_phone_id,
            "whatsapp_verify_token": whatsapp_verify_token,
        },
    )

    await db.execute(
        text(
            """
            INSERT INTO tenant_tokens (
                tenant_id,
                admin_token,
                reception_token,
                public_api_key,
                created_at,
                updated_at
            )
            VALUES (
                :tenant_id,
                :admin_token,
                :reception_token,
                :public_api_key,
                NOW(),
                NOW()
            );
            """
        ),
        {
            "tenant_id": tenant_id,
            "admin_token": admin_token,
            "reception_token": reception_token,
            "public_api_key": public_api_key,
        },
    )

    # default tenant settings
    await db.execute(
        text(
            """
            INSERT INTO tenant_settings (
                tenant_id,
                clinic_name,
                logo_url,
                brand_color,
                timezone,
                language,
                whatsapp_greeting,
                ai_tone,
                reminder_hours_before,
                enable_ai_booking,
                enable_reception_direct_booking,
                created_at,
                updated_at
            )
            VALUES (
                :tenant_id,
                :clinic_name,
                NULL,
                NULL,
                :timezone,
                :language,
                :whatsapp_greeting,
                :ai_tone,
                24,
                TRUE,
                FALSE,
                NOW(),
                NOW()
            )
            ON CONFLICT (tenant_id)
            DO NOTHING;
            """
        ),
        {
            "tenant_id": tenant_id,
            "clinic_name": clinic_name,
            "timezone": timezone,
            "language": default_language,
            "ai_tone": default_tone,
            "whatsapp_greeting": (
                f"مرحبًا بكم في *{clinic_name}* 🏥"
                if default_language == "ar"
                else f"Welcome to *{clinic_name}* 🏥"
            ),
        },
    )

    # seed default WhatsApp automation templates
    await seed_default_message_templates(
        db,
        tenant_id=tenant_id,
        clinic_name=clinic_name,
    )

    # default trial subscription for new tenant
    await db.execute(
        text(
            """
            INSERT INTO subscriptions (
                tenant_id,
                plan_code,
                status,
                started_at,
                trial_ends_at,
                created_at,
                updated_at
            )
            VALUES (
                :tenant_id,
                'starter',
                'TRIAL',
                NOW(),
                NOW() + INTERVAL '14 day',
                NOW(),
                NOW()
            )
            ON CONFLICT (tenant_id)
            DO NOTHING;
            """
        ),
        {"tenant_id": tenant_id},
    )

    await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "clinic_name": clinic_name,
        "admin_token": admin_token,
        "reception_token": reception_token,
        "public_api_key": public_api_key,
        "default_language": default_language,
        "default_tone": default_tone,
        "timezone": timezone,
    }