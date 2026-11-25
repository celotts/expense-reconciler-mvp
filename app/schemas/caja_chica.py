"""Lo que entra y sale de la API de caja chica y de la huella fiscal.

Los importes son `Decimal` y se cuantizan a dos decimales con `ROUND_HALF_UP`
antes de llegar al servicio. Un `float` ahi no es un detalle de tipo: `0.1+0.2`
no es `0.3` en binario, y la diferencia entre el saldo de la caja y el gasto del
ticket se decide en centavos.

Quien firma cada movimiento (`created_by`) sale del token, nunca del cuerpo:
ver regla 23 de `AGENTS.md`. `responsable_id` NO es una firma: identifica a la
persona a cuyo cargo queda la caja, y el front la toma de la sesión en vez de
dejar que se escriba a mano.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.core.enums import EstadoCaja, OrigenFondo, TipoMovimientoCaja

_CENTAVOS = Decimal("0.01")


def _dos_decimales(valor: Decimal) -> Decimal:
    """Cuantiza con `ROUND_HALF_UP`, el criterio que usa un contador."""
    return Decimal(valor).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


class CajaChicaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    responsable_id: UUID
    nombre: str = Field(..., min_length=1, max_length=120)
    monto_inicial: Decimal = Field(..., ge=0)
    umbral_alerta: Decimal = Field(default=Decimal("0.00"), ge=0)

    @field_validator("nombre")
    @classmethod
    def _nombre_no_vacio(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("El nombre de la caja no puede estar vacio")
        return valor

    @field_validator("monto_inicial", "umbral_alerta")
    @classmethod
    def _importes(cls, valor: Decimal) -> Decimal:
        return _dos_decimales(valor)


class CajaChicaUpdate(BaseModel):
    """`exclude_unset` en el router: ausente no es lo mismo que `null`."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    umbral_alerta: Decimal | None = Field(default=None, ge=0)
    responsable_id: UUID | None = None

    @field_validator("umbral_alerta")
    @classmethod
    def _importes(cls, valor: Decimal | None) -> Decimal | None:
        return None if valor is None else _dos_decimales(valor)


class CajaChicaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    responsable_id: UUID
    nombre: str
    monto_inicial: Decimal
    saldo_actual: Decimal
    umbral_alerta: Decimal
    estado: EstadoCaja
    opened_at: datetime
    closed_at: datetime | None

    @computed_field
    @property
    def saldo_bajo_umbral(self) -> bool:
        """`<=` y no `<`: un saldo igual al umbral ya no cubre un gasto menor.

        Se calcula en el servidor y se manda en el JSON: si el front lo calcula,
        dos clientes pueden defender umbrales distintos con el mismo saldo.
        """
        return self.saldo_actual <= self.umbral_alerta


