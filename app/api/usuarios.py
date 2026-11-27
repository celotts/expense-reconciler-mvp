"""Administrar las cuentas: listar, registrar, dar de baja, cambiar la contrasena.

EL ALTA DE CUENTAS: `POST /usuarios`
------------------------------------

Existia `scripts/crear_usuario.py` y nada mas. Para permitir el registro de usuarios
desde la aplicacion y la API sin abrir la puerta a atacantes:

- No hay autorregistro publico: la ruta requiere autenticacion.
- Exige `UsuarioVerificado`, o sea token **y** la contrasena de quien lo hace
  (`X-Contrasena-Actual`). Un atacante con un token robado no puede crear cuentas
  administrativas o de acceso en el sistema.
- Normaliza el correo a minusculas y comprueba unicidad (409 Conflict si ya existe).
- Hashea la clave con scrypt (`hashear_contrasena`) y devuelve `UsuarioResponse`,
  garantizando que ningun hash o clave en plano quede expuesto.

LO QUE ESTA PROTEGIDO Y LO QUE NO
----------------------------------

Cada endpoint de escritura pide `UsuarioVerificado`, o sea token **y** la
contrasena de quien lo hace (`X-Contrasena-Actual`). Un token robado no basta para
dar de baja a otra cuenta ni para cambiar una contrasena.

Lo que sigue SIN resolver aqui, y conviene no olvidar: estos endpoints no
deciden quién puede tocar a quién con base en un rol por empresa. `perfil` es
texto libre y sigue siendo una etiqueta; la autoridad de Mantenimiento se
exige en las rutas NUEVAS (`/usuarios/membresias`, `/usuarios/{id}/contrasena-
admin`), que son las que cambian roles y claves ajenas. Los endpoints
anteriores se conservan como estaban para no romper a quien ya los usa.

ORDEN DE LAS RUTAS
------------------

`/usuarios/{usuario_id}/contrasena` tiene dos segmentos, y las de un solo
segmento no colisionan con ella. Aun asi, las literales se declaran antes que los
placeholders por el criterio que `tickets.py` y `reconciliations.py` ya
documentaron: en FastAPI gana el primer match, y demostrar que dos rutas no
colisionan cuesta menos que demostrar que no colisionan.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import (
    EmpresaActiva,
    Sesion,
    UsuarioActual,
    UsuarioVerificado,
    requiere_rol,
)
from app.core.enums import RolEmpresa
from app.core.security import hashear_contrasena, verificar_contrasena
from app.models.user import UserModel
from app.services import membresias_service
from app.services.auditoria import (
    registrar_auditoria,
    registrar_update_auditoria,
)
from app.services.revocacion import (
    revocar_sesiones_de_usuario,
    revocar_token_actual,
)
from app.schemas.auth import (
    CambioContrasenaRequest,
    UsuarioCreate,
    UsuarioResponse,
    UsuarioUpdate,
)
from app.schemas.membresias import (
    MembresiaCreate,
    MembresiaItemResponse,
    MembresiaUpdate,
    ResetContrasenaAdminRequest,
)

router = APIRouter(tags=["Usuarios"])

# La autoridad para administrar cuentas NO es `perfil`: es el rol EN la empresa
# activa, ledo de la BD en cada peticion. `requiere_rol` no se aplica al router
# entero porque listar y cambiar la propia clave no son mantenimiento.
Administrador = Annotated[
    RolEmpresa,
    Depends(requiere_rol(RolEmpresa.OWNER, RolEmpresa.ADMIN)),
]


async def _cuenta(db: Sesion, usuario_id: UUID) -> UserModel:
    """La cuenta, o 404.

    Un 404 y no un 403, por la misma razon que en el inventario: un 403
    confirmaria que hay una cuenta con ese id. Y aqui el id lo elige quien llama,
    asi que la diferencia se nota.
    """
    cuenta = (
        await db.execute(select(UserModel).where(UserModel.id == usuario_id))
    ).scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="La cuenta no existe"
        )
    return cuenta


@router.get("", response_model=list[UsuarioResponse])
async def listar_cuentas(
    db: Sesion,
    _: UsuarioActual,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    solo_activas: bool = Query(False),
) -> list[UsuarioResponse]:
    """Las cuentas del sistema.

    `UsuarioActual` y no `UsuarioVerificado`: ver la lista es un endpoint de lectura
    y no compromete a nadie. Pedir la contrasena para mirar una lista haria que el
    uso diario molestase, y una defensa que molesta se deja de usar.

    `password_hash` no sale: `UsuarioResponse` no declara el campo, y con
    `from_attributes` eso es lo que lo mantiene fuera. Ver el modulo de
    `app/schemas/auth.py`.
    """
    consulta = select(UserModel)
    if solo_activas:
        consulta = consulta.where(UserModel.is_active.is_(True))
    cuentas = list(
        (
            await db.execute(
                consulta.order_by(UserModel.created_at).offset(skip).limit(limit)
            )
        ).scalars()
    )
    return [UsuarioResponse.model_validate(c) for c in cuentas]


@router.post("", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
async def registrar_usuario(
    datos: UsuarioCreate,
    request: Request,
    db: Sesion,
    actor: UsuarioVerificado,
) -> UsuarioResponse:
    """Registra una nueva cuenta en el sistema.

    SEGURIDAD
    ---------
    - Requiere `UsuarioVerificado`: token y cabecera `X-Contrasena-Actual` de quien
      ejecuta la operacion. Esto impide que un atacante con un token robado cree
      nuevas cuentas con credenciales conocidas.
    - El correo se normaliza en minusculas y se rechaza con 409 si ya existe.
    - La contrasena se hashea con `hashear_contrasena` (scrypt N=2**17) y NUNCA se
      almacena en texto plano.
    - La respuesta usa `UsuarioResponse`, por lo que el hash jamas se devuelve.
    """
    correo = datos.correo_normalizado
    existe = (
        await db.execute(
            select(UserModel).where(func.lower(UserModel.email) == correo)
        )
    ).scalar_one_or_none()

    if existe is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una cuenta con el correo {correo}",
        )

    nuevo_usuario = UserModel(
        email=correo,
        nombre=datos.nombre.strip(),
        password_hash=hashear_contrasena(datos.password),
        perfil=datos.perfil,
        grupo=datos.grupo,
        is_active=datos.is_active,
    )
    db.add(nuevo_usuario)
    try:
        await db.commit()
    except IntegrityError as err:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una cuenta con el correo {correo}",
        ) from err

    await db.refresh(nuevo_usuario)
    # Sin contraseñas ni hashes en el rastro: solo quien la creo y con que
    # perfil y grupo nacio la cuenta.
    await registrar_auditoria(
        accion="crear_usuario",
        modulo="usuarios",
        usuario_id=str(actor.id),
        registro_afectado=str(nuevo_usuario.id),
        detalles={
            "nombre": nuevo_usuario.nombre,
            "email": correo,
            "perfil": nuevo_usuario.perfil,
            "grupo": nuevo_usuario.grupo,
        },
        rol=actor.perfil,
        ip=request.client.host if request.client else "desconocida",
        resultado="exito",
        db=db,
    )
    return UsuarioResponse.model_validate(nuevo_usuario)


# Estas tres rutas van ANTES de `/{usuario_id}` a proposito. El parametro esta
# tipado como UUID, pero el patron de la ruta no tiene convertidor: sin este
# orden, GET /usuarios/membresias caeria en `obtener_cuenta` y daria un 422
# diciendo que "membresias" no es un UUID. El que manda es el primer match.
@router.get("/membresias", response_model=list[MembresiaItemResponse])
async def listar_membresias(
    db: Sesion,
    company_id: EmpresaActiva,
    _: Administrador,
) -> list[MembresiaItemResponse]:
    """Las cuentas y sus roles EN la empresa activa.

    No es `GET /companies`: aqui la pregunta es quien trabaja en ESTA empresa
    y con que autoridad, no que empresas existen en el sistema.
    """
    return await membresias_service.listar_membresias(db, company_id)


@router.post(
    "/membresias",
    response_model=MembresiaItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def asignar_membresia(
    datos: MembresiaCreate,
    request: Request,
    db: Sesion,
    company_id: EmpresaActiva,
    actor: UsuarioVerificado,
    rol: Administrador,
) -> MembresiaItemResponse:
    """Da acceso a una cuenta ya creada, con un rol en la empresa activa.

    La empresa NO se toma del cuerpo: viene de `X-Company-ID`, validado contra
    las membresias del que llama. Un `company_id` en el JSON recibe 422.
    """
    return await membresias_service.asignar_membresia(
        db,
        company_id,
        datos,
        actor_id=actor.id,
        actor_rol=rol,
        ip=request.client.host if request.client else "desconocida",
    )


@router.patch("/membresias/{usuario_id}", response_model=MembresiaItemResponse)
async def actualizar_membresia(
    usuario_id: UUID,
    datos: MembresiaUpdate,
    request: Request,
    db: Sesion,
    company_id: EmpresaActiva,
    actor: UsuarioVerificado,
    rol: Administrador,
) -> MembresiaItemResponse:
    """Cambia el rol o deja sin acceso a la cuenta EN la empresa activa.

    Dar de baja la membresia (`activo=false`) no borra la fila ni la cuenta:
    es la misma decision que `is_active` en `users`, pero con alcance de empresa.
    """
    return await membresias_service.actualizar_membresia(
        db,
        company_id,
        usuario_id,
        datos,
        actor_id=actor.id,
        actor_rol=rol,
        ip=request.client.host if request.client else "desconocida",
    )


@router.get("/{usuario_id}", response_model=UsuarioResponse)
async def obtener_cuenta(
    usuario_id: UUID, db: Sesion, _: UsuarioActual
) -> UsuarioResponse:
    """Una cuenta por id. Sin hash, como todas."""
    return UsuarioResponse.model_validate(await _cuenta(db, usuario_id))


@router.patch("/{usuario_id}", response_model=UsuarioResponse)
async def actualizar_cuenta(
    usuario_id: UUID,
    datos: UsuarioUpdate,
    request: Request,
    db: Sesion,
    actor: UsuarioVerificado,
) -> UsuarioResponse:
    """Cambia el nombre o da de baja la cuenta.

    LO QUE NO SE PUEDE CAMBIAR, Y POR QUE
    -------------------------------------

    - **`id`.** La PK la pone la base.
    - **`email`.** El correo va dentro de la clave del rate limit del login
      (`rate_limit.esta_bloqueado(correo, ip)`). Cambiarlo deja de contar los
      intentos de fuerza bruta de la cuenta antigua, y el bloqueo deja de
      proteger. Por eso no hay `email` en `UsuarioUpdate`.
    - **`password_hash` directo.** Por aqui no se cambia una contrasena: se cambia
      con el endpoint que exige la actual. Un `PATCH {"password_hash": "..."}`
      aceptaria un hash cualquiera sin verificar la contrasena de nadie.

    DAR DE BAJA A LA CUENTA PROPIA
    ------------------------------

    Se permite, y es decision. Bloquearlo tendria sentido si hubiera un
    administrador que te pudiera reponer el acceso; sin roles aqui, "no te puedes
    dar de baja" es tambien "no te puede dar de baja nadie", y el unico camino seria el
    script con la base de datos. Que sea el unico sistema con una sola cuenta y
    esta se cierre a si misma es un fallo de recuperacion, no un agujero: el
    efectivo del que se trata esta en la base y en los papeles.

    Lo que si se bloquea es dar de baja a la **ultima cuenta activa**, que si es
    un agujero: deja el sistema entero sin puerta de entrada y la unica
    recuperacion es entrar a Postgres a mano.
    """
    cuenta = await _cuenta(db, usuario_id)

    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No mandaste ningún campo que se pueda cambiar",
        )

    if ("perfil" in cambios or "grupo" in cambios) and actor.perfil not in (
        "admin",
        "develop",
        "programador",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un administrador puede modificar perfiles o grupos de acceso.",
        )

    if cambios.get("is_active") is False:
        activas = await db.execute(
            select(func.count()).select_from(UserModel).where(
                UserModel.is_active.is_(True), UserModel.id != cuenta.id
            )
        )
        if (activas.scalar_one() or 0) == 0:
            # 409 y no 422: el cuerpo es valido, el estado del sistema es el que
            # no lo permite. Un 422 diria "arregla el cuerpo", y el cuerpo esta
            # perfecto.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Esta es la ultima cuenta activa. Si la das de baja el "
                    "sistema se queda sin puerta de entrada y habria que "
                    "reposarla a mano en la base."
                ),
            )

    valores_antes = {k: getattr(cuenta, k, None) for k in cambios}
    for campo, valor in cambios.items():
        setattr(cuenta, campo, valor)
    await db.commit()
    await db.refresh(cuenta)
    # `is_active=False` ya cierra la puerta en la SIGUIENTE peticion, porque
    # `get_current_user` lee la fila cada vez. Ademas se revocan las sesiones
    # conocidas para que la baja quede escrita en tokens_revocados, que es
    # append-only y responde "quien cerro esto" sin depender del perfil.
    if cambios.get("is_active") is False:
        await revocar_sesiones_de_usuario(db, cuenta.id, "baja_cuenta")
    # Cubre el cambio de perfil y la baja (`is_active=False`): los dos son
    # el mismo hecho para la auditoria, que campos cambiaron y quien lo hizo.
    # Solo los campos que venian en el cuerpo (`exclude_unset`).
    await registrar_update_auditoria(
        modelo="usuarios",
        registro_id=str(cuenta.id),
        campos_modificados=sorted(cambios.keys()),
        usuario_id=str(actor.id),
        valores_anteriores=valores_antes,
        valores_nuevos=cambios,
        nombre=cuenta.nombre,
        rol=actor.perfil,
        ip=request.client.host if request.client else "desconocida",
        db=db,
    )
    return UsuarioResponse.model_validate(cuenta)


@router.post("/{usuario_id}/contrasena", status_code=status.HTTP_204_NO_CONTENT)
async def cambiar_contrasena(
    usuario_id: UUID,
    cuerpo: CambioContrasenaRequest,
    request: Request,
    db: Sesion,
    actor: UsuarioVerificado,
) -> None:
    """Rota la contrasena de una cuenta.

    DOS PUERTAS, Y POR QUE HACE FALTA LAS DOS
    ------------------------------------------

    - `X-Contrasena-Actual` (el header): la contrasena de **quien esta
      llamando**. Es `UsuarioVerificado`, y es lo que impide que un token robado
      tome la cuenta. Ver `deps.get_current_user_verificado`.
    - `contrasena_actual` (el cuerpo): la contrasena de **la cuenta que se va a
      cambiar**. Es lo que impide que quien llama cambie la contrasena de OTRA
      cuenta sin saber la de esa cuenta.

    Son la misma palabra y no se pueden fusionar, porque responden a preguntas
    distintas: "quien eres tu" y "de quien es esta cuenta". Con una sola, un
    atacante con un token robado podria cambiar la contrasena de cualquiera, que
    es el takeover permanente que `CambioContrasenaRequest` documenta.

    Y QUE PASA CON LOS TOKENS
    -------------------------

    Se revocan las sesiones CONOCIDAS de la cuenta (`tokens_revocados`,
    append-only), incluida la propia si es la cuenta de quien llama. Quien
    rotó su clave tiene que volver a entrar: es incómodo, pero es lo único que
    hace que "cambiar la contrasena" signifique algo para un token robado.

    Un token emitido pero nunca usado no tiene fila de actividad y no se puede
    nombrar: sigue vivo hasta `exp`. No se inventa un `jti` para el: la base
    no puede revocar lo que no conoce, y decirlo aqui evita prometer una
    revocacion total que no existe.
    """
    cuenta = await _cuenta(db, usuario_id)

    if not verificar_contrasena(cuerpo.contrasena_actual, cuenta.password_hash):
        # 400 y no 401: el token es valido —si no, `UsuarioVerificado` ya habria
        # cortado— y lo que no cuadra es el cuerpo. Un 401 diria "tu sesion no
        # sirve", que es falso, y llevaria al cliente a re-loguearse en vez de a
        # corregir el campo.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual de esa cuenta no es correcta",
        )

    # El scrypt va DESPUES de comparar, no antes. Con N=2**17 son ~100 ms y ~64 MB
    # por hash: comparar primero significa que solo se gasta uno, y solo cuando
    # hay algo que verificar. Es el mismo criterio que la regla 10 de `AGENTS.md`
    # para el login.
    cuenta.password_hash = hashear_contrasena(cuerpo.contrasena_nueva)  # type: ignore
    await db.commit()
    await revocar_sesiones_de_usuario(db, cuenta.id, "cambio_contrasena")
    if cuenta.id == actor.id:
        # Si el override de tests no puso jti en `request.state`, esto no hace
        # nada; en produccion cierra tambien la sesion que hizo el cambio.
        await revocar_token_actual(request, db, actor.id, "cambio_contrasena")
    # Se audita QUE se roto la contrasena y quien lo hizo, nunca el valor:
    # ni la nueva, ni la actual, ni el hash entran en el rastro.
    await registrar_auditoria(
        accion="cambiar_contrasena",
        modulo="usuarios",
        usuario_id=str(actor.id),
        registro_afectado=str(cuenta.id),
        detalles={"nombre": cuenta.nombre, "email": cuenta.email},
        rol=actor.perfil,
        ip=request.client.host if request.client else "desconocida",
        resultado="exito",
        db=db,
    )


@router.post("/{usuario_id}/contrasena-admin", status_code=status.HTTP_204_NO_CONTENT)
async def reset_contrasena_admin(
    usuario_id: UUID,
    cuerpo: ResetContrasenaAdminRequest,
    request: Request,
    db: Sesion,
    actor: UsuarioVerificado,
    rol: Administrador,
) -> None:
    """Fija la clave de otra cuenta, firmada por un administrador de la empresa.

    No es el endpoint anterior con un permiso distinto: son dos preguntas.
    `POST /{id}/contrasena` prueba que quien llama conoce la clave DE ESA
    cuenta, y sirve para cambiar la propia. Aqui nadie puede conocer la clave
    ajena: la prueba es la contrasena DE QUIEN ADMINISTRA (`UsuarioVerificado`)
    mas su rol en la empresa (`Administrador`). Sin las dos, un token robado
    bastaria para tomar permanentemente la cuenta de otro.

    La clave nueva no se guarda en texto, no se devuelve y nunca entra en la
    auditoria. Las sesiones conocidas de la cuenta quedan revocadas, con el
    motivo `reset_administrativo` en `tokens_revocados`. Un token emitido pero
    nunca usado no tiene `jti` conocido por la base y vive hasta `exp`: es la
    ventana que queda, y se dice aqui en vez de disimularla.
    """
    cuenta = await _cuenta(db, usuario_id)
    cuenta.password_hash = hashear_contrasena(cuerpo.contrasena_nueva)  # type: ignore
    await db.commit()
    await revocar_sesiones_de_usuario(db, cuenta.id, "reset_administrativo")
    await registrar_auditoria(
        accion="reset_contrasena_admin",
        modulo="usuarios",
        usuario_id=str(actor.id),
        registro_afectado=str(cuenta.id),
        detalles={"nombre": cuenta.nombre, "email": cuenta.email},
        rol=rol.value,
        ip=request.client.host if request.client else "desconocida",
        resultado="exito",
        db=db,
    )
