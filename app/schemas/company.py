from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    tax_id: str = Field(..., min_length=1, max_length=50)


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    tax_id: str | None = Field(None, min_length=1, max_length=50)


class CompanyResponse(CompanyBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)