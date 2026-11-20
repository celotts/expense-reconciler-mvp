"""Digitalizar un DIRECTORIO, probado por HTTP.

La diferencia frente a `upload-batch` no es el lector: los dos llaman a
`procesar_archivo`. La diferencia es la PUERTA.

* El navegador no puede leer el disco del servidor, asi que aqui el operador
  escribe una ruta. Sin lista blanca, ese campo seria lectura arbitraria:
  `POST /expenses/digitalizar-directorio {"carpeta": "/etc"}` subiria el
  `/etc/passwd` como si fuera un comprobante. Por eso la ruta se comprueba
  DESPUES de `resolve()` y solo se aceptan raices que el servidor declaró.
* Un directorio mezclado trae archivos que NO se pueden leer. Callarlos seria
  decir "todo salió bien" mientras el unico `.docx` de la carpeta sigue ahi
  esperando a que alguien lo mire. Por eso van en `archivos_no_permitidos`
  CON SU NOMBRE, separados de los que si se leyeron.
* El dedup sigue siendo por SHA-256 de los bytes, no por nombre de archivo:
  dos carpetas con `factura.pdf` distinto no pueden colisionar en el ledger.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.enums import SourceType
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.scan_file import ScanEventModel
from app.models.ticket import TicketModel
from app.services import capture
from app.services.ai_extractor import ai_extractor
from app.services.digitalizacion_directorio import EXTENSIONES_PERMITIDAS

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
    """Ni OCR ni modelo: la suite no depende de Tesseract ni de la red."""

    def _no_ocr(_datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado en la prueba")

    async def _no_modelo(*_a, **_k):
        raise RuntimeError("el modelo esta apagado en la prueba")

    monkeypatch.setattr(capture, "_ocr_por_defecto", _no_ocr)
    monkeypatch.setattr(ai_extractor, "extract_from_image", _no_modelo)
    monkeypatch.setattr(ai_extractor, "extract_from_text", _no_modelo)


@pytest.fixture
def lector_de_pdf(monkeypatch):
    """Los bytes deciden el lector; aqui los de un PDF traen este texto."""
    monkeypatch.setattr(capture, "extract_pdf_text", lambda _b: COMPROBANTE)


@pytest.fixture
def carpetas(monkeypatch, tmp_path):
    """La de entrada y una extra, las dos dentro de la lista blanca."""
    entrada = tmp_path / "entrada"
    extra = tmp_path / "otra_carpeta"
    entrada.mkdir()
    extra.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(entrada))
    monkeypatch.setattr(settings, "TICKETS_SCAN_RUTAS_PERMITIDAS", str(extra))
    monkeypatch.setattr(settings, "TICKETS_SCAN_RECURSIVO", True)
    return entrada, extra


def _pdf(total="1100.00") -> bytes:
    return b"%PDF-1.7\n" + COMPROBANTE.replace("1100.00", total).encode()


def _xml() -> bytes:
    return b"<?xml version='1.0'?>\n<Comprobante>" + COMPROBANTE.encode() + b"</Comprobante>"


async def _digitalizar(client, company_id, **campos):
    return await client.post(
        f"{P}/expenses/digitalizar-directorio",
        data={"company_id": str(company_id), **campos},
    )


async def _tickets(db_session, company_id):
    return (
        await db_session.execute(
            select(TicketModel).where(TicketModel.company_id == company_id)
        )
    ).scalars().all()


class TestLaPuertaDeLaRuta:
    async def test_sin_token_es_401(
        self, async_client_sin_autenticar, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """La autenticacion la pone el router, no el handler."""
        r = await _digitalizar(async_client_sin_autenticar, test_company.id)
        assert r.status_code == 401

    async def test_ruta_vacia_usa_la_configurada(
        self, async_client, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """Por omision se lee TICKETS_INPUT_DIR, sin escribir nada en el form."""
        entrada, _ = carpetas
        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["carpeta"] == str(entrada)
        assert cuerpo["archivos"] == []
        assert cuerpo["archivos_no_permitidos"] == []
        assert ".pdf" in cuerpo["extensiones_permitidas"]
        assert ".jpg" in cuerpo["extensiones_permitidas"]
        assert ".xml" in cuerpo["extensiones_permitidas"]

    async def test_ruta_fuera_de_la_lista_blanca_es_400(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """`/etc` no es un comprobante, aunque quien pida tenga sesion."""
        r = await _digitalizar(async_client, test_company.id, carpeta="/etc")

        assert r.status_code == 400, r.text
        assert "no esta permitida" in r.json()["detail"]
        assert len(await _tickets(db_session, test_company.id)) == 0

    async def test_subdirectorio_de_la_raiz_si_entra(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """La lista blanca defiende el PERIMETRO, no prohibe ordenar en carpetas."""
        entrada, _ = carpetas
        sub = entrada / "2025" / "marzo"
        sub.mkdir(parents=True)
        (sub / "factura.pdf").write_bytes(_pdf())

        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["nombres"] == ["2025/marzo/factura.pdf"]
        assert len(await _tickets(db_session, test_company.id)) == 1

    async def test_otra_raiz_declarada_si_entra(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """Escribir una ruta permitida es el caso que el campo quiere habilitar."""
        _, extra = carpetas
        (extra / "factura.pdf").write_bytes(_pdf())

        r = await _digitalizar(async_client, test_company.id, carpeta=str(extra))

        assert r.status_code == 200, r.text
        assert r.json()["carpeta"] == str(extra)
        tickets = await _tickets(db_session, test_company.id)
        assert len(tickets) == 1
        assert tickets[0].source_type == SourceType.BULK.value


class TestElDedupPorBytes:
    async def test_mismo_nombre_en_dos_carpetas_no_colisiona(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """La clave es el SHA-256, no `factura.pdf`.

        Dos personas guardan su comprobante con el mismo nombre en carpetas
        distintas. Si la clave de `scan_files` fuera el nombre, la segunda
        corrida diria "sin cambios" y el gasto de la segunda empresa —o del
        mismo mes— no se contaria nunca.
        """
        entrada, extra = carpetas
        (entrada / "factura.pdf").write_bytes(_pdf("1100.00"))
        (extra / "factura.pdf").write_bytes(_pdf("1200.00"))

        primera = await _digitalizar(async_client, test_company.id)
        segunda = await _digitalizar(async_client, test_company.id, carpeta=str(extra))

        assert primera.json()["archivos"][0]["accion"] == "CREADO"
        assert segunda.json()["archivos"][0]["accion"] == "CREADO", segunda.text
        assert len(await _tickets(db_session, test_company.id)) == 2


class TestArchivosMezclados:
    async def test_mezcla_pdf_jpg_y_xml_y_nombra_los_rechazados(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """El usuario pide imagenes y PDF; XML entra porque ya se sabe leer."""
        entrada, _ = carpetas
        (entrada / "a_factura.pdf").write_bytes(_pdf("1100.00"))
        # El detector decide por los BYTES, no por el nombre: este `.jpg` trae
        # bytes de PDF y por eso lo lee el mismo lector de texto. Es exactamente
        # la razon por la que el `accept` del navegador no decide nada.
        (entrada / "b_foto.jpg").write_bytes(_pdf("1200.00"))
        (entrada / "c_cfdi.xml").write_bytes(_xml())
        (entrada / "d_notas.docx").write_bytes(b"no soy un comprobante")

        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["nombres"] == ["a_factura.pdf", "b_foto.jpg", "c_cfdi.xml"]
        # `origen` dice QUIEN LEYO; `formatos` dice QUE SON LOS BYTES. El
        # `.jpg` con bytes de PDF es la prueba de que no son lo mismo, y por eso
        # van en dos listas paralelas en vez de un solo campo.
        assert cuerpo["formatos"] == ["pdf", "pdf", "xml"]
        assert [a["origen"] for a in cuerpo["archivos"]] == [
            "pdf_text", "pdf_text", "xml"
        ]
        assert len(cuerpo["archivos"]) == 3

        rechazados = cuerpo["archivos_no_permitidos"]
        assert len(rechazados) == 1
        assert rechazados[0]["nombre"] == "d_notas.docx"
        assert rechazados[0]["extension"] == ".docx"
        assert "no permitida" in rechazados[0]["motivo"]

        # Los tres leidos traen los datos JUNTOS a su veredicto, no un
        # `relative_path` que resulte ser un hash ininteligible.
        for detalle in cuerpo["archivos"]:
            assert detalle["datos"] is not None
            assert detalle["datos"]["extraction_status"] is not None

        assert len(await _tickets(db_session, test_company.id)) == 3

    async def test_rechazados_siguen_apareciendo_en_la_segunda_corrida(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """El dedup calla a los ya leidos, NO a los que nadie ha mirado."""
        entrada, _ = carpetas
        (entrada / "factura.pdf").write_bytes(_pdf())
        (entrada / "pendiente.docx").write_bytes(b"sin lector")

        primera = (await _digitalizar(async_client, test_company.id)).json()
        segunda = (await _digitalizar(async_client, test_company.id)).json()

        assert primera["archivos"][0]["accion"] == "CREADO"
        assert segunda["archivos"][0]["accion"] == "SIN_CAMBIOS"
        assert [x["nombre"] for x in segunda["archivos_no_permitidos"]] == ["pendiente.docx"]
        assert len(await _tickets(db_session, test_company.id)) == 1

    async def test_el_tope_no_callos_rechazados(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf, monkeypatch
    ):
        """Archivos que quedaron fuera por tope se cuentan, no se silencian."""
        entrada, _ = carpetas
        (entrada / "a.pdf").write_bytes(_pdf("1100.00"))
        (entrada / "b.pdf").write_bytes(_pdf("1200.00"))
        (entrada / "c.docx").write_bytes(b"sin lector")
        monkeypatch.setattr(settings, "TICKETS_SCAN_MAX_ARCHIVOS", 1)

        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert len(cuerpo["archivos"]) == 1
        assert cuerpo["archivos_omitidos_por_tope"] == 1
        assert [x["nombre"] for x in cuerpo["archivos_no_permitidos"]] == ["c.docx"]

    async def test_enlace_simbolico_se_reporta_y_no_se_sigue(
        self, async_client, db_session, carpetas, test_company, sin_modelo, lector_de_pdf
    ):
        """Un symlink es la forma mas corta de leer fuera de la raiz permitida."""
        entrada, extra = carpetas
        fuera = extra / "afuera.pdf"
        fuera.write_bytes(_pdf())
        (entrada / "enlace.pdf").symlink_to(fuera)

        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["archivos"] == []
        rechazados = cuerpo["archivos_no_permitidos"]
        assert len(rechazados) == 1
        assert rechazados[0]["nombre"] == "enlace.pdf"
        assert "simbolico" in rechazados[0]["motivo"]


class TestElPeriodoCerradoEnElDirectorio:
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
        async_client, db_session, carpetas, test_company, marzo_cerrado,
        sin_modelo, lector_de_pdf,
    ):
        """La misma defensa que en `upload-batch`, no una segunda puerta."""
        entrada, _ = carpetas
        (entrada / "factura.pdf").write_bytes(_pdf())

        r = await _digitalizar(async_client, test_company.id)

        assert r.status_code == 200, r.text
        detalle = r.json()["archivos"][0]
        assert detalle["accion"] == "PERIODO_CERRADO"
        assert "2025-03" in detalle["detalle"]
        assert len(await _tickets(db_session, test_company.id)) == 0

        eventos = (
            await db_session.execute(select(ScanEventModel).where(ScanEventModel.action == "OMITIDO"))
        ).scalars().all()
        assert len(eventos) == 1
        assert "PERIODO_CERRADO" in eventos[0].detail

    async def test_override_con_motivo_firma_con_el_token(
        self,
        async_client, db_session, carpetas, test_company, marzo_cerrado,
        sin_modelo, lector_de_pdf, usuario_de_prueba,
    ):
        """Quien firma sale del token, nunca del formulario (regla 23)."""
        entrada, _ = carpetas
        (entrada / "factura.pdf").write_bytes(_pdf())

        r = await _digitalizar(
            async_client,
            test_company.id,
            periodo_cerrado_override="true",
            periodo_cerrado_motivo="Factura tardia de marzo, revisada por la contadora",
        )

        assert r.status_code == 200, r.text
        assert r.json()["archivos"][0]["accion"] == "CREADO"
        ticket = (await _tickets(db_session, test_company.id))[0]
        assert ticket.periodo_cerrado_override is True
        assert ticket.periodo_cerrado_autorizado_por == usuario_de_prueba.email
        assert ticket.periodo_cerrado_motivo == (
            "Factura tardia de marzo, revisada por la contadora"
        )

    async def test_company_inexistente_es_404(
        self, async_client, carpetas, sin_modelo, lector_de_pdf
    ):
        r = await _digitalizar(async_client, uuid4())
        assert r.status_code == 404
