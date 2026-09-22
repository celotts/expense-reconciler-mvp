from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReconciliationBase(BaseModel):
    ticket_id: UUID | None = None
    bank_transaction_id: UUID | None = None
    match_status: str = Field(..., pattern="^(PERFECT|MANUAL|DISCREPANCY)$")


class ReconciliationCreate(ReconciliationBase):
    pass


class ReconciliationResponse(ReconciliationBase):
    id: UUID
    matched_at: datetime
    ticket: Optional["TicketResponse"] = None
    bank_transaction: Optional["BankTransactionResponse"] = None

    model_config = ConfigDict(from_attributes=True)


# Forward references
from app.schemas.bank_transaction import BankTransactionResponse
from app.schemas.ticket import TicketResponse

ReconciliationResponse.model_rebuild()


class ReconciliationRunRequest(BaseModel):
    company_id: UUID
    date_from: date | None = None
    date_to: date | None = None
    amount_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    date_tolerance_days: int = Field(default=3, ge=0)


class ReconciliationMatchDetail(BaseModel):
    ticket_id: UUID
    ticket_provider: str
    ticket_amount: Decimal
    ticket_date: date
    bank_transaction_id: UUID
    bank_description: str
    bank_amount: Decimal
    bank_date: date
    match_status: str
    amount_diff: Decimal
    date_diff_days: int


class ReconciliationRunResponse(BaseModel):
    total_tickets: int
    total_bank_transactions: int
    perfect_matches: int
    manual_review: int
    discrepancies: int
    unmatched_tickets: int
    unmatched_bank_transactions: int
    matches: list[ReconciliationMatchDetail]