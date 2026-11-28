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


class DesactivarEmpresaRequest(BaseModel):
    """La nota de la baja. El cuerpo es opcional por contrato antiguo."""

    motivo: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class CompanyResponse(CompanyBase):
    id: UUID
    created_at: datetime
    # Lo calcula la fila, no el front: saber si una empresa esta de baja no es
    # una preferencia de pantalla, es el estado que el servidor va a obedecer.
    estado: EstadoEmpresa
    # `puede_eliminarse` tambien lo decide el servidor: el boton de la pantalla
    # no puede adivinar cuantas filas de trabajo tiene la empresa ni si es la
    # ultima activa con datos en el sistema.
    puede_eliminarse: bool = True
    motivo_baja: str | None = None
    bajada_en: datetime | None = None
    bajada_por: str | None = None

    model_config = ConfigDict(from_attributes=True)