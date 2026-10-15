"""¿Dos motores juntos leen las cifras que Tesseract solo falla?

La pregunta es una sola y es binaria: en la foto de Oxxo el papel dice 51.50 y
Tesseract lee 31.50 con confianza 93. Un segundo motor que lo lea bien demuestra
que la lectura se puede corroborar; uno que lea otra cosa distinto confirma que
el problema es el papel, no el motor.

Este script NO decide nada por su cuenta: imprime lo que leyo cada motor y lo
compara con la verdad. La politica ("si coinciden se afirma, si no a revision")
es una decision de producto y se implementa aparte, en la ruta de captura.
"""

import io
import pathlib
import sys
import time
from decimal import Decimal

import pytesseract
from PIL import Image, ImageOps

from app.services import ocr as OCR
from app.services.parser_service import _parse_receipt_text

# La verdad, rotulada a mano mirando el papel. NO la saca el sistema: si la
# verdad la pone el OCR, el script mide que el OCR coincide consigo mismo.
VERDAD = {
    "1C2C52A2-648D-480D-84A6-D5211B5DF459.jpeg": {
        "total": Decimal("51.50"),
        "proveedor": "Cadena Comercial Oxxo, S.A. de C.V.",
    },
    "IMG_4220.jpeg": {
        "total": Decimal("97.56"),
        "proveedor": "CMT QUERETARO REVOLUCION",
    },
    "IMG_4222.jpeg": {
        "total": Decimal("48.00"),
        "proveedor": "MERCASTAR",
    },
}


def con_tesseract(datos: bytes) -> tuple[str, float | None]:
    r = OCR.leer_imagen(datos)
    return r.texto, r.confianza_media


def con_easyocr(datos: bytes) -> str:
    import easyocr

    global _LECTOR
    try:
        _LECTOR
    except NameError:
        t0 = time.time()
        print("    (cargando EasyOCR; la primera vez descarga ~100 MB de pesos)")
        _LECTOR = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
        print(f"    EasyOCR cargado en {time.time() - t0:.0f} s")

    # EasyOCR NO acepta una imagen de Pillow: acepta ruta, bytes o numpy. Es el
    # mismo bug que tiene `ocr.py:_leer_easyocr`, que hoy no funciona (esta
    # medido en el reporte; ver el `ValueError` que tira).
    #
    # Se pasa por `OCR._preparar` a proposito: es el MISMO preprocesado que
    # Tesseract, incluido el exif_transpose. Si EasyOCR recibiera la foto cruda,
    # comparariamos dos motores con entradas distintas y el resultado no diria
    # nada sobre el motor.
    import numpy as np

    gris = OCR._preparar(datos)
    rgb = np.array(gris.convert("RGB"), dtype=np.uint8)
    lineas = _LECTOR.readtext(rgb, detail=1, paragraph=False)
    return "\n".join(str(l[1]) for l in lineas if len(l) >= 2 and str(l[1]).strip())


def main() -> int:
    carpeta = pathlib.Path("/tickets")
    filas = []
    for nombre, verdad in VERDAD.items():
        ruta = carpeta / nombre
        if not ruta.is_file():
            print(f"no esta {ruta}")
            continue
        datos = ruta.read_bytes()
        print("=" * 74)
        print(nombre, f"| el papel dice {verdad['total']}")

        t0 = time.time()
        texto_t, conf = con_tesseract(datos)
        par_t = _parse_receipt_text(texto_t)
        print(f"  tesseract  {time.time() - t0:5.1f}s  conf={conf}")
        print(f"      total leido = {par_t.total_amount}   acierta={par_t.total_amount == verdad['total']}")
        print(f"      proveedor  = {par_t.provider_name!r}")

        try:
            t0 = time.time()
            texto_e = con_easyocr(datos)
            par_e = _parse_receipt_text(texto_e)
        except Exception as exc:  # noqa: BLE001
            print(f"  easyocr    NO SE PUDO: {exc}")
            continue

        print(f"  easyocr    {time.time() - t0:5.1f}s")
        print(f"      total leido = {par_e.total_amount}   acierta={par_e.total_amount == verdad['total']}")
        print(f"      proveedor  = {par_e.provider_name!r}")

        coinciden = par_t.total_amount == par_e.total_amount and par_t.total_amount > 0
        correcto = par_t.total_amount == verdad['total']
        print(f"      >> los dos motores coinciden: {coinciden} | el valor es el del papel: {correcto}")
        filas.append((nombre, str(verdad["total"]), str(par_t.total_amount), str(par_e.total_amount), coinciden, correcto))

    print("=" * 74)
    print(f"\n{'archivo':26} {'papel':>8} {'tesseract':>10} {'easyocr':>10}  coinciden  correcto")
    for f in filas:
        print(f"{f[0][:26]:26} {f[1]:>8} {f[2]:>10} {f[3]:>10}  {str(f[4]):>9}  {str(f[5]):>8}")

    ok_t = sum(1 for f in filas if f[2] == f[1])
    ok_e = sum(1 for f in filas if f[3] == f[1])
    ambos_ok = sum(1 for f in filas if f[4] and f[5])
    print(f"\naciertos tesseract: {ok_t}/{len(filas)}   easyocr: {ok_e}/{len(filas)}")
    print(f"casos donde AMBOS aciertan y ademas coinciden entre si: {ambos_ok}/{len(filas)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())