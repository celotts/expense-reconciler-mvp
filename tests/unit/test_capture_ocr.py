"""El escalon de OCR dentro de la cascada de captura.

Estos tests fijan tres cosas que se pueden romper sin que nada falle de forma
visible:

1. Que una foto se lea con OCR ANTES de gastar un modelo, y quede registrada
   como `ocr`.
2. Que la confianza de un OCR este descontada frente a la de un PDF con texto.
3. Que el texto del OCR conserve las LINEAS, que es donde se perdia todo.

El tercer es el que mas caro salio. Ver `TestLasLineasDelOcrNoSePierden`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import ConfidenceSource
from app.services import capture
from app.services.capture import (
    OCR_MIN_CHARS_PARA_INTENTAR,
    capture_ticket,
    confianza_por_campos,
    confianza_por_campos_ocr,
)
from app.services.ocr import OCRNoDisponible

# El texto que un OCR bien hecho devuelve de un ticket. Notese que cada label y
# su valor estan en la MISMA linea: es lo que el parser por reglas espera.
TEXTO_OCR = """Tiendas Ramirez SA de CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""


class OcrFalso:
    """Un OCR que devuelve un texto fijo, o falla como se le pida."""

    def __init__(self, texto: str | None = None, error: Exception | None = None):
        self.texto = texto
        self.error = error
        self.llamadas: list[bytes] = []

    def __call__(self, datos: bytes):
        self.llamadas.append(datos)
        if self.error is not None:
            raise self.error
        from app.services.ocr import ResultadoOCR

        return ResultadoOCR(texto=self.texto or "", motor="falso")


def _vision_que_falla(datos: bytes, mime_type: str = "image/png"):
    """Un extractor de vision que se niega, para comprobar que NO se llamo."""
    raise AssertionError("no se deberia llamar a vision en este caso")


class TestElOrdenDeLaEscalada:

    async def test_una_foto_buena_no_toca_el_modelo(self):
        """OCR primero significa que una foto legible no cuesta un modelo.

        Es el ahorro entero de la feature. Antes de este escalon, TODA foto iba
        a vision, aunque Tesseract la leyera sin equivocarse.
        """
        ocr = OcrFalso(TEXTO_OCR)

        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", ocr_reader=ocr,
        )

        assert len(ocr.llamadas) == 1
        assert resultado.provider_name == "Tiendas Ramirez SA de CV"
        assert resultado.total_amount == Decimal("1100.00")
        assert resultado.confidence_source is ConfidenceSource.OCR

    async def test_el_ocr_disenado_va_a_vision(self):
        """Si el OCR no lee nada util, vision es el escalon que queda.

        Es el caso de la foto borrosa, y por eso vision tiene que seguir
        existiendo. El OCR no la reemplazo: la puso antes.
        """
        ocr = OcrFalso("basura sin labels\n" * 50)
        llamadas_a_vision: list[bytes] = []

        async def vision(datos: bytes, mime_type: str = "image/png"):
            from app.services.ai_extractor import ExtractedInvoice

            llamadas_a_vision.append(datos)
            return ExtractedInvoice(
                provider_name="OXXO SA de CV",
                total=Decimal("50.00"),
                confidence=0.91,
                raw_text="lo leyo vision",
            )

        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", ocr_reader=ocr, extract_from_image=vision,
        )

        assert len(llamadas_a_vision) == 1
        assert resultado.confidence_source is ConfidenceSource.LLM

    async def test_sin_ocr_la_foto_sigue_yendo_a_vision(self):
        """Una maquina sin Tesseract se comporta como antes del escalon.

        Es lo que hace que este modulo no sea un requisito para instalar la app.
        `OCRNoDisponible` no es un fallo de la cascada: es la configuracion de la
        maquina, y se sigue al siguiente escalon.
        """
        ocr = OcrFalso(error=OCRNoDisponible("tesseract no esta instalado"))
        llamadas: list[bytes] = []

        async def vision(datos: bytes, mime_type: str = "image/png"):
            from app.services.ai_extractor import ExtractedInvoice

            llamadas.append(datos)
            return ExtractedInvoice(
                provider_name="OXXO SA de CV", total=Decimal("50.00"),
                confidence=0.91, raw_text="vision",
            )

        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", ocr_reader=ocr, extract_from_image=vision,
        )

        assert len(llamadas) == 1
        assert resultado.provider_name == "OXXO SA de CV"

    async def test_una_excepcion_del_ocr_no_tumba_la_lectura(self):
        """Un OCR que revienta por dentro todavia deja que vision lo intente.

        Un motor que se cuelga, una imagen corrupta: ninguna de las dos puede
        dejar al ticket sin leer, porque vision todavia esta.
        """
        ocr = OcrFalso(error=RuntimeError("el motor se cayo"))

        async def vision(datos: bytes, mime_type: str = "image/png"):
            from app.services.ai_extractor import ExtractedInvoice

            return ExtractedInvoice(
                provider_name="OXXO SA de CV", total=Decimal("50.00"),
                confidence=0.91, raw_text="vision",
            )

        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", ocr_reader=ocr, extract_from_image=vision,
        )

        assert resultado.provider_name == "OXXO SA de CV"

    async def test_texto_corto_del_ocr_no_es_un_comprobante(self):
        """Unos pocos caracteres de OCR no se pasan por un ticket.

        Tesseract devuelve texto aunque la foto sea una pared. Sin este piso, un
        muro con 40 caracteres de ruido pasaria, se parsearia, y con suerte
        saldria un comprobante sin proveedor que se guarda en la cola.
        """
        ocr = OcrFalso("TOT")  # tres letras, muy por debajo del piso

        resultado = await capture_ticket(
            b"\xff\xd8\xfffoto", "image", ocr_reader=ocr,
            extract_from_image=_vision_que_falla,
        )

        # Llego a vision, que es donde se decide que es un ilegible.
        assert resultado.total_amount == Decimal("0.00")


