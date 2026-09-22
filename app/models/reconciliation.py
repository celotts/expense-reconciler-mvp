import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class ReconciliationModel(Base):
    __tablename__ = "reconciliations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    bank_transaction_id = Column(UUID(as_uuid=True), ForeignKey("bank_transactions.id", ondelete="SET NULL"), nullable=True)
    match_status = Column(String(50), nullable=False)
    matched_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    ticket = relationship("TicketModel", back_populates="reconciliations")
    bank_transaction = relationship("BankTransactionModel", back_populates="reconciliations")