import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Reexportada: los schemas son la frontera publica y quien importa de aqui
# no necesita saber que el contrato vive en core.enums.
from app.core.enums import UNKNOWN_PROVIDER as UNKNOWN_PROVIDER

# RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos (YYMMDD), 3 homoclave
RFC_REGEX = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")



class TicketBase(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    expense_date: date
    category: str | None = Field(None, max_length=100)
    raw_text: str | None = None

    @field_validator("provider_name")
    @classmethod
    def _reject_unknown_provider(cls, v: str) -> str:
        """El parser emite "Unknown Provider" cuando no logra leer el emisor.
        Aceptarlo guardaria un ticket inservible para la conciliacion."""
        name = v.strip()
        if not name:
            raise ValueError("provider_name no puede estar vacio")
        if name == UNKNOWN_PROVIDER:
            raise ValueError(
                "La extraccion no identifico al proveedor. Corrigelo antes de guardar."
            )
        return name

    @field_validator("provider_tax_id")
    @classmethod
    def _validate_rfc(cls, v: str | None) -> str | None:
        """RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos, 3 alfanumericos."""
        if v is None or not v.strip():
            return None
        rfc = v.strip().upper()
        if not RFC_REGEX.fullmatch(rfc):
            raise ValueError(
                f"RFC invalido: {rfc!r}. Formato esperado: "
                "3-4 letras, 6 digitos (YYMMDD) y 3 caracteres alfanumericos."
            )
        return rfc

    @model_validator(mode="after")
    def _tax_not_greater_than_total(self) -> "TicketBase":
        if self.tax_amount > self.total_amount:
            raise ValueError(
                f"tax_amount ({self.tax_amount}) no puede ser mayor "
                f"que total_amount ({self.total_amount})."
            )
        return self


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

    @field_validator("provider_name")
    @classmethod
    def _reject_unknown_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        name = v.strip()
        if not name:
            raise ValueError("provider_name no puede estar vacio")
        if name == UNKNOWN_PROVIDER:
            raise ValueError(
                "La extraccion no identifico al proveedor. Corrigelo antes de guardar."
            )
        return name

    @field_validator("provider_tax_id")
    @classmethod
    def _validate_rfc(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        rfc = v.strip().upper()
        if not RFC_REGEX.fullmatch(rfc):
            raise ValueError(
                f"RFC invalido: {rfc!r}. Formato esperado: "
                "3-4 letras, 6 digitos (YYMMDD) y 3 caracteres alfanumericos."
            )
        return rfc

    @model_validator(mode="after")
    def _tax_not_greater_than_total(self) -> "TicketUpdate":
        if (
            self.tax_amount is not None
            and self.total_amount is not None
            and self.tax_amount > self.total_amount
        ):
            raise ValueError(
                f"tax_amount ({self.tax_amount}) no puede ser mayor "
                f"que total_amount ({self.total_amount})."
            )
        return self


class TicketResponse(BaseModel):
    """Forma de LECTURA de un ticket.

    No hereda de TicketBase a proposito. Los validadores de TicketBase son de
    escritura: un ticket no puede CREARSE con total 0 ni con proveedor
    "Unknown Provider". Pero un ticket PENDIENTE existe precisamente porque
    tiene esos datos rotos: es la foto de un papel que la IA no pudo leer.

    Si esta clase heredara los validadores de entrada, la cola de revision
    reventaria con un 500 al intentar devolver justamente los tickets que
    existen para revision. El error sale de nuevo: un ticket ilegible es
    invisible, que es el unico resultado inaceptable.
    """

    id: UUID
    company_id: UUID
    provider_name: str
    provider_tax_id: str | None = None
    total_amount: Decimal
    tax_amount: Decimal
    expense_date: date
    category: str | None = None
    raw_text: str | None = None
    # La columna admite NULL, asi que la respuesta lo admite. Declararla
    # obligatoria convertia una fila con created_at nulo en un 500 al serializar,
    # y la fila con created_at nulo es justamente la que uno no quiere perder:
    # sin fecha de creacion no se puede calcular la antiguedad, y eso lo
    # vuelve mas importante verla, no menos.
    created_at: datetime | None = None
    confidence: Decimal | None = None
    confidence_source: str | None = None
    extraction_status: str
    source_type: str | None = None
    source_file: str | None = None
    validation_errors: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TicketReviewRequest(BaseModel):
    """Accion de revision humana sobre un ticket en cola.

    Aprobar exige datos validos. Es la contraparte del gate: si el humano
    confirma, los checks se vuelven a correr para que no entre a conciliacion
    algo que el gate habia bloqueado.
    """

    action: Literal["approve", "reject", "request_info"]
    notes: str | None = Field(None, max_length=2000)
    # Correcciones aplicadas durante la revision (opcional).
    provider_name: str | None = Field(None, min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    expense_date: date | None = None
    category: str | None = Field(None, max_length=100)


class TicketReviewQueueResponse(BaseModel):
    """Cola de revision agrupada por estado, para la pantalla de pendientes."""

    company_id: UUID | None = None
    total_open: int
    por_estado: dict[str, int]
    antiguedad_promedio_dias: float | None = None
    tickets: list[TicketResponse]