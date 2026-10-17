"""Cuantos de los comprobantes de una carpeta PAGAN una llamada al modelo.

La cascada sube a vision cuando `_es_extraccion_util` es False (no hay
proveedor, el total no es positivo, o el nombre parece basura). Este script
NO llama al modelo: solo corre el OCR y contesta, por archivo, si la cascada
escalaría o no. Sirve para saber cuantas llamadas de ~60s se van a pagar.

    docker compose exec expense-api python scripts/cuantos_escalan.py /tickets
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

from app.core import archivo_real  # noqa: E402
from app.services import capture, ocr  # noqa: E402

SUFIJOS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".txt", ".webp"}


def main() -> None:
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "/tickets")
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in SUFIJOS)

    print(f"{len(archivos)} archivos   ESCALAR_A_IA_SIN_LINEAS="
          f"{capture.settings.ESCALAR_A_IA_SIN_LINEAS}")
    print("-" * 96)

    escalan = 0
    t_ocr = 0.0
    for ruta in archivos:
        datos = ruta.read_bytes()
        t0 = time.perf_counter()
        try:
            leido = ocr.leer_imagen(datos)
            texto = (leido.texto or "").strip()
            r = capture._marcar_por_reglas(
                capture._parse_receipt_text(texto), capture.ConfidenceSource.OCR
            )
            util = capture._es_extraccion_util(r)
            basura = capture._nombre_parece_basura(r.provider_name)
            motivo = ""
            if r.provider_name == capture.UNKNOWN_PROVIDER:
                motivo = "proveedor=UNKNOWN"
            elif r.total_amount <= 0:
                motivo = f"total={r.total_amount}"
            elif basura:
                motivo = "nombre parece basura"
        except Exception as exc:
            texto, util, motivo = "", False, f"ERROR {exc}"
            r = None
        dt = time.perf_counter() - t0
        t_ocr += dt

        if not util:
            escalan += 1
        print(
            f"{ruta.name[:22]:22} {dt:5.1f}s esc={'SI' if not util else 'no ':3} "
            f"chars={len(texto):5} tot={str(r.total_amount) if r else '?':>9} "
            f"prov={(r.provider_name if r else '')[:22]:22} {motivo}",
            flush=True,
        )

    print("-" * 96)
    print(f"ESCALAN A VISION: {escalan} de {len(archivos)}")
    print(f"tiempo de OCR de los {len(archivos)} archivos: {t_ocr:.1f}s")


if __name__ == "__main__":
    main()