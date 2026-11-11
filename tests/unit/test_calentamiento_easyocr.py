"""`GET /scan/ocr` ligero y calentamiento de EasyOCR en segundo plano.

Abrir el Scanner tardaba 30-60 s la primera vez porque el endpoint de estado
construia el `easyocr.Reader` dentro del handler, y el front esperaba esa
llamada antes de pintar el formulario. La correccion separa preguntar de
cargar: `disponibilidad_ligera()` responde sin construir nada, y el Reader se
carga en un hilo aparte que arranca con ese mismo GET.

Lo que estos tests fijan:

- Preguntar el estado NO construye EasyOCR, ni aunque este instalado.
- El calentamiento arranca una sola vez, aunque el GET se repita.
- Un calentamiento que falla no tumba nada y el proximo GET lo reintenta.
- El endpoint arranca el calentamiento y devuelve lo ligero.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.api.scans import estado_del_ocr
from app.services import ocr as ocr_modulo


def _usuario():
    return SimpleNamespace(email="ana@test.local")


def _esperar_hasta(condicion, timeout_segundos=5.0):
    """Espera a que `condicion()` sea verdad, sin dormir de mas."""
    inicio = time.monotonic()
    while not condicion():
        if time.monotonic() - inicio > timeout_segundos:
            return False
        time.sleep(0.05)
    return True


def test_disponibilidad_ligera_no_construye_easyocr(monkeypatch):
    """Preguntar el estado no construye el Reader: eso era lo que hacia lento abrir el Scanner."""

    def _prohibido(*_args, **_kwargs):
        raise AssertionError("disponibilidad_ligera no debe construir EasyOCR")

    monkeypatch.setattr(ocr_modulo, "_motor_easyocr", _prohibido)
    monkeypatch.setattr(ocr_modulo, "_motor_tesseract", lambda: object())
    monkeypatch.setattr(ocr_modulo, "_CACHES", {})

    estado = ocr_modulo.disponibilidad_ligera()

    assert estado["motores"]["easyocr"]["cargando"] is False
    assert estado["motores"]["easyocr"]["disponible"] is False
    assert estado["motores"]["tesseract"]["disponible"] is True
    assert "easyocr" not in ocr_modulo._CACHES


def test_calentamiento_arranca_una_sola_vez(monkeypatch):
    """Cada `GET /scan/ocr` lo llama, pero solo el primero lanza el hilo."""
    monkeypatch.setattr(ocr_modulo, "_CACHES", {})
    monkeypatch.setitem(ocr_modulo._CALENTAMIENTO_EASYOCR, "en_curso", False)
    monkeypatch.setitem(ocr_modulo._CALENTAMIENTO_EASYOCR, "error", None)

    llamadas: list[int] = []

    def _falso_motor():
        llamadas.append(1)
        time.sleep(0.05)
        ocr_modulo._CACHES["easyocr"] = object()
        return ocr_modulo._CACHES["easyocr"]

    monkeypatch.setattr(ocr_modulo, "_motor_easyocr", _falso_motor)

    ocr_modulo.iniciar_calentamiento_easyocr()
    ocr_modulo.iniciar_calentamiento_easyocr()
    ocr_modulo.iniciar_calentamiento_easyocr()

    assert _esperar_hasta(lambda: "easyocr" in ocr_modulo._CACHES)
    assert len(llamadas) == 1

    estado = ocr_modulo._estado_easyocr_ligero()
    assert estado["disponible"] is True
    assert estado["cargado"] is True
    assert estado["cargando"] is False


def test_calentamiento_que_falla_no_tumba_nada_y_se_reintenta(monkeypatch):
    """Si la carga falla, el error queda registrado y el proximo GET lo reintenta."""
    monkeypatch.setattr(ocr_modulo, "_CACHES", {})
    monkeypatch.setitem(ocr_modulo._CALENTAMIENTO_EASYOCR, "en_curso", False)
    monkeypatch.setitem(ocr_modulo._CALENTAMIENTO_EASYOCR, "error", None)

    def _falla():
        raise RuntimeError("torch roto")

    monkeypatch.setattr(ocr_modulo, "_motor_easyocr", _falla)

    ocr_modulo.iniciar_calentamiento_easyocr()

    # Esperar a que el hilo TERMINE (error registrado Y flag bajado): el
    # error se escribe en el `except` y el flag en el `finally`, asi que
    # esperar solo al error seria mirar antes de que el hilo salga.
    assert _esperar_hasta(
        lambda: ocr_modulo._CALENTAMIENTO_EASYOCR["error"] is not None
        and ocr_modulo._CALENTAMIENTO_EASYOCR["en_curso"] is False
    )
    assert "torch roto" in str(ocr_modulo._CALENTAMIENTO_EASYOCR["error"])
    assert ocr_modulo._CALENTAMIENTO_EASYOCR["en_curso"] is False

    estado = ocr_modulo._estado_easyocr_ligero()
    assert estado["disponible"] is False
    assert estado["cargando"] is False

    # El proximo GET lo reintenta: al arrancar, el error anterior se limpia.
    llamadas: list[int] = []

    def _ahora_si():
        llamadas.append(1)
        return object()

    monkeypatch.setattr(ocr_modulo, "_motor_easyocr", _ahora_si)
    ocr_modulo.iniciar_calentamiento_easyocr()

    assert ocr_modulo._CALENTAMIENTO_EASYOCR["error"] is None
    assert _esperar_hasta(lambda: len(llamadas) == 1)


async def test_get_scan_ocr_arranca_el_calentamiento_y_devuelve_lo_ligero(monkeypatch):
    """El endpoint arranca el calentamiento y responde sin construir el motor."""
    from app.api import scans as scans_modulo

    llamadas: list[int] = []
    monkeypatch.setattr(
        scans_modulo, "iniciar_calentamiento_easyocr", lambda: llamadas.append(1)
    )
    respuesta = {
        "motores": {"easyocr": {"disponible": False, "cargando": True}},
    }
    monkeypatch.setattr(
        scans_modulo, "disponibilidad_ligera", lambda: respuesta
    )

    assert await estado_del_ocr(_usuario()) is respuesta
    assert llamadas == [1]
