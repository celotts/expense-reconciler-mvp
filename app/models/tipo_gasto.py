import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    TIMESTAMP,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow


class TipoGastoModel(Base):
    """Una cuenta de gasto. El catalogo, por empresa.

    QUE ES Y QUE NO ES
    -----------------
    Es la cuenta en la que se imputa un gasto: "RENTA", "COMBUSTIBLE". No es una
    categoria de marketing ni una etiqueta libre: el `category` de `tickets` era
    texto y por eso nunca se pudo contar con el. Una cuenta tiene clave (lo que se
    agrupa y va al archivo del contador), nombre (lo que ve la persona) y si es
    gasto variable (que es lo que separa "subio porque hubo mas actividad" de
    "subio porque se fue de las manos").

    POR QUE TIENE `variable`
    ------------------------
    Porque sin eso el cierre no puede contestar la pregunta que lo hace util. Un
    gasto que sube 30% con lo que subio la actividad es lo normal; el mismo 30%
    en un gasto fijo es una alarma. El dato sale de `Categoria.variable` del
    catalogo que vivia en codigo, y se conserva porque no era decorativo.

    POR QUE `activo` Y NO UN DELETE
    ------------------------------
    Regla 24, y aqui es literal: un gasto ya contabilizado apunta a esta fila para
    siempre. Si se borra, el reporte de un mes que ya se cerro deja de poder decir
    en que cuenta estaba el gasto, que es justo lo que un cierre tiene que poder
    decir. Se da de baja con `activo=false` y el historico sigue completo.
    """

    __tablename__ = "tipos_gasto"
    __table_args__ = (
        UniqueConstraint("company_id", "clave", name="ux_tipos_gasto_empresa_clave"),
        CheckConstraint("length(trim(clave)) > 0", name="ck_tipos_gasto_clave_no_vacia"),
        CheckConstraint("length(trim(nombre)) > 0", name="ck_tipos_gasto_nombre_no_vacio"),
        # La clave se agrupa por texto en el cierre y en el archivo del
        # contador, asi que "supermercado" y "SUPERMERCADO" siendo la misma
        # cuenta darian dos filas. La normalizacion esta en el servicio; aqui
        # solo se prohibe que la base acepte lo que el servicio no deberia
        # mandar, para que un `psql` tampoco pueda.
        CheckConstraint("clave = upper(clave)", name="ck_tipos_gasto_clave_mayuscula"),
        Index("ix_tipos_gasto_empresa_activo", "company_id", "activo"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    clave = Column(String(50), nullable=False)
    nombre = Column(String(120), nullable=False)
    variable = Column(Boolean, nullable=False, default=True)
    activo = Column(Boolean, nullable=False, default=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    company = relationship("CompanyModel", backref="tipos_gasto")

    # `back_populates` en los dos lados (convencion del proyecto). El `passive_deletes`
    # no se pone a proposito: sin `ON DELETE CASCADE` hacia estos lados, el borrado
    # de una empresa tiene que fallar en vez de dejar gastos apuntando a una cuenta
    # que ya no existe, que es un cierre que no cierra.
    tickets = relationship("TicketModel", back_populates="tipo_gasto")
    productos = relationship("ProductoModel", back_populates="tipo_gasto")