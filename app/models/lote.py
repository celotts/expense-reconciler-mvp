"""Un lote de digitalizacion: el grupo de tickets que se escanearon juntos.

Por que existe esta tabla y no basta con `tickets.expense_date`:

La fecha del papel dice EN QUE MES se gasto. No dice con que otros papeles se
digitalizo, ni si ese grupo ya se reviso, ni si el contador lo dio por cerrado.
Sin un lote, "los tickets de octubre" son una consulta por fecha que cualquiera
puede reescribir, y "cerrar octubre" no tiene donde guardarse: `cierres_periodo`
registra que un periodo se firmo, pero no que papeles entraron en el.

El lote es el grupo: un folio (`LOT-2026-10-001`), un periodo, un estado, y los
tickets y archivos que pertenecen a el. El cierre del periodo se sigue
registrando en `cierres_periodo`; el lote es lo que se cierra, no el cierre
mismo.

QUE NO HACE ESTE MODULO
-----------------------
No asigna tickets ni archivos al lote. Eso lo hace `scan_service` cuando lee
(otro trabajo, con este contrato): un archivo solo entra a un lote ABIERTO o EN
REVISION, nunca a uno CERRADO, y al asignarlo actualiza `total_escaneado` e
`importe_total`. Esos dos contadores son un CACHE de lo que dicen `tickets` y
`scan_files`: la verdad esta en las filas, y `GET /lotes/{id}` la vuelve a
contar en vivo. Si un dia discrepan, manda la cuenta en vivo.

`lote_config`: la configuracion por empresa
-------------------------------------------
Vive como JSON en `companies.lote_config` y NO como tabla aparte, por tres
razones:

1. Son cuatro escalares (`tipo_periodo`, `exige_total_esperado`,
   `permite_reapertura`, `prefijo_folio`) que se leen siempre con la empresa y
   nunca se buscan por su valor: no hay consulta "que empresas exigen total"
   que justifique una tabla ni un indice.
2. No tiene historia: cambiar el prefijo no necesita saber cual era antes.
3. Una tabla mas es un modelo mas, una migracion mas y un join mas, para un
   dato que cabe en la fila que ya se lee.

`NULL` significa "sin configurar": se aplican los defaults de
`CONFIG_LOTE_DEFECTO`. Un JSON parcial tambien vale: lo que no venga se
completa con el default, asi que anadir una clave nueva no obliga a migrar las
empresas que ya existen.

Sobre el periodo de un ticket (regla de extemporaneidad)
--------------------------------------------------------
Un ticket pertenece al periodo de SU FECHA DE PAPEL, no al del dia en que se
escaneo: un ticket del 06/10/2026 escaneado el 06/11/2026 pertenece a octubre.
Y es EXTEMPORANEO cuando ese periodo es anterior al periodo del escaneo, es
decir, cuando `fecha_ticket < inicio del periodo de escaneo` (ver
`es_extemporaneo`). No es un error: es una senal para el contador de que el
papel llego tarde, y el lote al que pertenece sigue siendo el de octubre.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow

TipoPeriodo = Literal["mensual", "quincenal", "semanal"]

TIPOS_PERIODO = ("mensual", "quincenal", "semanal")
ESTADOS_LOTE = ("ABIERTO", "EN_REVISION", "CERRADO")

ESTADO_ABIERTO = "ABIERTO"
ESTADO_EN_REVISION = "EN_REVISION"
ESTADO_CERRADO = "CERRADO"

# Los defaults de `companies.lote_config`. Ver el docstring del modulo: un
# JSON parcial se completa con estos, y un NULL es esto entero.
CONFIG_LOTE_DEFECTO = {
    "tipo_periodo": "mensual",
    "exige_total_esperado": False,
    "permite_reapertura": True,
    "prefijo_folio": "LOT",
}


def config_lote_para(empresa) -> dict:
    """La configuracion de lotes de una empresa, con defaults aplicados.

    `empresa` es un `CompanyModel` (o cualquier objeto con `lote_config`).
    Un JSON parcial, un NULL o una clave desconocida no rompen nada: lo que
    falta se completa con el default y lo que sobra se ignora. Un
    `tipo_periodo` que no existe vuelve a "mensual" en vez de reventar la
    creacion de un lote, porque una configuracion corrupta no puede dejar sin
    digitalizar a una empresa entera.
    """
    guardado = getattr(empresa, "lote_config", None) or {}
    if not isinstance(guardado, dict):
        guardado = {}
    config = {**CONFIG_LOTE_DEFECTO, **{k: v for k, v in guardado.items() if k in CONFIG_LOTE_DEFECTO}}
    if config["tipo_periodo"] not in TIPOS_PERIODO:
        config["tipo_periodo"] = CONFIG_LOTE_DEFECTO["tipo_periodo"]
    prefijo = str(config["prefijo_folio"] or "").strip().upper()
    config["prefijo_folio"] = prefijo or CONFIG_LOTE_DEFECTO["prefijo_folio"]
    return config


def periodo_de_fecha(
    fecha: date, tipo_periodo: TipoPeriodo = "mensual"
) -> tuple[int, int, date, date]:
    """El periodo al que pertenece una fecha: (ejercicio, mes, inicio, fin).

    - mensual: el mes natural. 2026-10-06 -> (2026, 10, 01/10, 31/10).
    - quincenal: del 1 al 15, o del 16 a fin de mes. 2026-10-20 ->
      (2026, 10, 16/10, 31/10).
    - semanal: de lunes a domingo (semana ISO). 2026-10-06 (martes) ->
      (2026, 10, 05/10, 11/10). El ejercicio y el mes son los del LUNES, no
      los de la fecha: una semana que cruza de mes pertenece al mes en que
      empieza, para que una misma semana no se parta en dos lotes.

    Es funcion pura a proposito: la usan el API (para derivar las fechas de un
    lote cuando no se mandan), los tests, y manana `scan_service` para decidir
    a que lote va un ticket por su fecha de papel.
    """
    if tipo_periodo == "mensual":
        ultimo = calendar.monthrange(fecha.year, fecha.month)[1]
        return fecha.year, fecha.month, date(fecha.year, fecha.month, 1), date(
            fecha.year, fecha.month, ultimo
        )
    if tipo_periodo == "quincenal":
        ultimo = calendar.monthrange(fecha.year, fecha.month)[1]
        if fecha.day <= 15:
            return fecha.year, fecha.month, date(fecha.year, fecha.month, 1), date(
                fecha.year, fecha.month, 15
            )
        return (
            fecha.year,
            fecha.month,
            date(fecha.year, fecha.month, 16),
            date(fecha.year, fecha.month, ultimo),
        )
    if tipo_periodo == "semanal":
        lunes = fecha - timedelta(days=fecha.weekday())
        domingo = lunes + timedelta(days=6)
        return lunes.year, lunes.month, lunes, domingo
    raise ValueError(f"tipo_periodo desconocido: {tipo_periodo!r}")


def es_extemporaneo(
    fecha_ticket: date,
    fecha_escaneo: date,
    tipo_periodo: TipoPeriodo = "mensual",
) -> bool:
    """Si un ticket llego tarde: su periodo es anterior al del escaneo.

    La regla es `fecha_ticket < inicio del periodo de escaneo`. Un ticket de
    octubre escaneado en noviembre es extemporaneo; uno de noviembre escaneado
    en noviembre, no. Una fecha FUTURA (ticket de diciembre escaneado en
    noviembre) NO es extemporanea: es otro problema (fecha futura) y no se
    esconde bajo este nombre.
    """
    _, _, inicio_escaneo, _ = periodo_de_fecha(fecha_escaneo, tipo_periodo)
    return fecha_ticket < inicio_escaneo


class LoteModel(Base):
    """Un grupo de tickets digitalizados juntos, con su folio y su estado."""

    __tablename__ = "lotes"
    __table_args__ = (
        # El folio es unico POR EMPRESA, no global: dos empresas pueden tener
        # su propio LOT-2026-10-001. Sin `company_id` en la constraint, la
        # segunda empresa no podria abrir su primer lote de octubre.
        UniqueConstraint("company_id", "folio", name="ux_lotes_empresa_folio"),
        CheckConstraint("mes BETWEEN 1 AND 12", name="ck_lotes_mes"),
        CheckConstraint("fecha_inicio <= fecha_fin", name="ck_lotes_fechas"),
        CheckConstraint(
            "estado IN ('ABIERTO', 'EN_REVISION', 'CERRADO')",
            name="ck_lotes_estado",
        ),
        CheckConstraint(
            "tipo_periodo IN ('mensual', 'quincenal', 'semanal')",
            name="ck_lotes_tipo_periodo",
        ),
        # La lista de la pantalla: los lotes de la empresa, por periodo y
        # estado. Sin este indice, filtrar por mes es un seq scan.
        Index("ix_lotes_empresa_periodo", "company_id", "ejercicio", "mes", "estado"),
        Index("ix_lotes_padre", "lote_padre_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # LOT-2026-10-001: prefijo (de la config de la empresa) + ejercicio +
    # mes + secuencial por empresa y mes. Lo genera el API, no el cliente:
    # aceptar el folio del cuerpo seria dejar que dos lotes nazcan con el
    # mismo y que la constraint decida cual vive.
    folio = Column(String(30), nullable=False)
    nombre = Column(String(150), nullable=False)
    ejercicio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    tipo_periodo = Column(String(20), nullable=False, default="mensual")
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    estado = Column(String(20), nullable=False, default=ESTADO_ABIERTO)

    # Cuantos tickets se ESPERABAN en el lote, si alguien lo sabe. NULL y no
    # cero: un lote sin expectativa no es un lote al que le falten todos, y un
    # 0 haria que "faltan 0" pareciera "completo".
    total_esperado = Column(Integer, nullable=True)
    # Contadores CACHE (ver el docstring del modulo): los mantiene
    # `scan_service` al asignar archivos. La cuenta en vivo esta en
    # `GET /lotes/{id}`.
    total_escaneado = Column(Integer, nullable=False, default=0)
    importe_total = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    # El lote del que este es complemento (o alcance). NULL = lote normal.
    # `SET NULL` y no `CASCADE`: borrar un lote padre no puede borrar el
    # complemento que ya tiene tickets, que es justo el historico que el
    # complemento existe para conservar.
    lote_padre_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lotes.id", ondelete="SET NULL"),
        nullable=True,
    )
    # El motivo de la reapertura, o de que exista el complemento. Es texto
    # libre y no un codigo: lo lee una persona, y "falto un ticket de Soriana"
    # dice mas que un codigo que hay que ir a buscar a otra tabla.
    motivo = Column(Text, nullable=True)

    # Quien lo creo, desde el token (regla 23). Texto y no FK, como
    # `tickets.spot_checked_by`: la firma tiene que sobrevivir a la baja de
    # la cuenta.
    creado_por = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    cerrado_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # `passive_deletes=True`: la FK es `ON DELETE CASCADE` en la base y sin
    # este `True` el ORM manda `UPDATE lotes SET company_id = NULL` antes del
    # DELETE de la empresa, que revienta con `NotNullViolationError`. Mismo
    # argumento que en `app/models/ticket.py`.
    company = relationship(
        "CompanyModel", backref="lotes", passive_deletes=True
    )
    padre = relationship(
        "LoteModel", remote_side=[id], back_populates="complementarios"
    )
    complementarios = relationship("LoteModel", back_populates="padre")
    tickets = relationship("TicketModel", back_populates="lote")
    archivos = relationship("ScanFileModel", back_populates="lote")
