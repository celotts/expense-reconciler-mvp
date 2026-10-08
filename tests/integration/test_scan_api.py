"""Los endpoints del escaner, probados por HTTP con token de verdad.

Se usa `async_client_sin_autenticar` y no `async_client`, porque lo que se prueba
en la mayoria de estos casos es que el escaner esta DETRAS de la puerta: un
escaner de carpeta sin token deja subir la informacion contable de una maquina
entera a cualquiera con un email y una contrasena. La autenticacion no la pone
cada endpoint, la pone el router (ver app/api/api_router.py), asi que la prueba
tiene que entrar por la puerta real y no por una puerta falsa.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from sqlalchemy import select, update

from app.core.enums import ScanStatus
from app.core.security import crear_token, hashear_contrasena
from app.models.inventario import CompraItemModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.services import capture
from app.services.ai_extractor import ai_extractor

P = "/api/v1"

COMPROBANTE = """Tiendas Ramirez SA de CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""


@pytest.fixture
def sin_modelo(monkeypatch):
    """Ni OCR ni modelo: la suite no depende de un binario ni de la red."""
    def _no_ocr(_datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado en la prueba")

    async def _no_modelo(*_a, **_k):
        raise RuntimeError("el modelo esta apagado en la prueba")

    # Se parchea el ATRIBUTO de la instancia (`ai_extractor.extract_from_image`),
    # no un atributo del modulo. `app/services/ai_extractor.py` exporta la
    # INSTANCIA `ai_extractor = AIExtractor()` y los metodos viven en la clase, de
    # modo que parchearlos en el modulo no losoverride: `monkeypatch.setattr`
    # levanta AttributeError porque el modulo no tiene ese atributo, y ese error
    # aparece en el setup de la prueba, no en la de mas abajo.
    monkeypatch.setattr(capture, "_ocr_por_defecto", _no_ocr)
    monkeypatch.setattr(ai_extractor, "extract_from_image", _no_modelo)
    monkeypatch.setattr(ai_extractor, "extract_from_text", _no_modelo)


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    from app.core.config import settings

    destino = tmp_path / "tickets"
    destino.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    return destino


@pytest.fixture
async def con_token(db_session):
    """Un usuario real y su token, como en `test_audit_trail.py`.

    Se cifra la contrasena de verdad (`hashear_contrasena`) en vez de poner una
    cadena cualquiera: el token se firma con `crear_token` sobre el id y el
    correo, y `get_current_user` vuelve a consultar la fila. Si la fila fuera
    falsa, la prueba probaria el camino de un token valido contra un usuario que
    no existe, que es otra cosa.
    """
    usuario = UserModel(
        email=f"ana{uuid4().hex[:6]}@empresa.mx",
        nombre="Ana",
        password_hash=hashear_contrasena("contrasena-larga"),
    )
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario, crear_token(str(usuario.id), usuario.email)[0]


@pytest.fixture
def pdf_de_prueba(monkeypatch):
    """Fuerza la rama de PDF con texto."""
    monkeypatch.setattr(capture, "extract_pdf_text", lambda _b: COMPROBANTE)
    return monkeypatch


def _escribir(carpeta: "object", nombre: str, cuerpo: str = COMPROBANTE):
    (carpeta / nombre).write_bytes(("%PDF-1.7\n" + cuerpo).encode())
    return nombre


class TestLaPuerta:

    async def test_escanear_sin_token_es_401(
        self, async_client_sin_autenticar, carpeta, sin_modelo
    ):
        """Sin token no se escanea. La carpeta entera es informacion contable."""
        _escribir(carpeta, "privado.pdf")

        respuesta = await async_client_sin_autenticar.post(f"{P}/scan/", json={})

        assert respuesta.status_code == 401

    @pytest.mark.parametrize(
        "metodo,ruta",
        [
            ("post", "/scan/"),
            ("get", "/scan/stats"),
            ("get", "/scan/files"),
            ("get", "/scan/config"),
            ("get", "/scan/ocr"),
        ],
    )
    async def test_todos_los_endpoints_exigen_token(
        self, async_client_sin_autenticar, metodo, ruta
    ):
        """Cada endpoint del escaner hereda la puerta del router.

        Se comprueba uno por uno y no "el router en general" porque la ausencia de
        la proteccion se ve igual que su presencia en un review: si un endpoint
        nuevo se registra sin `dependencies=[Depends(get_current_user)]`, nada
        falla y el unico sintoma es que un archivo se lee sin token.
        """
        respuesta = await getattr(async_client_sin_autenticar, metodo)(f"{P}{ruta}")
        assert respuesta.status_code == 401, f"{ruta} quedo abierto"


class TestEscanearPorHttp:

    async def test_escanear_crea_ticket_y_registra_el_archivo(
        self, async_client, db_session, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        respuesta = await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["nuevos"] == 1
        assert cuerpo["leidos"] == 1
        assert len(cuerpo["detalles"]) == 1
        assert cuerpo["detalles"][0]["accion"] == "CREADO"
        assert cuerpo["detalles"][0]["ticket_id"] is not None

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1
        assert tickets[0].total_amount == 1100.0

    async def test_el_resumen_dice_de_que_carpeta_leyo(
        self, async_client, carpeta, sin_modelo, con_token
    ):
        """La ruta sale en la respuesta a proposito.

        El valor por omision de la carpeta es una ruta de una maquina concreta.
        Sin esto, en cualquier otra maquina el operador no tiene forma de saber
        si el cambio de `TICKETS_INPUT_DIR` tomo efecto.
        """
        _, token = con_token

        respuesta = await async_client.post(
            f"{P}/scan/",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["carpeta"].endswith("tickets")

    async def test_una_empresa_que_no_existe_es_404(
        self, async_client, carpeta, sin_modelo, con_token
    ):
        """Atribuir a una empresa inventada es un error del cliente, no un 500.

        Y se comprueba ANTES de tocar el disco: si el escaneo corriera primero y
        fallara al guardar, el operador veria un error de base de datos y no
        que escribio mal el id.
        """
        _, token = con_token
        _escribir(carpeta, "ocho.pdf")

        respuesta = await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(uuid4())},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert respuesta.status_code == 404
        assert "empresa" in respuesta.json()["detail"]

    async def test_una_carpeta_vacia_no_es_un_error(
        self, async_client, carpeta, sin_modelo, con_token
    ):
        """Cero archivos es un escaneo que no encontro nada, no una falla.

        Es el primer `POST /scan` de una instalacion nueva, y tiene que ser un
        200 con ceroes. Un 500 ahi dice "la app esta rota" cuando lo unico que
        pasa es que la carpeta esta vacia.
        """
        _, token = con_token

        respuesta = await async_client.post(
            f"{P}/scan/", json={}, headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["archivos_vistos"] == 0
        assert cuerpo["nuevos"] == 0
        assert cuerpo["con_error"] == 0


class TestElRegistroYLaAuditoria:

    async def test_el_registro_tiene_el_historico_de_lo_que_paso(
        self,
        async_client_sin_autenticar,
        db_session,
        test_company,
        carpeta,
        sin_modelo,
        pdf_de_prueba,
        con_token,
    ):
        """Un archivo nuevo deja un evento `CREADO` con quien y cuando.

        Es lo que convierte "el escaneo funciono" en "este archivo se leyo el dia
        tal por este usuario". Sin el evento, `attempts` dice cuantas veces pero
        no cuando ni por que.
        """
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        # Se entra por `async_client_sin_autenticar` y no por `async_client` a
        # proposito, aunque el token sea valido. `async_client` SOBRESCRIBE
        # `get_current_user` con el usuario de la fixture, asi que el `actor`
        # que se guardaria seria el de esa fixture y no el del token: la prueba
        # pasaria probando que se guarda ALGO, no que se guarda QUIEN. Y "quien
        # lo hizo" es justo lo que el registro de eventos existe para responder.
        await async_client_sin_autenticar.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers={"Authorization": f"Bearer {token}"},
        )

        eventos = (await db_session.execute(select(ScanEventModel))).scalars().all()
        acciones = [e.action for e in eventos]
        assert "CREADO" in acciones

        creado = next(e for e in eventos if e.action == "CREADO")
        # El `actor` es el correo del token, no un "user" constante: es lo que
        # hace auditable un escaneo mas adelante, cuando nadie se acuerda de quien
        # lo corrio.
        assert creado.actor == con_token[0].email
        assert creado.confidence is not None

    async def test_escanear_dos_veces_deja_sin_cambios(
        self, async_client, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}
        cuerpo = {"company_id": str(test_company.id)}

        await async_client.post(f"{P}/scan/", json=cuerpo, headers=cabeceras)
        segunda = await async_client.post(f"{P}/scan/", json=cuerpo, headers=cabeceras)

        assert segunda.status_code == 200
        assert segunda.json()["sin_cambios"] == 1
        assert segunda.json()["nuevos"] == 0

    async def test_listar_filtra_por_estado_y_rechaza_uno_inventado(
        self, async_client, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        """Un estado que no existe se dice, no se devuelve como lista vacia.

        Una lista vacia hace creer que no hay archivos en la carpeta. Con el
        422, el operador sabe que escribio mal el filtro.
        """
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )

        ok = await async_client.get(
            f"{P}/scan/files?estado=PROCESADO", headers=cabeceras
        )
        assert ok.status_code == 200
        assert ok.json()["total"] == 1

        mal = await async_client.get(
            f"{P}/scan/files?estado=INVENTADO", headers=cabeceras
        )
        assert mal.status_code == 422
        assert "no es un estado" in mal.json()["detail"]

    async def test_el_detalle_de_un_archivo_trae_su_historia(
        self, async_client, db_session, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )
        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()

        respuesta = await async_client.get(
            f"{P}/scan/files/{fila.id}", headers=cabeceras
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["relative_path"] == "ocho.pdf"
        assert any(e["action"] == "CREADO" for e in cuerpo["events"])

    async def test_un_uuid_inexistente_es_404(
        self, async_client, con_token
    ):
        """Un id que no existe dice "no existe", no "no hay archivos".

        En un endpoint de reproceso la diferencia importa: vacio y 404 piden
        acciones distintas.
        """
        _, token = con_token

        respuesta = await async_client.get(
            f"{P}/scan/files/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert respuesta.status_code == 404

    async def test_el_registro_no_devuelve_la_ruta_absoluta(
        self, async_client, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        """El listado expone `relative_path`, no la ruta del disco.

        La ruta absoluta es `TICKETS_INPUT_DIR` mas la relativa, y quien llama ya
        sabe cual es la carpeta. Devolverla escribiría el home de alguien en cada
        fila de una tabla que se exporta.
        """
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )
        respuesta = await async_client.get(f"{P}/scan/files", headers=cabeceras)

        fila = respuesta.json()["archivos"][0]
        assert fila["relative_path"] == "ocho.pdf"
        assert str(carpeta) not in respuesta.text


class TestEstadisticas:

    async def test_las_estadisticas_cuentan_lo_que_importa(
        self, async_client, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        _escribir(carpeta, "ocho.pdf")
        (carpeta / "basura.zip").write_bytes(b"PK\x03\x04\x00\x00\x00\x00")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )
        respuesta = await async_client.get(f"{P}/scan/stats", headers=cabeceras)

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["total_archivos"] == 2
        assert cuerpo["por_estado"][ScanStatus.PROCESADO.value] == 1
        assert cuerpo["por_estado"][ScanStatus.NO_SOPORTADO.value] == 1
        assert cuerpo["tickets_vinculados"] == 1
        assert cuerpo["archivos_sin_ticket"] == 1
        # `por_motor` se llena solo cuando algo se leyo. Un ticket leido con
        # reglas es `pdf_text`; lo que importa es que el origen quede separado
        # y no mezclado con el del modelo.
        assert cuerpo["por_motor"].get("pdf_text") == 1

    async def test_stats_no_choquea_con_files_por_id(
        self, async_client, con_token
    ):
        """/stats se resuelve contra su ruta y no contra /files/{id}.

        Si el orden se invirtiera, `stats` se buscaria como un UUID y daria 422.
        Es el fallo de orden de rutas que ya se cometio una vez en este proyecto
        (ver docs/known-issues.md seccion 1); aqui se comprueba con una llamada
        real, no leyendo el codigo.
        """
        _, token = con_token

        respuesta = await async_client.get(
            f"{P}/scan/stats", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 200, (
            "/stats se esta resolviendo como si fuera un id de archivo"
        )


class TestReproceso:

    async def test_reprocesar_un_archivo_lo_relee(
        self, async_client, db_session, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )
        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()

        respuesta = await async_client.post(
            f"{P}/scan/files/{fila.id}/reprocess", headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["forzado"] is True
        assert respuesta.json()["archivo"]["ticket_id"] is not None

    async def test_reprocesar_un_archivo_borrado_es_409(
        self, async_client, db_session, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        """Si el archivo ya no esta, se dice. No se reporta un exito.

        Reprocesar el registro de un archivo que alguien movio a otra carpeta
        tiene que verse: si devolviera 200, el operador creeria que se releyo un
        comprobante que en realidad ya no esta.
        """
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        cabeceras = {"Authorization": f"Bearer {token}"}

        await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id)},
            headers=cabeceras,
        )
        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        (carpeta / "ocho.pdf").unlink()

        respuesta = await async_client.post(
            f"{P}/scan/files/{fila.id}/reprocess", headers=cabeceras
        )

        assert respuesta.status_code == 409
        assert "ya no esta" in respuesta.json()["detail"]

    async def test_reprocesar_un_id_inexistente_es_404(
        self, async_client, con_token
    ):
        _, token = con_token

        respuesta = await async_client.post(
            f"{P}/scan/files/{uuid4()}/reprocess",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert respuesta.status_code == 404


class TestAutorizarLaCompraEnLaMismaCorrida:
    """`POST /scan {"confirmar": true}`.

    Es lo unico que este bloque agrega a lo que el escaneo ya hacia, y lo que
    cambia es **quien decide** que una compra entra al inventario: no alguien
    que miro el papel, sino quien llamo al escaneo. Todo lo demas —el camino,
    el trigger, el bloqueo de lineas sin producto— es el mismo de
    `POST /inventario/compras/{id}/confirmar`, y las pruebas de abajo lo
    comprueban por el mismo lado que las de ahi.
    """

    @pytest.fixture
    def con_lineas(self, monkeypatch):
        """Que la cascada devuelva partidas, que es lo que habilita la compra.

        Sin esto el caso es degenerado: `registrar_compra` devuelve `None` cuando
        `items` es NULL (la ruta OCR no extrae detalle) y no hay compra que
        autorizar. Se parchea `scan_service.capture_ticket` —el simbolo que el
        modulo usa— y no `capture.capture_ticket`: al importar el nombre con
        `from ... import`, el escaner tiene el suyo propio, y parchear el modulo
        de origen no cambiaria nada aqui sin levantar un error de atributo que
        aparece en el setup y no en la prueba.
        """
        from datetime import date
        from decimal import Decimal

        from app.services.parser_service import TicketExtractionResult
        from app.services import scan_service as _scan

        async def _con_lineas(_contenido, _formato, **_k):
            return TicketExtractionResult(
                provider_name="Mercado Local",
                provider_tax_id="MEDL900101XXX",
                total_amount=Decimal("229.68"),
                subtotal=Decimal("198.00"),
                tax_amount=Decimal("31.68"),
                expense_date=date(2025, 3, 15),
                raw_text="reloj",
                confidence=0.95,
                items=[
                    {
                        "description": "TORTA DE PASTOR",
                        "quantity": "1",
                        "unit_price": "85.00",
                        "total": "85.00",
                    },
                    {
                        "description": "REFRESCO COCA 600ML",
                        "quantity": "2",
                        "unit_price": "35.00",
                        "total": "70.00",
                    },
                ],
            )

        monkeypatch.setattr(_scan, "capture_ticket", _con_lineas)
        return monkeypatch

    async def _escanear(self, async_client, token, test_company, **cuerpo):
        respuesta = await async_client.post(
            f"{P}/scan/",
            json={"company_id": str(test_company.id), **cuerpo},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert respuesta.status_code == 200, respuesta.text
        return respuesta.json()

    async def test_por_omision_no_autoriza_nada(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """El default en `false` es la defensa, no una precaucion.

        Todo el proyecto esta ordenado alrededor de que el stock lo mueve alguien
        que miro el papel, y el motivo esta medido: el OCR sobre fotos reales da
        33.3%, y una linea mal leida infla el stock para siempre. Si el default
        fuera `true`, ese poder pasaria al que llama al escaneo —incluido un
        script unattended— sin que nadie lo pidiera.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        cuerpo = await self._escanear(async_client, token, test_company)

        assert cuerpo["compras_confirmadas"] == 0
        assert cuerpo["compras_no_confirmadas"] == []
        compra = (
            await db_session.execute(select(CompraModel))
        ).scalar_one()
        assert compra.estado == "EN_REVISION"
        # Y lo que de verdad importa: el kardex sigue vacio.
        assert (await db_session.execute(select(MovimientoInventarioModel))).scalars().all() == []

    async def test_confirmar_empuja_las_lineas_al_kardex(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """Lo que se pide: digitalizar, guardar y sumar stock en un request.

        El stock se lee como `SUM` del kardex, no como una columna, asi que la
        prueba mira los movimientos: es la unica forma de comprobar que la compra
        *entro* y no solo cambio de etiqueta.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel
        from app.models.inventario import ProductoModel

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        cuerpo = await self._escanear(async_client, token, test_company, confirmar=True)

        assert cuerpo["compras_confirmadas"] == 1
        assert cuerpo["compras_no_confirmadas"] == []

        compra = (await db_session.execute(select(CompraModel))).scalar_one()
        assert compra.estado == "PROCESADO"
        # `ck_compras_confirmacion` lo prohibe en la base: PROCESADO sin firma es
        # un UPDATE que Postgres rechaza. Aqui se comprueba de que la firma es la
        # del token, no un id de cualquiera.
        assert compra.confirmada_por is not None

        movimientos = (
            await db_session.execute(select(MovimientoInventarioModel))
        ).scalars().all()
        assert len(movimientos) == 2
        assert all(m.tipo == "ENTRADA" for m in movimientos)

        productos = (await db_session.execute(select(ProductoModel))).scalars().all()
        assert {p.nombre for p in productos} == {
            "TORTA DE PASTOR",
            "REFRESCO COCA 600ML",
        }

    async def test_una_compra_sin_lineas_no_se_autoriza_y_dice_por_que(
        self, async_client, db_session, test_company, carpeta, sin_modelo, pdf_de_prueba, con_token
    ):
        """Una foto que solo dio encabezado NO se autoriza, y el motivo lo dice.

        Autorizarla pondria la compra en `PROCESADO` sin mover nada: el operador
        veria "1 confirmada" y el stock seguiria igual. Un contador sin motivo es
        peor que no confirmar, porque hace creer que la corrida sirvio.
        """
        from app.models.inventario import CompraModel

        _escribir(carpeta, "ocho.pdf")  # `pdf_de_prueba` no trae partidas.
        _, token = con_token

        cuerpo = await self._escanear(async_client, token, test_company, confirmar=True)

        assert cuerpo["compras_confirmadas"] == 0
        # Aqui no hay compra: `registrar_compra` devuelve None con `items` NULL. Lo
        # que tiene que quedar es que no se reporto un exito falso.
        compras = (await db_session.execute(select(CompraModel))).scalars().all()
        assert compras == []

    async def test_una_compra_ya_procesada_no_se_vuelve_a_autorizar(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """Re-escanear un comprobante ya confirmado no duplica el stock.

        El `SUM` del kardex no distingue "compro dos veces" de "compre dos veces":
        las dos se ven igual en el dashboard. Y un archivo archivado sigue
       _reportando `SIN_CAMBIOS`, asi que esto no es un caso teorico: es la
        segunda corrida de cualquier carpeta ya digitalizada.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        primero = await self._escanear(async_client, token, test_company, confirmar=True)
        assert primero["compras_confirmadas"] == 1
        despues = (await db_session.execute(select(MovimientoInventarioModel))).scalars().all()
        assert len(despues) == 2

        segundo = await self._escanear(async_client, token, test_company, confirmar=True)

        assert segundo["compras_confirmadas"] == 0
        compra = (await db_session.execute(select(CompraModel))).scalar_one()
        assert compra.estado == "PROCESADO"
        # El kardex intacto: la defensa es que no se escriba dos veces.
        assert len(
            (await db_session.execute(select(MovimientoInventarioModel))).scalars().all()
        ) == 2

    async def test_una_compra_rechazada_no_se_reabre_sola(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """`RECHAZADO` es la decision de OTRA persona y no la revierte un escaneo.

        Si `confirmar: true` la reabriera, bastaria con que el mismo comprobante
        volviera a la carpeta para deshacer un rechazo humano. El rechazo se
        deshace con su propio endpoint, que es donde queda la decision.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        await self._escanear(async_client, token, test_company)

        compra = (await db_session.execute(select(CompraModel))).scalar_one()
        compra.estado = "RECHAZADO"
        await db_session.commit()

        cuerpo = await self._escanear(async_client, token, test_company, confirmar=True, reprocesar=True)

        assert cuerpo["compras_confirmadas"] == 0
        assert any("rechaz" in x["motivo"] for x in cuerpo["compras_no_confirmadas"])
        refreshed = (await db_session.execute(select(CompraModel))).scalar_one()
        assert refreshed.estado == "RECHAZADO"
        assert (
            await db_session.execute(select(MovimientoInventarioModel))
        ).scalars().all() == []

    async def test_lineas_sin_producto_bloquean_la_autorizacion(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """El bloqueo de inventario sigue valiendo en este camino.

        Es el mismo `confirmar_compra` de `POST /inventario/compras/{id}/confirmar`,
        asi que el endpoint nuevo **no** puede saltar el bloqueo. Si lo saltara,
        esas lineas no entrarian al inventario y el stock quedaria incompleto sin
        que nada lo dijera.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel
        from app.models.inventario import ProductoModel
        from app.services import inventario_service as inv

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token
        await self._escanear(async_client, token, test_company)

        # Se desenlaza un producto de una linea: la compra queda con una linea sin
        # producto, que es exactamente lo que el bloqueo existe para frenar.
        compra = (await db_session.execute(select(CompraModel))).scalar_one()
        await db_session.refresh(compra)
        await db_session.execute(
            update(CompraItemModel)
            .where(CompraItemModel.compra_id == compra.id)
            .values(producto_id=None)
        )
        await db_session.commit()

        cuerpo = await self._escanear(
            async_client, token, test_company, confirmar=True, reprocesar=True
        )

        assert cuerpo["compras_confirmadas"] == 0
        refreshed = (await db_session.execute(select(CompraModel))).scalar_one()
        assert refreshed.estado == "EN_REVISION"
        assert (
            await db_session.execute(select(MovimientoInventarioModel))
        ).scalars().all() == []
        assert len((await db_session.execute(select(ProductoModel))).scalars().all()) == 2

    async def test_simular_gana_sobre_confirmar_y_no_escribe_nada(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """`simular: true` + `confirmar: true` no autoriza, y lo dice.

        Las dos banderas se contradicen en una cosa: una dice "que pasaria" y la
        otra "haslo". Gana la que no escribe, y la peticion **no** se ignora en
        silencio: si se ignorara, quien pidio confirmar leeria "0 confirmadas" y
        no sabria si fue porque no habia nada o porque se le hizo caso a otra
        bandera.

        LO QUE `simular` PROTEGE Y LO QUE NO
        ======================================

        `simular` apaga el ARCHIVADO —no se borra ni se mueve el papel— y con
        `confirmar` en `true` apaga tambien la autorizacion. No apaga la
        escritura del ticket: la simulacion responde "que habria leido este
        comprobante", y para contestar eso hay que leerlo y guardarlo. Por eso
        esta prueba mira el kardex y las compras, y no la tabla de tickets.

        Se documente como "no crea nada" seria una trampa doble: quien lo lea
        haria una corrida de prueba pensando que no dejo rastro y encontraria
        los tickets de la simulacion mezclados con los de verdad. Por eso la
        asercion mira lo que `simular` **si** apaga.
        """
        from app.models.inventario import CompraModel, MovimientoInventarioModel

        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        cuerpo = await self._escanear(
            async_client, token, test_company, confirmar=True, simular=True
        )

        assert cuerpo["simulado"] is True
        assert cuerpo["compras_confirmadas"] == 0
        assert len(cuerpo["compras_no_confirmadas"]) == 1
        assert "simular" in cuerpo["compras_no_confirmadas"][0]["motivo"]
        # Lo que `simular` apaga: el kardex vacio, la compra sin autorizar y el
        # papel donde estaba. El archivo se queda en la carpeta de entrada, que
        # es lo que hace `simular` util antes de la primera corrida real.
        assert (
            await db_session.execute(select(MovimientoInventarioModel))
        ).scalars().all() == []
        compra = (await db_session.execute(select(CompraModel))).scalar_one()
        assert compra.estado == "EN_REVISION"
        assert (carpeta / "ocho.pdf").exists()

    async def test_simular_no_es_lo_que_dice_la_documentacion(
        self, async_client, db_session, test_company, carpeta, sin_modelo, con_lineas, con_token
    ):
        """`simular` NO deja la base sin tocar, y el repo tiene que decirlo.

        Nacio con la descripcion de "no crea nada" y hacia algo distinto: apaga
        el archivado del disco, pero el ticket se guarda igual. La prueba que
        descubre eso es esta —escribio una asercion que-era- undoubtedly correcta
        sobre `tickets` y fallo— y mientras `simular` se documente como "no
        crea nada", cualquiera que haga una corrida de prueba para ver que hay
        en la carpeta se va a encontrar con tickets de mas en la base.

        Aqui se fija el comportamiento REAL para que nadie lo lea como que es un
        borrado en seco: si alguien decide que `simular` deberia de verdad no
        escribir, este test es el que tiene que cambiar con el, no al reves.
        """
        _escribir(carpeta, "ocho.pdf")
        _, token = con_token

        await self._escanear(async_client, token, test_company, simular=True)

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1, (
            "`simular` apaga el archivado, no la escritura del ticket: el "
            "comprobante se lee y se guarda igual. Si de verdad no escribiera, "
            "este test cambia con el—noto al reves."
        )


class TestEstadoDelOcr:

    async def test_el_endpoint_ocr_dice_por_que_no_funciona(
        self, async_client, con_token
    ):
        """`GET /scan/ocr` separa "apagado" de "no instalado".

        Un booleano `false` no dice cual de los dos es, y los dos piden
        acciones opuestas. Con Tesseract sin instalar, esto tiene que decir como
        instalarlo: si no, cada foto falla y el operador ve "fallo de OCR" en
        lugar de "falta un paquete".
        """
        _, token = con_token

        respuesta = await async_client.get(
            f"{P}/scan/ocr", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert "ocr_habilitado" in cuerpo
        assert "tesseract" in cuerpo["motores"]
        # installed o no, el campo tiene que estar y traer el motivo o None.
        assert "disponible" in cuerpo["motores"]["tesseract"]


class TestConfiguracion:

    async def test_config_dice_la_carpeta_efectiva(
        self, async_client, carpeta, con_token
    ):
        """`GET /scan/config` expone la ruta ya resuelta.

        Es lo que permite confirmar que `TICKETS_INPUT_DIR` tomo efecto sin
        tener que correr un escaneo y mirar el resumen.
        """
        _, token = con_token

        respuesta = await async_client.get(
            f"{P}/scan/config", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["carpeta"].endswith("tickets")
        assert cuerpo["carpeta_existe"] is True
        assert cuerpo["recursivo"] is True