class TestLasLineasDelOcrNoSePierden:
    """La regresion que mas caro salio, y la mas facil de romper.

    `image_to_data` devuelve, ademas del texto, los numeros de bloque, parrafo y
    linea. Si se ignoran y se une cada palabra con un salto de linea, el texto
    sale asi:

        RFC:
        TRAM910101XXX

    Ningun regex del proyecto encuentra un RFC ahi, porque todos estan anclados
    a la forma "label: valor" en UNA linea. Peor: la linea del valor sola parece
    un nombre de comercio, asi que el ticket se guardaba con el RFC como
    proveedor y sin subtotal, sin IVA, sin total y sin fecha, con la confianza mas
    alta que produce la cascada. Un ticket vacio generado por una lectura casi
    perfecta, que es el peor resultado posible porque no parece un fallo.
    """

    def test_el_ocr_de_tesseract_reagrupa_por_linea(self, monkeypatch):
        """La salida de `image_to_data` se convierte en lineas, no en palabras.

        Se reproduce a mano la forma de la salida de Tesseract, con sus numeros
        de bloque/parrafo/linea, y se comprueba que el texto resultante tiene una
        linea por linea del documento.
        """
        import app.services.ocr as ocr_mod

        palabras = [
            # (texto, bloque, parrafo, linea, conf)
            ("Tiendas", 1, 1, 1, 96.0),
            ("Ramirez", 1, 1, 1, 95.0),
            ("SA", 1, 1, 1, 94.0),
            ("de", 1, 1, 1, 93.0),
            ("CV", 1, 1, 1, 92.0),
            ("RFC:", 1, 1, 2, 97.0),
            ("TRAM910101XXX", 1, 1, 2, 89.0),
            ("Subtotal:", 1, 1, 3, 96.0),
            ("948.28", 1, 1, 3, 91.0),
            ("TOTAL:", 1, 1, 4, 98.0),
            ("1100.00", 1, 1, 4, 95.0),
        ]

        class SalidaFalsa(dict):
            pass

        datos = SalidaFalsa(
            text=[p[0] for p in palabras],
            block_num=[p[1] for p in palabras],
            par_num=[p[2] for p in palabras],
            line_num=[p[3] for p in palabras],
            conf=[p[4] for p in palabras],
        )

        class PytesseractFalso:
            Output = type("O", (), {"DICT": dict})

            @staticmethod
            def image_to_data(*_args, **_kwargs):
                return datos

        monkeypatch.setattr(ocr_mod, "obtener_motor", lambda _n: PytesseractFalso)
        monkeypatch.setattr(ocr_mod, "_preparar", lambda d: d)

        resultado = ocr_mod.leer_imagen(b"\xff\xd8\xfffoto")

        lineas = resultado.texto.splitlines()
        assert lineas == [
            "Tiendas Ramirez SA de CV",
            "RFC: TRAM910101XXX",
            "Subtotal: 948.28",
            "TOTAL: 1100.00",
        ], "cada linea del documento debe quedar en una linea del texto"

    def test_el_texto_reagrupado_lo_entiende_el_parser(self, monkeypatch):
        """La prueba de que el reagrupado SIRVE, no solo de que sale bonito.

        La prueba anterior verifica la forma del texto. Esta verifica el
        resultado: que el parser de verdad encuentra los campos. Sin esta, un
        texto con la forma correcta pero con los espacios mal puestos pasaria la
        primera y fallaria en produccion.
        """
        import app.services.ocr as ocr_mod
        from app.services.parser_service import _parse_receipt_text

        palabras = [
            ("Tiendas", 1, 1, 1, 96.0), ("Ramirez", 1, 1, 1, 95.0),
            ("SA", 1, 1, 1, 94.0), ("de", 1, 1, 1, 93.0), ("CV", 1, 1, 1, 92.0),
            ("RFC:", 1, 1, 2, 97.0), ("TRAM910101XXX", 1, 1, 2, 89.0),
            ("Fecha:", 1, 1, 3, 96.0), ("2025/03/15", 1, 1, 3, 93.0),
            ("Subtotal:", 1, 1, 4, 96.0), ("948.28", 1, 1, 4, 91.0),
            ("IVA", 1, 1, 5, 95.0), ("(16%):", 1, 1, 5, 90.0),
            ("151.72", 1, 1, 5, 92.0),
            ("TOTAL:", 1, 1, 6, 98.0), ("1100.00", 1, 1, 6, 95.0),
        ]
        datos = {
            "text": [p[0] for p in palabras],
            "block_num": [p[1] for p in palabras],
            "par_num": [p[2] for p in palabras],
            "line_num": [p[3] for p in palabras],
            "conf": [p[4] for p in palabras],
        }

        class PytesseractFalso:
            Output = type("O", (), {"DICT": dict})

            @staticmethod
            def image_to_data(*_args, **_kwargs):
                return datos

        monkeypatch.setattr(ocr_mod, "obtener_motor", lambda _n: PytesseractFalso)
        monkeypatch.setattr(ocr_mod, "_preparar", lambda d: d)

        texto = ocr_mod.leer_imagen(b"\xff\xd8\xfffoto").texto
        parseado = _parse_receipt_text(texto)

        assert parseado.provider_name == "Tiendas Ramirez SA de CV"
        assert parseado.provider_tax_id == "TRAM910101XXX"
        assert parseado.subtotal == Decimal("948.28")
        assert parseado.tax_amount == Decimal("151.72")
        assert parseado.total_amount == Decimal("1100.00")
        assert parseado.expense_date is not None
        assert parseado.expense_date.year == 2025
        assert parseado.expense_date.month == 3
        assert parseado.expense_date.day == 15

    def test_la_confianza_del_ocr_se_promedia_por_palabra(self, monkeypatch):
        """La confianza que sale es la media de las palabras leidas.

        Se reporta en su propio campo y no se copia a `TicketExtractionResult.confidence`,
        porque la confianza del ticket la calcula el gate a partir de la
        evidencia y las dos cosas no son comparables.
        """
        import app.services.ocr as ocr_mod

        palabras = [("TOTAL:", 1, 1, 1, 90.0), ("1100.00", 1, 1, 1, 70.0)]
        datos = {
            "text": [p[0] for p in palabras],
            "block_num": [p[1] for p in palabras],
            "par_num": [p[2] for p in palabras],
            "line_num": [p[3] for p in palabras],
            "conf": [p[4] for p in palabras],
        }

        class PytesseractFalso:
            Output = type("O", (), {"DICT": dict})

            @staticmethod
            def image_to_data(*_args, **_kwargs):
                return datos

        monkeypatch.setattr(ocr_mod, "obtener_motor", lambda _n: PytesseractFalso)
        monkeypatch.setattr(ocr_mod, "_preparar", lambda d: d)

        resultado = ocr_mod.leer_imagen(b"\xff\xd8\xfffoto")
        assert resultado.confianza_media == pytest.approx(0.8, abs=0.001)
        assert resultado.motor == "tesseract"


