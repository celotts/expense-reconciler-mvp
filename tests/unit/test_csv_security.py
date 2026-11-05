"""Que el import de CSV no sea un vector de abuso de codecs ni filtre errores."""

from __future__ import annotations

import io
import pytest

from app.services.parser_service import parse_bank_csv


CSV_BASE = b"""fecha,importe,concepto
15/01/2025,1100.00,OXXO
"""


class TestEncodingAllowlist:
    """Solo utf-8, latin-1, cp1252, iso-8859-1. El resto: 400 claro."""

    def test_utf_8_pasa(self):
        parse_bank_csv(CSV_BASE, encoding="utf-8")

    def test_latin_1_pasa(self):
        parse_bank_csv(CSV_BASE, encoding="latin-1")

    def test_cp1252_pasa(self):
        parse_bank_csv(CSV_BASE, encoding="cp1252")

    def test_iso_8859_1_pasa(self):
        parse_bank_csv(CSV_BASE, encoding="iso-8859-1")

    def test_utf_7_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="utf-7")

    def test_utf_16_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="utf-16")

    def test_zlib_codec_rechazado(self):
        """zlib_codec descomprime en memoria -> bomba de descompresion."""
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="zlib_codec")

    def test_bz2_codec_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="bz2_codec")

    def test_raw_unicode_escape_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="raw_unicode_escape")

    def test_unicode_escape_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="unicode_escape")

    def test_rot_13_rechazado(self):
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="rot_13")

    def test_cualquier_otra_rechazada(self):
        """La lista es cerrada: lo que no esta en el set, fuera."""
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="ascii")

    def test_case_insensitive(self):
        """utf-8, UTF-8, UtF-8 todos validos; UTF-7, UtF-7 invalidos."""
        parse_bank_csv(CSV_BASE, encoding="UTF-8")
        parse_bank_csv(CSV_BASE, encoding="Utf-8")
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="UTF-7")


class TestNoReflectionDeErrores:
    """El mensaje que ve el cliente es generico; los detalles quedan en el log."""

    def test_fila_invalida_no_vuelca_la_fila(self):
        """El mensaje no incluye el contenido de la fila completa."""
        csv = b"""fecha,importe,concepto
no-fecha,100.00,TEST
"""
        try:
            parse_bank_csv(csv, date_format="%d/%m/%Y")
        except ValueError as e:
            msg = str(e)
            assert "Error en el formato del CSV" in msg
            assert "no-fecha" not in msg, (
                f"la fila se volco en el mensaje: {msg}"
            )
            assert "100.00" not in msg, f"el importe se volco: {msg}"
            assert "TEST" not in msg, f"el concepto se volco: {msg}"

    def test_error_interno_no_se_refleja(self):
        """Si el parser lanza algo inesperado, el cliente no ve el traceback.

        Forzamos un error en la conversion de importe: un valor que no es
        numero. El parser lo atrapa y devuelve el mensaje generico.
        """
        csv = b"""fecha,importe,concepto
15/01/2025,no-es-numero,TEST
"""
        try:
            parse_bank_csv(csv)
        except ValueError as e:
            msg = str(e)
            assert "Error en el formato del CSV" in msg
            assert "no-es-numero" not in msg
            assert "pandas" not in msg.lower()
            assert "ValueError" not in msg
            assert "could not convert" not in msg.lower()

    def test_mensaje_codificacion_no_permitida_no_filtra_nada(self):
        """El mensaje de encoding invalido dice cual era, pero no el contenido."""
        with pytest.raises(ValueError, match="Codificacion no permitida") as exc:
            parse_bank_csv(CSV_BASE, encoding="utf-7")
        msg = str(exc.value)
        assert "utf-7" in msg.lower()
        assert "OXXO" not in msg
        assert "1100" not in msg


class TestMutacionEncodingAllowlist:
    """Si quitas el allowlist, estas pruebas fallan."""

    def test_si_quito_el_allowlist_utf_7_pasa_y_ejecuta_xss(self):
        """MUTACION: comenta el `if encoding.lower() not in ENCODINGS_PERMITIDOS`.

        Si el allowlist desaparece, utf-7 se acepta y convierte secuencias
        como +ADw-script+AD4- en <script>. El test de arriba espera que
        utf-7 sea rechazado; sin el allowlist, este test falla.
        """
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="utf-7")

    def test_si_quito_el_allowlist_zlib_pasa_y_descomprime(self):
        """MUTACION: zlib_codec no debe pasar."""
        with pytest.raises(ValueError, match="Codificacion no permitida"):
            parse_bank_csv(CSV_BASE, encoding="zlib_codec")


class TestMutacionNoReflection:
    """Si vuelves a reflejar el error interno, estas pruebas fallan."""

    def test_si_reflejas_la_excepcion_interna_falla(self):
        """MUTACION: cambia `except Exception:` por `except Exception as e:` y
        `raise ValueError(f\"Error: {e}\")` en el parser.

        Si se refleja el error interno, el mensaje incluirá detalles de
        pandas y esta prueba fallara porque el mensaje ya no es el generico.
        """
        csv = b"""fecha,importe,concepto
no-fecha,100.00,TEST
"""
        try:
            parse_bank_csv(csv, date_format="%d/%m/%Y")
        except ValueError as e:
            msg = str(e)
            assert "Error en el formato del CSV" in msg
            assert "pandas" not in msg.lower()
            assert "ValueError" not in msg
            assert "day is out of range" not in msg