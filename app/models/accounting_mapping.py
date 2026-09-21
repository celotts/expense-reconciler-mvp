import uuid
from datetime import datetime
from sqlalchemy import Column, String, ForeignKey, TIMESTAMP, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class AccountingMappingModel(Base):
    __tablename__ = "accounting_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    software_name = Column(String(100), nullable=False)
    column_mappings = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    company = relationship("CompanyModel", backref="accounting_mappings")