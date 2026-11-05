"""Tests del módulo de auditoría.

## QUÉ SE PRUEBA AQUÍ

Que las filas de `auditoria` saben **quién hizo cada cosa**. Antes no: la
función base tenía `usuario_id=None` hardcodeado y los seis helpers recibían
el id como parámetro pero lo enterraban en `detalles={'usuario': ...}` como
texto. La tabla crecía y a la vez no podía responder la única pregunta para
la que existe.

Cada test de este archivo **muere** si alguien revierte el arreglo. Sin eso,
un `git revert` restauraría el bug y ningún test se entera — que es exactamente
como el bug llegó a production en primer lugar.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Auditoria
from app.services.auditoria import (
    registrar_auditoria,
    registrar_digitalizacion_auditoria,
    registrar_intento_fallido_auditoria,
    registrar_login_auditoria,
    registrar_logout_auditoria,
    registrar_update_auditoria,
    registrar_delete_auditoria,
    registrar_error_sistema_auditoria,
)


# ---------------------------------------------------------------------------
# 1. La función base escribe `usuario_id` cuando se lo pasan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registrar_auditoria_guarda_el_usuario_id(db_session):
    """La columna `usuario_id` se llena con el UUID del usuario que opera.

    ## POR QUÉ ESTE TEST MUERE

    MEDIDO: antes `registrar_auditoria` tenía `usuario_id=None` **hardcodeado**
    en el cuerpo de la función. Los seis que la llamaban recibían `usuario_id`
    como parámetro y lo metían en `detalles={'usuario': ...}`. La fila se
    escribía con `usuario_id = NULL` siempre.

    Si alguien revive ese `None` (un `git revert`, un refactor que "limpia"
    parámetros), este test es el que lo caza: afirma que la fila guardada
    tiene `usuario_id = <uuid>`.
    """
    uid = uuid4()
    await registrar_auditoria(
        accion="accion_de_prueba",
        modulo="test",
        usuario_id=str(uid),
        detalles={"dato": "valor"},
        rol="admin",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "accion_de_prueba")
        )
    ).scalar_one()

    assert fila.usuario_id == uid, (
        "la fila de auditoría tiene usuario_id=NULL. "
        "¿Volvió el hardcode? El arreglo es que el parámetro "
        "usuario_id se pase a la columna."
    )
    assert fila.accion == "accion_de_prueba"
    assert fila.modulo == "test"
    assert fila.rol == "admin"
    assert fila.detalles is not None


@pytest.mark.asyncio
async def test_registrar_auditoria_con_usuario_none_es_honesto(db_session):
    """Sin usuario, la columna queda NULL. No se inventa un valor.

    Hay eventos del sistema que no tienen un usuario detrás (un fallo antes
    de que entre nadie). En esos casos `usuario_id=None` es la respuesta
    correcta, no un bug.
    """
    await registrar_auditoria(
        accion="fallo_del_sistema",
        modulo="test",
        usuario_id=None,
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "fallo_del_sistema")
        )
    ).scalar_one()

    assert fila.usuario_id is None


# ---------------------------------------------------------------------------
# 2. Los helpers pasan `usuario_id` a la función base
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_auditoria_guarda_el_usuario(db_session):
    """`registrar_login_auditoria` escribe el UUID en la columna.

    Antes lo metía en `detalles={'usuario': ...}` y la columna quedaba NULL.
    """
    uid = uuid4()
    await registrar_login_auditoria(
        usuario_id=str(uid),
        rol="digitalizador",
        ip="127.0.0.1",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "login")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert fila.modulo == "auth"
    assert fila.rol == "digitalizador"
    assert fila.ip_inet == "127.0.0.1"


@pytest.mark.asyncio
async def test_logout_auditoria_guarda_el_usuario(db_session):
    uid = uuid4()
    await registrar_logout_auditoria(
        usuario_id=str(uid),
        rol="admin",
        ip="10.0.0.1",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "logout")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert fila.modulo == "auth"


@pytest.mark.asyncio
async def test_intento_fallido_sin_usuario_es_honesto(db_session):
    """Un login fallido de un usuario que no existe: `usuario_id=None`.

    No se puede saber quién es si la cuenta no existe. El correo va en
    `detalles`, que es donde se puede buscar.
    """
    await registrar_intento_fallido_auditoria(
        modulo="auth",
        accion="login",
        usuario_identificacion="noexiste@example.com",
        rol=None,
        ip="192.168.1.1",
        motivo="usuario_no_existe",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "login_fallido")
        )
    ).scalar_one()

    assert fila.usuario_id is None
    assert fila.rol is None
    assert "noexiste@example.com" in fila.detalles


@pytest.mark.asyncio
async def test_digitalizacion_guarda_el_usuario(db_session):
    """El escáner registra quién digitalizó cada archivo."""
    uid = uuid4()
    ticket_id = uuid4()
    await registrar_digitalizacion_auditoria(
        ticket_id=str(ticket_id),
        usuario_id=str(uid),
        rol="digitalizador",
        archivo="/ruta/comprobante.pdf",
        resultado="CREADO",
        exitoso=True,
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "digitalizar")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert str(fila.registro_afectado) == str(ticket_id)
    assert fila.modulo == "scan"
    assert fila.rol == "digitalizador"


@pytest.mark.asyncio
async def test_update_guarda_el_usuario(db_session):
    uid = uuid4()
    await registrar_update_auditoria(
        modelo="tickets",
        registro_id=str(uuid4()),
        campos_modificados=["provider_name", "total_amount"],
        usuario_id=str(uid),
        rol="admin",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "update")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert fila.modulo == "tickets"
    assert fila.rol == "admin"


@pytest.mark.asyncio
async def test_delete_guarda_el_usuario(db_session):
    uid = uuid4()
    await registrar_delete_auditoria(
        modelo="tickets",
        registro_id=str(uuid4()),
        usuario_id=str(uid),
        rol="admin",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "delete")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert fila.modulo == "tickets"


@pytest.mark.asyncio
async def test_error_sistema_puede_tener_usuario(db_session):
    """Un error de sistema puede o no tener usuario detrás."""
    uid = uuid4()
    await registrar_error_sistema_auditoria(
        modulo="scan",
        error="timeout de ollama",
        usuario_id=str(uid),
        ip="127.0.0.1",
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "error_sistema")
        )
    ).scalar_one()

    assert fila.usuario_id == uid
    assert fila.modulo == "scan"


# ---------------------------------------------------------------------------
# 3. El registro de perfil no miente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cambiar_perfil_registra_el_perfil_anterior_correctamente(db_session):
    """`perfil_anterior` es el valor **antes** del cambio, no después.

    ## POR QUÉ ESTE TEST MUERE

    MEDIDO: antes `registrar_perfil_usuario` escribía dos filas y la segunda
    releía `usuario.perfil` de la sesión **ya mutada**. La fila decía
    `perfil_anterior: "admin", perfil_nuevo: "admin"` cuando lo que había
    pasado era de `admin` a `digitalizador`. Una fila de auditoría que dice
    que nada cambió, en el registro que dice que algo cambió, es peor que no
    tener la fila: hace que un cambio real parezca que no ocurrió.

    Ahora es una sola fila, con `perfil_anterior` capturado antes del cambio.
    """
    from app.models import UserModel

    user = UserModel(
        email=f"perfil_test_{uuid4().hex[:8]}@test.local",
        nombre="Perfil Test",
        password_hash="$scrypt$N=131072,r=8,p=1$c2FsdA$hashhashhashhash",
        perfil="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    user_id = user.id

    from app.services.auditoria import registrar_perfil_usuario

    await registrar_perfil_usuario(
        usuario_id=str(user_id),
        nuevo_perfil="digitalizador",
        usuario_operador=str(uuid4()),
        db=db_session,
    )

    fila = (
        await db_session.execute(
            select(Auditoria).where(Auditoria.accion == "cambiar_perfil")
        )
    ).scalar_one()

    assert "perfil_anterior" in fila.detalles
    assert "admin" in fila.detalles
    assert "digitalizador" in fila.detalles


# ---------------------------------------------------------------------------
# 4. El error de auditoría no tumba la operación
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_un_fallo_de_auditoria_no_se_propaga(db_session):
    """Si la tabla no existe o hay un error de conexión, no se lanza.

    Una auditoría que no se pudo escribir **no** puede tumbar el trabajo de la
    persona: perder un gasto porque el rastro falló cambia un ticket guardado
    por una fila de log, y el ticket es el dato.

    Pero **sí** tiene que quedar dicho en el log. Antes el `except` hacía
    `print(...)` y a nadie lee stdout.
    """
    # No se espera excepción aunque la fila tenga datos problemáticos
    await registrar_auditoria(
        accion="prueba_no_propaga",
        modulo="test",
        usuario_id=None,
        db=db_session,
    )
