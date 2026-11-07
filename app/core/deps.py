"""La dependencia que decide si una peticion puede entrar.

Se aplica al router completo de la v1, no endpoint por endpoint. Escribir
`Depends(get_current_user)` en 35 endpoints es 35 oportunidades de que el
endpoint 36 quede sin proteger y no se note en la revision: la omission se ve
igual que la presencia. Aparte, un endpoint nuevo nace protegido por defecto,
que es la unica forma segura de que lo este.

La excepcion es `POST /auth/login`, que es justamente la puerta: no puede
pedir un token que todavia no existe.

P0-1 (multi-empresa): el token autentica, pero la EMPRESA se resuelve aqui,
de la fila de `users` que se vuelve a leer en cada peticion, nunca del
cuerpo ni del query del cliente. Ver `resolver_empresa` mas abajo.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ErrorDeToken, TokenCaducado, leer_token
from app.models.company import CompanyModel
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

    # Segunda cerradura (P0-1): Row Level Security. Si el usuario tiene
    # empresa, la sesion de Postgres la lleva puesta y las politicas de
    # `0020_usuario_empresa.sql` filtran aunque el codigo se olvide de
    # hacerlo. `set_config(..., true)` es local a la transaccion: si el
    # endpoint hace commit a mitad de la peticion, se pierde, y por eso el
    # filtro de la aplicacion sigue siendo la primera cerradura, no esta.
    # En SQLite (tests) no existe: se salta sin hacer ruido.
    if usuario.company_id is not None:
        await _fijar_empresa_rls(db, usuario.company_id)

    return usuario


async def _fijar_empresa_rls(db: AsyncSession, company_id: UUID) -> None:
    """Pone `app.company_id` en la sesion de Postgres para el RLS.

    La RLS es la segunda cerradura, no la primera: si no se puede fijar,
    la peticion sigue con el filtro de la aplicacion y no se rompe nada.
    Por eso el `except` traga el error en vez de convertirlo en 500.
    """
    try:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT set_config('app.company_id', :valor, true)"),
                {"valor": str(company_id)},
            )
    except Exception:
        pass


async def get_current_user(
    credenciales: Credenciales,
    db: Sesion,
) -> UserModel:
    """El usuario del token, o 401. Ver `_usuario_del_token`."""
    return await _usuario_del_token(credenciales, db)


UsuarioActual = Annotated[UserModel, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Roles y empresa (P0-1, multi-empresa)
# ---------------------------------------------------------------------------
#
# Roles canonicos. `perfil` es texto libre en la base, asi que los valores
# viejos se normalizan aqui y no en una migracion: 'develop' y 'programador'
# son el admin de siempre (el que despliega), 'qa' es quien audita y solo
# lee. Un perfil que nadie reconoce se trata como 'digitalizador': es el
# perfil por defecto de toda cuenta nueva y el que menos rompe.
ROL_ADMIN = "admin"
ROL_CONTADOR = "contador"
ROL_APROBADOR = "aprobador"
ROL_DIGITALIZADOR = "digitalizador"
ROL_SOLO_LECTURA = "solo_lectura"

ROLES = frozenset(
    {ROL_ADMIN, ROL_CONTADOR, ROL_APROBADOR, ROL_DIGITALIZADOR, ROL_SOLO_LECTURA}
)

_PERFIL_A_ROL = {
    "admin": ROL_ADMIN,
    "develop": ROL_ADMIN,
    "programador": ROL_ADMIN,
    "contador": ROL_CONTADOR,
    "aprobador": ROL_APROBADOR,
    "digitalizador": ROL_DIGITALIZADOR,
    "qa": ROL_SOLO_LECTURA,
    "solo_lectura": ROL_SOLO_LECTURA,
}

# Que puede hacer cada rol. "escribir" es crear/editar/borrar datos de su
# empresa; "aprobar" es dar veredictos (autorizar tickets, confirmar o
# rechazar compras); "administrar" es tocar cuentas y empresas; "cerrar" es
# firmar un cierre de periodo. Un rol sin el permiso no llega al endpoint.
PERMISOS_POR_ROL = {
    ROL_ADMIN: frozenset({"leer", "escribir", "aprobar", "administrar", "cerrar"}),
    ROL_CONTADOR: frozenset({"leer", "escribir", "aprobar", "cerrar"}),
    ROL_APROBADOR: frozenset({"leer", "aprobar"}),
    ROL_DIGITALIZADOR: frozenset({"leer", "escribir"}),
    ROL_SOLO_LECTURA: frozenset({"leer"}),
}


def rol_de(usuario: UserModel) -> str:
    """El rol canonico de un usuario, normalizando perfiles viejos."""
    return _PERFIL_A_ROL.get((usuario.perfil or "").strip().lower(), ROL_DIGITALIZADOR)


def puede(usuario: UserModel, permiso: str) -> bool:
    """Si el rol del usuario tiene este permiso."""
    return permiso in PERMISOS_POR_ROL[rol_de(usuario)]


def exige_rol(*permisos: str):
    """Dependencia FastAPI: 403 si el usuario no tiene NINGUNO de los permisos.

    Se usa como `Depends(exige_rol("administrar"))`. El 403 es a proposito:
    aqui el usuario SI esta autenticado y la empresa SI existe; lo que no
    tiene es el rol. Es distinto del 404 de `resolver_empresa`, que es para
    no confirmar que existe algo de otra empresa.
    """

    async def _exige(usuario: UsuarioActual) -> UserModel:
        if not any(puede(usuario, permiso) for permiso in permisos):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu rol no permite esta accion",
            )
        return usuario

    return _exige


async def exige_escritura(usuario: UsuarioActual) -> UserModel:
    """403 si el usuario es de solo lectura. Para endpoints de escritura."""
    if not puede(usuario, "escribir"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu rol es de solo lectura",
        )
    return usuario


Escribir = Annotated[UserModel, Depends(exige_escritura)]


def empresa_del_usuario(usuario: UserModel) -> UUID | None:
    """La empresa del usuario, o None si no tiene ninguna asignada."""
    return usuario.company_id


def _sin_empresa() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Tu cuenta no tiene empresa asignada",
    )


async def resolver_empresa(
    usuario: UserModel,
    company_id_cliente: UUID | None,
    db: AsyncSession,
) -> UUID:
    """La empresa que vale para esta peticion, o 404.

    REGLA CENTRAL DE P0-1: el `company_id` del cliente NUNCA decide nada
    por si solo. Lo que decide es la empresa del usuario autenticado.

    - Usuario con empresa: la suya. Si el cliente pide otra, 404 (y no
      403, para no confirmar que esa otra empresa existe ni que hay datos
      en ella). Si no pide ninguna, la suya.
    - Usuario SIN empresa y con rol admin: operador de arranque (el admin
      sembrado antes de que existieran las empresas). Puede pedir una
      empresa explicita, y solo si existe; sin ella, 404.
    - Usuario SIN empresa y sin rol admin: 404 siempre. "Sin empresa no
      ve nada" es la aceptacion de P0-1.

    Se llama EXPLICITAMENTE dentro de cada endpoint, y no como dependencia,
    porque el `company_id` del cliente llega por sitios distintos segun el
    endpoint: query en los listados, cuerpo en las creaciones, y a veces no
    llega (entonces vale la empresa del usuario).
    """
    propia = usuario.company_id
    if propia is not None:
        if company_id_cliente is not None and company_id_cliente != propia:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Empresa no encontrada",
            )
        return propia
    if rol_de(usuario) != ROL_ADMIN:
        raise _sin_empresa()
    if company_id_cliente is None:
        raise _sin_empresa()
    existe = (
        await db.execute(
            select(CompanyModel).where(CompanyModel.id == company_id_cliente)
        )
    ).scalar_one_or_none()
    if existe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Empresa no encontrada",
        )
    return company_id_cliente


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
# **Roles y empresa (P0-1).** Ya hay roles (`rol_de`, `PERMISOS_POR_ROL`) y
# cada usuario lleva su `company_id` (`resolver_empresa`): un usuario con
# empresa solo ve la suya, y uno sin empresa no ve nada salvo que sea el
# operador admin de arranque. Lo que esta dependencia NO hace es decidir
# quien puede tocar a quien DENTRO de la empresa: eso es `exige_rol` y
# `exige_escritura` en cada endpoint, porque el permiso depende de lo que
# el endpoint hace, no de quien entra.
#
# Lo que sigue fuera (seguimiento de P0-1, no de esta dependencia): que
# crear/editar/borrar empresas y que `POST /scan/reset` y `GET /sistema/backup`
# pidan rol admin. Hoy esos endpoints los puede llamar cualquier cuenta
# autenticada, como antes de P0-1, porque los tests existentes lo ejercen
# asi y cambiarlo exige actualizarlos a la vez. La tenencia de datos ya esta
# cerrada; la administracion de la instalacion es el siguiente paso.
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
