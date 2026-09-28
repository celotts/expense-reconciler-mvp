"""El tramo donde la captura se convierte en una fila de la base de datos.

Los tests de `test_capture.py` comprueban que se elige el escalon correcto. Los
de aqui comprueban lo que importa para el resto del sistema: que lo que se
guarda en la BD dice la verdad sobre como se leyo el documento.

Eso no es un detalle. `confidence_source` es la columna que hace posible medir
el 96% de exactitud que se acordo: si un comprobante leido con un regex queda
marcado como lectura de modelo, la medicion promedia la IA con un metodo que
casi nunca falla, el numero sale inflado, y la conclusion que se tome de el
(ch "¿hay que cambiar de modelo?") sale erronea.

Ademas se comprueba que un documento sin fecha no queda fechado como si fuera
un gasto de hoy. El cierre mensual es el producto que se va a reportar, y un
gasto de marzo contado en septiembre no es un error de redondeo: es una cifra
falsa.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.enums import UNKNOWN_PROVIDER, ConfidenceSource
from app.services.capture import capture_ticket
from app.services.parser_service import TicketExtractionResult


def _texto_ticket(**kw) -> str:
    base = (
        "Tiendas Ramirez SA de CV\n"
        "RFC: TRAM910101XXX\n"
        "FECHA EXPEDICION: 15/03/2025\n"
        "SUBTOTAL 964.00\n"
        "IVA (16%) 136.00\n"
        "TOTAL 1,100.00\n"
    )
    for viejo, nuevo in kw.items():
        base = base.replace(viejo.replace("_", " "), nuevo)
    return base


async def _persistir(db_session, company_id, extracted, content, file_type):
    """La misma funcion que usa /extract-and-create, sin el HTTP de en medio.

    El `source_type` se calcula con la misma regla del endpoint: si el tipo de
    archivo no existe en el enum, se guarda como `image`. No se copia la regla
    "a mano" porque entonces el test dejaria de comprobar lo que la API hace.
    """
    from app.api.tickets import _persist_extracted
    from app.core.enums import SourceType

    source_type = (
        SourceType(file_type)
        if file_type in {t.value for t in SourceType}
        else SourceType.IMAGE
    )
    return await _persist_extracted(
        db_session, company_id, extracted, content, source_type,
        source_file="comprobante",
    )


class TestLoQueSeGuardaDicenLaVerdad:
    """`confidence_source` no es metadato decorativo: es la base de la medicion."""

    @pytest.mark.asyncio
    async def test_un_pdf_impreso_no_se_guarda_como_lectura_de_modelo(
        self, db_session, test_company
    ):
        """El caso que justifico todo el trabajo.

        Un PDF impreso tiene el texto ahi. Se lee con reglas, sin modelo. Si la
        fila quedara como `llm`, no habria forma de distinguirlos, y la
        exactitud que se mida despues seria la del regex, no la de la IA.
        """
        from fpdf import FPDF
        import io

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Courier", size=11)
        for linea in _texto_ticket().strip().split("\n"):
            pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
        buffer = io.BytesIO()
        pdf.output(buffer)

        contenido = buffer.getvalue()
        # Sin extractores: la cascada tiene que resolverlo sola, con reglas.
        extracted = await capture_ticket(contenido, "pdf")

        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "pdf",
        )

        assert ticket.confidence_source == ConfidenceSource.PDF_TEXT.value
        assert ticket.extraction_status == "AUTO_APROBADO"
        assert ticket.provider_name == "Tiendas Ramirez SA de CV"
        assert ticket.expense_date == date(2025, 3, 15)

    @pytest.mark.asyncio
    async def test_texto_plano_se_guarda_como_reglas(self, db_session, test_company):
        """Un `.txt` se lee con reglas, aunque la columna de via diga `image`.

        No es ideal que se llame `image` a un texto plano: el enum `SourceType`
        no tiene un valor para eso y el endpoint cae a `image` para cualquier
        tipo que no reconozca. Es una deuda pequena, pero conviene que el test
        la deje escrita en vez de taparla.
        """
        contenido = _texto_ticket().encode()
        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        assert ticket.confidence_source == ConfidenceSource.RULES.value
        assert ticket.source_type == "image"

    @pytest.mark.asyncio
    async def test_la_confianza_guardada_es_la_de_la_evidencia_encontrada(
        self, db_session, test_company
    ):
        """La confianza no se inventa: sale de lo que el parser encontro.

        Con RFC, subtotal y fecha, la fila mas alta de la tabla. Es la misma
        que se ve en el servicio, sin que nada la cambie por el camino.
        """
        from app.services.capture import confianza_por_campos

        contenido = _texto_ticket().encode()
        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        assert float(ticket.confidence) == pytest.approx(
            confianza_por_campos(tiene_rfc=True, tiene_subtotal=True, tiene_fecha=True)
        )

    @pytest.mark.asyncio
    async def test_el_mismo_documento_se_puede_guardar_dos_veces_por_idempotencia(
        self, db_session, test_company
    ):
        """El hash de contenido evita el ticket duplicado en carga masiva.

        Con una carpeta de 500 archivos es facil que el mismo PDF llegue dos
        veces (un reintento, una copia, un drag-and-drop repetido). El segundo
        tiene que devolver el primero, no crear un gasto duplicado que nadie
        va a notar hasta el cierre.
        """
        contenido = _texto_ticket().encode()
        extracted = await capture_ticket(contenido, "text")

        primero = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        segundo = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        assert primero.id == segundo.id


class TestFechaInventadaEnLaFila:
    """La fecha es lo que decide a que mes pertenece un gasto.

    Este es el bug mas caro que se corrigio: `_parse_receipt_text` ponia
    `date.today()` cuando el documento no traia fecha. El ticket resultante era
    indistinguible de un gasto real de hoy, y el cierre mensual lo contaba en el
    mes equivocado.
    """

    @pytest.mark.asyncio
    async def test_sin_fecha_el_ticket_queda_en_la_cola_con_el_motivo(
        self, db_session, test_company
    ):
        contenido = b"TIENDAS RAMIREZ SA DE CV\nRFC: TRAM910101XXX\nTOTAL: 1,100.00\n"
        extracted = await capture_ticket(contenido, "text")
        assert extracted.expense_date is None

        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )

        # El motivo tiene que estar a la vista: sin esto, la fecha de hoy que
        # se guarda pasa por real.
        assert "date_missing" in (ticket.validation_errors or "")
        assert ticket.extraction_status == "PENDIENTE"
        assert ticket.is_open_for_review

    @pytest.mark.asyncio
    async def test_la_fecha_guardada_es_la_de_hoy_pero_el_ticket_avisa(
        self, db_session, test_company
    ):
        """La columna es NOT NULL, asi que se guarda algo. Lo que importa es que
        se sepa que no es real, y eso lo dice `date_missing` en la cola.

        Se compara contra la fecha LOCAL, no contra la de UTC. Un servidor en
        UTC-6 que corre por la tarde ya tiene manana en UTC, asi que fechar con
        UTC daria un dia de diferencia: el mismo error que se corrige, con un
        dia de desfase.
        """
        contenido = b"TIENDAS RAMIREZ SA DE CV\nRFC: TRAM910101XXX\nTOTAL: 1,100.00\n"
        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        assert ticket.expense_date == date.today()

    @pytest.mark.asyncio
    async def test_la_confianza_baja_sin_fecha(
        self, db_session, test_company
    ):
        """Un documento sin fecha no puede estar en la confianza mas alta.

        Sin fecha, el ticket podria ser de cualquier mes, y la confianza dice
        "esto se puede dar por bueno sin que nadie lo mire". Con la fecha
        ausente, eso no se puede afirmar.
        """
        from app.services.capture import confianza_por_campos

        contenido = b"TIENDAS RAMIREZ SA DE CV\nRFC: TRAM910101XXX\nTOTAL: 1,100.00\n"
        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        assert float(ticket.confidence) == pytest.approx(
            confianza_por_campos(tiene_rfc=True, tiene_subtotal=False, tiene_fecha=False)
        )


class TestLoQueLaIaNoPudoLeer:
    """Un documento que el sistema no pudo leer tiene que seguir existiendo.

    La tentacion cuando algo falla es devolver un error. Es la peor opcion: el
    archivo se pierde, nadie sabe que hubo que subirlo, y no hay nada que
    revisar. La cola es el destino de un fallo de lectura, no un error fatal.
    """

    @pytest.mark.asyncio
    async def test_ia_apagada_no_deja_una_fila_con_proveedor_falso(
        self, db_session, test_company
    ):
        """`AI_DISABLED` no puede llegar como nombre de comercio.

        Es una cadena no vacia, asi que el check de proveedor del gate la
        aceptaria, y la cola la mostraria como si fuera un negocio. Ademas el
        motivo que recibe el revisor seria "proveedor falta" cuando la verdad es
        "no hay extractor encendido": dos causas que piden arreglos opuestos.
        """
        from app.services.ai_extractor import ExtractedInvoice

        async def extractor_que_no_esta(datos, mime_type="image/png"):
            return ExtractedInvoice(
                provider_name="AI_DISABLED", total=Decimal("0"), raw_text="",
            )

        contenido = b"\xff\xd8\xfffoto-de-un-ticket"
        extracted = await capture_ticket(
            contenido, "image", extract_from_image=extractor_que_no_esta,
        )
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "image",
        )

        assert ticket.provider_name == UNKNOWN_PROVIDER
        assert ticket.provider_name != "AI_DISABLED"
        assert ticket.extraction_status == "PENDIENTE"
        # El motivo de la cola tiene que nombrar la causa real.
        assert "extractor de IA esta apagado" in (ticket.raw_text or "")

    @pytest.mark.asyncio
    async def test_una_excepcion_del_modelo_no_borra_el_documento(
        self, db_session, test_company
    ):
        async def extractor_que_explota(datos, mime_type="image/png"):
            raise ConnectionResetError("connection reset by peer")

        contenido = b"\xff\xd8\xfffoto-de-otro-ticket"
        extracted = await capture_ticket(
            contenido, "image", extract_from_image=extractor_que_explota,
        )
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "image",
        )

        assert ticket.extraction_status == "PENDIENTE"
        assert ticket.is_open_for_review
        # El error concreto llega a la cola, no solo al log del servidor.
        assert "connection reset by peer" in (ticket.raw_text or "")

    @pytest.mark.asyncio
    async def test_un_tipo_de_archivo_desconocido_no_crea_ruido(
        self, db_session, test_company
    ):
        """Un `file_type` que no existe es un error de cliente, no un ticket.

        Meterlo en la cola esconde un bug de programacion detras de "documento
        ilegible", y llena la cola de entradas que nadie va a poder resolver.
        """
        from app.services.capture import ExtractionUnavailable

        with pytest.raises(ExtractionUnavailable):
            await capture_ticket(b"contenido", "xlsx")


class TestVerificacionAritmeticaEnLaRutaBarata:
    """El check mas fuerte es el que no depende de nada externo."""

    @pytest.mark.asyncio
    async def test_cuentas_que_no_cuadran_no_se_auto_aprueban(
        self, db_session, test_company
    ):
        """PDF con texto, reglas limpias, confianza alta, cuentas rotas.

        Este es el escenario completo que el gate tiene que atrapar. El
        documento es legible, el parser hizo bien su parte, la confianza por
        evidencia es alta... y el total no cuadra. Si esto se auto-aprueba, el
        error entra a conciliacion y aparece en el cierre.
        """
        contenido = (
            b"Tiendas Ramirez SA de CV\n"
            b"RFC: TRAM910101XXX\n"
            b"FECHA EXPEDICION: 15/03/2025\n"
            b"SUBTOTAL 964.00\n"
            b"IVA (16%) 136.00\n"
            b"TOTAL 1,160.00\n"  # 964 + 136 = 1100, no 1160
        )
        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )

        assert "subtotal_plus_tax_mismatch" in (ticket.validation_errors or "")
        assert ticket.extraction_status != "AUTO_APROBADO"
        assert ticket.is_open_for_review
