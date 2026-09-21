from uuid import UUID
from datetime import datetime
from typing import Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class AccountingMappingBase(BaseModel):
    software_name: str = Field(..., min_length=1, max_length=100)
    column_mappings: Dict[str, Any]


class AccountingMappingCreate(AccountingMappingBase):
    company_id: UUID


class AccountingMappingResponse(AccountingMappingBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)