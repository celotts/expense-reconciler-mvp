import uuid
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP, CheckConstraint, Column, Date, ForeignKey, Index, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.enums import OPEN_STATUSES, SETTLED_STATUSES, ExtractionStatus, SourceType
from app.core.time import utcnow

# Las condiciones se construyen desde el enum para que la constraint y el indice
# no puedan divergir cuando se agregue un estado nuevo.
_SIN_CERRAR = "extraction_status NOT IN ({})".format(
    ", ".join(f"'{s.value}'" for s in SETTLED_STATUSES)
)
_SIN_ABRIR = "extraction_status IN ({})".format(
    ", ".join(f"'{s.value}'" for s in OPEN_STATUSES)
)


class TicketModel(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # Invariante a nivel de base, y es condicional a proposito.
        #
        # Lo que de verdad hay que proteger es la conciliacion: un ticket que
        # entra a conciliar no puede traer un total de 0. Pero un documento
        # ilegible SI tiene que poder guardarse como PENDIENTE, porque si no
        # se descarta en silencio y nadie se entera de que existio. Con un
        # CHECK incondicional, una foto de un papel quemado daba
        # IntegrityError -> 500 -> documento perdido. Un error invisible es
        # peor que un error visible.
        #
        # Lo mismo con RECHAZADO: si el constraint lo alcanzara, un documento
        # ilegible no se podria ni guardar en la cola ni descartar, y la cola
        # no se podria vaciar nunca.
        CheckConstraint(
            f"{_SIN_CERRAR} OR total_amount > 0",
            name="ck_tickets_total_positive_when_settled",
        ),
        CheckConstraint(
            f"{_SIN_CERRAR} OR tax_amount >= 0",
            name="ck_tickets_tax_non_negative_when_settled",
        ),
        CheckConstraint(
            f"{_SIN_CERRAR} OR tax_amount <= total_amount",
            name="ck_tickets_tax_lte_total_when_settled",
        ),
        # Cola de revision: indice parcial sobre los estados abiertos. El
        # indice solo contiene lo que esta en la cola, asi que no crece con el
        # historico y la consulta de la cola no tiene que ordenar sobre miles de
        # tickets ya cerrados.
        Index(
            "ix_tickets_review_queue",
            "extraction_status", "company_id",
            postgresql_where=text(_SIN_ABRIR),
        ),
        # Recepcion de lotes idempotente por hash de contenido. Unico solo
        # cuando existe: los tickets manuales no tienen hash.
        Index("ix_tickets_source_hash", "source_hash", unique=True,
              postgresql_where=text("source_hash IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    provider_name = Column(String(150), nullable=False)
    provider_tax_id = Column(String(50), nullable=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    expense_date = Column(Date, nullable=False)
    category = Column(String(100), nullable=True)
    raw_text = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # --- Trazabilidad de la extraccion (Fase 0) -------------------------
    # La confianza que la IA ya calculaba se descartaba. Ahora se persiste y
    # gobierna el flujo: es lo que permite medir exactitud real en vez de
    # suponerla.
    confidence = Column(Numeric(4, 3), nullable=True)
    confidence_source = Column(String(20), nullable=True)
    extraction_status = Column(
        String(20), nullable=False, default=ExtractionStatus.PENDIENTE.value,
    )
    source_type = Column(String(20), nullable=True, default=SourceType.MANUAL.value)
    source_file = Column(String(500), nullable=True)
    source_hash = Column(String(64), nullable=True)

    # Checks deterministas que fallo. Guardarlos permite ver donde se
    # concentra el error (OCR, vision, reglas) en vez de suponerlo.
    validation_errors = Column(Text, nullable=True)

    # --- Revision humana -----------------------------------------------
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)

    company = relationship("CompanyModel", backref="tickets")
    reconciliations = relationship("ReconciliationModel", back_populates="ticket")

    @property
    def is_open_for_review(self) -> bool:
        return ExtractionStatus(self.extraction_status).is_open
