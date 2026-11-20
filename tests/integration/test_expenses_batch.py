"""La carga explicita por lote, probada por HTTP.

`POST /api/v1/expenses/upload-batch` estuvo muerto durante todo el MVP: no estaba
montado, tenia dos TODO (dedup y periodo cerrado) y abria su propia canalización
de lectura con OpenCV y Ollama directo. Hoy es un adaptador finito sobre
`procesar_archivo`, que es el UNICO camino de captura del proyecto.

Lo que se prueba aqui no es que devuelva 200, sino las cuatro cosas que lo hacen
real:

1. **La puerta.** Sin token no se sube nada; el router hereda la autenticacion
   como todos los demas.
2. **El dedup.** Lo hace `scan_files`, no un diccionario en el router. Dos bytes
   iguales no producen dos tickets, aunque cambie el nombre del archivo.
3. **El periodo cerrado.** Se comprueba DESPUES de leer la fecha y ANTES de
   escribir el ticket. Un rechazo no deja un gasto contabilizado en un mes que ya
   estaba cerrado, y el override lleva la firma de quien lo autorizo desde el
   token.
4. **La respuesta.** Es `ScanItemResponse`, igual que `/scan`: los datos del papel
   y el veredicto del gate viajan en el MISMO objeto. Un `ExpenseReceiptSchema`
   aparte dejaria a quien lo lee creyendo que el sistema responde por unos numeros
   que el gate mando a revision.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.enums import SourceType
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.services import capture
from app.services.ai_extractor import ai_extractor

P = "/api/v1"

# La fecha del papel decide el periodo que se comprueba. La de abajo es marzo
# 2025, que es la que se cierra en los tests de periodo.
COMPROBANTE = """Tiendas Ramirez SA de CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""


