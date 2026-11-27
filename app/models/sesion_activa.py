"""Estado operativo de una sesion mientras esta abierta.

No es evidencia: es el dato que permite MEDIR la inactividad en el servidor.
La evidencia de que una sesion se cerro vive en `tokens_revocados`, que es
append-only; esta fila se puede borrar al cerrar sesion porque el token ya
quedo revocado por su `jti`.

La actividad NO viaja en el token. Un claim firmado con la hora de la ultima
peticion no se puede actualizar sin emitir un token nuevo, y un `setTimeout`
del navegador no es una defensa: cualquiera con el token puede llamar al API
sin pasar por la pantalla. Por eso el instante se guarda aqui y se compara en
cada peticion autenticada.
"""

from sqlalchemy import TIMESTAMP, Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.time import utcnow


class SesionActiva(Base):
    __tablename__ = "sesiones_activas"

    jti = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    emitida_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    expira_at = Column(TIMESTAMP(timezone=True), nullable=False)
    ultima_actividad = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
