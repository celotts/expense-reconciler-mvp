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
    async def test_sin_proveedor_escala_a_la_ia_aunque_tenga_total(
        self, db_session, test_company
    ):
        """LA DEFENSA. Si `_es_extraccion_util` cambia el `or` por un `and`, muere.

        Es el unico caso donde las DOS mitades del "o" importan, y por eso
        necesita un test propio en vez de quedar dentro de "el PDF se leyo bien".

        Que falte el proveedor es motivo suficiente para escalar, y se ve con un
        total PERFECTO al lado: `1,100.00` esta perfectamentelido. Con `and`, esa
        lectura pasaria por buena y el ticket se guardaria con
        `provider_name="UNKNOWN_PROVIDER"` y el total puesto — que es un gasto sin
        emisor, que el gate mandaria a la cola pero sin motivo que lo explique,
        y que en el dashboard aparece como un gasto mas.

        La version con proveedor y sin total es la misma defensa por el otro
        lado, y la comprueba `test_sin_total_escala_aunque_tenga_proveedor`.
        """
        from fpdf import FPDF
        import io

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Courier", size=11)
        # Sin la linea del proveedor, pero con el total intacto.
        for linea in _texto_ticket().replace("Tiendas Ramirez SA de CV", "").strip().split("\n"):
            pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
        buffer = io.BytesIO()
        pdf.output(buffer)

        llamado = []

        async def extractor_que_no_esta(datos):
            from app.services.ai_extractor import ExtractedInvoice

            llamado.append(True)
            return ExtractedInvoice(
                provider_name="Tiendas Ramirez SA de CV",
                total=Decimal("1100.00"),
                confidence=0.93,
                raw_text="leido por el modelo",
            )

        extracted = await capture_ticket(
            buffer.getvalue(),
            "pdf",
            extract_from_text=extractor_que_no_esta,
            extract_from_image=extractor_que_no_esta,
        )

        assert llamado, (
            "sin proveedor NO se debe guardar la lectura de reglas: un total "
            "leido sin saber de quien es no identifica un gasto."
        )
        assert extracted.provider_name != UNKNOWN_PROVIDER
        assert extracted.confidence_source == ConfidenceSource.LLM

    @pytest.mark.asyncio
    async def test_sin_total_escala_aunque_tenga_proveedor(
        self, db_session, test_company
    ):
        """LA DEFENSA, por el otro lado del mismo `or`.

        Un proveedor sin total es el caso que mas se da en la practica: el
        membrete se lee bien y las cifras no. Y es el que mas caro sale si se
        deja pasar, porque "proveedor conocido, total 0" es un ticket que cuadra
        con la aritmetica (`0 + 0 == 0`) y entraria a la cola sin un solo check
        en rojo.
        """
        from fpdf import FPDF
        import io

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Courier", size=11)
        texto = (
            _texto_ticket()
            .replace("TOTAL 1,100.00", "TOTAL")
            .replace("SUBTOTAL 964.00", "")
            .replace("IVA (16%) 136.00", "")
        )
        for linea in texto.strip().split("\n"):
            pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
        buffer = io.BytesIO()
        pdf.output(buffer)

        llamado = []

        async def extractor_que_no_esta(datos):
            from app.services.ai_extractor import ExtractedInvoice

            llamado.append(True)
            return ExtractedInvoice(
                provider_name="Tiendas Ramirez SA de CV",
                total=Decimal("1100.00"),
                confidence=0.93,
                raw_text="leido por el modelo",
            )

        extracted = await capture_ticket(
            buffer.getvalue(),
            "pdf",
            extract_from_text=extractor_que_no_esta,
            extract_from_image=extractor_que_no_esta,
        )

        assert llamado, "un total de 0 no es un gasto: hay que pedirlo al modelo."
        assert extracted.total_amount > 0
        assert extracted.confidence_source == ConfidenceSource.LLM

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


