"""El `file_type` declarado por el cliente no elige la ruta de lectura.

Por que estos tests van por HTTP y no llaman a la funcion
---------------------------------------------------------

Porque una defensa puede estar bien escrita y no estar conectada. Ya paso: al
aplicar el cambio, mutar la linea del endpoint de
`tipo_real = _tipo_real_del_archivo(...)` a `tipo_real = file_type` dejo TODOS
los tests en verde, porque ningun test miraba el endpoint. Estos lo llaman de
verdad, con el mismo `async_client` que el resto de la suite.

Y con el mismo motivo no se reimplementa la regla dentro del test: una copia a
mano de la logica del endpoint deja de comprobar lo que la API hace en cuanto
la API cambia. `tests/integration/test_capture_pipeline.py` ya lo advierte en su
propio docstring, y este archivo lo respeta.

Que se midio antes de arreglar
------------------------------

El mismo `factura_papeleria_central.pdf` subido de tres formas:

    file_type=pdf   ->  pdf_text  ->  0.97  ->  PAPELERIA Y SUMINISTROS  ->  4094.80
    file_type=image ->  llm       ->  None  ->  Unknown Provider        ->  0.00

La segunda fila es la falla: la regla 3 de `AGENTS.md` ("un PDF con texto no
toca el modelo") la salta quien llama, y el ticket entra con
`confidence_source=llm`, que es el origen con el que agrupa el reporte de
exactitud. Una lectura que no fue lectura, falseando la evidencia del SLO.

Y no es un 4xx
--------------

El documento es valido; lo que estaba mal era la etiqueta. Un contador no puede
perder su comprobante porque su cliente mando `image` en vez de `pdf`. Se
procesa igual, con el formato deducido, y la correccion queda en el log.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import AsyncClient

RAIZ = Path(__file__).resolve().parents[2]


def _pdf_de_prueba() -> bytes:
    """Un PDF real del repo, con texto suficiente para que las reglas trabajen.

    Se usa un archivo de verdad y no un stub porque lo que se comprueba es
    justamente el camino de `pdf_text`: con un PDF sintetico minimo no habria
    texto que parsear y el test pasaria por el motivo equivocado.
    """
    return (RAIZ / "test-files" / "factura_papeleria_central.pdf").read_bytes()


def _png_real() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (210, 210, 205)).save(buf, format="PNG")
    return buf.getvalue()


async def _subir(client: AsyncClient, contenido: bytes, nombre: str, file_type: str):
    return await client.post(
        "/api/v1/tickets/extract",
        files={"file": (nombre, contenido, "application/octet-stream")},
        data={"file_type": file_type},
    )


class TestLaEtiquetaDelClienteNoCambiaElResultado:
    """La propiedad, dicha sin depender de una sola combinacion."""

    @pytest.mark.parametrize("declarado", ["pdf", "image", "text", "PDF", "cualquier-cosa"])
    @pytest.mark.asyncio
    async def test_un_pdf_siempre_acaba_leido_como_pdf(self, async_client, declarado):
        r = await _subir(async_client, _pdf_de_prueba(), "factura.pdf", declarado)

        assert r.status_code == 200, f"file_type={declarado!r} devolvio {r.status_code}"
        cuerpo = r.json()
        assert cuerpo["confidence_source"] == "pdf_text", (
            f"Con file_type={declarado!r} salio origen={cuerpo['confidence_source']!r}. "
            "Los bytes son un PDF con texto: tiene que pasar por las reglas, que "
            "es el escalon que el cliente no puede elegir saltarse."
        )
        assert cuerpo["total_amount"] not in (0, 0.0, "0.00"), "El total se perdio"

    @pytest.mark.asyncio
    async def test_una_foto_no_entra_al_parser_de_pdf(self, async_client):
        """El reves. Mandar una foto como `pdf` no puede devolver un total inventado."""
        r = await _subir(async_client, _png_real(), "foto.png", "pdf")

        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["confidence_source"] != "pdf_text", (
            "Una imagen entro por el camino de PDF. Las reglas no la entenderian "
            "y el total saldría de cualquier parte."
        )

    @pytest.mark.asyncio
    async def test_un_documento_valido_no_se_pierde_por_una_etiqueta_mala(
        self, async_client
    ):
        """El peor escenario posible seria un 4xx: el contador pierde el papel.

        Aqui la etiqueta esta mal a proposito y el archivo tiene que salir igual
        de bien que con la etiqueta correcta.
        """
        mal = await _subir(async_client, _pdf_de_prueba(), "factura.pdf", "image")
        bien = await _subir(async_client, _pdf_de_prueba(), "factura.pdf", "pdf")

        assert mal.status_code == bien.status_code == 200
        assert mal.json()["total_amount"] == bien.json()["total_amount"]


class TestLoQueSeGuardaDiceLaVerdad:
    """El `source_type` persistido tampoco puede venir de la etiqueta."""

    @pytest.mark.asyncio
    async def test_el_origen_guardado_no_lo_elige_el_cliente(
        self, async_client, test_company, db_session
    ):
        """Dos subidas del MISMO archivo a empresas DISTINTAS.

        La distincia no es cosmetica: el idempotente es
        `(company_id, source_hash)` (`AGENTS.md` regla 4), asi que el mismo
        archivo a la misma empresa devuelve el ticket ya existente y las dos
        comparaciones darian el mismo valor sin mirar nada. Con dos empresas
        cada POST crea el suyo, y la comparacion si compara.
        """
        from app.models.company import CompanyModel

        otra = CompanyModel(name="Otra empresa", tax_id="OPE999999999")
        db_session.add(otra)
        await db_session.commit()
        await db_session.refresh(otra)

        archivos = {"file": ("factura.pdf", _pdf_de_prueba(), "application/pdf")}

        mal = await async_client.post(
            "/api/v1/tickets/extract-and-create",
            files=archivos,
            data={"company_id": str(test_company.id), "file_type": "image"},
        )
        bien = await async_client.post(
            "/api/v1/tickets/extract-and-create",
            files=archivos,
            data={"company_id": str(otra.id), "file_type": "pdf"},
        )

        assert mal.status_code == 201, mal.text
        assert bien.status_code == 201, bien.text
        assert mal.json()["id"] != bien.json()["id"], "No se crearon dos tickets"
        assert mal.json()["source_type"] == bien.json()["source_type"], (
            f"source_type differio: {mal.json()['source_type']!r} vs "
            f"{bien.json()['source_type']!r}. El origen lo dicen los bytes."
        )

    @pytest.mark.asyncio
    async def test_el_pdf_no_se_guarda_como_una_lectura_de_modelo(
        self, async_client, test_company
    ):
        """La consecuencia que importa: contaminar la medicion de origen.

        El reporte de exactitud agrupa por `confidence_source`
        (`accuracy_service.py:346-357`). Un ticket que entro por vision y salio
        sin datos cuenta como lectura del modelo, y eso falsea el SLO.
        """
        r = await async_client.post(
            "/api/v1/tickets/extract-and-create",
            files={"file": ("factura.pdf", _pdf_de_prueba(), "application/pdf")},
            data={"company_id": str(test_company.id), "file_type": "image"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["confidence_source"] == "pdf_text", (
            "Un PDF con texto se guardo como lectura de modelo. El muestreo del "
            "5% y el SLO se calculan sobre origen, asi que esto no es un detalle "
            "de etiqueta: es evidencia falsa."
        )


class TestLaCorreccionQuedaRastro:
    def test_el_motivo_nombra_las_dos_etiquetas(self):
        from app.core.archivo_real import resolver_tipo

        _, motivo = resolver_tipo(_pdf_de_prueba(), "image")
        assert motivo is not None, "Sin motivo, la correccion es invisible"
        assert "image" in motivo and "pdf" in motivo

    @pytest.mark.asyncio
    async def test_el_endpoint_avisa_al_log(self, async_client, caplog):
        """El motivo tiene que llegar al log, no quedarse en un return.

        Sin esto, un cliente que etiqueta mal sus subidas deja de notarlo y el
        problema se descubre cuando el reporte de exactitud ya esta manchado.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="app.api.tickets"):
            await _subir(async_client, _pdf_de_prueba(), "factura.pdf", "image")

        # `getMessage()` y no `r.message`: el record guarda el formato y los
        # argumentos por separado, y con `%` lazy aplicarlos a mano revienta.
        mensajes = [r.getMessage() for r in caplog.records]
        assert any("file_type" in m for m in mensajes), (
            "La correccion de tipo no se registro. Sin el log, quien etiqueta "
            "mal sus archivos no se entera.\n"
            f"Registrado: {mensajes}"
        )


