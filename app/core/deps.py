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

from fastapi import Depends, Header, HTTPException, status
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


async def _usuario_del_token(
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


async def get_current_user(
    credenciales: Credenciales,
    db: Sesion,
) -> UserModel:
    """El usuario del token, o 401. Ver `_usuario_del_token`."""
    return await _usuario_del_token(credenciales, db)


UsuarioActual = Annotated[UserModel, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Los endpoints que comprometen a otro usuario
# ---------------------------------------------------------------------------
#
# `get_current_user` solo mira el token. Un token robado —de un portapapeles, de un
# `.env` filtrado, del historial de un navegador— sirve para TODO lo que el sistema
# hace, y entre esas cosas esta dar de baja a otra cuenta o cambiar su contrasena.
# Eso no es un endpoint de lectura: es tomar el control de la instalacion.
#
# Por eso existe `get_current_user_verificado`: exige ademas la contrasena, y solo
# para lo que compromete a OTRO usuario. No se aplica al router entero porque
# volverlo a pedir en cada endpoint haria que el uso diario molestase y —lo que es
# peor— que la gente se acostumbrara a escribirlo donde no hace falta, que es como
# una defensa se vuelve decorativa.
#
# LO QUE ESTA FUERA DE ESTA DEPENDENCIA, Y POR QUE
# -------------------------------------------------
#
# **`PATCH /reconciliations/{id}`**, aunque escriba `revisado_por`. Ese cambio deja
# rastro de quien fue, en la misma fila y con la misma fecha, y eso es lo que lo
# hace auditable despues. Lo que no hace es quitarle el acceso a nadie.
#
# **Los endpoints de escritura de datos** (tickets, empresas, inventario). Mover el
# stock de una empresa es serio, pero la accion queda en el kardex con actor y
# fecha, y las defenses de datos no son estas: son `compras.ticket_id` UNIQUE,
# `ck_compras_confirmacion` y el trigger del kardex. Anadir una segunda puerta de
# acceso ahi seria dar la sensacion de que el dato esta protegido cuando lo que lo
# protege son las constraints.
#
# LO QUE ESTA FUERA Y NO SE PUEDE ARREGLAR AQUI
# ----------------------------------------------
#
# **No hay roles.** Cualquier cuenta autenticada puede dar de baja a otra, porque
# `users` no tiene columna de rol y no hay multi-tenancy (`AGENTS.md`). Es una
# limitacion real y no un olvido: el producto esta disenado para "una maquina, un
# contador", donde las cuentas las crea quien tiene la base de datos
# (`scripts/crear_usuario.py`).
#
# Lo que esta dependencia hace es acotar el dano: la contrasena del propio actor,
# que es justo lo que un atacante con un token robado no tiene. Lo que NO hace es
# decidir quien puede tocar a quien, porque eso necesita el modelo de roles que no
# existe. Poner una comprobacion de rol aqui sin el modelo seria fingir que hay
# control de acceso donde solo hay autenticacion — y un control aparente es peor
# que ninguno, porque deja de revisarse.
#
# EL HEADER, Y POR QUE NO UN CAMPO EN EL CUERPO
# ----------------------------------------------
#
# `X-Contrasena-Actual` y no un `contrasena_actual` en el schema. Dos razones, y las
# dos practicas:
#
#   1. Si el campo fuera del cuerpo, cada schema de escritura tendria que declararlo
#      y el que se olvidara se quedaria sin la defensa sin que nadie lo notara —
#      que es justo el modo de fallo que `api_router.py` evita poniendo el token a
#      nivel de router.
#   2. Un header no lo guarda ningun log de cuerpo de peticion. Una contrasena que
#      llega al disco en un log de acceso es una contrasena que alguien va a leer.
#
# Y el 401 del fallo es el MISMO que el del token invalido, no un 403: para quien
# esta probando, "tu contrasena no es la correcta" y "tu token no sirve" tienen que
# ser la misma respuesta.


async def get_current_user_verificado(
    usuario: UsuarioActual,
    contrasena: Annotated[str | None, Header(alias="X-Contrasena-Actual")] = None,
) -> UserModel:
    """El usuario del token, y ademas su contrasena. O 401.

    SE DECLARA CON `UsuarioActual` Y NO LLAMANDO A `_usuario_del_token`, Y ES A
    PROPOSITO. Dependency overrides replaces porDEPENDENCIA, no por nombre de
    funcion: si aqui se llamara a `_usuario_del_token` directo, un test que
    sobrescribiera `get_current_user` —que es lo que hace `tests/conftest.py` para
    no tener que hacer login en cada prueba— no alcanzaria a esta funcion, y estos
    endpoints serian los unicos que no se podrian probar sin un token de verdad.

    Con `Depends(get_current_user)` el override llega aqui, y la comprobacion de la
    contrasena sigue siendo real: el test puede decir "el token vale" sin decir
    "la contrasena vale". Que es justo lo que necesitan estos tests, porque lo que
    se prueba es precisamente que la contrasena se exige.

    El scrypt se gasta despues de validar el token: si el token no es valido no
    hay a quien comparar la contrasena, y un scrypt por peticion anonima seria el
    mismo amplificador de DoS que `auth.login` evita con el rate limit (regla 10
    de `AGENTS.md`).
    """
    if contrasena is None or not contrasena:
        raise _no_autorizado(
            "Falta X-Contrasena-Actual. Dar de baja una cuenta o cambiar una "
            "contrasena pide la contrasena de quien lo hace."
        )

    from app.core.security import verificar_contrasena

    if not verificar_contrasena(contrasena, usuario.password_hash):
        raise _no_autorizado("La contrasena no es la correcta")

    return usuario


UsuarioVerificado = Annotated[UserModel, Depends(get_current_user_verificado)]
