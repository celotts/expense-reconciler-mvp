"""Caja chica y su kardex.

El saldo NO se calcula sumando movimientos aqui: `cajas_chicas.saldo_actual` es
una columna que se mueve **en la misma transaccion** que el movimiento, con un
`UPDATE ... WHERE saldo_actual >= :monto` (en Postgres ademas `SELECT FOR UPDATE`).
Sin esa condicion, dos gastos concurrentes pueden dejar el saldo negativo aunque
cada uno haya leido un saldo suficiente.

Los importes son `NUMERIC(12,2)` y viajan como `Decimal`. El tipo del movements
no decide el signo por convencion suelta: `TipoMovimientoCaja.suma` es la lectura
en Python y el servicio la replica literalmente al actualizar el saldo.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow


class CajaChicaModel(Base):
    __tablename__ = "cajas_chicas"
    __table_args__ = (
        CheckConstraint("length(trim(nombre)) > 0", name="ck_cajas_chicas_nombre_no_vacio"),
        CheckConstraint(
            "monto_inicial >= 0 AND umbral_alerta >= 0",
            name="ck_cajas_chicas_montos_no_negativos",
        ),
        CheckConstraint(
            "estado IN ('abierta', 'en_revision', 'cerrada')",
            name="ck_cajas_chicas_estado",
        ),
        # Cerrar sin hora no es cerrar: la fila diria `cerrada` sin que nadie
        # pueda decir cuando. La base lo prohibe igual que el servicio.
        CheckConstraint(
            "estado <> 'cerrada' OR closed_at IS NOT NULL",
            name="ck_cajas_chicas_cierre_coherente",
        ),
        Index("ix_cajas_chicas_company_estado", "company_id", "estado"),
        Index("ix_cajas_chicas_responsable", "company_id", "responsable_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    responsable_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    nombre = Column(String(120), nullable=False)
    monto_inicial = Column(Numeric(12, 2), nullable=False)
    saldo_actual = Column(Numeric(12, 2), nullable=False)
    umbral_alerta = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    estado = Column(String(20), nullable=False, default="abierta")
    opened_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    closed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    movimientos = relationship(
        "MovimientoCajaModel",
        back_populates="caja",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MovimientoCajaModel(Base):
    __tablename__ = "movimientos_caja"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('apertura', 'reposicion', 'gasto_ticket', 'ajuste', 'cierre')",
            name="ck_movimientos_caja_tipo",
        ),
        CheckConstraint(
            "origen_fondo IS NULL OR origen_fondo IN ('efectivo', 'banco')",
            name="ck_movimientos_caja_origen",
        ),
        # Un banco sin nombre no identifica de donde vino el dinero. La
        # referencia es opcional (no todos los depositos la traen); el nombre
        # no.
        # La misma regla del DDL, escrita sin `IS DISTINCT FROM` para que
        # SQLite (donde corre la suite) pueda crear la tabla y ejecutarla.
        CheckConstraint(
            "origen_fondo IS NULL OR origen_fondo <> 'banco' OR "
            "(banco_nombre IS NOT NULL AND length(trim(banco_nombre)) > 0)",
            name="ck_movimientos_caja_banco_cuando_banco",
        ),
        CheckConstraint("monto >= 0", name="ck_movimientos_caja_monto_no_negativo"),
        Index("ix_movimientos_caja_company_caja", "company_id", "caja_chica_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    caja_chica_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cajas_chicas.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id = Column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    tipo = Column(String(20), nullable=False)
    origen_fondo = Column(String(20), nullable=True)
    banco_nombre = Column(String(80), nullable=True)
    referencia_bancaria = Column(String(100), nullable=True)
    monto = Column(Numeric(12, 2), nullable=False)
    notas = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    caja = relationship("CajaChicaModel", back_populates="movimientos")