class TestElSniffingNoTocaLosBytes:
    def test_el_archivo_original_no_se_altera(self):
        """De solo lectura: lo que va a `BYTEA` es exactamente lo que entro.

        Si el sniffing reescribiera el buffer, el comprobante guardado dejaria
        de ser el original y toda la cadena de custodia — que es la promesa del
        producto — seria falsa.
        """
        from app.core.archivo_real import resolver_tipo

        original = _pdf_de_prueba()
        copia = bytearray(original)
        resolver_tipo(copia, "image")
        assert bytes(copia) == original, "El contenido se modifico"

    @pytest.mark.asyncio
    async def test_lo_guardado_es_byte_a_byte_el_original(
        self, async_client, test_company
    ):
        """Cerrado por HTTP: el PDF que sale de la API es el que entro."""
        original = _pdf_de_prueba()
        creado = await async_client.post(
            "/api/v1/tickets/extract-and-create",
            files={"file": ("factura.pdf", original, "application/pdf")},
            data={"company_id": str(test_company.id), "file_type": "image"},
        )
        assert creado.status_code == 201, creado.text

        descarga = await async_client.get(
            f"/api/v1/tickets/{creado.json()['id']}/documento"
        )
        assert descarga.status_code == 200, descarga.text
        assert descarga.content == original, "El comprobante guardado no es el original"
