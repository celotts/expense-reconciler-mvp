import uuid

from sqlalchemy import JSON, TIMESTAMP, Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.time import utcnow


class CompanyModel(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    tax_id = Column(String(50), unique=True, nullable=False)
    # Configuracion de lotes de esta empresa (0019). JSON y no tabla aparte:
    # son cuatro escalares que se leen con la empresa y nunca se buscan por su
    # valor; ver `app/models/lote.py` para los defaults. NULL = sin configurar.
    lote_config = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)