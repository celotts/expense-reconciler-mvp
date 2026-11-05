"""Guarda de imagen vacía o casi-blanca en `_leer_tesseract`.

## Qué protege este archivo

Una imagen sin tinta (o con < 0.5% de píxeles oscuros) hace que Tesseract
ejecute hasta 16 llamadas —4 rotaciones × 2 PSMs alternativos × etapa de
binarización— antes de confirmar que no hay nada que leer. Medido: ~19 s por
imagen fallida.

La guarda detecta eso ANTES de llamar a Tesseract: si la fracción de píxeles
oscuros está por debajo del umbral, devuelve `ResultadoOCR` vacío en < 50 ms
sin tocar el motor.

Tres reglas que mueren si se quita la guarda (y por eso son los tres casos más
importantes):

1. Una imagen en blanco puro NO llama a `obtener_motor`.
2. Una imagen casi-blanca (0.4% de píxeles oscuros, < 0.5% de umbral) tampoco.
3. Una imagen con suficiente tinta (1%) SÍ llama al motor.

La regla 1 es la que protege contra el abuso de ~19 s por imagen vacía.
La regla 2 ajusta el umbral: una imagen con ruido mínimo no es un ticket.
La regla 3 garantiza que la guarda no sobre-dispara: una foto real sigue leyendo.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import ocr as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _imagen_sintetica(fraccion_oscuros: float, ancho: int = 100, alto: int = 100):
    """PIL Image en gris con la fracción pedida de píxeles oscuros (< 128).

    Sirve como entrada a la guarda sin necesitar bytes reales de ticket.
    Los valores están hardcodeados para que el test no dependa de la aritmética
    de redondeo de Pillow.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow y numpy necesarios para este test")

    n_oscuros = int(ancho * alto * fraccion_oscuros)
    datos = [0] * n_oscuros + [255] * (ancho * alto - n_oscuros)
    arr = np.array(datos, dtype=np.uint8).reshape((alto, ancho))
    return Image.fromarray(arr, mode="L")


# ---------------------------------------------------------------------------
# Tests de la guarda
# ---------------------------------------------------------------------------


