"""La dependencia que decide si una peticion puede entrar.

Se aplica al router completo de la v1, no endpoint por endpoint. Escribir
`Depends(get_current_user)` en 35 endpoints es 35 oportunidades de que el
endpoint 36 quede sin proteger y no se note en la revision: la omission se ve
igual que la presencia. Aparte, un endpoint nuevo nace protegido por defecto,
que es la unica forma segura de que lo este.

La excepcion es `POST /auth/login`, que es justamente la puerta: no puede
pedir un token que todavia no existe.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ErrorDeToken, TokenCaducado, leer_token
from app.models.user import UserModel

# `auto_error=False` para poder responder 401 con NUESTRO mensaje en vez del
# "Not authenticated" generico del framework, y para poder distinguir token
# caducado de token invalido sin que el cliente lo note (el mensaje es el
# mismo: le diria a un atacante que el token existio).
esquema_bearer = HTTPBearer(auto_error=False, description="Token de acceso")

Credenciales = Annotated[HTTPAuthorizationCredentials | None, Depends(esquema_bearer)]
Sesion = Annotated[AsyncSession, Depends(get_db)]


def _no_autorizado(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detalle,
        # Sin esto, el cliente no tiene forma de diferenciar "caducado, ve a
        # iniciar sesion" de "no autorizado, no lo intentes otra vez", y lo
        # unico que hace es reintentar el mismo token indefinidamente.
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credenciales: Credenciales,
    db: Sesion,
) -> UserModel:
    """El usuario del token, o 401.

    Se vuelve a consultar la base y no se confía solo en los claims. Un token
    de una cuenta dada de baja sigue siendo criptograficamente valido hasta
    que caduca: si el token fuera la unica fuente de verdad, desactivar a
    alguien no tendria efecto hasta las 8 horas siguientes.
    """
    if credenciales is None or not credenciales.credentials:
        raise _no_autorizado("Falta el token de acceso")

    try:
        claims = leer_token(credenciales.credentials)
    except TokenCaducado as exc:
        # Unico caso con un mensaje propio, porque el cliente SI necesita
        # saberlo: tiene que ir al login. El token no se acepta ni se intenta
        # refrescar aqui.
        raise _no_autorizado("La sesion termino. Vuelve a iniciar sesion.") from exc
    except ErrorDeToken as exc:
        raise _no_autorizado("El token no es valido") from exc

    # El `sub` viaja como texto (es un claim de JSON, no tiene tipo) y la
    # columna es UUID. Compararlos sin convertir hace que el driver lance un
    # error de tipo en la base: eso es un 500 en vez de un 401, y ademas el
    # cliente distingue "mi token esta raro" de "tu sesion no sirve". Se
    # convierte aqui, y un `sub` que no sea un UUID se trata como token
    # invalido en vez de propagar la excepcion.
    try:
        usuario_id = UUID(claims["sub"])
    except (ValueError, AttributeError, TypeError) as exc:
        raise _no_autorizado("El token no es valido") from exc

    usuario = (
        await db.execute(select(UserModel).where(UserModel.id == usuario_id))
    ).scalar_one_or_none()

    if usuario is None:
        raise _no_autorizado("El token no es valido")

    if not usuario.is_active:
        raise _no_autorizado("La cuenta esta dada de baja")

    return usuario


UsuarioActual = Annotated[UserModel, Depends(get_current_user)]
