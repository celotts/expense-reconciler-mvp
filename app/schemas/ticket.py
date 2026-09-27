import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos (YYMMDD), 3 homoclave
RFC_REGEX = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")

UNKNOWN_PROVIDER = "Unknown Provider"


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
        if name == "Unknown Provider":
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
        if name == "Unknown Provider":
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


class TicketResponse(TicketBase):
    id: UUID
    company_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)