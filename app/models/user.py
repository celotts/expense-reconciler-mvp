import uuid

from sqlalchemy import TIMESTAMP, Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.time import utcnow


class UserModel(Base):
    """Alguien que puede entrar al sistema.

    No hay registro publico: las cuentas las crea quien administra, con
    `scripts/crear_usuario.py`. Un formulario de "registrate" abierto es la
    forma mas rapida de que un tercero se cuele en el historico contable, y el
    historico contable es justo lo que no se puede deshacer.

    `password_hash` es la cadena de `app/core/security.hashear_contrasena` y
    NUNCA sale por la API. No hay un campo `password` en ningun schema a
    proposito: si existe, alguien lo serializa por accidente.
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True, index=True)
    nombre = Column(String(120), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    last_login_at = Column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuracion
        return f"<UserModel {self.email} activo={self.is_active}>"