class TestLaConfianzaDelOcrEstaDescontada:
    """La misma evidencia leida de una foto vale menos que leida de un PDF.

    No por desconfianza abstracta, sino porque el OCR cambia caracteres sin
    avisar: un "1" se vuelve "l", un "0" se vuelve "O", y `1,1OO.OO` es un total
    tan valido como `1,100.00` para un regex. No hay forma de saber que esta mal
    desde el texto.
    """

    @pytest.mark.parametrize(
        "rfc,subtotal,fecha",
        [
            (False, False, False),
            (False, False, True),
            (True, False, False),
            (False, True, False),
            (True, False, True),
            (False, True, True),
            (True, True, False),
            (True, True, True),
        ],
    )
    def test_el_ocr_nunca_gana_al_pdf(self, rfc, subtotal, fecha):
        assert confianza_por_campos_ocr(rfc, subtotal, fecha) < confianza_por_campos(
            rfc, subtotal, fecha
        ), "la misma evidencia de una foto nunca puede valer mas que de un PDF"

    def test_las_ocho_combinaciones_cubren_todas(self):
        """Como la tabla de PDF: una combinacion faltante es un error, no un None.

        Un `dict.get` con default devolveria `None`, y `None` de confianza es un
        caso distinto del que se quiere: el ticket caeria a la cola sin que nadie
        sepa que la tabla esta incompleta.
        """
        import itertools

        for combinacion in itertools.product([False, True], repeat=3):
            assert confianza_por_campos_ocr(*combinacion) > 0

    def test_solo_el_completo_puede_auto_aprobar(self):
        """Con OCR, auto-aprobar exige RFC, subtotal Y fecha.

        Es la linea que hace que el descuento signifique algo. Sin subtotal no
        hay `subtotal + IVA == total` que verifique, y el unico dato que el OCR
        necesita acertar para fabricar un error financiero es el total. Sin
        subtotal no hay nada que lo verifique, asi que no cierra solo.
        """
        from app.core.enums import AUTO_APPROVE_CONFIDENCE

        assert confianza_por_campos_ocr(True, True, True) >= AUTO_APPROVE_CONFIDENCE
        assert confianza_por_campos_ocr(True, True, False) < AUTO_APPROVE_CONFIDENCE
        assert confianza_por_campos_ocr(False, True, True) < AUTO_APPROVE_CONFIDENCE
        assert confianza_por_campos_ocr(True, False, True) < AUTO_APPROVE_CONFIDENCE

    def test_el_uso_de_la_tabla_correcta_depende_del_origen(self):
        """El origen elige la tabla, y la eleccion esta en un solo punto.

        Si cada llamador eligiera, un quinto escalon podria marcar con la tabla
        de PDF lo que leyo de una foto, y el numero seria mas alto que el de un
        PDF con la misma evidencia.
        """
        from app.services.parser_service import _parse_receipt_text

        texto = TEXTO_OCR

        con_ocr = capture._marcar_por_reglas(
            _parse_receipt_text(texto), ConfidenceSource.OCR
        )
        con_pdf = capture._marcar_por_reglas(
            _parse_receipt_text(texto), ConfidenceSource.PDF_TEXT
        )

        assert con_ocr.confidence == confianza_por_campos_ocr(True, True, True)
        assert con_pdf.confidence == confianza_por_campos(True, True, True)
        assert con_ocr.confidence < con_pdf.confidence