@pytest.fixture
def sin_modelo(monkeypatch):
    """Ni OCR ni modelo: la suite no depende de Tesseract ni de la red."""

    def _no_ocr(_datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado en la prueba")

    async def _no_modelo(*_a, **_k):
        raise RuntimeError("el modelo esta apagado en la prueba")

    monkeypatch.setattr(capture, "_ocr_por_defecto", _no_ocr)
    monkeypatch.setattr(ai_extractor, "extract_from_image", _no_modelo)
    monkeypatch.setattr(ai_extractor, "extract_from_text", _no_modelo)


@pytest.fixture
def pdf_de_prueba(monkeypatch):
    """Fuerza la rama de PDF con texto, que es la que lee el comprobante."""
    monkeypatch.setattr(capture, "extract_pdf_text", lambda _b: COMPROBANTE)


def _pdf() -> bytes:
    return b"%PDF-1.7\n" + COMPROBANTE.encode()


async def _subir(client, company_id, *, nombre="comprobante.pdf", contenido=None, **campos):
    datos = {"company_id": str(company_id), **campos}
    return await client.post(
        f"{P}/expenses/upload-batch",
        data=datos,
        files={"files": (nombre, contenido if contenido is not None else _pdf(), "application/pdf")},
    )


async def _tickets(db_session, company_id):
    return (
        await db_session.execute(
            select(TicketModel).where(TicketModel.company_id == company_id)
        )
    ).scalars().all()


class TestLaPuerta:
    async def test_sin_token_es_401(
        self, async_client_sin_autenticar, test_company, sin_modelo, pdf_de_prueba
    ):
        """Sin token no se lee un comprobante.

        La autenticacion la pone el router en `api_router.py`, no el handler. Un
        test que llamara a la funcion directamente no veria esta defensa.
        """
        r = await _subir(async_client_sin_autenticar, test_company.id)
        assert r.status_code == 401

    async def test_company_inexistente_es_404(
        self, async_client, sin_modelo, pdf_de_prueba
    ):
        """Un `company_id` inventado no deja tickets huerfanos."""
        r = await _subir(async_client, uuid4())
        assert r.status_code == 404


class TestLaCapturaCompartida:
    async def test_sube_y_devuelve_datos_con_veredicto(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """La respuesta es la del escaner, no un schema estrecho aparte."""
        r = await _subir(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert len(cuerpo) == 1
        detalle = cuerpo[0]
        assert detalle["accion"] == "CREADO"
        assert detalle["ticket_id"] is not None

        # Los datos y el veredicto van JUNTOS. Si faltara `datos` el cliente
        # tendria que ir a buscar el ticket por otro endpoint, y si faltara
        # `extraction_status` creeria que el sistema responde por el total.
        datos = detalle["datos"]
        assert datos is not None
        assert Decimal(str(datos["total_amount"])) == Decimal("1100.00")
        assert datos["extraction_status"] is not None

        # El origen de la carga es BULK, no DIRECTORY: es la misma captura,
        # pero el reporte tiene que poder distinguir la subida explicita del
        # escaneo desatendido de la carpeta.
        tickets = await _tickets(db_session, test_company.id)
        assert len(tickets) == 1
        assert tickets[0].source_type == SourceType.BULK.value

    async def test_mismo_bytes_no_crea_dos_tickets(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """El dedup lo hace `scan_files`, no una lista en el router."""
        primera = await _subir(async_client, test_company.id)
        segunda = await _subir(async_client, test_company.id)

        assert primera.json()[0]["accion"] == "CREADO"
        # `relative_path` se deriva del hash, asi que la segunda encuentra la
        # misma fila y no hay que volver a leer el papel.
        assert segunda.json()[0]["accion"] == "SIN_CAMBIOS"
        assert len(await _tickets(db_session, test_company.id)) == 1

    async def test_mismo_nombre_bytes_distintos_se_relee(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """El nombre no puede ser la clave: el papel puede corregirse y volver."""
        otro = _pdf().replace(b"1100.00", b"1200.00")
        primera = await _subir(async_client, test_company.id, nombre="comprobante.pdf")
        segunda = await _subir(
            async_client, test_company.id, nombre="comprobante.pdf", contenido=otro
        )

        assert primera.json()[0]["accion"] == "CREADO"
        assert segunda.json()[0]["accion"] == "CREADO", segunda.text
        assert len(await _tickets(db_session, test_company.id)) == 2

    async def test_mismo_bytes_con_otro_nombre_es_duplicado(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """Cambiar el nombre del archivo no cambia los bytes del comprobante."""
        primera = await _subir(async_client, test_company.id, nombre="original.pdf")
        segunda = await _subir(async_client, test_company.id, nombre="copia.png")

        assert primera.json()[0]["accion"] == "CREADO"
        # Distinta extension -> distinto `relative_path`, pero el hash de los
        # bytes es el mismo y `_ticket_de_otro_archivo` defiende el ticket.
        segundo = segunda.json()[0]
        assert segundo["accion"] == "OMITIDO"
        assert segundo["status"] == "DUPLICADO"
        assert len(await _tickets(db_session, test_company.id)) == 1

    async def test_varios_archivos_se_procesan_en_secuencia(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """El lote no Comparte sesion entre corrutinas."""
        otro = b"%PDF-1.7\n" + COMPROBANTE.replace("1100.00", "1200.00").encode()
        r = await async_client.post(
            f"{P}/expenses/upload-batch",
            data={"company_id": str(test_company.id)},
            files=[
                ("files", ("a.pdf", _pdf(), "application/pdf")),
                ("files", ("b.pdf", otro, "application/pdf")),
            ],
        )

        assert r.status_code == 200, r.text
        assert [d["accion"] for d in r.json()] == ["CREADO", "CREADO"]
        assert len(await _tickets(db_session, test_company.id)) == 2
        filas = (
            await db_session.execute(
                select(func.count()).select_from(ScanFileModel)
            )
        ).scalar()
        assert filas == 2


class TestElPeriodoCerrado:
    @pytest.fixture
    async def marzo_cerrado(self, db_session, test_company):
        cierre = CierrePeriodoModel(
            company_id=test_company.id,
            periodo="2025-03",
            cerrado_por="contadora@empresa.mx",
        )
        db_session.add(cierre)
        await db_session.commit()
        return cierre

    async def test_sin_override_no_crea_ticket(
        self,
        async_client,
        db_session,
        test_company,
        marzo_cerrado,
        sin_modelo,
        pdf_de_prueba,
    ):
        """El rechazo va en la fila del archivo, no en un 500."""
        r = await _subir(async_client, test_company.id)

        assert r.status_code == 200, r.text
        detalle = r.json()[0]
        assert detalle["accion"] == "PERIODO_CERRADO"
        assert "2025-03" in detalle["detalle"]
        assert len(await _tickets(db_session, test_company.id)) == 0

        # El evento de auditoria usa una accion YA permitida por
        # `ck_scan_events_action`. El motivo no se pierde: va en `detail`.
        eventos = (
            await db_session.execute(select(ScanEventModel).where(ScanEventModel.action == "OMITIDO"))
        ).scalars().all()
        assert len(eventos) == 1
        assert "PERIODO_CERRADO" in eventos[0].detail

    async def test_override_sin_motivo_tampoco_pasa(
        self, async_client, db_session, test_company, marzo_cerrado, sin_modelo, pdf_de_prueba
    ):
        """Un `true` sin motivo no es una decision trazable."""
        r = await _subir(
            async_client, test_company.id, periodo_cerrado_override="true"
        )

        assert r.json()[0]["accion"] == "PERIODO_CERRADO"
        assert len(await _tickets(db_session, test_company.id)) == 0

    async def test_override_con_motivo_firma_con_el_token(
        self,
        async_client,
        db_session,
        test_company,
        marzo_cerrado,
        sin_modelo,
        pdf_de_prueba,
        usuario_de_prueba,
    ):
        """Quien firma sale del token, nunca del cuerpo (regla 23)."""
        r = await _subir(
            async_client,
            test_company.id,
            periodo_cerrado_override="true",
            periodo_cerrado_motivo="Factura tardia de marzo, revisada por la contadora",
        )

        assert r.json()[0]["accion"] == "CREADO", r.text
        tickets = await _tickets(db_session, test_company.id)
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.periodo_cerrado_override is True
        assert ticket.periodo_cerrado_motivo == (
            "Factura tardia de marzo, revisada por la contadora"
        )
        assert ticket.periodo_cerrado_autorizado_por == usuario_de_prueba.email
        assert ticket.periodo_cerrado_autorizado_at is not None

    async def test_despues_del_rechazo_se_puede_reintentar_con_override(
        self,
        async_client,
        db_session,
        test_company,
        marzo_cerrado,
        sin_modelo,
        pdf_de_prueba,
        usuario_de_prueba,
    ):
        """Un rechazo no deja la archivo atrapado en `SIN_CAMBIOS`.

        La fila de `scan_files` queda con el mismo hash y sin ticket. El atajo
        de "sin cambios" mira `ticket_id`, y por eso el reintento con override
        si vuelve a leer y crea el ticket.
        """
        rechazo = await _subir(async_client, test_company.id)
        assert rechazo.json()[0]["accion"] == "PERIODO_CERRADO"

        reintento = await _subir(
            async_client,
            test_company.id,
            periodo_cerrado_override="true",
            periodo_cerrado_motivo="Se autorizo despues de revisar el papel",
        )
        assert reintento.json()[0]["accion"] == "CREADO", reintento.text
        assert len(await _tickets(db_session, test_company.id)) == 1

    async def test_override_en_periodo_abierto_no_deja_firma(
        self, async_client, db_session, test_company, sin_modelo, pdf_de_prueba
    ):
        """No hay firma donde no hay cierre: no se inventa una autorizacion."""
        r = await _subir(
            async_client,
            test_company.id,
            periodo_cerrado_override="true",
            periodo_cerrado_motivo="no hace falta, pero lo marco",
        )

        assert r.json()[0]["accion"] == "CREADO", r.text
        ticket = (await _tickets(db_session, test_company.id))[0]
        assert ticket.periodo_cerrado_override is False
        assert ticket.periodo_cerrado_autorizado_por is None
