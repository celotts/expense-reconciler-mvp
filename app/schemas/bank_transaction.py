from uuid import UUID
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class BankTransactionBase(BaseModel):
    transaction_date: date
    amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    description: str = Field(..., min_length=1)
    reference: Optional[str] = Field(None, max_length=100)


class BankTransactionCreate(BankTransactionBase):
    company_id: UUID


class BankTransactionUpdate(BaseModel):
    transaction_date: Optional[date] = None
    amount: Optional[Decimal] = Field(None, max_digits=12, decimal_places=2)
    description: Optional[str] = Field(None, min_length=1)
    reference: Optional[str] = Field(None, max_length=100)
    is_reconciled: Optional[bool] = None


class BankTransactionResponse(BankTransactionBase):
    id: UUID
    company_id: UUID
    is_reconciled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)