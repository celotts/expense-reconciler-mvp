"""El tipo de archivo lo dicen los BYTES, no el que declaro el cliente.

Por que este archivo existe
---------------------------

Los tres endpoints de comprobante reciben `file_type` como campo de formulario
y ese valor elegia el escalon de la cascada. Medido en este repo, con el mismo
PDF de las dos formas:

    file_type=pdf   ->  pdf_text  ->  confianza 0.97  ->  total 4094.80
    file_type=image ->  llm       ->  confianza None  ->  total 0.00

Tres cosas malas, en orden de gravedad:

1. **By-pass de la regla 3 de `AGENTS.md`.** "Un PDF con texto no toca el
   modelo" es la barrera conceptual mas importante del sistema, y la salta
   quien llama a la API. Sin el escalon de reglas, la inferencia se gasta en un
   PDF que las expresiones regulares ya resuelven.
2. **Contamina la medicion.** El ticket entra con `confidence_source=llm` y el
   reporte de exactitud agrupa por origen (`accuracy_service.py:346-357`). Una
   lectura que no fue lectura falsea la estadistica que sostiene el SLO.
3. **Amplificador de DoS.** Forzar vision sobre 10 MB obliga a renderizar,
   reescalar e inferir. Con un token valido, es la forma mas barata de quemar
   la maquina.

Y por que no se arregla con un 400
-----------------------------------

El documento es valido. Lo que estaba mal era la etiqueta. Devolver un error
haria que un contador perdiera su comprobante porque su cliente mando `image`
en vez de `pdf`: el fallo seria nuestro, no suyo. Se procesa igual, con el
formato deducido, y el motivo queda en el log.

Que se verifica
---------------

Que el `file_type` declarado deja de decidir, que las firmas funcionan, y que lo
que no se puede decodificar NO se manda al modelo.
"""

from __future__ import annotations

import io

import pytest

from app.core.archivo_real import (
    FORMATO_DESCONOCIDO,
    FORMATO_IMAGEN,
    FORMATO_PDF,
    FORMATO_TEXTO,
    TIPO_IMAGEN,
    TIPO_PDF,
    detectar_tipo_real,
    es_texto_plano,
    resolver_tipo,
    sniff_tipo,
)


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


class TestLasFirmasReconocenLoQueSon:
    def test_pdf(self):
        assert sniff_tipo(b"%PDF-1.7\nresto del archivo").formato == FORMATO_PDF

    def test_pdf_no_se_acepta_con_preambulo(self):
        """La especificacion pone %PDF en el byte 0, y el visor del sistema tambien."""
        assert sniff_tipo(b"basura%PDF-1.7").formato != FORMATO_PDF

    def test_png(self):
        assert sniff_tipo(_png()).formato == FORMATO_IMAGEN

    @pytest.mark.parametrize(
        "contenido",
        [
            b"\xff\xd8\xff\xe0JFIF",       # JPEG
            b"GIF89a....",                   # GIF
            b"II*\x00\x08\x00\x00\x00",     # TIFF little-endian
            b"MM\x00*\x00\x00\x00\x08",     # TIFF big-endian
        ],
    )
    def test_otros_formatos_de_imagen(self, contenido):
        assert sniff_tipo(contenido).formato == FORMATO_IMAGEN

    def test_heic_por_el_marca_ftyp(self):
        """Un iPhone produce HEIC. Sin esto, el producto no acepta su propia promesa."""
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1"
        assert sniff_tipo(heic).formato == FORMATO_IMAGEN

    def test_vacio_es_desconocido(self):
        assert sniff_tipo(b"").formato == FORMATO_DESCONOCIDO

    def test_basura_no_se_adivina_como_nada(self):
        for basura in (b"\x00" * 40, b"PK\x03\x04zip", b"#!/bin/sh\nrm -rf /"):
            assert sniff_tipo(basura).formato == FORMATO_DESCONOCIDO


