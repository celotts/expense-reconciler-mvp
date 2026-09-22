from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BankTransactionBase(BaseModel):
    transaction_date: date
    amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    description: str = Field(..., min_length=1)
    reference: str | None = Field(None, max_length=100)


class BankTransactionCreate(BankTransactionBase):
    company_id: UUID


class BankTransactionUpdate(BaseModel):
    transaction_date: date | None = None
    amount: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    description: str | None = Field(None, min_length=1)
    reference: str | None = Field(None, max_length=100)
    is_reconciled: bool | None = None


class BankTransactionResponse(BankTransactionBase):
    id: UUID
    company_id: UUID
    is_reconciled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)