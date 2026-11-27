"""La sesion se cierra en el SERVIDOR, no solo en la pantalla.

Antes de `jti`, logout era un aviso al navegador y un token robado vivia 8
horas. Aqui se prueba el recorrido HTTP con la guardia REAL: el fixture
`async_client` sobrescribe `get_current_user` y por eso no sirve para esto.

Dos tiempos, dos preguntas:

- `exp`: caducidad absoluta, no se mueve por estar activo;
- inactividad: tiempo deslizante medido en `sesiones_activas`.

Y una propiedad que importa mas que el numero: vencer por inactividad NO
elimina la fila. Si se eliminara, la peticion siguiente la crearia de nuevo y
el token volveria a servir sin que nadie hubiera vuelto a entrar.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import crear_token, leer_token
from app.core.time import como_utc, utcnow
from app.models.sesion_activa import SesionActiva
from app.models.token_revocado import TokenRevocado

RUTA = "/api/v1/auth/me"


def _cabecera(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestLaSesionSeCierraEnElServidor:
    async def test_el_token_lleva_un_jti_unico_y_firmado(self):
        uno, _ = crear_token("alguien@example.com", "alguien@example.com")
        dos, _ = crear_token("alguien@example.com", "alguien@example.com")

        jti_uno = leer_token(uno)["jti"]
        jti_dos = leer_token(dos)["jti"]

        assert UUID(jti_uno) != UUID(jti_dos)

    async def test_logout_revoca_y_el_token_deja_de_servir(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba
    ):
        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        jti = UUID(leer_token(token)["jti"])

        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        salida = await async_client_sin_autenticar.post(
            "/api/v1/auth/logout", headers=_cabecera(token)
        )
        assert salida.status_code == 204

        revocacion = (
            await db_session.execute(
                select(TokenRevocado).where(TokenRevocado.jti == jti)
            )
        ).scalar_one_or_none()
        assert revocacion is not None
        assert revocacion.motivo == "logout"
        assert (
            await db_session.execute(
                select(SesionActiva).where(SesionActiva.jti == jti)
            )
        ).scalar_one_or_none() is None

        despues = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert despues.status_code == 401
        assert "cerrada" in despues.json()["detail"].lower()

    async def test_la_inactividad_cierra_y_no_se_reabre_sola(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba
    ):
        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        jti = UUID(leer_token(token)["jti"])
        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        sesion = (
            await db_session.execute(select(SesionActiva).where(SesionActiva.jti == jti))
        ).scalar_one()
        sesion.ultima_actividad = utcnow() - timedelta(minutes=31)
        await db_session.commit()

        vencida = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert vencida.status_code == 401
        assert "inactividad" in vencida.json()["detail"].lower()

        # La fila sigue ahi, con la actividad vieja: no se borro ni se actualizo.
        sesion = (
            await db_session.execute(select(SesionActiva).where(SesionActiva.jti == jti))
        ).scalar_one()
        assert como_utc(sesion.ultima_actividad) < utcnow() - timedelta(minutes=30)

        otra = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert otra.status_code == 401

    async def test_la_inactividad_se_mide_desde_la_ultima_peticion_no_desde_el_login(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba
    ):
        """Un token recien emitido tambien puede estar inactivo.

        Si el servidor mirara `emitida_at`, una persona que trabajo 29 minutos,
        se fue a comer y volvio con el mismo token tendria la sesion abierta
        aunque nadie la hubiera usado en ese tiempo. El reloj es deslizante.
        """
        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        jti = UUID(leer_token(token)["jti"])
        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        sesion = (
            await db_session.execute(select(SesionActiva).where(SesionActiva.jti == jti))
        ).scalar_one()
        sesion.emitida_at = utcnow()
        sesion.ultima_actividad = utcnow() - timedelta(minutes=31)
        await db_session.commit()

        vencida = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert vencida.status_code == 401
        assert "inactividad" in vencida.json()["detail"].lower()

    async def test_una_peticion_reciente_renueva_la_actividad(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba
    ):
        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        jti = UUID(leer_token(token)["jti"])
        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        sesion = (
            await db_session.execute(select(SesionActiva).where(SesionActiva.jti == jti))
        ).scalar_one()
        marca = utcnow() - timedelta(minutes=29)
        sesion.ultima_actividad = marca
        await db_session.commit()

        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        sesion = (
            await db_session.execute(select(SesionActiva).where(SesionActiva.jti == jti))
        ).scalar_one()
        assert como_utc(sesion.ultima_actividad) > marca

    async def test_la_actividad_no_extiende_el_exp(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba, monkeypatch
    ):
        """El reloj deslizante no es un refresh: 8 horas siguen siendo 8 horas."""
        token, _ = crear_token(
            str(usuario_de_prueba.id),
            usuario_de_prueba.email,
            expira_en_minutos=1,
        )
        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        import app.core.security as modulo

        ahora = int(__import__("time").time())
        monkeypatch.setattr(modulo.time, "time", lambda: ahora + 120)

        caducado = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert caducado.status_code == 401
        assert "sesion termino" in caducado.json()["detail"].lower()

    async def test_cambiar_la_contrasena_revoca_la_sesion_conocida(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba
    ):
        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        jti = UUID(leer_token(token)["jti"])
        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200

        respuesta = await async_client_sin_autenticar.post(
            f"/api/v1/usuarios/{usuario_de_prueba.id}/contrasena",
            headers={**_cabecera(token), "X-Contrasena-Actual": "contrasena-de-prueba"},
            json={
                "contrasena_actual": "contrasena-de-prueba",
                "contrasena_nueva": "nueva-contrasena-123",
            },
        )
        assert respuesta.status_code == 204

        revocacion = (
            await db_session.execute(
                select(TokenRevocado).where(TokenRevocado.jti == jti)
            )
        ).scalar_one_or_none()
        assert revocacion is not None
        assert revocacion.motivo == "cambio_contrasena"

        despues = await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))
        assert despues.status_code == 401

    async def test_un_token_anterior_al_jti_sigue_sirviendo_hasta_exp(
        self, async_client_sin_autenticar, db_session, usuario_de_prueba, monkeypatch
    ):
        """No queremos dejar a nadie sin token al desplegar.

        Los tokens firmados antes de este cambio no traen `jti`. Forzar la
        caducidad habria cerrado la sesion de quien estaba trabajando en el
        mismo despliegue. Siguen valiendo hasta `exp`; no se pueden revocar ni
        medir su inactividad, y eso se dice en el codigo.
        """
        import app.core.deps as deps

        token, _ = crear_token(str(usuario_de_prueba.id), usuario_de_prueba.email)
        claims = leer_token(token)
        del claims["jti"]
        monkeypatch.setattr(deps, "leer_token", lambda _token: claims)

        assert (await async_client_sin_autenticar.get(RUTA, headers=_cabecera(token))).status_code == 200
        assert (
            await db_session.execute(select(SesionActiva))
        ).scalars().all() == []
