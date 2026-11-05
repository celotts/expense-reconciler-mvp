"""Mide el ensamblado de lineas de EasyOCR contra la verdad conocida.

La pregunta: con las lineas reconstruidas, ¿el parser por reglas encuentra el
total que EasyOCR si lee? Antes del ensamblado devolvia 0.00 en las tres.

    docker run --rm --memory 5g -v "$PWD:/app" \
        -v "$HOME/Documents/Tickets/Tickets_app:/tickets:ro" -w /app \
        expense-ocr-lab:local python scripts/medir_ensamblado.py
"""

import pathlib
import sys
import time

from app.services import ocr as OCR
from app.services.parser_service import _parse_receipt_text

VERDAD = {
    "1C2C52A2-648D-480D-84A6-D5211B5DF459.jpeg": ("51.50", "2026-09-19"),
    "IMG_4220.jpeg": ("97.56", "2026-09-28"),
    "IMG_4222.jpeg": ("48.00", "2026-10-01"),
}


def main() -> int:
    print(f"{'archivo':28} {'papel':>8} {'easyocr':>10} {'tesseract':>10}  aciertos")
    aciertos_e = aciertos_t = 0
    for nombre, (papel, fecha) in VERDAD.items():
        ruta = pathlib.Path("/tickets") / nombre
        if not ruta.is_file():
            continue
        datos = ruta.read_bytes()

        t0 = time.time()
        leido = OCR.leer_imagen(datos, motor="easyocr")
        par = _parse_receipt_text(leido.texto)
        seg_e = time.time() - t0

        t0 = time.time()
        leido_t = OCR.leer_imagen(datos, motor="tesseract")
        par_t = _parse_receipt_text(leido_t.texto)
        seg_t = time.time() - t0

        ok_e = par.total_amount == float(papel.replace(",", ".")) or str(par.total_amount) == papel
        ok_t = str(par_t.total_amount) == papel
        aciertos_e += ok_e
        aciertos_t += ok_t
        print(
            f"{nombre[:28]:28} {papel:>8} {str(par.total_amount):>10} {str(par_t.total_amount):>10}"
            f"  easyocr={ok_e} tesseract={ok_t}   ({seg_e:.0f}s / {seg_t:.0f}s)"
        )
        print(f"      fecha easyocr={par.expense_date} (papel {fecha}) | tesseract={par_t.expense_date}")

    print(f"\naciertos easyocr: {aciertos_e}/{len(VERDAD)}   tesseract: {aciertos_t}/{len(VERDAD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())