class TestPisoDeCaracteresDelOcr:

    def test_el_piso_del_ocr_no_rechaza_un_ticket_minimo(self):
        """El piso tiene que caber DEBAJO del ticket mas chico que se acepta.

        Se puso en 120 y habia que rechazaba tickets reales: un comprobante
        minimo con proveedor, RFC, fecha, subtotal, IVA y total son 112
        caracteres. Con 120, cada foto de un ticket chico se iba a vision y
        pagaba un modelo por algo que el OCR leia bien.

        El numero de esta prueba es la MEDIDA del ticket minimo, no un ejemplo.
        Si se sube el piso, esta prueba falla y hay que volver a medir.
        """
        ticket_minimo = """OXXO SA de CV
RFC: XXXXX010101XXX
Fecha: 2025/03/15
Subtotal: 43.10
IVA (16%): 6.90
TOTAL: 50.00
"""
        assert len(ticket_minimo) > OCR_MIN_CHARS_PARA_INTENTAR, (
            f"un ticket minimo real son {len(ticket_minimo)} caracteres y el piso "
            f"es {OCR_MIN_CHARS_PARA_INTENTAR}: el piso lo rechazaria"
        )

    def test_el_piso_del_ocr_es_mas_alto_que_el_del_pdf(self):
        """60 contra 40, y no es un numero arbitrario.

        El OCR devuelve texto aunque la foto sea una pared, asi que el piso
        tiene que estar mas arriba que el del PDF. Con el mismo piso, una foto
        que no es un comprobante pasaria y se guardaria en la cola.
        """
        assert OCR_MIN_CHARS_PARA_INTENTAR > capture.PDF_MIN_CHARS_PARA_INTENTAR


