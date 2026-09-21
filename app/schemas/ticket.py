from uuid import UUID
from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class TicketBase(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=150)
    provider_tax_id: Optional[str] = Field(None, max_length=50)
    total_amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    expense_date: date
    category: Optional[str] = Field(None, max_length=100)
    raw_text: Optional[str] = None


class TicketCreate(TicketBase):
    company_id: UUID


class TicketUpdate(BaseModel):
    provider_name: Optional[str] = Field(None, min_length=1, max_length=150)
    provider_tax_id: Optional[str] = Field(None, max_length=50)
    total_amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=2)
    tax_amount: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=2)
    expense_date: Optional[date] = None
    category: Optional[str] = Field(None, max_length=100)
    raw_text: Optional[str] = None


class TicketResponse(TicketBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)