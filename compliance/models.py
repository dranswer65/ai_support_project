from sqlalchemy import Column, Integer, String, DateTime, JSON, Index
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from datetime import datetime


Base = declarative_base()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)

    # Multi-tenant key (must never be NULL)
    client_id = Column(String, nullable=False, index=True)

    event_type = Column(String, nullable=False)

    # Use JSON for payload (works well in Postgres as JSON/JSONB depending on dialect)
    payload = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Fast tenant + time queries
        Index("idx_client_time", "client_id", "created_at"),
    )