class TestEscaneoDePdfSinTexto:
    async def test_un_pdf_escaneado_se_lee_con_ocr_primero(self, monkeypatch):
        """Un PDF sin capa de texto pasa por OCR antes de por vision.

        Un PDF escaneado es una foto dentro de un PDF: la misma decision que
        para una imagen suelta, con el mismo ahorro. Y el OCR se aplica sobre las
        PAGINAS RENDERIZADAS, que es lo que ya hacia vision, asi que no hay una
        segunda manera de convertir un PDF en imagen.
        """
        # Se parchea `capture.render_pdf_pages` y no
        # `parser_service.render_pdf_pages`: `capture` lo tiene en su namespace
        # desde el import, asi que parchear el modulo de origen no cambia la
        # referencia que la cascada usa de verdad.
        monkeypatch.setattr(capture, "extract_pdf_text", lambda _b: "   ")

        paginas = [b"\xff\xd8\xffpagina1", b"\xff\xd8\xffpagina2"]
        monkeypatch.setattr(capture, "render_pdf_pages", lambda *a, **k: paginas)

        vistas: list[bytes] = []

        def ocr(datos: bytes):
            vistas.append(datos)
            from app.services.ocr import ResultadoOCR

            return ResultadoOCR(texto=TEXTO_OCR, motor="falso")

        resultado = await capture_ticket(
            b"%PDF-1.7 escaneado", "pdf", ocr_reader=ocr,
            extract_from_image=_vision_que_falla,
        )

        assert len(vistas) == 2, "se intento OCR en cada pagina renderizada"
        assert resultado.provider_name == "Tiendas Ramirez SA de CV"
        assert resultado.total_amount == Decimal("1100.00")
        assert resultado.confidence_source is ConfidenceSource.OCR
