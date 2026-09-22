from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccountingMappingBase(BaseModel):
    software_name: str = Field(..., min_length=1, max_length=100)
    column_mappings: dict[str, Any]


class AccountingMappingCreate(AccountingMappingBase):
    company_id: UUID


class AccountingMappingResponse(AccountingMappingBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)