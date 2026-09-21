import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import Column, String, Text, Numeric, Date, Boolean, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class BankTransactionModel(Base):
    __tablename__ = "bank_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    transaction_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    reference = Column(String(100), nullable=True)
    is_reconciled = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    company = relationship("CompanyModel", backref="bank_transactions")
    reconciliations = relationship("ReconciliationModel", back_populates="bank_transaction")