class TestGuardaImagenVaciaOCR:
    """La guarda de imagen vacía no llama a Tesseract."""

    def test_imagen_completamente_blanca_no_llama_al_motor(self):
        """0% de píxeles oscuros → early-exit antes de `obtener_motor`."""
        imagen = _imagen_sintetica(fraccion_oscuros=0.0)

        llamadas = []

        def _mock_obtener_motor(nombre="tesseract"):
            llamadas.append(nombre)
            raise AssertionError("obtener_motor no deberia haberse llamado")

        with patch.object(mod, "obtener_motor", _mock_obtener_motor):
            with patch.object(mod, "_preparar", return_value=imagen):
                resultado = mod._leer_tesseract(b"bytes-de-prueba")

        assert llamadas == [], "La guarda debe cortarse antes de pedir el motor"
        assert resultado.texto == ""
        assert resultado.motor == "tesseract"
        assert resultado.confianza_media is None

    def test_imagen_casi_blanca_no_llama_al_motor(self):
        """0.4% de píxeles oscuros (< umbral 0.5%) → early-exit."""
        imagen = _imagen_sintetica(fraccion_oscuros=0.004)  # 0.4%

        llamadas = []

        def _mock_obtener_motor(nombre="tesseract"):
            llamadas.append(nombre)
            raise AssertionError("obtener_motor no deberia haberse llamado")

        with patch.object(mod, "obtener_motor", _mock_obtener_motor):
            with patch.object(mod, "_preparar", return_value=imagen):
                resultado = mod._leer_tesseract(b"bytes-de-prueba")

        assert llamadas == []
        assert resultado.texto == ""

    def test_imagen_con_tinta_suficiente_pasa_al_motor(self):
        """1% de píxeles oscuros (≥ umbral 0.5%) → la guarda NO dispara."""
        imagen = _imagen_sintetica(fraccion_oscuros=0.01)  # 1%

        # Motor falso que responde texto vacío pero SÍ se llama.
        llamadas = []

        class MotorFalso:
            class Output:
                DICT = "dict"

            def image_to_data(self, *args, **kwargs):
                llamadas.append("image_to_data")
                return {
                    "text": [],
                    "conf": [],
                    "block_num": [],
                    "par_num": [],
                    "line_num": [],
                    "left": [],
                    "top": [],
                    "width": [],
                    "height": [],
                }

        with patch.object(mod, "obtener_motor", return_value=MotorFalso()):
            with patch.object(mod, "_preparar", return_value=imagen):
                mod._leer_tesseract(b"bytes-de-prueba")

        assert len(llamadas) >= 1, "Con suficiente tinta el motor debe llamarse"

    def test_sin_numpy_la_guarda_se_salta_sin_error(self):
        """Si numpy no está instalado, la guarda falla silenciosamente y continúa."""
        imagen = _imagen_sintetica(fraccion_oscuros=0.0)

        llamadas_motor = []

        class MotorFalso:
            class Output:
                DICT = "dict"

            def image_to_data(self, *args, **kwargs):
                llamadas_motor.append("ok")
                return {
                    "text": [],
                    "conf": [],
                    "block_num": [],
                    "par_num": [],
                    "line_num": [],
                    "left": [],
                    "top": [],
                    "width": [],
                    "height": [],
                }

        # Simular ImportError de numpy dentro de la guarda.
        import builtins

        real_import = builtins.__import__

        def _import_sin_numpy(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("numpy simulado ausente")
            return real_import(name, *args, **kwargs)

        with patch.object(mod, "obtener_motor", return_value=MotorFalso()):
            with patch.object(mod, "_preparar", return_value=imagen):
                with patch("builtins.__import__", side_effect=_import_sin_numpy):
                    # NO debe lanzar excepción: la guarda se salta y se continúa.
                    mod._leer_tesseract(b"bytes-de-prueba")

        # El motor SI se llamó porque la guarda falló silenciosamente.
        assert llamadas_motor, "Sin numpy la guarda se salta y el motor continúa"

    def test_con_bytes_crudos_en_lugar_de_imagen_la_guarda_no_aplica(self):
        """Si `_preparar` devuelve bytes (Pillow no disponible), la guarda se salta."""
        # `hasattr(bytes, 'convert')` = False → la guarda no corre.
        bytes_crudos = b"\xff\xd8\xff"  # cabecera JPEG sin procesar

        llamadas_motor = []

        class MotorFalso:
            class Output:
                DICT = "dict"

            def image_to_data(self, *args, **kwargs):
                llamadas_motor.append("ok")
                return {
                    "text": [],
                    "conf": [],
                    "block_num": [],
                    "par_num": [],
                    "line_num": [],
                    "left": [],
                    "top": [],
                    "width": [],
                    "height": [],
                }

        with patch.object(mod, "obtener_motor", return_value=MotorFalso()):
            with patch.object(mod, "_preparar", return_value=bytes_crudos):
                mod._leer_tesseract(b"bytes-de-prueba")

        assert llamadas_motor, (
            "Con bytes crudos la guarda no aplica y el motor continua"
        )

    def test_resultado_vacio_no_pasa_el_gate(self):
        """El ResultadoOCR vacío produce puntuación 0, que el gate marca como pendiente.

        Esta regla garantiza que la guarda no 'silencia' un comprobante real:
        el ticket va a la cola con motivo visible, igual que si Tesseract hubiera
        leído y no encontrado nada.
        """
        from app.services.ocr import ResultadoOCR

        resultado = ResultadoOCR(texto="", motor="tesseract", confianza_media=None)
        assert resultado.texto == ""
        # `_puntuar("", None)` = 0, que está por debajo del corte de _es_buena (70)
        # y por debajo de 20, que es el umbral de "ninguna lectura producida".
        puntos = mod._puntuar(resultado.texto, resultado.confianza_media)
        assert puntos == 0
        assert not mod._es_buena(
            mod.Lectura(texto="", confianza=None, rotacion=0, puntos=puntos)
        )
