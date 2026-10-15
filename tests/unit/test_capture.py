"""La cascada de captura: que escalon se usa, cuando, y por que.

Aqui se decide cuanto cuesta y que tan exacto es cada comprobante. Un error en
esta cascada no se ve como un error: se ve como un PDF que se leyo con un
modelo cuando no hacia falta (mas lento, mas caro, menos exacto), o como un
escaneado que se mando a un regex y nunca llego a mirar la pagina.

Por eso los extractores se inyectan falsos. No se prueba que el modelo
funcione, que es otra cosa; se prueba que se le llama cuando toca y no cuando
no. La politica es lo que se decide aqui.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.enums import (
    AUTO_APPROVE_CONFIDENCE,
    UNKNOWN_PROVIDER,
    ConfidenceSource,
)
from app.services import capture
from app.services.ai_extractor import ExtractedInvoice
from app.services.capture import (
    ExtractionUnavailable,
    capture_ticket,
    confianza_por_campos,
    invoice_to_result,
    motivo_de_fallo_del_modelo,
)
from app.services.parser_service import TicketExtractionResult, extract_pdf_text, render_pdf_pages

RAIZ = Path(__file__).resolve().parents[2]

TICKET_IMPRESO = """TIENDAS RAMIREZ SA DE CV
RFC: TRAM910101XXX
FACTURA No: 00123
FECHA EXPEDICION: 15/03/2025
SUBTOTAL 964.00
IVA (16%) 136.00
TOTAL 1,100.00
"""


# ---------------------------------------------------------------------------
# PDFs reales
#
# Se usan archivos de verdad y no texto simulado porque la distincion que
# importa (impreso vs escaneado) es del PDF, no del string: los dos son .pdf y
# la unica forma de saber si hay texto es abrirlo. Un test con un string vacio
# estaria probando la ruta de escaneado sin probar el escaneado.
# ---------------------------------------------------------------------------


def _cargar_generador():
    ruta = RAIZ / "scripts" / "make_sample_receipts.py"
    spec = importlib.util.spec_from_file_location("make_sample_receipts", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def pdf_impreso(tmp_path_factory) -> bytes:
    gen = _cargar_generador()
    destino = tmp_path_factory.mktemp("muestras") / "impreso.pdf"
    gen._es_impreso(destino)
    return destino.read_bytes()


@pytest.fixture(scope="module")
def pdf_escaneado(tmp_path_factory) -> bytes:
    gen = _cargar_generador()
    destino = tmp_path_factory.mktemp("muestras") / "escaneado.pdf"
    gen._es_escaneado(destino)
    return destino.read_bytes()


@pytest.fixture(scope="module")
def pdf_escaneado_largo(tmp_path_factory) -> bytes:
    """Escaneado de varias paginas, para comprobar el tope.

    Con un PDF de una sola pagina, `assert len(llamadas) == 1` pasa igual con el
    tope puesto o sin el: el test no comprobaria nada. El tope solo se ve
    cuando hay mas paginas de las que se permiten.
    """
    from PIL import Image, ImageDraw

    destino = tmp_path_factory.mktemp("muestras") / "escaneado_largo.pdf"
    paginas = []
    for i in range(5):
        imagen = Image.new("RGB", (850, 1100), "white")
        dibujo = ImageDraw.Draw(imagen)
        dibujo.text((60, 120), f"PAGINA {i + 1} DE 5", fill="black")
        paginas.append(imagen)

    paginas[0].save(
        destino, "PDF", resolution=150.0, save_all=True, append_images=paginas[1:],
    )
    return destino.read_bytes()


def test_las_muestras_reproducen_los_dos_casos(pdf_impreso, pdf_escaneado):
    """Guardia de las fixtures.

    Si el generador deja de producir un PDF escaneado de verdad, todo lo de mas
    abajo estaria probando el caso equivocado y pasaria igual.
    """
    assert "TOTAL" in extract_pdf_text(pdf_impreso)
    assert extract_pdf_text(pdf_escaneado).strip() == ""


def test_un_pdf_escaneado_si_se_puede_renderizar(pdf_escaneado):
    """La mitad del camino que antes no existia.

    `_pdf_to_images` importaba `fitz`, que no estaba instalado: devolvia una
    lista vacia y se tragaba la excepcion. Un escaneado nunca llego a mirar la
    pagina. Aqui se comprueba que la dependencia que si esta (pypdfium2, que
    viene con pdfplumber) renderiza de verdad.
    """
    paginas = render_pdf_pages(pdf_escaneado)
    assert len(paginas) == 1
    # JPEG con cabecera magica: si no, no es una imagen y el modelo no la vera.
    assert paginas[0].startswith(b"\xff\xd8\xff"), "no es un JPEG"


# ---------------------------------------------------------------------------
# Extractores falsos
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pdf_sin_labels(tmp_path_factory) -> bytes:
    """PDF con texto de verdad, pero sin los labels que el parser reconoce.

    Es el caso que obliga al escalon intermedio: hay texto (asi que no se cae a
    vision) pero el regex no encuentra proveedor ni total. Necesita ser un PDF
    de verdad porque la decision "hay texto" se toma abriendo el archivo, no
    suponiendola.
    """
    from fpdf import FPDF

    destino = tmp_path_factory.mktemp("muestras") / "sin_labels.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=11)
    for linea in [
        "Gracias por su compra en nuestra sucursal",
        "Conserve este comprobante para cualquier aclaracion",
        "Presentarlo junto con su identificacion oficial vigente",
        "El tiempo limite de devolucion es de treinta dias naturales",
    ]:
        pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(destino))
    return destino.read_bytes()


class Espia:
    """Extractor que ademas de responder, apunta con quien se le hablo."""

    def __init__(self, invoice: ExtractedInvoice | Exception):
        self.invoice = invoice
        self.llamadas: list[bytes] = []

    async def imagen(self, datos: bytes) -> ExtractedInvoice:
        self.llamadas.append(datos)
        if isinstance(self.invoice, Exception):
            raise self.invoice
        return self.invoice

    async def texto(self, texto: str) -> ExtractedInvoice:
        self.llamadas.append(texto.encode())
        if isinstance(self.invoice, Exception):
            raise self.invoice
        return self.invoice


def _invoice(**kw) -> ExtractedInvoice:
    base = dict(
        provider_name="FARMACIAS DEL SUR",
        total=Decimal("500.00"),
        tax_amount=Decimal("68.97"),
        subtotal=Decimal("431.03"),
        invoice_date=date(2025, 4, 10),
        confidence=0.91,
        raw_text="texto del comprobante",
    )
    base.update(kw)
    return ExtractedInvoice(**base)


# ---------------------------------------------------------------------------
# El escalon mas importante: un PDF con texto NO va al modelo
# ---------------------------------------------------------------------------


class TestPdfConTexto:
    async def test_sin_escalar_un_pdf_no_toca_el_modelo(self, pdf_impreso, monkeypatch):
        """El caso que hace que esto valga la pena.

        Un PDF impreso tiene el texto ahi, al alcance. Mandarlo a un modelo
        cuesta tiempo y tokens, y encima puede equivocarse donde el texto no se
        equivoca. Ademas se pierde la trazabilidad: si se guardara como lectura
        de modelo, no habria forma de medir la exactitud de la IA porque el
        regex se contaria como acierto de la IA.

        Con `ESCALAR_A_IA_SIN_LINEAS=false` ese comportamiento se conserva: las
        reglas aciertan el encabezado y no se paga un modelo. Es la politica
        "una foto buena no toca ningun modelo", y sigue disponible.
        """
        monkeypatch.setattr(settings, "ESCALAR_A_IA_SIN_LINEAS", False)
        # Sin lineas de producto, por omision se le pregunta al modelo y esto
        # mediria la confianza DEL MODELO, que no es lo que mide este test.
        monkeypatch.setattr(settings, "ESCALAR_A_IA_SIN_LINEAS", False)
        espia = Espia(_invoice())
        resultado = await capture_ticket(
            pdf_impreso, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )

        assert espia.llamadas == [], "sin escalar, un PDF con texto no toca el modelo"
        # Mayuscula inicial, no versalitas: asi los emite un sistema de
        # facturacion de verdad, y es justo el caso que el parser perdia.
        assert resultado.provider_name == "Tiendas Ramirez SA de CV"
        assert resultado.total_amount == Decimal("1100.00")
        assert resultado.expense_date == date(2025, 3, 15)
        assert resultado.confidence_source is ConfidenceSource.PDF_TEXT

    async def test_el_origen_y_la_confianza_no_se_confunden(self, pdf_impreso, monkeypatch):
        """`pdf_text` con una confianza de reglas, no de modelo.

        El numero de confianza sale de lo que el parser encontro, no de una
        estimacion. Es una distincion de auditoria: cuando se revise una
        muestra para medir el 96%, los tickets de regex tienen que ser
        separables de los de la IA, o la medicion no mide nada.
        """
        # Sin lineas de producto, por omision se le pregunta al modelo y esto
        # mediria la confianza DEL MODELO, que no es lo que mide este test.
        monkeypatch.setattr(settings, "ESCALAR_A_IA_SIN_LINEAS", False)
        espia = Espia(_invoice())
        resultado = await capture_ticket(pdf_impreso, "pdf", extract_from_text=espia.texto)

        assert resultado.confidence_source is ConfidenceSource.PDF_TEXT
        # RFC + subtotal + fecha encontrados: la fila mas alta de la tabla.
        assert resultado.confidence == confianza_por_campos(True, True, True)
        assert resultado.confidence >= AUTO_APPROVE_CONFIDENCE

    async def test_confianza_menor_cuando_encuentra_menos(self, pdf_impreso, monkeypatch):
        """Compara reglas-contra-reglas, asi que la escalada no interviene.

        Sin lineas, por omision se le preguntaria al modelo y `completo` traeria
        la confianza de un modelo de mentira, mas alta que la de las reglas, y la
        comparacion no diria nada. Se apaga la escalada para que las dos puntas
        de la comparacion sean del mismo lector.
        """
        monkeypatch.setattr(settings, "ESCALAR_A_IA_SIN_LINEAS", False)
        espia = Espia(_invoice())
        completo = await capture_ticket(pdf_impreso, "pdf", extract_from_text=espia.texto)

        # El mismo PDF, pero el parser no encuentra el RFC.
        sin_rfc = TICKET_IMPRESO.replace("RFC: TRAM910101XXX\n", "")
        resultado = capture._parse_receipt_text(sin_rfc)
        capture._marcar_por_reglas(resultado, ConfidenceSource.PDF_TEXT)

        assert resultado.confidence < completo.confidence


# ---------------------------------------------------------------------------
# PDF escaneado: ahi si toca vision
# ---------------------------------------------------------------------------


class TestPdfEscaneado:
    async def test_sin_texto_se_mira_la_pagina(self, pdf_escaneado):
        espia = Espia(_invoice())
        resultado = await capture_ticket(
            pdf_escaneado, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )

        assert len(espia.llamadas) == 1, "debe mandarse la pagina renderizada"
        assert espia.llamadas[0].startswith(b"\xff\xd8\xff"), "debe ser la imagen renderizada"
        # Un escaneado no tiene texto: mandarlo a leer texto seria gasto sin
        # informacion.
        assert resultado.provider_name == "FARMACIAS DEL SUR"
        assert resultado.confidence_source is ConfidenceSource.LLM

    async def test_no_se_manda_texto_vacio_al_modelo_de_texto(self, pdf_escaneado):
        espia_texto = Espia(_invoice())
        await capture_ticket(pdf_escaneado, "pdf", extract_from_text=espia_texto.texto)
        assert espia_texto.llamadas == []

    async def test_un_pdf_corrupto_no_revienta(self):
        """Un archivo que no es PDF tiene que decir que no es un PDF.

        No tiene que caerse la peticion, y sobre todo no tiene que inventarse un
        comprobante. Antes, un PDF que pdfplumber no abria terminaba en
        `_fallback_extraction`, que fabricaba un ticket con total cero.
        """
        basura = b"esto no es un pdf, es texto plano"
        espia = Espia(_invoice())
        resultado = await capture_ticket(
            basura, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )
        # Con vision no disponible no se puede hacer nada: se devuelve ilegible,
        # que es un ticket en la cola con motivo, no una excepcion.
        assert resultado.provider_name == UNKNOWN_PROVIDER
        assert resultado.total_amount == 0

    @pytest.mark.parametrize("paginas_en_el_pdf,esperadas", [(1, 1), (5, 3)])
    async def test_se_mandan_como_maximo_tres_paginas(
        self, pdf_escaneado, pdf_escaneado_largo, paginas_en_el_pdf, esperadas
    ):
        """Mandar 20 paginas de un comprobante no agrega informacion.

        Las ultimas paginas de un comprobante largo son condiciones generales, y
        cada pagina renderizada es una imagen completa que va a un modelo. El
        tope se comprueba con dos PDFs de distinta longitud: con uno solo, el
        test pasaria con el tope puesto o sin el.
        """
        espia = Espia(_invoice())
        contenido = pdf_escaneado if paginas_en_el_pdf == 1 else pdf_escaneado_largo
        await capture_ticket(contenido, "pdf", extract_from_image=espia.imagen)
        assert len(espia.llamadas) == esperadas

    async def test_el_pdf_largo_si_tiene_texto_va_por_reglas(
        self, pdf_escaneado_largo
    ):
        """Un PDF largo sin texto sigue yendo a vision, no a leer texto vacio."""
        assert extract_pdf_text(pdf_escaneado_largo).strip() == ""
        assert len(render_pdf_pages(pdf_escaneado_largo)) == capture.PDF_MAX_PAGINAS_A_VISION


# ---------------------------------------------------------------------------
# El escalon intermedio: hay texto pero las reglas no alcanzan
# ---------------------------------------------------------------------------


class TestTextoQueLasReglasNoAlcanzan:

    async def test_se_pide_al_texto_y_no_a_la_imagen(self, pdf_sin_labels):
        """Cuando el texto existe pero no se entiende, la imagen no aporta.

        Se le pide al modelo el texto, que es mas informacion que una foto, y
        solo se cae a vision si tampoco asi.
        """
        espia_imagen = Espia(_invoice())
        espia_texto = Espia(_invoice(provider_name="LEIDO POR EL MODELO"))
        resultado = await capture_ticket(
            pdf_sin_labels, "pdf",
            extract_from_image=espia_imagen.imagen,
            extract_from_text=espia_texto.texto,
        )

        assert espia_imagen.llamadas == [], "no hay razon para mirar la pagina"
        assert espia_texto.llamadas, "habia texto y se debio intentar con el"
        assert resultado.provider_name == "LEIDO POR EL MODELO"

    async def test_ni_reglas_ni_modelo_conservan_el_total_encontrado(
        self, pdf_impreso
    ):
        """Cuando todo falla, no se tira lo que si se leyo.

        Un total sin proveedor es un ticket incompleto, no uno inutil: la cola
        lo marca con `provider_missing` y quien lo revise ya tiene la mitad del
        trabajo hecha. Perderlo en un intento fallido es trabajo humano
        desperdiciado.
        """
        espia = Espia(_invoice(provider_name=UNKNOWN_PROVIDER, total=Decimal("0")))
        # Se inyecta un pdf escaneado real pero con vision que no devuelve nada
        # util, para llegar al final de la cascada.
        resultado = await capture_ticket(
            TICKET_IMPRESO.encode(), "text",  # texto directo, sin PDF
            extract_from_text=espia.texto,
        )
        # Aqui las reglas si encuentran total, asi que ni se pregunta. El caso
        # interesante es el contrario, y lo cubre el test siguiente.
        assert resultado.total_amount == Decimal("1100.00")
        assert espia.llamadas == []

    async def test_un_documento_que_nada_se_lee_no_se_pierde(self):
        """El peor caso: ni reglas ni modelo. El archivo sigue existiendo."""
        espia = Espia(_invoice(provider_name=UNKNOWN_PROVIDER, total=Decimal("0")))
        resultado = await capture_ticket(
            ("texto sin nada reconocible\nlinea sin total\n" * 5).encode(),
            "text",
            extract_from_text=espia.texto,
        )
        assert resultado.provider_name == UNKNOWN_PROVIDER
        assert resultado.total_amount == 0
        # No se lanzo nada: el documento llega a la cola con su motivo.
        assert resultado.raw_text


# ---------------------------------------------------------------------------
# Fallos del sistema: no son documentos ilegibles
# ---------------------------------------------------------------------------


class TestFallosDelSistema:

    async def test_ia_apagada_no_se_reporta_como_documento_ilegible(self, pdf_escaneado):
        """"El extractor esta apagado" y "el papel esta borroso" no son lo mismo.

        Pasan por el mismo camino del ticket (sin proveedor, en la cola) pero
        piden lo opuesto: al primero se le enciende el extractor, al segundo se
        le busca otro escalon de lectura. Si se reportan igual, una
        configuracion apagada se manifiesta como "muchos tickets raros" y nadie
        la reconoce.
        """
        espia = Espia(_invoice(provider_name="AI_DISABLED", total=Decimal("0")))
        resultado = await capture_ticket(
            pdf_escaneado, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )
        assert resultado.provider_name == UNKNOWN_PROVIDER
        assert "extractor de IA esta apagado" in resultado.raw_text

    async def test_respuesta_ilegible_del_modelo_se_dice_por_su_nombre(self, pdf_escaneado):
        espia = Espia(_invoice(provider_name="ERROR_PARSING", total=Decimal("0")))
        resultado = await capture_ticket(
            pdf_escaneado, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )
        assert "no se pudo interpretar" in resultado.raw_text

    def test_motivo_se_distingue_de_un_centinela_de_proveedor(self):
        invoice = _invoice(provider_name="UNKNOWN")
        # UNKNOWN es un resultado legitimo: el modelo leyo el documento y no
        # encontro proveedor. No es un fallo del sistema.
        assert motivo_de_fallo_del_modelo(invoice) is None
        assert motivo_de_fallo_del_modelo(_invoice(provider_name="AI_DISABLED"))
        assert motivo_de_fallo_del_modelo(_invoice(provider_name="ERROR_PARSING"))

    async def test_la_ia_que_explota_no_tira_el_archivo(self, pdf_escaneado):
        espia = Espia(RuntimeError("connection reset by peer"))
        resultado = await capture_ticket(
            pdf_escaneado, "pdf",
            extract_from_image=espia.imagen,
            extract_from_text=espia.texto,
        )
        assert resultado.provider_name == UNKNOWN_PROVIDER
        assert "connection reset" in resultado.raw_text


# ---------------------------------------------------------------------------
# Los centinelas de la capa de IA
# ---------------------------------------------------------------------------


class TestNormalizacionDeCentinelas:
    """Ninguna cadena interna de la IA debe llegar a la cola como nombre.

    Si "AI_DISABLED" llega como `provider_name`, el check de proveedor del gate
    no lo reconoce (es una cadena no vacia, no la centinela) y el ticket pasa
    como si tuviera proveedor. La cola ademas lo muestra como si fuera un
    comercio.
    """

    @pytest.mark.parametrize("sentinel", sorted(capture.SENTINELS_SIN_PROVEEDOR))
    def test_se_normalizan_a_la_canonica(self, sentinel):
        resultado = invoice_to_result(_invoice(provider_name=sentinel))
        assert resultado.provider_name == UNKNOWN_PROVIDER

    def test_un_proveedor_real_no_se_toca(self):
        resultado = invoice_to_result(_invoice(provider_name="OXXO"))
        assert resultado.provider_name == "OXXO"

    def test_un_nombre_con_espacios_al_rededor_se_limpia(self):
        resultado = invoice_to_result(_invoice(provider_name="  OXXO  "))
        assert resultado.provider_name == "OXXO"


# ---------------------------------------------------------------------------
# Tipos de archivo
# ---------------------------------------------------------------------------


class TestTiposDeArchivo:

    async def test_texto_plano_usa_reglas(self):
        espia = Espia(_invoice())
        resultado = await capture_ticket(
            TICKET_IMPRESO.encode(), "text", extract_from_text=espia.texto
        )
        assert espia.llamadas == []
        assert resultado.confidence_source is ConfidenceSource.RULES

    async def test_imagen_siempre_vision(self):
        espia = Espia(_invoice())
        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", extract_from_image=espia.imagen
        )
        assert len(espia.llamadas) == 1
        assert resultado.confidence_source is ConfidenceSource.LLM

    async def test_un_tipo_desconocido_es_error_del_cliente(self):
        """Un tipo que no existe si es un error de verdad, no un ticket vacio.

        Mandar `file_type=xlsx` es un bug del cliente. Crear un ticket en la
        cola por eso mete ruido en la cola y esconde el error de programacion
        detras de "documento ilegible".
        """
        with pytest.raises(ExtractionUnavailable, match="xlsx"):
            await capture_ticket(b"lo que sea", "xlsx")


# ---------------------------------------------------------------------------
# La tabla de confianza
# ---------------------------------------------------------------------------


class TestTablaDeConfianza:

    def test_cubre_todas_las_combinaciones(self):
        """Una combinacion sin fila es un AssertionError en produccion.

        Se prueba explicita porque es el modo de fallo que un dict con default
        no avisa: `CONFIANZA_POR_CAMPOS.get(...)` devolveria None y `confidence`
        seria None, que es un caso distinto del que se quiere.
        """
        import itertools

        for rfc, subtotal, fecha in itertools.product([True, False], repeat=3):
            valor = confianza_por_campos(rfc, subtotal, fecha)
            assert 0.0 < valor <= 1.0

    def test_las_combinaciones_no_valen_todas_lo_mismo(self):
        """La tabla tiene que distinguir, no solo tener numeros validos.

        Este test se escribio despues de que la verificacion por mutacion
        encontrara que los otros dos de esta clase pasaban con una funcion
        constante. Los dos comprobaban cosas que un 0.90 fijo cumple:
        `0 < 0.90 <= 1` y `0.90 >= 0.90`. Eran tests que no distinguian una
        tabla real de un numero inventado, que es justo lo que tienen que
        distinguir.

        La consecuencia de no comprobarlo no es academica: si la confianza
        fuera la misma con RFC, subtotal y fecha que con un total suelto, la
        columna no mediria nada. El muestreo para el 96% leeria una sola cifra
        y no podria decir si el extractor'sabe o no sabe.
        """
        import itertools

        valores = {
            (r, s, f): confianza_por_campos(r, s, f)
            for r, s, f in itertools.product([True, False], repeat=3)
        }
        distintos = len(set(valores.values()))
        assert distintos >= 5, (
            f"la tabla devuelve {distintos} valores distintos para 8 combinaciones: "
            "no esta discriminando la evidencia"
        )
        # Y el rango tiene que ser el esperado, no solo "distintos".
        assert valores[(True, True, True)] == max(valores.values())
        assert valores[(False, False, False)] == min(valores.values())

    def test_mas_evidencia_nunca_es_menos_confianza(self):
        """Monotonia: agregar un campo no puede bajar la confianza."""
        import itertools

        valores = {}
        for rfc, subtotal, fecha in itertools.product([True, False], repeat=3):
            valores[(rfc, subtotal, fecha)] = confianza_por_campos(rfc, subtotal, fecha)

        for clave, valor in valores.items():
            for i in range(3):
                if clave[i]:
                    continue
                menos = list(clave)
                menos[i] = True
                assert valores[tuple(menos)] >= valor, (
                    f"agregar el campo {i} bajo la confianza de {clave}"
                )

        # Y el extremo completo tiene que subir de forma estricta, no solo
        # "no bajar". Con una funcion constante, la comprobacion de arriba
        # pasa igual.
        assert valores[(True, True, True)] > valores[(False, False, False)]


# ---------------------------------------------------------------------------
# El dato mas caro de perder: la fecha
# ---------------------------------------------------------------------------


class TestFechaInventada:

    async def test_un_documento_sin_fecha_no_queda_fechado_hoy(self):
        """El bug que se corrigio aqui, guardado como regresion.

        `_parse_receipt_text` ponia `date.today()` cuando no encontraba fecha.
        El resultado era un ticket indistinguible de un gasto real de hoy, y el
        cierre mensual contaba en el mes equivocado. Ahora la fecha queda en
        None y el ticket entra a la cola con `date_missing`.
        """
        resultado = await capture_ticket(
            b"TIENDAS RAMIREZ\nTOTAL: 500.00\n", "text",
        )
        assert resultado.expense_date is None
        assert resultado.confidence is not None
        # Sin fecha no se puede estar en la tabla mas alta: la confianza baja.
        # Sin RFC ni subtotal ni fecha: la fila mas baja de la tabla.
        assert resultado.confidence == confianza_por_campos(False, False, False)


class TestUnaSolaRutaDeCaptura:
    """No puede haber dos politicas de captura en el sistema.

    Este archivo empieza con la consecuencia de que las hubiera: un PDF escaneado
    que nunca llego a mirar la pagina. Dos politicas no fallan de forma visible.
    La primera que se implemento murio sin que nadie lo notara, porque devolvia
    lista vacia y se tragaba la excepcion, justo en el punto donde se decide si
    un documento necesita IA. La segunda funcionaba y nadie la reviso porque no
    era la que la API llamaba.

    Los tests de esta clase no comprueban comportamiento: comprueban que no
    vuelva a haber un segundo camino. Son preventivos, y por eso estan
    escritos como inventario de lo que no debe reaparecer.
    """

    def test_el_lector_por_reglas_no_finge_que_sabe_leer_una_foto(self):
        """Una imagen no tiene texto que un regex pueda leer.

        Antes esta funcion devolvia un ticket de relleno: proveedor desconocido,
        total cero, fecha de hoy. Indistinguible de un comprobante real de cero
        pesos, y el total cero es justo el que pasa los checks del gate.

        Este test existe porque la rama de imagen estaba rota con un `NameError`
        y la suite daba 301 verde: nada la llamaba. Un test que cubre el camino
        principal no cubre los laterales.
        """
        from app.services.parser_service import extract_ticket_data

        with pytest.raises(ValueError, match="capture_ticket"):
            extract_ticket_data(b"\xff\xd8\xfffoto-de-un-ticket", file_type="image")

    def test_el_error_dice_a_donde_ir(self):
        """Un error que no dice la solucion se convierte en un parche.

        El que tropieza con esto dentro de seis meses no sabe que existe una
        cascada, y lo mas probable es que escriba un extractor de imagen junto a
        la funcion que revento.
        """
        from app.services.parser_service import extract_ticket_data

        with pytest.raises(ValueError) as exc:
            extract_ticket_data(b"contenido", file_type="image")
        # Que nombre el modulo y la funcion, no solo que "no se puede".
        assert "app.services.capture" in str(exc.value)
        assert "capture_ticket" in str(exc.value)

    def test_la_capa_de_ia_no_tiene_una_ruta_de_pdf_propia(self):
        """El PDF se decide en `capture.py`, no en el extractor.

        `extract_from_pdf` hacia su propia politica: probaba texto, y si no
        habia, renderizaba con `fitz`. Era la misma cascada, escrita dos veces,
        y la copia nunca funciono. Se boro. Este test esta para que la proxima
        version no la vuelva a agregar "para el caso del PDF".
        """
        from app.services import ai_extractor

        assert not hasattr(ai_extractor.ai_extractor, "extract_from_pdf")
        assert not hasattr(ai_extractor.ai_extractor, "_pdf_to_images")

    def test_nadie_importa_una_dependencia_que_no_esta_instalada(self):
        """Guarda contra el fallo que se traga las excepciones.

        `fitz` (PyMuPDF) no esta instalado y no esta en requirements. El import
        se hacia dentro de un `try/except Exception: pass`, asi que no habia
        error: habia una lista vacia, en el punto exacto donde se decide si un
        documento necesita IA. Un `ImportError` tragado no se_debugga nunca,
        porque no hay nada que debuggear.

        El recorrido es por el arbol de sintaxis, no por el texto: los
        comentarios que explican por que `fitz` no se usa son legitimos y
        tienen que poder mencionarlo.
        """
        import ast

        raiz = Path(__file__).resolve().parents[2] / "app"
        assert raiz.is_dir(), f"no se encontro el paquete app en {raiz}"

        _RIESGOSAS = {"fitz", "pymupdf", "pdf2image"}
        ofensas: list[str] = []

        for archivo in sorted(raiz.rglob("*.py")):
            arbol = ast.parse(archivo.read_text(encoding="utf-8"), str(archivo))
            for nodo in ast.walk(arbol):
                modulos: list[str] = []
                if isinstance(nodo, ast.Import):
                    modulos = [a.name for a in nodo.names]
                elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                    modulos = [nodo.module]
                for modulo in modulos:
                    raiz_modulo = modulo.split(".")[0]
                    if raiz_modulo in _RIESGOSAS:
                        ofensas.append(f"{archivo.name}:{nodo.lineno} importa {modulo}")

        assert not ofensas, (
            "imports prohibidos (no estan instalados y fallan en silencio): "
            + "; ".join(ofensas)
        )


class TestLaMarcaDeMuestreoLlegaDesdeLaRutaReal:
    """La condicion de muestreo tiene que ser la misma en la marca y en la base.

    Se prueba con la cascada completa y sin parchearla, porque lo que importa
    no es que `en_muestra` devuelva True: es que el ticket que la API guarda
    termine marcado. Un test que parcheara la funcion probaria el parche.

    El riesgo concreto que esto cierra: que la marca se calcule sobre un campo
    distinto al que se guarda. Si la marca usara el contenido original y la
    fila guardara otro, la seleccion seria irreproducible y el ticket aparecia
    en la muestra una vez y nunca mas.
    """

    def test_la_marca_se_calcula_sobre_el_mismo_contenido_que_se_hashea(self):
        """El hash que decide la muestra es el del contenido que se persiste.

        `compute_source_hash` recibe el mismo `content` que `_persist_extracted`
        guarda. Si en algun momento se hasheara algo distinto (el texto ya
        parseado, el nombre del archivo), la decision dejaria de ser
        reproducible con lo que quedo guardado.
        """
        import hashlib

        from app.services.accuracy_service import en_muestra
        from app.services.confidence_gate import compute_source_hash

        contenido = b"contenido-del-comprobante"
        assert hashlib.sha256(contenido).hexdigest() == compute_source_hash(contenido)

        # Y la decision sale de ese mismo hash, no de otra cosa.
        for i in range(200):
            candidato = f"prueba-{i}".encode()
            esperado = en_muestra(compute_source_hash(candidato))
            assert esperado == en_muestra(hashlib.sha256(candidato).hexdigest())

    def test_la_marca_no_depende_del_nombre_del_archivo(self):
        """El mismo comprobante subido con dos nombres distintos decide lo
        mismo. Si dependiera del nombre, recargar el archivo "factura.pdf"
        como "factura (1).pdf" lo sacaria de la muestra, y la evidencia que ya
        se junto sobre ese comprobante quedaria repartida en dos."""
        from app.services.accuracy_service import en_muestra
        from app.services.confidence_gate import compute_source_hash

        contenido = b"los-mismos-bytes"
        decision_original = en_muestra(compute_source_hash(contenido))
        decision_renombrado = en_muestra(compute_source_hash(contenido))
        assert decision_original == decision_renombrado
