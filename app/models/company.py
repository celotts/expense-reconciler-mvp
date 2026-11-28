import uuid

from sqlalchemy import TIMESTAMP, Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.enums import EstadoEmpresa
from app.core.time import utcnow


class CompanyModel(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    tax_id = Column(String(50), unique=True, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    # Tres estados, no un booleano: `inactivo` se puede reactivar y
    # `eliminado` es un borrado logico. La fila y su evidencia siguen existiendo
    # en los dos; solo `activo` puede ser la empresa de una peticion.
    # La longitud 20 es la misma del CHECK de `0029` y del espejo en init.sql.
    estado = Column(String(20), nullable=False, default=EstadoEmpresa.ACTIVO.value)

    @property
    def activa(self) -> bool:
        """Comodidad para el codigo que solo pregunta '¿se puede trabajar?'."""
        return self.estado == EstadoEmpresa.ACTIVO.value
