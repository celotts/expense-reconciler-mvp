"""La conciliacion: que ticket se empareja con que movimiento bancario.

`match_status` no es decorativo
-------------------------------

De este campo depende que la fila se exporte: `export_service` filtra por
`MATCHED_STATUSES`, y `DISCREPANCY` esta fuera a proposito. Cambiar el estado
cambia lo que ve el contador, y por eso el estado no es solo un dato de la fila.

`revisado_por`: por que hace falta, y por que es texto
----------------------------------------------------

`PATCH /reconciliations/{id}` deja que una persona corrija a mano el veredicto
que puso el motor. Sin `revisado_por`, un `PERFECT` que decidio el motor y uno
que aprobo una persona serian la misma fila, y no habria forma de saber cual de
los dos se esta exportando a CONTPAQI.

Es el mismo motivo por el que existe `confidence_source` en `tickets`: "el
automatico lo leyo" y "lo leyo una persona" no se suman, y confundirlos
produce un numero que no describe a ninguno de los dos. Ver la nota larga en
`db/migrations/0013_compras_rechazables.sql` §3.

`NULL` y no cadena vacia: `NULL` es el hecho "el motor lo decidio y nadie lo ha
tocado". `''` seria "alguien lo toco y no lo dijo", que es peor que no
tenerlo.

Texto y no llave foranea, por el precedente de `compras.confirmada_por`,
`tickets.spot_checked_by` y `cierres_periodo.cerrado_por`: una decision
contabil tiene que sobrevivir a la baja de la cuenta que la tomo.
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utcnow


class ReconciliationModel(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        # Las dos columnas de revision van o ninguna. Una revision sin fecha no
        # se puede ordenar en el tiempo, que es justo lo que se necesita para
        # auditar un cierre; y una fecha sin autor no dice quien decidio.
        CheckConstraint(
            "(revisado_por IS NULL AND revisado_at IS NULL) "
            "OR (revisado_por IS NOT NULL AND length(trim(revisado_por)) > 0 "
            "    AND revisado_at IS NOT NULL)",
            name="ck_reconciliations_revision",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # La empresa de esta conciliacion (P0-1). Se copia del ticket al crearla:
    # una conciliacion no puede ser de dos empresas a la vez, y el enlace
    # ticket <-> movimiento bancario solo vale si los dos son de la misma.
    # Nullable porque las conciliaciones viejas se rellenan en la migracion
    # 0020 desde el ticket; las nuevas siempre la llevan.
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    bank_transaction_id = Column(UUID(as_uuid=True), ForeignKey("bank_transactions.id", ondelete="SET NULL"), nullable=True)
    match_status: Mapped[str] = mapped_column(String(50), nullable=False)
    matched_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # Quien toco el veredicto a mano. NULL = lo puso el motor. Ver el modulo.
    revisado_por: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revisado_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    ticket = relationship("TicketModel", back_populates="reconciliations")
    bank_transaction = relationship("BankTransactionModel", back_populates="reconciliations")
