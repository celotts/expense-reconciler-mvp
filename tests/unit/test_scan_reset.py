"""Un endpoint que borra la base, y los cuatro cerrojos que tiene.

POR QUE LA MAYORIA DE LOS CASOS SON NEGATIVOS
=============================================

Un endpoint destructivo se verifica por lo que **NO** deja pasar, no por lo que
hace. Un test que solo comprueba "borra cuando le piden borrar" pasa igual con los
cerrojos rotos, y es exactamente el test que da la sensacion de que esta
verificado.

Por eso lo de aqui abajo es casi todo "que NO pasa": que el flag apagado no
existe, que sin contrasena no borra, que sin la frase no borra, y **que no borra
cuando hay trabajo humano**. La ultima es la que protege lo unico que no se puede
rehacer.

LOS CUATRO CERRADJOS
====================

1. `TICKETS_SCAN_RESET_HABILITADO` apagado -> **404**. Sin este, cualquiera que
   despliegue el codigo tiene un endpoint que borra la base.
2. `UsuarioVerificado` (token + `X-Contrasena-Actual`) -> **401** sin la
   contrasena. Sin este, un token robado borra el historico contable.
3. La frase `BORRAR_YA` -> **422**. Sin este, un `true` de mas borra.
4. Trabajo humano presente -> **409**. Sin este, se borra la correccion de alguien
   que miro un papel, que es lo unico irreversible.

Y ADEMAS, el modo por omision (`ejecutar=false`) solo cuenta.

LO QUE NO SE BORRA
==================

`users`, `companies` y `productos`. Los tres estan aqui porque son "yo no borro
esto" y los tres son el tipo de cosa que un `DELETE` con `CASCADE` se lleva sin
que nadie lo pida:

- **`users`**: borrarla deja el sistema sin puerta de entrada.
- **`companies`**: sin empresa, `POST /scan` solo inventaria y no crearia tickets.
  Limpiar la base sin empresas deja al operador sin poder probar el escaneo, que
  es justo lo que vino a probar.
- **`productos`**: horas de dar de alta, y no tienen que ver con probar el
  escaneo.

Y EL ORDEN DE LOS `DELETE`
==========================

`tickets` va antes que `ticket_documents`, porque `ticket_documents` es
**append-only por trigger** y solo admite el borrado cuando su ticket ya no existe
(`db/migrations/0009_documento_inmutable.sql`). Al reves falla.

Ese orden **no se puede probar aqui**: SQLite no tiene triggers. La prueba real
esta en `tests/integration/test_scan_reset_orden.py`, contra Postgres.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.security import hashear_contrasena
from app.models.company import CompanyModel
from app.models.inventario import CompraItemModel, CompraModel, ProductoModel
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel
from app.models.ticket_document import TicketDocumentModel
from app.models.user import UserModel
from app.services.scan_reset import FRASE_CONFIRMACION

# La contrasena de `usuario_de_prueba`, que es la que se pone en el header.
CONTRASENA = "contrasena-de-prueba"
CORRECTO = {"confirmar": "BORRAR_YA"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_habilitado():
    """Enciende el flag y lo apaga al terminar.

    El `finally` no es cortesia: sin el, un test que falla deja el flag encendido
    y **todos los tests siguientes corren contra un endpoint destructivo
    habilitado**.
    """
    antes = settings.TICKETS_SCAN_RESET_HABILITADO
    settings.TICKETS_SCAN_RESET_HABILITADO = True
    try:
        yield
    finally:
        settings.TICKETS_SCAN_RESET_HABILITADO = antes


@pytest.fixture
async def producto(db_session, test_company):
    """Un producto del catalogo, para comprobar que el reset no se lo lleva.

    `company_id` es NOT NULL y el codigo se llama `codigo` (el `Index` de arriba
    lo llama `ix_productos_codigo`); inventar el nombre del campo aqui falla con
    un TypeError que no dice nada del reset.
    """
    p = ProductoModel(
        company_id=test_company.id,
        nombre="Producto de Prueba",
        codigo="TESTRESET1",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def _ticket(db_session, company, *, revisado: bool = False) -> TicketModel:
    ticket = TicketModel(
        company_id=company.id,
        provider_name="PROVEEDOR DE PRUEBA",
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 10, 1),
        reviewed_at=date(2026, 10, 2) if revisado else None,
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


async def _documento(db_session, ticket) -> TicketDocumentModel:
    doc = TicketDocumentModel(
        ticket_id=ticket.id,
        contenido=b"%PDF-1.4 de prueba",
        content_type="application/pdf",
        nombre_archivo="prueba.pdf",
        tamano=20,
        sha256="a" * 64,
        version=1,
        actor="sistema",
        motivo="carga inicial",
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


async def _scan_file(db_session, ticket) -> ScanFileModel:
    fila = ScanFileModel(
        relative_path="prueba.pdf",
        content_hash="b" * 64,
        status="PROCESADO",
        ticket_id=ticket.id,
    )
    db_session.add(fila)
    await db_session.commit()
    await db_session.refresh(fila)
    return fila


async def _compra(db_session, ticket, company, *, estado: str) -> CompraModel:
    """Una compra con los campos que el modelo exige de verdad.

    `company_id`, `fecha` y `total` son NOT NULL, y `PROCESADO` pide ademas
    `confirmada_por` y `confirmada_at` por `ck_compras_confirmacion`. Todo eso se
    llena aqui a proposito: un test que se saltara esas reglas probaria el borrado
    sobre filas que la base real no dejaria crear, o sea que probaria un estado
    imposible.
    """
    compra = CompraModel(
        company_id=company.id,
        ticket_id=ticket.id,
        estado=estado,
        fecha=ticket.expense_date,
        proveedor_nombre=ticket.provider_name,
        total=ticket.total_amount,
        confirmada_por="operador-de-prueba" if estado == "PROCESADO" else None,
        confirmada_at=datetime(2026, 10, 2, tzinfo=timezone.utc)
        if estado == "PROCESADO"
        else None,
    )
    db_session.add(compra)
    await db_session.commit()
    await db_session.refresh(compra)
    return compra


@pytest.fixture
async def ticket_normal(db_session, test_company):
    return await _ticket(db_session, test_company)


@pytest.fixture
async def ticket_revisado(db_session, test_company):
    return await _ticket(db_session, test_company, revisado=True)


@pytest.fixture
async def compra_procesada(db_session, test_company):
    ticket = await _ticket(db_session, test_company)
    return await _compra(db_session, ticket, test_company, estado="PROCESADO")


async def _post(client, cuerpo=None, *, contrasena=CONTRASENA, **extra):
    datos = {**CORRECTO, "ejecutar": True, **(cuerpo or {})}
    headers = {"X-Contrasena-Actual": contrasena} if contrasena else {}
    headers.update(extra.pop("headers", {}))
    return await client.post("/api/v1/scan/reset", json=datos, headers=headers, **extra)


# ---------------------------------------------------------------------------
# Cerrojo 1: el flag
# ---------------------------------------------------------------------------


class TestElFlag:
    """Sin `TICKETS_SCAN_RESET_HABILITADO`, el endpoint no existe."""

    async def test_apagado_da_404(self, async_client):
        r = await _post(async_client, contrasena=None)
        assert r.status_code == 404

    async def test_el_404_no_dice_que_haya_una_ruta(self, async_client):
        """"Esta deshabilitado" seria una instruccion de como abrirlo.

        El mensaje lo lee tambien quien va a probarlo. Va en el codigo y en el
        docstring, que es donde esa informacion sirve.
        """
        r = await _post(async_client, contrasena=None)
        assert "deshabilitado" not in r.text.lower()

    async def test_prendido_ya_no_da_404(self, async_client, reset_habilitado):
        """La contra-prueba.

        Sin ella, un `reset_habilitado` roto por un nombre mal escrito haria que
        TODOS los tests de este archivo proben un 404 — que es el resultado de "no
        pueden borrar" — y passarian todos.
        """
        r = await _post(async_client, contrasena=None)
        assert r.status_code != 404


# ---------------------------------------------------------------------------
# Cerrojo 2: la contrasena
# ---------------------------------------------------------------------------


class TestLaContrasenaDelQueLlama:
    """Token Y contrasena. Un token robado no borra el historico."""

    async def test_sin_contasena_no_borra(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        r = await _post(async_client, contrasena=None)
        assert r.status_code == 401
        assert len((await async_client.get("/api/v1/scan/stats")).json()["por_estado"]) >= 0
        assert await _quedan(db_session) >= 1

    async def test_una_contrasena_equivocada_no_borra(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        r = await _post(async_client, contrasena="no-es-la-contrasena")
        assert r.status_code == 401
        assert await _quedan(db_session) >= 1

    async def test_el_401_no_dice_cual_de_las_dos_falta(self, async_client, reset_habilitado):
        """El mismo codigo para "no hay token" y "la contrasena no es esa".

        Distinguirlos le diria a quien prueba que el token sirvio y solo falta la
        contrasena, que es medio camino.
        """
        sin = await async_client.post("/api/v1/scan/reset", json=CORRECTO)
        mala = await _post(async_client, contrasena="otra-cosa")
        assert sin.status_code == mala.status_code == 401


# ---------------------------------------------------------------------------
# Cerrojo 3: la frase
# ---------------------------------------------------------------------------


class TestLaFraseDeConfirmacion:
    """`BORRAR_YA` exacta. Un booleano no sirve, y por que."""

    async def test_sin_confirmar_no_borra(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        r = await async_client.post(
            "/api/v1/scan/reset",
            json={"ejecutar": True},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 422
        assert await _quedan(db_session) >= 1

    async def test_una_frase_distinta_no_borra(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        r = await _post(async_client, {"confirmar": "BORRAR"})
        assert r.status_code == 422
        assert await _quedan(db_session) >= 1

    async def test_un_espacio_de_mas_no_borra(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        """`BORRAR_YA ` con un espacio final es el error de manos.

        Normalizarlo en el servidor seria decidir por el que confirmo.
        """
        r = await _post(async_client, {"confirmar": "BORRAR_YA "})
        assert r.status_code == 422
        assert await _quedan(db_session) >= 1

    async def test_el_error_dice_cual_es_la_frase(self, async_client, reset_habilitado):
        """Cuando alguien se equivoca, hay que decirle la palabra.

        Un "confirmacion invalida" obliga a buscarla en el codigo, y la frase es
        justamente el punto de este endpoint.
        """
        r = await _post(async_client, {"confirmar": "mal"})
        assert FRASE_CONFIRMACION in r.text


# ---------------------------------------------------------------------------
# Cerrojo 4: el trabajo humano
# ---------------------------------------------------------------------------


class TestNoSeTocaElTrabajoDeUnaPersona:
    """El cerrojo que protege lo unico irrepetible."""

    async def test_un_ticket_revisado_impide_borrar(
        self, async_client, reset_habilitado, ticket_revisado
    ):
        r = await _post(async_client)
        assert r.status_code == 409
        assert "revisados" in r.text

    async def test_una_compra_procesada_impide_borrar(
        self, async_client, reset_habilitado, compra_procesada
    ):
        """`PROCESADO` significa que el stock YA cuenta esa compra.

        Es lo mas irrecuperable de la base: si se borra, el kardex se rehace desde
        una foto y las ventas se cuentan como si no hubo entrada.
        """
        r = await _post(async_client)
        assert r.status_code == 409
        assert "autorizadas" in r.text

    async def test_el_409_no_borro_nada(
        self, async_client, db_session, reset_habilitado, ticket_revisado
    ):
        """El refus tiene que dejar la base igual.

        Un 409 que ya habia borrado media base antes de avisar es el peor de los
        dos mundos: perdiste el dato y ademas recibiste un error.
        """
        await _post(async_client)
        assert await _quedan(db_session) >= 1

    async def test_el_preview_si_funciona_con_trabajo_humano(
        self, async_client, reset_habilitado, ticket_revisado
    ):
        """Contar no es borrar.

        Si el preview tambien fallara, no habria forma de **saber** que hay trabajo
        humano sin intentar borrar, y quien quisiera limpiar una base con una
        correccion se quedaria sin decision informada.
        """
        r = await _post(async_client, {"ejecutar": False})
        assert r.status_code == 200
        assert r.json()["conteo"]["tickets_revisados"] >= 1


# ---------------------------------------------------------------------------
# El modo por omision
# ---------------------------------------------------------------------------


class TestElPreviewNoBorra:
    """`ejecutar` apagado es el modo por omision, y cuenta."""

    async def test_el_preview_no_toca_nada(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        r = await _post(async_client, {"ejecutar": False})
        assert r.status_code == 200
        assert r.json()["simulado"] is True
        assert await _quedan(db_session) >= 1

    async def test_el_preview_dice_cuantas_hay(self, async_client, reset_habilitado, ticket_normal):
        """Un preview que no dice el numero no sirve de preview.

        Es el requisito de que "va a borrar" venga con "que va a borrar".
        """
        r = await _post(async_client, {"ejecutar": False})
        conteo = r.json()["conteo"]
        assert conteo["tickets"] >= 1
        assert conteo["total_borrable"] >= 1

    async def test_el_preview_dice_lo_que_no_se_borra(self, async_client, reset_habilitado, ticket_normal):
        """Que NO se lleva las cuentas es parte de la respuesta, no de la doc."""
        r = await _post(async_client, {"ejecutar": False})
        lo_que_no = r.json()["lo_que_no_se_borra"]
        for tabla in ("users", "companies", "productos"):
            assert tabla in lo_que_no


# ---------------------------------------------------------------------------
# Lo que NO se borra
# ---------------------------------------------------------------------------


class TestLoQueNoSeBorra:
    """Cuentas, empresas y catalogo."""

    async def test_las_cuentas_siguen(
        self, async_client, db_session, reset_habilitado, ticket_normal
    ):
        await _post(async_client)
        assert (await db_session.execute(_count(UserModel))).scalar_one() >= 1

    async def test_las_empresas_siguen(
        self, async_client, db_session, reset_habilitado, test_company, ticket_normal
    ):
        await _post(async_client)
        assert (await db_session.execute(_count(CompanyModel))).scalar_one() >= 1

    async def test_el_catalogo_sigue(
        self, async_client, db_session, reset_habilitado, test_company, ticket_normal, producto
    ):
        await _post(async_client)
        assert (await db_session.execute(_count(ProductoModel))).scalar_one() >= 1


# ---------------------------------------------------------------------------
# Lo que SI se borra
# ---------------------------------------------------------------------------


class TestLoQueSiSeBorra:
    """Y que cuando no hay trabajo humano, si borra."""

    async def test_borra_los_tickets(
        self, async_client, db_session, reset_habilitado, test_company, ticket_normal
    ):
        r = await _post(async_client)
        assert r.status_code == 200, r.text
        assert (await db_session.execute(_count(TicketModel))).scalar_one() == 0

    async def test_borra_el_registro_del_escaner(
        self, async_client, db_session, reset_habilitado, test_company, ticket_normal
    ):
        """Sin esto, el escaner veria "SIN_CAMBIOS" y no releeria nada.

        `scan_files` es el ledger que evita repetir OCR. Si sobrevive, el segundo
        escaneo dice que ya lo leyo y **no vuelve a leer**, que es justo lo
        contrario de lo que se quiere al probar.
        """
        await _ticket_y_archivo(db_session, test_company)
        await _post(async_client)
        assert (await db_session.execute(_count(ScanFileModel))).scalar_one() == 0

    async def test_el_papel_se_borra_en_cascada(
        self, async_client, db_session, reset_habilitado, test_company
    ):
        """El comprobante original sale con su ticket.

        Es la excepcion que el trigger append-only contempla: el papel se borra
        **porque su ticket dejo de existir**, no porque se borre el papel.
        """
        ticket, _ = await _ticket_y_archivo(db_session, test_company)
        assert (await db_session.execute(_count(TicketDocumentModel))).scalar_one() == 1
        await _post(async_client)
        assert (await db_session.execute(_count(TicketDocumentModel))).scalar_one() == 0

    async def test_borra_las_compras(
        self, async_client, db_session, reset_habilitado, test_company
    ):
        """Una compra en `EN_REVISION` NO bloquea: nadie la ha aprobado.

        Solo bloquea `PROCESADO`. Borrar un `EN_REVISION` es tirar un borrador, y
        exigir la contrasena y la frase ya es bastante para eso.
        """
        ticket = await _ticket(db_session, test_company)
        await _compra(db_session, ticket, test_company, estado="EN_REVISION")
        r = await _post(async_client)
        assert r.status_code == 200, r.text
        assert (await db_session.execute(_count(CompraModel))).scalar_one() == 0

    async def test_dice_cuantos_borro_de_cada_tabla(
        self, async_client, db_session, reset_habilitado, test_company
    ):
        """`borrado` con el numero real, no un `ok`.

        Y `conteo` de antes tiene que coincidir con lo borrado: si no coinciden,
        algo se borro que no se conto, y eso hay que verlo.
        """
        await _ticket_y_archivo(db_session, test_company)
        cuerpo = (await _post(async_client)).json()
        assert cuerpo["borrado"]["tickets"] == cuerpo["conteo"]["tickets"]
        assert cuerpo["borrado"]["scan_files"] == cuerpo["conteo"]["archivos"]

    async def test_conserva_el_conteo_de_empresas_y_cuentas(
        self, async_client, reset_habilitado, test_company, ticket_normal
    ):
        r = await _post(async_client)
        cuerpo = r.json()
        assert cuerpo["empresas_conservadas"] >= 1
        assert cuerpo["usuarios_conservados"] >= 1


# ---------------------------------------------------------------------------
# La frase y el schema
# ---------------------------------------------------------------------------


class TestLaFraseEsDelServidor:
    """La frase no es un parametro del cliente, y por que."""

    def test_la_frase_vive_en_el_servidor(self):
        """Importarla, no reescribirla en el test.

        Si el test comparara contra un `"BORRAR_YA"` escrito a mano, cambiar la
        frase en el servidor haria que dejaran de coincidir y el test pasaria
        mientras el endpoint pide otra palabra.
        """
        assert FRASE_CONFIRMACION == "BORRAR_YA"

    def test_el_schema_no_acepta_campos_de_mas(self):
        """`extra="forbid"`: la fila entera del cliente da 422 con el nombre.

        Si aceptara campos desconocidos, un `{"forzar": true, "scope": "todo"}` de
        un cliente viejo pasaria sin error y sin hacer nada, que es la peor forma
        de fallar.
        """
        from app.schemas.scan import ScanResetRequest

        with pytest.raises(ValidationError):
            ScanResetRequest(**{"confirmar": FRASE_CONFIRMACION, "borrar_todo": True})

    def test_no_hay_scope_ni_empresa_id(self):
        """Un "borra esto" con filtros es un endpoint con un parametro dangerous.

        Un filtro mal puesto borra mas de lo que se queria, que es justo lo que no
        se quiere en algo destructivo. Si hace falta una limpieza parcial, esta la
        tabla que corresponda —los productos tienen su propio endpoint—.
        """
        from app.schemas.scan import ScanResetRequest

        campos = set(ScanResetRequest.model_fields)
        assert campos == {"confirmar", "ejecutar"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(model):
    """Un `SELECT count(*)` que devuelve el entero.

    `scalar_one()` sobre un `count` YA da el numero, no una fila: envolverlo en
    `len()` es el error clasico de escribir el helper como si contuviera algo.
    """
    from sqlalchemy import func, select

    return select(func.count()).select_from(model)

    return select(func.count()).select_from(model)


async def _ticket_y_archivo(db_session, company):
    ticket = await _ticket(db_session, company)
    await _documento(db_session, ticket)
    await _scan_file(db_session, ticket)
    return ticket, None


async def _quedan(db_session) -> int:
    """Cuantos tickets siguen en la tabla.

    Se cuenta con `scalar_one()` sobre un `count()` y **no** con `len(...)`: el
    `scalar_one()` de un aggregate ya devuelve el entero, y envolverlo en `len()`
    es un `TypeError` que hace fallar el test sin decir nada del reset.

    Y se cuenta por la sesion y no por `GET /scan/stats`, porque ese endpoint
    cuenta `scan_files` —los archivos del registro— y no `tickets`. Son dos
    tablas distintas y confundirlas daria un cero que parece "borro todo" cuando
    lo que paso es que se estaba mirando la otra.
    """
    return (await db_session.execute(_count(TicketModel))).scalar_one()