class TestElClienteNoDecide:
    """El nucleo de la defensa."""

    def test_pdf_declarado_como_imagen_usa_pdf(self):
        """El ataque medido. Antes iba a vision y salia con origen `llm`."""
        pdf = b"%PDF-1.7\ncontenido del comprobante con texto suficiente"
        usado, motivo = resolver_tipo(pdf, "image")
        assert usado == FORMATO_PDF
        assert motivo is not None, "La correccion tiene que quedar registrada"

    def test_pdf_declarado_como_texto_usa_pdf(self):
        usado, _ = resolver_tipo(b"%PDF-1.7\nalgo", "text")
        assert usado == FORMATO_PDF

    def test_imagen_declarada_como_pdf_usa_imagen(self):
        """Y al reves: una foto no debe entrar al parser de PDF."""
        usado, motivo = resolver_tipo(_png(), "pdf")
        assert usado == FORMATO_IMAGEN
        assert motivo is not None

    def test_si_coincide_no_se_inventa_un_motivo(self):
        usado, motivo = resolver_tipo(b"%PDF-1.7\nalgo", "pdf")
        assert usado == FORMATO_PDF
        assert motivo is None

    def test_sin_declarar_manda_el_deducido(self):
        assert resolver_tipo(b"%PDF-1.7\nalgo", None)[0] == FORMATO_PDF

    def test_todos_los_valores_declarados_dan_el_mismo_resultado(self):
        """La propiedad que importa: la etiqueta no cambia el resultado.

        Es la forma de decirlo sin depender de una sola combinacion. Si esto
        pasa, el `file_type` que manda el cliente es decorativo.
        """
        pdf = b"%PDF-1.7\ncomprobante con texto suficiente para el parser"
        resultados = {resolver_tipo(pdf, ft)[0] for ft in ("pdf", "image", "text", None, "")}
        assert resultados == {FORMATO_PDF}, f"Las etiquetas cambiaron el resultado: {resultados}"


class TestLoDesconocidoNoSePierde:
    """Degradar, no fallar. Un contador pierde su comprobante si se rechaza."""

    def test_texto_plano_respeta_lo_declarado(self):
        """`factura_gas.txt` es un caso de uso real y no tiene numero magico."""
        assert resolver_tipo(b"PROVEEDOR SA DE CV\nTOTAL 1234.56\n", "text")[0] == FORMATO_TEXTO

    def test_desconocido_sin_declarar_asume_texto(self):
        usado, motivo = resolver_tipo(b"lo que sea", None)
        assert usado == FORMATO_TEXTO
        assert motivo is not None

    def test_vacio_con_declaracion_invalida_no_rompe(self):
        assert resolver_tipo(b"", "no-existe")[0] in (FORMATO_TEXTO, FORMATO_IMAGEN)


class TestNoSeMandaBasuraAlModelo:
    """La segunda defensa: una imagen ilegible no se infiere, se declara.

    Antes, `extract_from_image` envolvia el decode en `except: pass` y mandaba
    los bytes crudos en base64 declarados `image/jpeg`. Para un HEIC eso es un
    byte stream que vision no descodifica, y lo que salia era basura con
    apariencia de lectura.
    """

    def test_basura_no_se_convierte(self):
        from app.services.ai_extractor import _a_jpeg

        assert _a_jpeg(b"esto no es una imagen" * 50) is None

    def test_un_png_si_se_convierte(self):
        from app.services.ai_extractor import _a_jpeg

        salida = _a_jpeg(_png())
        assert salida is not None
        assert salida[:2] == b"\xff\xd8", "No es un JPEG"

    def test_reescala_a_1600(self):
        from PIL import Image

        from app.services.ai_extractor import _a_jpeg

        grande = io.BytesIO()
        Image.new("RGB", (4000, 3000), (1, 2, 3)).save(grande, format="PNG")
        salida = _a_jpeg(grande.getvalue())
        assert Image.open(io.BytesIO(salida)).width == 1600

    def test_heic_se_abre_si_la_dependencia_esta(self):
        """Si `pillow-heif` esta, un HEIC real tiene que convertirse.

        Se salta si la libreria no esta: el import es opcional a proposito, y
        un test que falla por una dependencia ausente no es un test del
        comportamiento, es un test del entorno.
        """
        from app.services import ai_extractor

        if not ai_extractor._HEIC_DISPONIBLE:
            pytest.skip("pillow-heif no instalada en este interprete")

        import pillow_heif

        # HEIF minimo: caja ftyp + una imagen minuscule. Pillow lo necesita
        # completo, asi que se construye con la propia libreria a partir de un
        # PNG, que es lo que haria un telefono.
        heic_buf = io.BytesIO()
        pillow_heif.from_pillow(_png_image(64, 64)).save(heic_buf, format="HEIF")
        assert ai_extractor._a_jpeg(heic_buf.getvalue()) is not None


