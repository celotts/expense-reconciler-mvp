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

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import RolEmpresa
from app.core.permisos import a_rol_de_empresa, Permiso, roles_validos, tiene_permiso
from app.core.security import ErrorDeToken, TokenCaducado, leer_token
from app.models.usuario_empresa import UsuarioEmpresaModel
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
# Empresa activa y rol por empresa
# ---------------------------------------------------------------------------
#
# La empresa NO viaja en el token. Un claim firmado solo se puede cambiar
# emitiendo otro token, asi que cambiar de empresa obligaria a hacer login de
# nuevo, y ademas un token emitido con la empresa de ayer seguiria valido hoy.
# Se toma de la cabecera `X-Company-ID` y se comprueba contra
# `usuarios_empresas` EN LA BASE, en la misma request: si se da de baja una
# membresia, surte efecto inmediato, igual que `is_active` en `_usuario_del_token`.
#
# El rol tampoco viaja en el token, por la misma razon y por una mas: es POR
# EMPRESA. Un mismo humano puede ser `operator` en una y `admin` en otra, y un
# claim global no podria representar eso sin mentir.
#
# Compatibilidad hacia atras: si no viene la cabecera y la cuenta solo tiene
# UNA empresa, se toma esa. Asi los clientes que hoy no envian la cabecera
# siguen funcionando. Con dos o mas empresas, la ausencia es un 409 que pide
# elegir: adivinar cual quiso el usuario es una decision de seguridad tomada
# por el orden de las filas.


async def empresa_activa(
    usuario: UsuarioActual,
    db: Sesion,
    x_company_id: Annotated[str | None, Header(alias="X-Company-ID")] = None,
) -> UUID:
    """La empresa en la que opera esta peticion, o 403/409.

    403 para "no tienes acceso a esa empresa" y 409 para "tienes acceso a
    varias y no dijiste cual". Son respuestas distintas porque piden acciones
    distintas: la primera no tiene arreglo del lado del cliente, la segunda se
    arregla enviando la cabecera.
    """
    company_ids = set(
        (
            await db.execute(
                select(UsuarioEmpresaModel.company_id).where(
                    UsuarioEmpresaModel.user_id == usuario.id,
                    UsuarioEmpresaModel.activo.is_(True),
                )
            )
        ).scalars().all()
    )

    if not company_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta no tiene acceso a ninguna empresa",
        )

    if x_company_id is None:
        if len(company_ids) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Elija la empresa activa con la cabecera X-Company-ID",
            )
        return next(iter(company_ids))

    # Un header con basura es un 400, no un 500: `UUID(str)` lanza ValueError y
    # un error de conversion aqui se veria como fallo del servidor.
    try:
        elegida = UUID(x_company_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Company-ID no es un UUID valido",
        ) from exc

    if elegida not in company_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta no tiene acceso a esa empresa",
        )
    return elegida


EmpresaActiva = Annotated[UUID, Depends(empresa_activa)]


async def rol_actual(
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    db: Sesion,
) -> RolEmpresa:
    """El rol de esta cuenta EN la empresa activa, ledo de la BD.

    Es una dependencia separada de `empresa_activa` porque los servicios necesitan
    el rol para decidir alcance (un `operator` solo toca sus cajas), pero no todos
    los endpoints exigen un rol concreto. FastAPI cachea `empresa_activa` dentro
    de la misma request, asi que pedir las dos no duplica la consulta de empresas.
    """
    # No se filtra `activo` de nuevo: `company_id` VIENE de `empresa_activa`,
    # que ya exigio una membresia activa. Poner aqui el mismo filtro seria una
    # defensa que no puede cambiar el veredicto y que por eso no se puede probar
    # (lo detecto el verificador de mutaciones: sobrevivio al quitarla).
    rol = await db.scalar(
        select(UsuarioEmpresaModel.rol).where(
            UsuarioEmpresaModel.user_id == usuario.id,
            UsuarioEmpresaModel.company_id == company_id,
        )
    )
    rol_validado = a_rol_de_empresa(rol)
    if rol is None or rol_validado is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta no tiene acceso a esa empresa",
        )
    return rol_validado


RolEnEmpresa = Annotated[RolEmpresa, Depends(rol_actual)]


def requiere_rol(*roles: RolEmpresa):
    """Factoria de dependencia: exige uno de estos roles EN LA EMPRESA ACTIVA.

    Se consulta en BD en cada request, no se lee del token. Un token robado o
    manipulado no puede ascender de rol, y un cambio de rol surte efecto sin
    esperar a que caduque el token (regla 9, la misma que `is_active`).
    """

    permitidos = {rol.value for rol in roles}

    async def _requiere_rol(rol: RolEnEmpresa) -> RolEmpresa:
        if rol not in permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El rol de la cuenta no permite esta operacion",
            )
        return rol

    return _requiere_rol


# ---------------------------------------------------------------------------
# La guardia de escrituras: el front oculta botones, esto es la autoridad
# ---------------------------------------------------------------------------
#
# `requiere_rol` protege rutas concretas. La experiencia con el menu anterior
# enseno que eso no basta: una pantalla que no pinta el boton SIGUE siendo una
# ruta HTTP a la que se puede llamar con curl. Por eso esta guardia va a nivel
# de router, igual que el token, y no se copia endpoint por endpoint.
#
# No pide `empresa_activa` como dependencia: hacerlo obligaria a elegir empresa
# incluso para mirar el dashboard, que es una lectura. Lee las membresias solo
# cuando la peticion ESCRIBE o exporta, y resuelve la empresa de la cabecera
# `X-Company-ID`. Sin cabecera y con una sola membresia, usa esa; con varias,
# basta con que una permita la accion para no bloquear a quien trabaja en otra:
# adivinar cual quiso el usuario es una decision de seguridad, y `empresa_activa`
# ya pide 409 cuando hace falta elegir.

