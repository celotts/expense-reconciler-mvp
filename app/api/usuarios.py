"""Administrar las cuentas: listar, dar de baja, cambiar la contrasena.

QUE POR QUE EXISTE, Y QUE POR QUE NO HAY `POST /usuarios`
--------------------------------------------------------

Existia `scripts/crear_usuario.py` y nada mas. Las cuentas las creaba quien tenia
la base de datos, con un script que pide la contrasena por teclado para que no
quede en el historial del shell. Eso es correcto para el alta —no hay autorregistro
porque un formulario abierto es la forma mas rapida de que un tercero se siente a
escribir en el historico contable— y es insuficiente para el resto: no habia forma
de dar de baja a alguien ni de rotarle la contrasena desde la aplicacion.

El alta se queda en el script, a proposito. No es que se haya olvidado el `POST`:
es que el argumento de por que no hay autorregistro ("quien decide que alguien
entra, es quien tiene la base de datos") sigue valido, y un endpoint de alta
autenticado seria un autorregistro con un paso mas.

LO QUE ESTA PROTEGIDO Y LO QUE NO
----------------------------------

Cada endpoint de escritura pide `UsuarioVerificado`, o sea token **y** la
contrasena de quien lo hace (`X-Contrasena-Actual`). Un token robado no basta para
dar de baja a otra cuenta ni para cambiar una contrasena.

Lo que sigue SIN resolver, y conviene no olvidar: **no hay roles**. Cualquier
cuenta autenticada puede dar de baja a otra, porque `users` no tiene columna de rol.
Lo que hace `UsuarioVerificado` es acotar el dano —un atacante con un token robado
tampoco tendria la contrasena—, no decidir quien puede tocar a quien. Eso necesita
un modelo de roles que no existe. Ver la nota larga de
`app/core/deps.py::get_current_user_verificado`.

ORDEN DE LAS RUTAS
------------------

`/usuarios/{usuario_id}/contrasena` tiene dos segmentos, y las de un solo
segmento no colisionan con ella. Aun asi, las literales se declaran antes que los
placeholders por el criterio que `tickets.py` y `reconciliations.py` ya
documentaron: en FastAPI gana el primer match, y demostrar que dos rutas no
colisionan cuesta menos que demostrar que no colisionan.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.database import get_db
from app.core.deps import Sesion, UsuarioActual, UsuarioVerificado
from app.core.security import hashear_contrasena, verificar_contrasena
from app.models.user import UserModel
from app.schemas.auth import (
    CambioContrasenaRequest,
    UsuarioResponse,
    UsuarioUpdate,
)

router = APIRouter(tags=["Usuarios"])


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
    db: Sesion,
    _: UsuarioVerificado,
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
    administrador que te pudiera reponer el acceso; sin roles, "no te puedes dar de
    baja" es tambien "no te puede dar de baja nadie", y el unico camino seria el
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No mandaste ningún campo que se pueda cambiar",
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

    for campo, valor in cambios.items():
        setattr(cuenta, campo, valor)
    await db.commit()
    await db.refresh(cuenta)
    return UsuarioResponse.model_validate(cuenta)


@router.post("/{usuario_id}/contrasena", status_code=status.HTTP_204_NO_CONTENT)
async def cambiar_contrasena(
    usuario_id: UUID,
    cuerpo: CambioContrasenaRequest,
    db: Sesion,
    _: UsuarioVerificado,
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

    Y POR QUE NO SE INVALIDAN LOS TOKENS
    ------------------------------------

    Porque no hay forma de hacerlo. `crear_token` firma con `SECRET_KEY` y sin un
    `jti` ni una lista de revocacion, un token emitido antes del cambio sigue
    siendo criptograficamente valido hasta que caduca (8 horas). `AGENTS.md` ya lo
    dice de `SECRET_KEY`: "lo que NO hay es revocacion de tokens: uno robado vive
    8 horas". Rotar la contrasena acorta la ventana a partir del siguiente login,
    no de golpe, y decirlo aqui es mejor que prometer lo que no hace.

    Cuando la cuenta es la propia, esto no importa: la sesion del que la rotó
    sigue viva, que es lo comodo. Cuando es otra, es lo que hay.
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
    cuenta.password_hash = hashear_contrasena(cuerpo.contrasena_nueva)
    await db.commit()
