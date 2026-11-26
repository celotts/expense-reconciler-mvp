"""Un token que dejo de valer antes de su `exp`.

La fila no se edita ni se borra: ver `db/migrations/0025_tokens_revocados.sql`.
El modelo existe para que `tests/conftest.py` construya la misma tabla que
construye `init.sql` y para que el test de espejo DDL/modelo la vigile.
"""

from sqlalchemy import TIMESTAMP, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.time import utcnow

# Los motivos son los unicos que puede escribir la app. La base los repite en
# un CHECK: si aparece uno nuevo, hay que nombrarlo en los dos sitios.
MOTIVOS_REVOCACION = (
    "logout",
    "cambio_contrasena",
    "reset_administrativo",
    "baja_cuenta",
)


class TokenRevocado(Base):
    __tablename__ = "tokens_revocados"

    jti = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    motivo = Column(String(40), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