# POST que son una consulta, no una escritura. Estar aqui no habilita nada: la
# ruta sigue pidiendo token y, si corresponde, empresa activa.
_RUTAS_DE_SOLO_LECTURA = frozenset({"/api/v1/duplicados/buscar"})

_PERMISO_POR_METODO: dict[str, Permiso] = {
    "POST": Permiso.CREAR,
    "PUT": Permiso.EDITAR,
    "PATCH": Permiso.EDITAR,
    "DELETE": Permiso.BORRAR,
}


def _permiso_de_la_peticion(request: Request) -> Permiso | None:
    """Que accion exige esta peticion. `None` es una lectura, que no se toca.

    El verbo solo no basta. Un `POST /reconciliations/run` no es "crear" sino
    conciliar; un `PATCH /tickets/{id}/review` firma un veredicto; un PDF es una
    salida de datos. Si la guardia mirara solo el metodo, `operator` podria
    aprobar y exportar con un `POST` o un `GET` que el menu no le ofrece, y el
    mapa de permisos seria decorativo. Por eso las acciones especiales se
    comprueban ANTES del default por verbo.
    """
    ruta = request.url.path
    if ruta in _RUTAS_DE_SOLO_LECTURA:
        return None
    metodo = request.method.upper()

    # Un PDF, un Excel o un respaldo son una SALIDA de datos, no una lectura
    # inofensiva. Un `viewer` puede mirar el informe en pantalla y no puede
    # llevarse el archivo: son dos preguntas distintas.
    if metodo == "GET":
        if ruta.endswith(".pdf") or "/export/" in ruta or ruta.endswith("/backup"):
            return Permiso.EXPORTAR
        return None

    # Aprobar no es editar: quien corrige un ticket no por eso puede firmarlo.
    if "/spot-check" in ruta or ruta.endswith("/review"):
        return Permiso.APROBAR
    if ruta.startswith("/api/v1/reconciliations/run"):
        return Permiso.CONCILIAR
    # Cerrar, enviar a revision y reabrir son decisiones de caja, no altas.
    if ruta.endswith(("/cerrar", "/en-revision", "/reabrir")):
        return Permiso.CERRAR
    # Administrar cuentas y empresas no es crear un ticket. Las rutas viejas de
    # `/usuarios` se conservan, pero su escritura tambien cambia la autoridad.
    if ruta.startswith("/api/v1/companies") or ruta.startswith("/api/v1/usuarios"):
        return Permiso.ADMINISTRAR
    # El reset borra lo digitalizado: es una decision de administrador, no un
    # DELETE cualquiera. Su cerrojo propio sigue vigente; esto es uno mas.
    if ruta.startswith("/api/v1/scan/reset"):
        return Permiso.ADMINISTRAR
    if ruta.startswith("/api/v1/auditoria/evento"):
        return Permiso.AUDITAR

    return _PERMISO_POR_METODO.get(metodo)


async def guardia_de_permisos(
    request: Request,
    usuario: UsuarioActual,
    db: Sesion,
) -> None:
    """403 si el rol en la empresa activa no permite escribir o exportar.

    Se instala en `api_router` para todos los routers protegidos. Si no hay
    membresias, NO bloquea: son cuentas anteriores a `0022` y quitarles el
    acceso por una migracion seria romper lo que ya funcionaba, igual que el
    backfill de administrador. Un rol leido de `perfil` o del JWT no cuenta.
    """
    permiso = _permiso_de_la_peticion(request)
    if permiso is None:
        return

    filas = (
        await db.execute(
            select(UsuarioEmpresaModel.rol, UsuarioEmpresaModel.company_id).where(
                UsuarioEmpresaModel.user_id == usuario.id,
                UsuarioEmpresaModel.activo.is_(True),
            )
        )
    ).all()
    if not filas:
        return

    cabecera = request.headers.get("X-Company-ID")
    if cabecera:
        try:
            elegida = UUID(cabecera)
        except ValueError as exc:
            # No se adivina cual empresa quiso decir el cliente. Devolver aqui
            # seria dejar que una cabecera basura saltara la guardia en routers
            # que no piden `empresa_activa` (tickets, reconciliations, ...).
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Company-ID no es un UUID valido",
            ) from exc
        # Un rol que no entendemos se descarta, no se convierte en admin ni
        # revienta con ValueError en medio de la peticion.
        roles = roles_validos(rol for rol, company_id in filas if company_id == elegida)
        if not roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La cuenta no tiene acceso a esa empresa",
            )
    else:
        roles = roles_validos(rol for rol, _ in filas)

    if not any(tiene_permiso(rol, permiso) for rol in roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"El rol de la cuenta no permite {permiso.value} en esta empresa",
        )


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
# **Roles y empresas.** Desde `0022_roles_y_empresa_activa.sql` el rol vive por
# empresa en `usuarios_empresas` y se comprueba con `empresa_activa` y
# `requiere_rol`. Esta dependencia SIGUE sin decidir quien puede tocar a quien:
# solo pide la contrasena del propio actor, que es justo lo que un atacante con
# un token robado no tiene. Anadir aqui un `requiere_rol` global seria ademas
# incorrecto, porque el rol no es global: es por empresa, y esta dependencia no
# recibe la empresa activa. Las rutas nuevas de Mantenimiento piden las DOS
# cosas por separado —esta dependencia y `requiere_rol(owner, admin)`— porque
# son dos preguntas: "quien eres" y "que autoridad tienes en ESTA empresa".
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
