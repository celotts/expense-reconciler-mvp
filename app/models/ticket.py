import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import TIMESTAMP, Column, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class TicketModel(Base):
    __tablename__ = "tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    provider_name = Column(String(150), nullable=False)
    provider_tax_id = Column(String(50), nullable=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), default=Decimal("0.00"))
    expense_date = Column(Date, nullable=False)
    category = Column(String(100), nullable=True)
    raw_text = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    company = relationship("CompanyModel", backref="tickets")
    reconciliations = relationship("ReconciliationModel", back_populates="ticket")