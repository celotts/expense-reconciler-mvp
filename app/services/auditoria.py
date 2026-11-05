"""Módulo de auditoría para registrar todas las operaciones del sistema.

Registra:
- Login/logout de usuarios
- Intento fallido de acceso
- Digitalización de comprobantes
- Inserciones, actualizaciones y deletes de datos
- Expiración de sesiones
- Cada operación va acompañada de: usuario, rol, acción, módulo, registro afectado y detalles
"""

from datetime import datetime, timezone
import logging
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models import Auditoria

logger = logging.getLogger(__name__)


async def registrar_auditoria(
    accion: str,
    modulo: str,
    usuario_id: Optional[str] = None,
    registro_afectado: Optional[str] = None,
    detalles: Optional[Dict[str, Any]] = None,
    rol: Optional[str] = None,
    ip: Optional[str] = None,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    """Registra una operación en la tabla de auditoría.

    ## POR QUE `usuario_id` ESTA EN LA FIRMA Y ANTES NO

    MEDIDO: esta función tenía `usuario_id=None` **hardcodeado**, y las seis
    que la llamaban recibían `usuario_id` como parámetro — lo metían en
    `detalles={'usuario': ...}` como texto y nunca en la columna. O sea que la
    tabla **crecía** (el rastro existía) y a la vez **no podía responder quién
    hizo nada**, que es lo único para lo que sirve.

    La incoherencia con el resto del proyecto era el argumento: `AGENTS.md`
    lista como regla 23 que «quien firma sale del token, nunca del cuerpo», y
    hay cinco columnas que guardan quién firmó —`confirmada_por`,
    `revisado_por`, `cerrado_por`, `autorizado_por`, `reviewed_by`—, todas
    pobladas. La tabla genérica de auditoría era la única que no lo sabía.

    ## POR QUÉ ES OBLIGATORIO Y POR QUÉ NO TIENE VALOR POR OMISIÓN DE VERDAD

    Es `Optional`, y el default `None` es honesto: hay eventos sin usuario
    (un fallo del sistema antes de que entre nadie, el propio arranque). Lo que
    **no** puede es ser `None` por descuido con un usuario disponible, que es lo
    que pasaba.

    ## POR QUÉ EL ERROR NO TUMBA LA OPERACIÓN

    Una auditoría que no se pudo escribir **no** puede tumbar el trabajo de la
    persona: perder un gasto porque el rastro falló cambia un ticket guardado por
    una fila de log, y el ticket es el dato. Pero **sí tiene que quedar dicho**,
    y antes no quedaba: el `except` hacía `print(...)` y a nadie lee stdout.

    Por eso ahora va a `logger.error` con la excepción completa. La diferencia
    entre las dos cosas es la que importa: *no tumbar la app* es una decisión;
    *no enterarse de que el rastro se perdió* es un fallo silencioso.

    ## POR QUÉ `db` ES OPCIONAL Y POR QUÉ EXISTE

    La sesión se puede inyectar para los tests. Sin ella, la función abre su
    propia conexión —que es lo correcto en producción, donde el llamador no
    tiene una sesión que pasar— pero en la suite de tests la conexión apunta a
    Postgres real y el test revienta con `Connect call failed`.

    La alternativa era no aceptar sesión y que los tests verificaran la fila
    desde `AsyncSessionLocal`, que es la misma conexión... pero en tests no lo
    es: `AsyncSessionLocal` lee `DATABASE_URL` y en la suite eso es el SQLite en
    memoria, que tiene sus propias tablas y no es el motor al que se conecta la
    función cuando `db` no se pasa.

    Con `db` como parámetro keyword-only, el llamador de producción no lo ve
    (es después del `*`), y el test lo usa sin tocar la firma pública.
    """
    # `usuario_id` y `registro_afectado` llegan como string desde los callers
    # (`str(usuario.id)`, `str(ticket.id)`), pero la columna es
    # `UUID(as_uuid=True)`, que espera un objeto UUID. Sin esta conversión,
    # SQLite revienta con `'str' object has no attribute 'hex'`.
    #
    # Y si el string no es un UUID válido (el `actor` del escáner puede ser
    # un nombre como "ana"), se queda `None`: la columna es nullable y un
    # identificador que no es un UUID no puede guardarse como UUID.
    uid: Optional[UUID] = None
    if usuario_id is not None:
        if isinstance(usuario_id, str):
            try:
                uid = UUID(usuario_id)
            except ValueError:
                uid = None
        else:
            uid = usuario_id

    rid: Optional[UUID] = None
    if registro_afectado is not None:
        rid = UUID(registro_afectado) if isinstance(registro_afectado, str) else registro_afectado

    if db is not None:
        entrada = Auditoria(
            usuario_id=uid,
            rol=rol,
            accion=accion,
            modulo=modulo,
            registro_afectado=rid,
            detalles=detalle_a_json(detalles),
            ip_inet=ip,
            fecha=datetime.now(timezone.utc),
        )
        db.add(entrada)
        await db.commit()
        return

    try:
        async with AsyncSessionLocal() as session:
            entrada = Auditoria(
                usuario_id=uid,
                rol=rol,
                accion=accion,
                modulo=modulo,
                registro_afectado=rid,
                detalles=detalle_a_json(detalles),
                ip_inet=ip,
                fecha=datetime.now(timezone.utc),
            )

            session.add(entrada)
            await session.commit()
    except Exception:
        # Ver el docstring: no se propaga, pero tampoco se pierde en silencio.
        logger.exception("no se pudo registrar la auditoria de %s/%s", modulo, accion)


def detalle_a_json(detalles: Optional[Dict[str, Any]]) -> Optional[str]:
    if detalles is None:
        return None
    import json
    try:
        return json.dumps(detalles, default=str)
    except (TypeError, ValueError):
        return str(detalles)


async def registrar_login_auditoria(
    usuario_id: str, rol: str, ip: Optional[str] = None, *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='login', modulo='auth', usuario_id=usuario_id,
        detalles={'ip': ip}, rol=rol, ip=ip, db=db,
    )


async def registrar_logout_auditoria(
    usuario_id: str, rol: str, ip: Optional[str] = None, *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='logout', modulo='auth', usuario_id=usuario_id,
        detalles={'ip': ip}, rol=rol, ip=ip, db=db,
    )


async def registrar_intento_fallido_auditoria(
    modulo: str,
    accion: str,
    usuario_identificacion: str,
    rol: str,
    ip: Optional[str] = None,
    motivo: Optional[str] = None,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    """Un intento que NO entro. Se registra sin `usuario_id` a proposito.

    El identificador que se guardo es el que **se escribio** (el correo del
    intento), no el `id` de una cuenta: un login fallido no tiene cuenta
    asociada —por eso fallo—, y rellenar la columna con algo inventado seria
    afirmar que se sabe quien es. El correo va en `detalles`, que es donde se
    puede buscar.

    Y `rol` tampoco se pone: no se sabe el perfil de alguien que no pudo entrar.
    """
    await registrar_auditoria(
        accion=f'{accion}_fallido', modulo=modulo, usuario_id=None,
        detalles={'motivo': motivo, 'ip': ip, 'identificacion': usuario_identificacion},
        rol=None, ip=ip, db=db,
    )


async def registrar_digitalizacion_auditoria(
    ticket_id: str,
    usuario_id: str,
    rol: str,
    archivo: str,
    resultado: str,
    exitoso: bool = True,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='digitalizar', modulo='scan', usuario_id=usuario_id,
        registro_afectado=ticket_id,
        detalles={'archivo': archivo, 'resultado': resultado, 'exitoso': exitoso},
        rol=rol, db=db,
    )


async def registrar_update_auditoria(
    modelo: str,
    registro_id: str,
    campos_modificados: list,
    usuario_id: str,
    rol: str,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='update', modulo=modelo, usuario_id=usuario_id,
        registro_afectado=registro_id,
        detalles={'campos_modificados': campos_modificados},
        rol=rol, db=db,
    )


async def registrar_delete_auditoria(
    modelo: str,
    registro_id: str,
    usuario_id: str,
    rol: str,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='delete', modulo=modelo, usuario_id=usuario_id,
        registro_afectado=registro_id, rol=rol, db=db,
    )


async def registrar_error_sistema_auditoria(
    modulo: str,
    error: str,
    usuario_id: Optional[str] = None,
    ip: Optional[str] = None,
    *,
    db: Optional[AsyncSession] = None,
) -> None:
    await registrar_auditoria(
        accion='error_sistema', modulo=modulo, usuario_id=usuario_id,
        detalles={'error': error, 'ip': ip}, rol=None, ip=ip, db=db,
    )


async def registrar_perfil_usuario(
    usuario_id: str,
    nuevo_perfil: str,
    usuario_operador: str,
    *,
    db: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """Mantenedor de perfiles de usuario.

    ## LAS DOS FILAS QUE ESCRIBIA, Y POR QUE UNA MENTIA

    MEDIDO: escribia `cambiar_perfil` **antes** del commit y
    `perfil_actualizado` **despues**. La segunda releia `usuario.perfil` de la
    sesion, que ya valia el perfil **nuevo**: la fila decia
    `perfil_anterior: "admin", perfil_nuevo: "admin"` cuando lo que habia
    pasado era de `admin` a `digitalizador`.

    Una fila de auditoria que dice que nada cambio, en el registro que dice que
    algo cambio, es peor que no tener la segunda fila: hace que un cambio real
    parezca que no ocurrio.

    Ademas usaba `AsyncSessionLocal()` en vez de la sesion de la peticion, o
    sea que corria en **otra transaccion** y leia un estado que la peticion aun
    no habia confirmado.

    Ahora: **una sola fila**, escrita con los dos valores capturados **antes**
    del cambio, y el `perfil_anterior` se toma de la variable local, no del
    atributo del objeto mutado.

    ## POR QUÉ `db` ES OPCIONAL

    En producción, la función abre su propia sesión (no hay sesión de petición
    que heredar porque se llama desde un router que no la pasa). En tests, se
    inyecta `db_session` para que la escritura caiga en el SQLite en memoria.
    """
    perfiles_validos = ['consultor', 'digitalizador', 'supervisor', 'admin', 'qa', 'programador', 'develop']

    if nuevo_perfil not in perfiles_validos:
        return {'exito': False, 'mensaje': f'Perfil inválido. Los perfiles válidos son: {", ".join(perfiles_validos)}'}

    _cerrar = False
    if db is None:
        db = AsyncSessionLocal()
        _cerrar = True

    try:
        from app.models import UserModel

        # `usuario_id` llega como string desde los callers, pero `UserModel.id`
        # es `UUID(as_uuid=True)`. Sin esta conversión, la consulta revienta
        # con `'str' object has no attribute 'hex'`.
        uid = UUID(usuario_id) if isinstance(usuario_id, str) else usuario_id

        result = await db.execute(select(UserModel).where(UserModel.id == uid))
        usuario = result.scalar_one_or_none()

        if not usuario:
            return {'exito': False, 'mensaje': 'Usuario no encontrado'}

        # ANTES de mutar. Este es el valor que la fila tiene que decir.
        perfil_anterior = usuario.perfil

        usuario.perfil = nuevo_perfil
        if hasattr(usuario, 'updated_at'):
            usuario.updated_at = datetime.now(timezone.utc)
        db.add(usuario)
        await db.commit()

        # Una sola fila, y con los dos valores. `rol` es el perfil
        # ANTERIOR: es el rol con el que se estaba operando cuando se
        # decidió cambiarlo, y es el que responde "quien podia hacer esto".
        await registrar_auditoria(
            accion='cambiar_perfil',
            modulo='users',
            usuario_id=usuario_operador,
            registro_afectado=usuario_id,
            detalles={
                'perfil_anterior': perfil_anterior,
                'perfil_nuevo': nuevo_perfil,
            },
            rol=perfil_anterior,
            db=db,
        )

        return {
            'exito': True,
            'mensaje': f'Perfil cambiado de {perfil_anterior} a {nuevo_perfil}',
        }
    except Exception as e:
        logger.exception("no se pudo cambiar el perfil de %s", usuario_id)
        return {'exito': False, 'mensaje': f'Error al cambiar perfil: {str(e)}'}
    finally:
        if _cerrar:
            await db.close()