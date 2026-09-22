import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class CompanyModel(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    tax_id = Column(String(50), unique=True, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)