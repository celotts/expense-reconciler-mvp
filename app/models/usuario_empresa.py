import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class UsuarioEmpresaModel(Base):
    """Membresia de una cuenta en una empresa, con su rol en ESA empresa.

    No hay relacion `back_populates` aqui a proposito: `users` y `companies`
    siguen en `Column(...)` legacy y meter relaciones obligaria a tocar los dos
    modelos para añadir una tabla nueva. Las consultas de autorizacion filtran
    directamente por `user_id`/`company_id`, que es lo unico que necesitan.

    La llave primaria compuesta es la defensa: no puede haber dos filas con
    roles distintos para el mismo par. Si eso pasara, `empresa_activa` tendria
    que elegir entre ellas, y esa eleccion seria una decision de seguridad
    tomada por el orden de las filas.
    """

    __tablename__ = "usuarios_empresas"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rol: Mapped[str] = mapped_column(String(20), nullable=False, default="operator")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=utcnow
    )
