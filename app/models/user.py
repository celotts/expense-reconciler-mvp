import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

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
    # Este campo, y solo este, lleva anotacion `Mapped`. El resto de la clase
    # sigue en `Column(...)` legacy, como los otros nueve modelos.
    #
    # La razon es que este es el unico que se usa como condicion: `deps.py:90` y
    # `auth.py:125` hacen `if not usuario.is_active`, y ahi el tipo estatico miente
    # de forma que se ve. Sin anotacion, `Mapped` no se aplica y pyright deduce
    # `Column[bool]`, que no es un bool; de ahi el "Invalid conditional operand" y
    # el "__bool__ returns NoReturn rather than bool". En ejecucion el descriptor
    # de SQLAlchemy devuelve el valor real de la fila, asi que el aviso es falso.
    #
    # Anotarlo corrige el tipo en el punto donde el error de tipo puede hacer
    # dano (una defensa de auth leida como columna), en vez de silenciar la regla
    # entera. `reportGeneralTypeIssues` tambien caza bugs reales, y AGENTS.md
    # prohibe anadir una tercera regla silenciada.
    #
    # NO cambia el DDL: `Mapped[bool]` + `mapped_column(Boolean, nullable=False)`
    # declara exactamente la misma columna que declaraba `Column(Boolean, ...)` —
    # BOOLEAN NOT NULL. Por eso esto no necesita migracion.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    # Anotado por la misma razon que `is_active`, y por el mismo motivo de que
    # aqui se nota: `auth.py:138` hace `usuario.last_login_at = utcnow()`. Sin
    # anotacion, pyright deduce `Column[datetime]` y dice que no se le puede
    # asignar un datetime — un error de lectura, porque lo que se asigna es
    # justamente la columna. En ejecucion el descriptor acepta el valor y lo
    # escribe.
    #
    # El `| None` no es opcionalismo: la columna es nullable (`init.sql:26`) y
    # una cuenta que nunca ha entrado tiene `last_login_at` a NULL. Sin el None,
    # el tipo prometeria un datetime donde hay un NULL, y el primer `.isoformat()`
    # de esa columna reventaria.
    #
    # Tampoco cambia el DDL: mapea la misma TIMESTAMP WITH TIME ZONE nullable.
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuracion
        return f"<UserModel {self.email} activo={self.is_active}>"
