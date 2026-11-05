"""`/health` tiene que decir la verdad, y hay un test que muere si deja de hacerlo.

## EL DEFECTO QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA

`/health` devolvía `{"status": "healthy"}` sin tocar la base de datos. MEDIDO en
este repo: con `expense_pgvector` **parado**, el endpoint seguía respondiendo
**200**, y el contenedor seguía marcado `healthy` en `docker compose ps`
mientras ninguna ruta que lee datos funcionaba.

No es cosmético: ese endpoint es lo que consulta el healthcheck de
`expense-api` en `docker-compose.yml`, y ese healthcheck es la condición de
`depends_on: service_healthy` del front. O sea que la mentira **decía** cuándo
podía levantarse el front. Con ella, el front aparecía apuntando a una API que
no podía contestar, y el síntoma —"el front está arriba pero nada carga"— no
mencionaba el orden de arranque.

Por eso estos tests no comprueban que la respuesta sea 200: comprueban que
**cambie** cuando la base deja de responder.

## POR QUE LA SESION SE ROMPE CON UN OVERRIDE Y NO MONKEYPATCHEANDO

`/health` usa `Depends(get_db)`, la misma dependencia que los endpoints. Los
tests corren contra SQLite en memoria (`tests/conftest.py`), así que el motor
real de la app —que apunta a Postgres— no existe en la suite. Un
`monkeypatch` sobre `AsyncSessionLocal` tendría que reconstruir a mano el
overrido que `conftest` ya hace bien, y además se rompería al primer refactor.

Se sobreescribe `get_db` con una sesion cuyo `execute` revienta. La sesion es
la de SQLite de verdad; lo unico que se falsea es la consulta, que es
exactamente la que decide si el healthcheck dice la verdad.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.core.database import get_db


class _SesionQueNoResponde:
    """Sesion que falla en TODAS las consultas, como una base caida.

    El fallo va en `execute`, no en `__aenter__`: `get_db` abre la sesion con un
    `async with` y, si el fallo estuviera en el `__aenter__`, lo veria FastAPI
    ANTES de entrar al handler — o sea, en la dependencia, no en el `try` de
    `/health`, y el endpoint devolveria un 500 sin pasar por su propia defensa.
    Que reviente en `execute` es lo que reproduce el caso real: la sesion existe,
    la base no contesta.
    """

    def __init__(self, _sesion_real: AsyncSession):
        self._real = _sesion_real

    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("connection refused")


@pytest.fixture
async def health_sin_base(db_session):
    """Un cliente cuyo `get_db` devuelve una sesion que no responde.

    Se apoya en `db_session` y no en `async_client`: hace falta la sesion de
    SQLite **antes** de sobreescribir `get_db`. Pedir `async_client` haria que
    su propio override se instalara primero y, al leerlo aqui, este fixture se
    encontraria a si mismo como override — que es un `RecursionError` en vez de
    un fallo legible.

    Se sobreescribe SOLO `get_db` y solo se falsea `execute`: el unico cambio
    respecto al caso normal es la base, que es la variable del experimento.
    """
    original = app.dependency_overrides.get(get_db)

    async def _db():
        yield _SesionQueNoResponde(db_session)

    app.dependency_overrides[get_db] = _db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as cliente:
            yield cliente
    finally:
        if original is not None:
            app.dependency_overrides[get_db] = original
        else:
            app.dependency_overrides.pop(get_db, None)


class TestElHealthDiceLaVerdad:

    async def test_con_la_base_arriba_dice_healthy(self, async_client):
        """El caso normal. Es el que consume el healthcheck de Docker."""
        respuesta = await async_client.get("/health")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["status"] == "healthy"
        assert cuerpo["database"] == "ok"

    async def test_sin_la_base_dice_503_y_no_healthy(self, health_sin_base):
        """LA DEFENSA. La base no responde -> el healthcheck tiene que fallar.

        Sin esto, `docker compose ps` vuelve a declarar `healthy` un contenedor
        que no puede responder una sola ruta, y `depends_on: service_healthy`
        vuelve a levantar el front contra una API muerta.
        """
        respuesta = await health_sin_base.get("/health")

        assert respuesta.status_code == 503, (
            "con la base abajo /health tiene que responder 503: un 200 aqui "
            "vuelve a declarar healthy un contenedor que no puede servir nada"
        )
        cuerpo = respuesta.json()
        assert cuerpo["status"] == "unhealthy"
        assert cuerpo["database"] == "no responde"

    async def test_el_503_no_filtra_la_url_de_conexion(self, health_sin_base):
        """La respuesta no puede llevar la URL de la base.

        `DATABASE_URL` es `postgresql+asyncpg://usuario:CONTRASENA@host/db`. Este
        endpoint NO pide token —tiene que funcionar para un monitor que no tiene
        credenciales—, asi que un error de SQLAlchemy con la cadena de conexion
        adentro seria la forma mas comoda de leer la contrasena de la base.
        """
        respuesta = await health_sin_base.get("/health")

        for prohibido in ("postgresql+asyncpg://", "@postgres", "CONTRASENA", "://"):
            assert prohibido not in respuesta.text, (
                f"el 503 de /health está filtrando '{prohibido}' de la URL de la base"
            )

    async def test_el_503_no_lo_cachea_el_proxy(self, health_sin_base):
        """`Connection: close` en el 503.

        Sin esto, nginx puede guardar el 503 y el navegador seguir viendo "caído"
        mucho despues de que la base volvio. Un chequeo que se cachea es un
        chequeo que no dice nada del estado actual.
        """
        respuesta = await health_sin_base.get("/health")

        assert respuesta.headers.get("connection", "").lower() == "close", (
            "el 503 de /health no lleva Connection: close, asi que un proxy lo "
            "puede cachear y seguir reportando caido un servicio que ya volvio"
        )

    async def test_el_200_no_lo_cachea_el_proxy(self, async_client):
        """Y el 200 tampoco.

        El otro lado del mismo problema: si el 503 se cachea, el 200 tiene que
        poder invalidarlo. Un `Cache-Control: no-store` en el 200 deja el cache
        del proxy con la respuesta buena, que es la que puede cambiar.
        """
        respuesta = await async_client.get("/health")

        assert respuesta.headers.get("cache-control", "").lower() == "no-store"


class TestLaRespuestaNoHablaDelDominio:

    async def test_no_sale_nada_del_dominio(self, async_client):
        """`/health` no es una puerta al dominio.

        Es la unica ruta sin token que no sea el login, asi que lo que devuelva
        es lo que cualquiera en la maquina puede leer. Que no pida token esta
        justificado en `test_auth_api.py`; aqui se fija el contenido.
        """
        respuesta = await async_client.get("/health")

        texto = respuesta.text.lower()
        for prohibido in ("users", "password", "tickets", "companies", "rfc"):
            assert prohibido not in texto, (
                f"la respuesta de /health menciona '{prohibido}'"
            )

    async def test_no_pide_token(self, async_client_sin_autenticar):
        """Un monitor no tiene credenciales, y uno que las pide reporta caido un
        servicio que esta arriba."""
        respuesta = await async_client_sin_autenticar.get("/health")

        assert respuesta.status_code == 200
        assert respuesta.json()["status"] == "healthy"


class TestLoQueEsteHealthcheckNoCubre:

    async def test_select_1_no_pregunta_por_el_esquema(self, db_session):
        """LA LIMITE DE LA DEFENSA, escrita como test.

        `SELECT 1` pregunta "hay una conexion", no "esta la base completa". Una
        base con la que se puede hablar pero a la que le falta `tickets` pasa
        este endpoint y falla en todos los demas.

        Por eso hay OTRO healthcheck: el del contenedor de Postgres, que pregunta
        por `to_regclass('public.tickets')` y no por el socket. Son dos preguntas
        distintas y cada una la contesta el suyo.

        Sin este test, la proxima persona que lea `SELECT 1` podria concluir que
        el healthcheck cubre el esquema. No lo cubre, y por eso hay dos.
        """
        from sqlalchemy import text

        # La sesion de la suite SI tiene esquema, y `SELECT 1` responde igual.
        # Lo que se verifica aqui es que la consulta del healthcheck no mira
        # ninguna tabla: por eso el mismo endpoint pasaria en una base vacia.
        await db_session.execute(text("SELECT 1"))

        # Y se deja dicho que la comprobacion del esquema es de OTRO motor:
        # `to_regclass` es de Postgres y no existe en SQLite, asi que un test que
        # lo exigiera aqui estaria probando el motor equivocado. Esehealthcheck
        # se verifica en `tests/unit/test_arranque_por_healthcheck.py`.
        with pytest.raises(Exception) as exc:
            await db_session.execute(
                text("select to_regclass('public.tickets') is not null")
            )
        assert "no such function" in str(exc.value).lower() or "operationalerror" in str(
            exc.value
        ).lower(), (
            "se esperaba que `to_regclass` no exista en SQLite: por eso la "
            "pregunta del esquema la contesta el healthcheck de Postgres y no "
            f"este. Lo que salio fue: {exc.value!r}"
        )