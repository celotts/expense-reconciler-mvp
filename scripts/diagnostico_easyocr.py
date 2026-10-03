"""¿EasyOCR VE el total y el parser no lo entiende, o EasyOCR no lo ve?

Es la diferencia entre "el segundo motor no sirve" y "el segundo motor sirve y hay
que conectarlo bien". El script anterior solo comparaba el `total_amount` ya
parseado, y `0.00` no distingue entre las dos cosas.

Este vuelca el TEXTO CRUDO de EasyOCR para las fotos donde el total se sabe, y
busca el numero del papel dentro.

    python3 scripts/diagnostico_easyocr.py
"""

import pathlib
import sys
import time

import numpy as np

from app.services import ocr as OCR

# El numero que el papel dice, y una forma alternativa por si aparece con otro
# separador decimal.
VERDAD = {
    "1C2C52A2-648D-480D-84A6-D5211B5DF459.jpeg": ("51.50", ["51.50", "51,50", "5150"]),
    "IMG_4220.jpeg": ("97.56", ["97.56", "97,56", "9756"]),
    "IMG_4222.jpeg": ("48.00", ["48.00", "48,00", "4800"]),
}


def main() -> int:
    import easyocr

    t0 = time.time()
    lector = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
    print(f"lector listo en {time.time() - t0:.0f} s\n")

    for nombre, (papel, formas) in VERDAD.items():
        ruta = pathlib.Path("/tickets") / nombre
        if not ruta.is_file():
            continue
        gris = OCR._preparar(ruta.read_bytes())
        rgb = np.array(gris.convert("RGB"), dtype=np.uint8)
        t0 = time.time()
        lineas = lector.readtext(rgb, detail=1, paragraph=False)
        # Cada linea: [bbox, texto, confianza]
        texto = "\n".join(str(l[1]) for l in lineas if len(l) >= 2 and str(l[1]).strip())

        print("=" * 72)
        print(f"{nombre}   el papel dice {papel}   ({time.time() - t0:.0f} s)")
        print("-" * 72)

        # 1. ¿aparece el numero en algun lado del texto?
        for forma in formas:
            if forma in texto:
                print(f"  SI aparece {forma!r} en el texto crudo de EasyOCR")
                break
        else:
            print("  NO aparece ninguna forma del numero en el texto crudo")

        # 2. ¿hay lineas con algo de "total"?
        candidatas = [l for l in texto.splitlines() if "total" in l.lower()]
        print(f"  lineas con 'total': {candidatas!r}")

        # 3. ¿hay lineas que sean solo numeros? (el total puede venir suelto)
        import re
        numerosas = [l for l in texto.splitlines() if re.fullmatch(r"[\s$€]*[\d.,]+", l)]
        print(f"  lineas que son solo numeros: {numerosas[:12]!r}")

        # 4. Las confidencias de las palabras que contienen el numero, si esta.
        for i, linea in enumerate(lineas):
            if len(linea) < 3:
                continue
            pal = str(linea[1])
            for forma in formas:
                if forma in pal:
                    print(f"  palabra {pal!r} conf={linea[2]:.2f}")
                    break

        print(f"  texto completo ({len(texto)} chars):")
        for l in texto.splitlines()[:40]:
            print(f"      | {l}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())