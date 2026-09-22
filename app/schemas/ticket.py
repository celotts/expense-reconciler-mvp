from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TicketBase(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    expense_date: date
    category: str | None = Field(None, max_length=100)
    raw_text: str | None = None


class TicketCreate(TicketBase):
    company_id: UUID


class TicketUpdate(BaseModel):
    provider_name: str | None = Field(None, min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    expense_date: date | None = None
    category: str | None = Field(None, max_length=100)
    raw_text: str | None = None


class TicketResponse(TicketBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)