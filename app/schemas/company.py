from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import EstadoEmpresa


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
    # Lo calcula la fila, no el front: saber si una empresa esta de baja no es
    # una preferencia de pantalla, es el estado que el servidor va a obedecer.
    estado: EstadoEmpresa

    model_config = ConfigDict(from_attributes=True)