class MovimientoCajaCreate(BaseModel):
    """Un movimiento del kardex de la caja.

    `company_id` es opcional a proposito: el router lo toma de la caja y el
    servicio compara los dos. Si el cliente lo manda y no coincide, es 409; si
    no lo manda, no se inventa una empresa.

    `ticket_id` solo tiene sentido en `gasto_ticket`. La comprobacion de que el
    ticket existe y es de la misma empresa es del servicio, porque el router no
    tiene la session de negocio.
    """

    model_config = ConfigDict(extra="forbid")

    company_id: UUID | None = None
    tipo: TipoMovimientoCaja
    monto: Decimal = Field(..., gt=0)
    origen_fondo: OrigenFondo | None = None
    banco_nombre: str | None = Field(default=None, max_length=80)
    referencia_bancaria: str | None = Field(default=None, max_length=100)
    ticket_id: UUID | None = None
    notas: str | None = Field(default=None, max_length=500)
    closed_at: datetime | None = None
    # `ajuste` es la correccion de un conteo. No tiene signo propio: el signo
    # vive aqui, para que el tipo siga respondiendo solo "que paso".
    resta: bool = False

    @field_validator("monto")
    @classmethod
    def _importes(cls, valor: Decimal) -> Decimal:
        valor = _dos_decimales(valor)
        if valor <= 0:
            # Un monto de 0.004 se cuantiza a 0.00: no es un gasto, es un hueco.
            raise ValueError("El monto debe ser mayor que cero despues de redondear")
        return valor

    @field_validator("banco_nombre", "referencia_bancaria", "notas")
    @classmethod
    def _texto(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return valor.strip() or None

    @model_validator(mode="after")
    def _coherencia(self) -> "MovimientoCajaCreate":
        if self.tipo is TipoMovimientoCaja.REPOSICION and self.origen_fondo is None:
            raise ValueError("La reposicion debe decir si el fondo viene de efectivo o de banco")
        if self.origen_fondo is OrigenFondo.BANCO and not self.banco_nombre:
            raise ValueError("Si el fondo viene de banco, banco_nombre es obligatorio")
        if self.origen_fondo is OrigenFondo.EFECTIVO and self.banco_nombre:
            raise ValueError("Un fondo en efectivo no lleva banco_nombre")
        if self.tipo is TipoMovimientoCaja.GASTO_TICKET and self.ticket_id is None:
            raise ValueError("Un gasto_ticket debe llevar ticket_id")
        if self.tipo is not TipoMovimientoCaja.GASTO_TICKET and self.ticket_id is not None:
            raise ValueError("Solo gasto_ticket puede llevar ticket_id")
        if self.tipo is TipoMovimientoCaja.CIERRE and self.closed_at is None:
            raise ValueError("Un cierre debe llevar closed_at")
        if self.resta and self.tipo is not TipoMovimientoCaja.AJUSTE:
            raise ValueError("Solo un ajuste puede restar del saldo")
        return self


class CierreCajaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closed_at: datetime | None = None


class MovimientoCajaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    caja_chica_id: UUID
    ticket_id: UUID | None
    tipo: TipoMovimientoCaja
    origen_fondo: OrigenFondo | None
    banco_nombre: str | None
    referencia_bancaria: str | None
    monto: Decimal
    notas: str | None
    created_by: UUID
    created_at: datetime


class CajaChicaConMovimientos(CajaChicaResponse):
    movimientos: list[MovimientoCajaResponse] = Field(default_factory=list)


class BuscarDuplicadoRequest(BaseModel):
    """La huella fiscal que se quiere comprobar antes de vincular un gasto."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    provider_tax_id: str | None = None
    folio: str | None = None
    expense_date: date
    total_amount: Decimal
    excluir_ticket_id: UUID | None = None

    @field_validator("total_amount")
    @classmethod
    def _importes(cls, valor: Decimal) -> Decimal:
        return _dos_decimales(valor)

    @field_validator("provider_tax_id", "folio")
    @classmethod
    def _texto(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return valor.strip().upper() or None


class DuplicadoFiscalItem(BaseModel):
    """El comprobante previo, con los datos que permiten compararlo a mano."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    company_id: UUID
    provider_name: str
    provider_tax_id: str | None
    folio: str | None
    expense_date: date
    total_amount: Decimal
    hash_duplicado: str | None
    estado_conciliacion: str | None
    source_file: str | None
    created_at: datetime | None


class DuplicadoFiscalResponse(BaseModel):
    es_duplicado: bool
    hash_duplicado: str | None
    candidatos: list[DuplicadoFiscalItem] = Field(default_factory=list)
    mensaje: str

    @model_validator(mode="after")
    def _coherencia(self) -> "DuplicadoFiscalResponse":
        # Un mensaje que dice "hay duplicado" con una lista vacia entrena al
        # operador a ignorar el aviso, que es peor que no tener aviso.
        if self.es_duplicado and not self.candidatos:
            raise ValueError("Un duplicado debe nombrar al comprobante previo")
        return self


class GrupoDuplicados(BaseModel):
    hash_duplicado: str | None
    # El primero es el original; el resto no cuenta dos veces.
    tickets: list[DuplicadoFiscalItem]
    monto_total: Decimal


class ResumenDuplicados(BaseModel):
    company_id: UUID
    grupos: list[GrupoDuplicados] = Field(default_factory=list)
    # Suma de conciliados contando una sola vez cada huella fiscal. El tablero
    # no suma los tickets a ciegas: un reenvio del mismo papel no es gasto nuevo.
    total_conciliado_sin_duplicados: Decimal = Decimal("0.00")
    advertencia: str | None = None