class TestElMismoArchivoEnDosEmpresas:
    """El hash de contenido es único DENTRO de una empresa, no en todo el sistema.

    `source_hash` es el SHA-256 del archivo. No lleva empresa dentro, asi que el
    mismo comprobante da el mismo hash en cualquier empresa, y el mismo papel
    puede rightfulmente estar en dos cuentas distintas.

    Buscando solo por hash, la segunda empresa que lo subia se encontraba con
    el ticket de la primera y lo devolvia tal cual. Eso no era un duplicado: era
    una fuga. El gasto no se guardaba para la segunda empresa, y de paso se le
    mostraba un ticket ajeno.
    """

    @pytest.mark.asyncio
    async def test_cada_empresa_registra_su_propio_ticket(
        self, db_session, test_company
    ):
        from uuid import uuid4

        from app.models.company import CompanyModel

        otra = CompanyModel(
            name="Segunda Empresa", tax_id=f"SEG{uuid4().hex[:9].upper()}",
        )
        db_session.add(otra)
        await db_session.commit()
        await db_session.refresh(otra)

        contenido = _texto_ticket().encode()
        extracted = await capture_ticket(contenido, "text")

        primero = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )
        segundo = await _persistir(
            db_session, otra.id, extracted, contenido, "text",
        )

        # Antes: los dos eran el MISMO ticket, y el de la segunda empresa
        # apuntaba a la primera.
        assert primero.id != segundo.id
        assert segundo.company_id == otra.id

    @pytest.mark.asyncio
    async def test_dentro_de_la_misma_empresa_si_deduplica(
        self, db_session, test_company
    ):
        """El otro lado de la garantia: repetir NO crea un gasto repetido.

        Corregir la fuga no puede costar el otro comportamiento. El indice paso de
        `source_hash` a `(company_id, source_hash)`, y lo que protege ahora es
        que el mismo archivo no se registre dos veces en la misma cuenta.
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


class TestElTopeDeRawText:
    """`raw_text` no es un vertedero: va a una columna y se lista en la cola.

    Sin tope, un PDF de condiciones generales deja varios MB en una fila, y el
    sintoma no aparece al escribir sino al=listar cien tickets para revisar.
    Medido antes del arreglo: 3 MB de texto en una sola fila.
    """

    def test_un_texto_normal_no_se_toca(self):
        from app.services.parser_service import recortar_raw_text

        texto = "TOTAL 1,100.00"
        # Sin marca: un texto que no se recorta no puede decir que se recorta.
        # Un "truncado" aqui haria que la busqueda de documentos incompletos
        # contara todos.
        assert recortar_raw_text(texto) == texto

    def test_se_conserva_la_cabeza_y_la_cola(self):
        """Las dos puntas, y no solo la cabeza.

        En un comprobante el proveedor esta arriba y el total, el IVA y la fecha
        de emision casi siempre abajo, en el bloque de sumas. Un recorte por
        cabeza guardaria el nombre del comercio y tiraria las cifras, que es
        justo lo que el revisor necesita contrastar contra el papel.
        """
        from app.services.parser_service import (
            RAW_TEXT_MAX_CHARS, recortar_raw_text,
        )

        enorme = "PROVEEDOR DE EJEMPLO SA DE CV\n" + ("linea " * 40_000) + \
                  "\nTOTAL 1,100.00"

        recortado = recortar_raw_text(enorme)

        assert len(recortado) < len(enorme)
        assert "PROVEEDOR DE EJEMPLO" in recortado
        assert "TOTAL 1,100.00" in recortado
        assert len(recortado) <= RAW_TEXT_MAX_CHARS + 200

    def test_el_recorte_avisa_que_hubo_recorte(self):
        """Un recorte silencioso hace que el campo parezca completo.

        El revisor busca "TOTAL 1,100.00" en el `raw_text`, no lo encuentra, y
        anota que el total no aparece en el documento, cuando lo que paso es que
        se quedo fuera de la ventana. Sin la marca, el dato pareceria faltar.
        """
        from app.services.parser_service import recortar_raw_text

        recortado = recortar_raw_text("x" * 100_000)
        assert "omitidos" in recortado

    def test_el_tope_no_se_puede_saltarse_por_la_puerta_de_atras(self):
        """El recorte esta en el validador, no en quien arma el resultado.

        Mismo argumento que el de `app/core/subida.py`: un control en cada
        lugar que arma el texto se puede olvidar, y el que se olvide no falla.
        Aqui se comprueba que un `TicketExtractionResult` construido a mano, sin
        pasar por el parser, tambien queda recortado.
        """
        from app.services.parser_service import (
            RAW_TEXT_MAX_CHARS, TicketExtractionResult,
        )

        resultado = TicketExtractionResult(
            provider_name="X SA DE CV",
            total_amount=Decimal("100.00"),
            raw_text="y" * 500_000,
        )
        assert len(resultado.raw_text) <= RAW_TEXT_MAX_CHARS + 200

    @pytest.mark.asyncio
    async def test_lo_que_llega_a_la_base_ya_va_recortado(
        self, db_session, test_company
    ):
        from app.services.parser_service import RAW_TEXT_MAX_CHARS

        contenido = (
            b"TIENDAS RAMIREZ SA DE CV\n"
            b"RFC: TRAM910101XXX\n"
            b"TOTAL 1,100.00\n"
        ) + b"linea de relleno\n" * 60_000

        extracted = await capture_ticket(contenido, "text")
        ticket = await _persistir(
            db_session, test_company.id, extracted, contenido, "text",
        )

        assert len(ticket.raw_text or "") <= RAW_TEXT_MAX_CHARS + 200


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

        async def extractor_que_no_esta(datos):
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
        async def extractor_que_explota(datos):
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
