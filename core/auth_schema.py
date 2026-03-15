from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_auth_tables(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        mobile TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE,
        last_login_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """))

    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS roles (
        id BIGSERIAL PRIMARY KEY,
        role_code TEXT NOT NULL UNIQUE,
        role_name TEXT NOT NULL,
        description TEXT,
        is_system BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """))

    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS permissions (
        id BIGSERIAL PRIMARY KEY,
        permission_code TEXT NOT NULL UNIQUE,
        permission_name TEXT NOT NULL,
        module_name TEXT,
        description TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """))

    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS role_permissions (
        role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (role_id, permission_id)
    )
    """))

    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS tenant_users (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
        doctor_key TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tenant_id, user_id, role_id)
    )
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant_id
    ON tenant_users (tenant_id)
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_tenant_users_user_id
    ON tenant_users (user_id)
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_tenant_users_doctor_key
    ON tenant_users (doctor_key)
    """))

    await db.commit()


async def seed_auth_defaults(db: AsyncSession):
    # roles
    await db.execute(text("""
    INSERT INTO roles (role_code, role_name, description, is_system)
    VALUES
        ('PLATFORM_ADMIN', 'Platform Admin', 'Full platform access', TRUE),
        ('CLINIC_ADMIN', 'Clinic Admin', 'Clinic-level administration', TRUE),
        ('RECEPTION', 'Reception', 'Reception dashboard and patient flow', TRUE),
        ('DOCTOR', 'Doctor', 'Doctor schedule and own appointments', TRUE)
    ON CONFLICT (role_code) DO NOTHING
    """))

    # permissions
    await db.execute(text("""
    INSERT INTO permissions (permission_code, permission_name, module_name, description)
    VALUES
        ('platform.analytics.view', 'View platform analytics', 'platform', 'Platform analytics access'),
        ('platform.tenants.manage', 'Manage tenants', 'platform', 'Create/update/manage tenants'),
        ('platform.billing.manage', 'Manage platform billing', 'platform', 'Subscriptions and billing'),
        ('clinic.settings.view', 'View clinic settings', 'clinic', 'View clinic settings'),
        ('clinic.settings.edit', 'Edit clinic settings', 'clinic', 'Edit clinic settings'),
        ('reception.dashboard.view', 'View reception dashboard', 'reception', 'Reception dashboard access'),
        ('appointments.view', 'View appointments', 'appointments', 'View appointments'),
        ('appointments.create', 'Create appointments', 'appointments', 'Create appointments'),
        ('appointments.edit', 'Edit appointments', 'appointments', 'Edit/reschedule/cancel appointments'),
        ('calendar.view', 'View calendar', 'calendar', 'View calendar'),
        ('calendar.manage', 'Manage calendar', 'calendar', 'Generate slots and time off'),
        ('doctors.view', 'View doctors', 'doctors', 'View doctors'),
        ('doctors.manage', 'Manage doctors', 'doctors', 'Create/update doctors'),
        ('patients.view', 'View patients', 'patients', 'View patient history'),
        ('patients.export', 'Export patients', 'patients', 'Export patient CSV/history'),
        ('billing.view', 'View internal billing', 'billing', 'View internal billing'),
        ('billing.collect', 'Collect payments', 'billing', 'Collect payment'),
        ('billing.invoice.create', 'Create invoices', 'billing', 'Create invoice')
    ON CONFLICT (permission_code) DO NOTHING
    """))

    # role -> permissions
    await db.execute(text("""
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON p.permission_code IN (
        'platform.analytics.view',
        'platform.tenants.manage',
        'platform.billing.manage',
        'clinic.settings.view',
        'clinic.settings.edit',
        'reception.dashboard.view',
        'appointments.view',
        'appointments.create',
        'appointments.edit',
        'calendar.view',
        'calendar.manage',
        'doctors.view',
        'doctors.manage',
        'patients.view',
        'patients.export',
        'billing.view',
        'billing.collect',
        'billing.invoice.create'
    )
    WHERE r.role_code = 'PLATFORM_ADMIN'
    ON CONFLICT DO NOTHING
    """))

    await db.execute(text("""
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON p.permission_code IN (
        'clinic.settings.view',
        'clinic.settings.edit',
        'reception.dashboard.view',
        'appointments.view',
        'appointments.create',
        'appointments.edit',
        'calendar.view',
        'calendar.manage',
        'doctors.view',
        'doctors.manage',
        'patients.view',
        'patients.export',
        'billing.view',
        'billing.collect',
        'billing.invoice.create'
    )
    WHERE r.role_code = 'CLINIC_ADMIN'
    ON CONFLICT DO NOTHING
    """))

    await db.execute(text("""
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON p.permission_code IN (
        'reception.dashboard.view',
        'appointments.view',
        'appointments.create',
        'appointments.edit',
        'calendar.view',
        'patients.view',
        'billing.view',
        'billing.collect',
        'billing.invoice.create'
    )
    WHERE r.role_code = 'RECEPTION'
    ON CONFLICT DO NOTHING
    """))

    await db.execute(text("""
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
    FROM roles r
    JOIN permissions p ON p.permission_code IN (
        'appointments.view',
        'calendar.view',
        'patients.view'
    )
    WHERE r.role_code = 'DOCTOR'
    ON CONFLICT DO NOTHING
    """))

    await db.commit()