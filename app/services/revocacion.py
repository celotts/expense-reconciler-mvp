"""Cerrar sesiones de verdad, en el servidor.

`tokens_revocados` es append-only y `sesiones_activas` es estado operativo.
Un cierre de sesion escribe la revocacion y borra la actividad: si solo se
borrara la actividad, la peticion siguiente crearia una fila nueva y el token
seguiria sirviendo. Si solo se escribiera la revocacion, la fila de actividad
se quedaria para siempre y la caducidad por inactividad no se podria medir.

Los motivos son los de `MOTIVOS_REVOCACION`: la base los repite en un CHECK y
los dos sitios tienen que coincidir. Este modulo no inventa uno al vuelo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token_revocado import MOTIVOS_REVOCACION, TokenRevocado
from app.models.sesion_activa import SesionActiva


def expira_de_claims(claims: dict[str, Any]) -> datetime:
    """El `exp` del token como instante UTC, para la columna `expires_at`."""
    exp = claims.get("exp")
    if not isinstance(exp, int):
        raise ValueError("el token no lleva exp valido")
    return datetime.fromtimestamp(exp, tz=timezone.utc)


async def _revocar(
    db: AsyncSession,
    jti: UUID,
    user_id: UUID,
    motivo: str,
    expires_at: datetime,
) -> None:
    if motivo not in MOTIVOS_REVOCACION:
        raise ValueError(f"motivo de revocacion no declarado: {motivo}")

    ya_revocado = (
        await db.execute(select(TokenRevocado).where(TokenRevocado.jti == jti))
    ).scalar_one_or_none()
    if ya_revocado is None:
        db.add(
            TokenRevocado(
                jti=jti,
                user_id=user_id,
                motivo=motivo,
                expires_at=expires_at,
            )
        )
    # La actividad se borra: el token ya no vale por el jti, y dejar la fila
    # haria que el proximo login con el MISMO jti pareciera una sesion viva.
    await db.execute(delete(SesionActiva).where(SesionActiva.jti == jti))


async def revocar_token_actual(
    request: Request,
    db: AsyncSession,
    usuario_id: UUID,
    motivo: str,
) -> None:
    """Revoca el token que viene en esta peticion, si tiene `jti`.

    Los tokens emitidos antes del `jti` no se pueden revocar: no hay un valor
    que escribir en la tabla. Siguen valiendo hasta `exp`. Es-la compatibilidad
    que se paga al desplegar, y se dice en la documentacion en vez de fingir
    que el logout los cerro.
    """
    jti = getattr(request.state, "jti", None)
    if jti is None:
        return
    claims = getattr(request.state, "claims", None) or {}
    await _revocar(db, UUID(str(jti)), usuario_id, motivo, expira_de_claims(claims))
    await db.commit()


async def revocar_sesiones_de_usuario(
    db: AsyncSession,
    usuario_id: UUID,
    motivo: str,
) -> None:
    """Revoca TODAS las sesiones conocidas de una cuenta.

    "Conocidas" importa: un token emitido pero nunca usado no tiene fila de
    actividad, asi que no se puede nombrar. La baja de la cuenta lo cubre por
    otra via (`is_active` se lee en cada peticion); un cambio de contrasena
    acorta la ventana a las sesiones que ya se han visto.
    """
    if motivo not in MOTIVOS_REVOCACION:
        raise ValueError(f"motivo de revocacion no declarado: {motivo}")

    filas = (
        await db.execute(
            select(SesionActiva).where(SesionActiva.user_id == usuario_id)
        )
    ).scalars().all()
    if not filas:
        return

    jtis = [fila.jti for fila in filas]
    ya_revocados = set(
        (
            await db.execute(
                select(TokenRevocado.jti).where(TokenRevocado.jti.in_(jtis))
            )
        ).scalars().all()
    )
    for fila in filas:
        if fila.jti not in ya_revocados:
            db.add(
                TokenRevocado(
                    jti=fila.jti,
                    user_id=usuario_id,
                    motivo=motivo,
                    expires_at=fila.expira_at,
                )
            )
        await db.delete(fila)
    await db.commit()