def _png_image(ancho: int, alto: int):
    from PIL import Image

    img = Image.new("RGB", (ancho, alto), (240, 240, 235))
    return img


# ---------------------------------------------------------------------------
# Lo que el escaner de carpeta necesita encima
# ---------------------------------------------------------------------------
#
# Arriba se prueba que el `file_type` del cliente no decide. Esto prueba el otro
# lado: cuando NO hay nada que contrastar, porque el archivo viene de un
# directorio y lo unico que hay es su nombre en el disco, tambien manda lo que
# dicen los bytes. Un `ticket.pdf` que es un JPEG tiene que leerse como imagen.


class TestDetectarSinDeclaracion:
    """`detectar_tipo_real` es `sniff_tipo` sin la degradacion a imagen.

    La diferencia importa para el escaner: un `.zip` en la carpeta no es una foto
    borrosa, es un archivo que no se puede leer. Degradarlo a imagen produce un
    ticket de relleno en la cola de revision; `None` lo marca `NO_SOPORTADO`, que
    es un estado que el operador puede ver y actuar.
    """

    @pytest.mark.parametrize(
        "contenido,esperado",
        [
            (b"%PDF-1.7\nresto", TIPO_PDF),
            (b"\xff\xd8\xff\xe0\x00\x10JFIF", TIPO_IMAGEN),
            (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", TIPO_IMAGEN),
            (b"GIF89a\x01\x00\x01\x00", TIPO_IMAGEN),
            (b"BM\x8a\x00\x00\x00", TIPO_IMAGEN),
            (b"II*\x00\x08\x00\x00\x00", TIPO_IMAGEN),
            (b"MM\x00*\x00\x00\x00\x08", TIPO_IMAGEN),
            (b"RIFF\x24\x00\x00\x00WEBPVP8 ", TIPO_IMAGEN),
            (b"8BPS\x00\x01", TIPO_IMAGEN),
            # HEIC / AVIF: la caja `ftyp` va en el byte 4, no en el 0.
            (b"\x00\x00\x00\x18ftypheic", TIPO_IMAGEN),
            (b"\x00\x00\x00\x20ftypavif", TIPO_IMAGEN),
        ],
    )
    def test_reconoce_cada_firma(self, contenido, esperado):
        assert detectar_tipo_real(contenido) == esperado

    def test_webp_se_reconoce_en_el_offset_8(self):
        """WEBP es un contenedor RIFF: su marca no esta en el byte 0.

        Buscando `WEBP` en el 0, ningun WEBP se reconoceria nunca.
        """
        assert detectar_tipo_real(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == TIPO_IMAGEN

    def test_lo_desconocido_es_none_y_no_imagen(self):
        """Aqui `None` significa "no se sabe", y el escaner lo registra.

        `sniff_tipo` degrada a imagen a proposito, porque para un endpoint HTTP
        procesar de mas es mejor que rechazar. Un `.bin` de la carpeta no tiene
        esa justificacion.
        """
        escritorio = b"PK\x03\x04\x14\x00\x00\x00"
        assert detectar_tipo_real(escritorio) is None
        assert sniff_tipo(escritorio).formato == FORMATO_DESCONOCIDO
        assert sniff_tipo(escritorio).degradado_a_imagen is True

    def test_archivo_vacio_es_none(self):
        assert detectar_tipo_real(b"") is None

    def test_un_pdf_con_basura_delante_no_es_pdf(self):
        """`%PDF` tiene que estar en el byte 0.

        Aceptar con preambulo es aceptar un archivo manipulado, y es lo que haria
        que un `.bin` que empieza con `%PDF` fuera a pdfplumber.
        """
        assert detectar_tipo_real(b"basura%PDF-1.7") is None

    def test_solo_necesita_los_primeros_bytes(self):
        """La firma esta al principio; recorrer 5 MB no cambia el veredicto.

        El escaner pasa el archivo entero, pero decidir no debe costar leerlo
        todo: es lo que hace que una carpeta grande se pueda recorrer rapido.
        """
        enorme = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)
        assert detectar_tipo_real(enorme) == TIPO_IMAGEN


class TestLaExtensionNoManda:
    """El motivo de que `detectar_tipo_real` exista, en tres casos."""

    def test_un_jpeg_que_se_llama_pdf_es_imagen(self):
        """`foto.pdf` que es un JPEG: la cascada de PDF lo rompe.

        Con la extension como selector, este archivo se mandaria a pdfplumber,
        que no lo abre, y la cascada caeria a vision. El resultado suele ser
        correcto, pero se pago un modelo por algo que el OCR leia gratis, y el
        motivo del reintento queda invisible.
        """
        assert detectar_tipo_real(b"\xff\xd8\xff\xe0\x00\x10JFIF") == TIPO_IMAGEN

    def test_un_pdf_que_se_llama_jpg_es_pdf(self):
        """`ticket.jpg` que es un PDF: vision cuando el texto era exacto.

        Es el caso caro. Mandarlo a vision funciona, pero el documento tenia
        capa de texto y se leyo como si fuera una foto. Ademas queda registrado
        como lectura de modelo, y el reporte de exactitud atribuye al modelo
        algo que no leyo.
        """
        assert detectar_tipo_real(b"%PDF-1.7\n") == TIPO_PDF

    def test_un_texto_que_se_llame_png_no_es_imagen(self):
        """`nota.png` que es texto: no se manda a vision.

        Pasa con capturas pegadas con el nombre cambiado. Si se creyera en la
        extension, esto iria a un modelo de vision que devolveria basura.
        """
        texto = b"RFC: GODE561231GR8\nTOTAL: 250.00\n" * 3
        assert detectar_tipo_real(texto) is None
        assert es_texto_plano(texto) is True


class TestEsTextoPlano:
    def test_texto_utf8_si(self):
        assert es_texto_plano(b"RFC: GODE561231GR8\nTOTAL: 250.00\n") is True

    def test_binario_con_nulos_no(self):
        """Los nulos delatan un binario antes de intentar decodificar.

        Un `.xlsx` o un `.exe` empiezan a ser "texto" con `errors="replace"`, y de
        ahi el parser puede sacar un total. El byte nulo no se puede desambiguar
        con ningun reemplazo, asi que corta aqui.
        """
        assert es_texto_plano(b"PK\x03\x04\x00\x00\x00\x00") is False

    def test_utf8_invalido_no(self):
        # Bytes que no son UTF-8 valido y NO empiezan con un BOM de UTF-16, para
        # que la prueba mida lo que dice medir.
        assert es_texto_plano(b"rece\xff\xfeipt\x80\x81 malformed") is False

    def test_utf16_con_nulos_si(self):
        """UTF-16 legible tiene nulos en las posiciones alternas.

        Se reconoce el BOM ANTES de la regla de nulos. Si no, un ticket exportado
        en UTF-16 se declararia binario, y el orden de las dos comprobaciones es
        la diferencia entre leerlo y perderlo.
        """
        texto_utf16 = "RFC: GODE561231GR8\nTOTAL: 250.00".encode("utf-16")
        assert b"\x00" in texto_utf16  # la premisa del caso
        assert es_texto_plano(texto_utf16) is True

    def test_vacio_no(self):
        assert es_texto_plano(b"") is False

    def test_texto_con_muchos_caracteres_de_control_no(self):
        """Poca tinta visible y mucho control: no es un comprobante.

        El umbral del 5% es arbitrario, y por eso es un numero con nombre y no un
        0.5 escondido en una comparacion. Un ticket tiene lineas de texto y unos
        cuantos saltos de linea; un archivo que es casi todo control no tiene un
        total que leer.
        """
        assert es_texto_plano(b"\x01\x02\x03\x04\x05" * 100) is False
