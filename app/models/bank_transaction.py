import uuid

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow


class BankTransactionModel(Base):
    __tablename__ = "bank_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    transaction_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    reference = Column(String(100), nullable=True)
    is_reconciled = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # `passive_deletes=True`: la FK es `ON DELETE CASCADE` en la base, y sin
    # este `True` el ORM manda `UPDATE bank_transactions SET company_id=NULL`
    # antes del DELETE. Ver el mismo argumento en `app/models/ticket.py`.
    company = relationship(
        "CompanyModel", backref="bank_transactions", passive_deletes=True
    )
    reconciliations = relationship("ReconciliationModel", back_populates="bank_transaction")