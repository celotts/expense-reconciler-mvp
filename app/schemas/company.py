from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    tax_id: str = Field(..., min_length=1, max_length=50)


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    tax_id: str | None = Field(None, min_length=1, max_length=50)
    # Cambiar el plan es decision comercial, no del usuario: el endpoint
    # lo permite pero el front no lo expone. Valores validos hoy: 'basico'
    # y 'demo' (ver `app/models/company.py`).
    plan: str | None = Field(None, min_length=1, max_length=20)
    trial_hasta: datetime | None = None


class CompanyResponse(CompanyBase):
    id: UUID
    created_at: datetime
    # Plan comercial, fin de la prueba y uso del mes (P0-1). El plan es hoy
    # un dato, no una cerradura: no bloquea nada hasta que exista facturacion.
    plan: str = "basico"
    trial_hasta: datetime | None = None
    tickets_usados_este_mes: int = 0

    model_config = ConfigDict(from_attributes=True)