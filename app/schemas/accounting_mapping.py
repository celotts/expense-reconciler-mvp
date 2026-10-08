from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccountingMappingBase(BaseModel):
    software_name: str = Field(..., min_length=1, max_length=100)
    column_mappings: dict[str, Any]


class AccountingMappingCreate(AccountingMappingBase):
    company_id: UUID


class AccountingMappingUpdate(BaseModel):
    """Lo que se puede corregir de un mapeo. Todo opcional.

    Mismo criterio que `ProductoUpdate`: `exclude_unset=True` en el router, para
    no borrar un `software_name` que el cliente no mando.

    `company_id` NO es editable, y no por omision: `AccountingMappingModel` no tiene
    `backref` a la empresa (`backref="accounting_mappings"`, que crea la relacion
    desde el lado del hijo) y mover el mapeo entre empresas haria que un
    `column_mappings` escrito para una quedara guiando el export CONTPAQI de otra.
    El export es el unico consumidor, asi que el error seria un archivo de
    polizas con las columnas de la empresa equivocada y sin marca de error.
    """

    model_config = ConfigDict(extra="forbid")

    software_name: str | None = Field(default=None, min_length=1, max_length=100)
    column_mappings: dict[str, Any] | None = None


class AccountingMappingResponse(AccountingMappingBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)