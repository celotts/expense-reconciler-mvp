"""Lo que entra y lo que sale de los lotes de digitalizacion.

`extra="forbid"` en todo lo que se escribe, por la regla 23 y sus vecinas: un
cliente que reenvia la fila entera —porque la leyo de la pantalla— tiene que
recibir un 422 con el nombre del campo, no un 200 que finge haber guardado lo
que le mandaron. En particular el FOLIO no se acepta del cuerpo: lo genera el
servidor, secuencial por empresa y mes, y aceptar uno del cliente seria dejar
que dos lotes nazcan con el mismo.

`fecha_inicio` y `fecha_fin` son opcionales en la creacion: si no vienen, el
lote cubre el periodo completo que toca al dia 1 de (`ejercicio`, `mes`) segun
`tipo_periodo` (el mes entero en mensual, la quincena que toca en quincenal,
la semana del dia 1 en semanal). Si viene una, tienen que venir las dos.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoteCreate(BaseModel):
    """Alta de un lote.

    `company_id` viene del cuerpo, como en `ScanRequest`: no hay multi-tenant
    (ver `AGENTS.md`) y el usuario del token no lleva empresa. La empresa se
    valida en el router (404 si no existe) antes de crear nada.
    """

    company_id: UUID
    nombre: str = Field(min_length=1, max_length=150)
    ejercicio: int = Field(ge=1900, le=2100)
    mes: int = Field(ge=1, le=12)
    tipo_periodo: Literal["mensual", "quincenal", "semanal"] = "mensual"
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    total_esperado: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _fechas_coherentes(self) -> LoteCreate:
        # Las dos o ninguna: una sola fecha es un periodo a medias.
        if (self.fecha_inicio is None) != (self.fecha_fin is None):
            raise ValueError("fecha_inicio y fecha_fin van juntas o no van")
        if self.fecha_inicio is not None and self.fecha_fin is not None:
            if self.fecha_inicio > self.fecha_fin:
                raise ValueError("fecha_inicio no puede ser posterior a fecha_fin")
            # El ejercicio y el mes del lote son los de su inicio: es lo que
            # hace que el folio LOT-2026-10-001 diga el periodo que cubre, y
            # que `periodo_de_fecha` no discrepe con lo guardado.
            if (
                self.fecha_inicio.year != self.ejercicio
                or self.fecha_inicio.month != self.mes
            ):
                raise ValueError(
                    "ejercicio y mes tienen que ser los de fecha_inicio"
                )
        return self


class LoteResponse(BaseModel):
    """Un lote, como lo ve la pantalla."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    folio: str
    nombre: str
    ejercicio: int
    mes: int
    tipo_periodo: str
    fecha_inicio: date
    fecha_fin: date
    estado: str
    total_esperado: int | None = None
    total_escaneado: int
    importe_total: Decimal
    lote_padre_id: UUID | None = None
    motivo: str | None = None
    creado_por: str | None = None
    created_at: datetime
    cerrado_at: datetime | None = None


class LoteDetalleResponse(LoteResponse):
    """Un lote con sus conteos en vivo.

    Los conteos se calculan desde `tickets` y `scan_files` en cada lectura,
    no desde `total_escaneado` / `importe_total` del lote, que son un cache
    que mantiene `scan_service`. Si discrepan, manda esto.

    `duplicados` cuenta los tickets de mas por grupo (proveedor + total +
    fecha): dos tickets iguales son 1 duplicado, tres son 2. Es la misma
    regla que `duplicados_por_contenido` del escaneo.
    """

    total_archivos: int
    total_tickets: int
    importe_total_vivo: Decimal
    duplicados: int


class ReabrirRequest(BaseModel):
    """Reabrir un lote cerrado. El motivo es obligatorio y lo dice el schema,
    no solo el servicio: el 422 sale antes de tocar la base."""

    motivo: str = Field(min_length=3, max_length=500)

    model_config = ConfigDict(extra="forbid")


class ComplementarioRequest(BaseModel):
    """Crear el lote complementario de uno que ya existe.

    Sin `nombre`, se llama "Complemento de {folio del padre}". El resto se
    hereda del padre (empresa, ejercicio, mes, fechas, tipo de periodo) y no
    se acepta del cuerpo: un complemento con otro periodo que su padre no es
    un complemento, es otro lote.
    """

    nombre: str | None = Field(default=None, min_length=1, max_length=150)

    model_config = ConfigDict(extra="forbid")
