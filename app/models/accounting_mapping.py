import uuid

from sqlalchemy import JSON, TIMESTAMP, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow


class AccountingMappingModel(Base):
    __tablename__ = "accounting_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    software_name = Column(String(100), nullable=False)
    column_mappings = Column(JSON, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # `passive_deletes=True`: la FK es `ON DELETE CASCADE` en la base y sin este
    # `True` el ORM manda `UPDATE ... SET company_id=NULL` antes del DELETE.
    # Mismo argumento que en `app/models/ticket.py`.
    company = relationship(
        "CompanyModel", backref="accounting_mappings", passive_deletes=True
    )