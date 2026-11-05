"""Mide en que se va el tiempo de digitalizar una carpeta de comprobantes.

No es parte del producto: es una herramienta de diagnostico. No reimplementa la
cascada, la **mide**: envuelve las funciones reales (`_una_lectura` y
`_desde_imagen_con_vision`) con cronometros y deja que el codigo de produccion
corra tal cual.

Se ejecuta DENTRO del contenedor (necesita tesseract y las mismas librerias):

    docker compose exec expense-api python scripts/medir_tiempo_digitalizacion.py /tickets
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

from app.core import archivo_real  # noqa: E402
from app.services import capture, ocr  # noqa: E402

# Ojo: `ai_extractor` es la INSTANCIA de `AIExtractor`, no el modulo. Asi la
# importa `scan_service`.
from app.services.ai_extractor import ai_extractor  # noqa: E402

SUFIJOS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".txt", ".webp", ".tif", ".tiff"}

# El cronometro actual: `time.perf_counter` original de la etapa que se mide.
_real_una_lectura = ocr._una_lectura
_marca: dict = {}


def _una_lectura_cronometrada(*a, **kw):
    t = time.perf_counter()
    try:
        return _real_una_lectura(*a, **kw)
    finally:
        _marca["tesseract"] = _marca.get("tesseract", 0.0) + (time.perf_counter() - t)
        _marca["lecturas"] = _marca.get("lecturas", 0) + 1


_real_vision = capture._desde_imagen_con_vision


async def _vision_cronometrada(*a, **kw):
    t = time.perf_counter()
    try:
        return await _real_vision(*a, **kw)
    finally:
        _marca["vision"] = _marca.get("vision", 0.0) + (time.perf_counter() - t)
        _marca["uso_vision"] = True


ocr._una_lectura = _una_lectura_cronometrada
capture._una_lectura = _una_lectura_cronometrada
capture._desde_imagen_con_vision = _vision_cronometrada


async def medir_un(ruta: Path) -> dict:
    datos = ruta.read_bytes()
    _marca.clear()
    _marca.update({"tesseract": 0.0, "vision": 0.0, "lecturas": 0, "uso_vision": False})

    # El tipo lo dicen los BYTES, no la extension (regla 3 del AGENTS.md): es la
    # misma funcion que usa el escaner.
    file_type = archivo_real.detectar_tipo_real(datos)

    t0 = time.perf_counter()
    # Los mismos extractores que pasa `scan_service.procesar_archivo`: sin ellos
    # la cascada no tiene con quien escalar a vision y acortaria el camino.
    r = await capture.capture_ticket(
        datos,
        file_type=file_type,
        extract_from_image=ai_extractor.extract_from_image,
        extract_from_text=ai_extractor.extract_from_text,
    )
    total = time.perf_counter() - t0

    return {
        "nombre": ruta.name,
        "tipo": file_type,
        "total_s": total,
        "tesseract": _marca["tesseract"],
        "vision": _marca["vision"],
        "lecturas": _marca["lecturas"],
        "uso_vision": _marca["uso_vision"],
        "status": r.confidence_source.value if r.confidence_source else "?",
        "conf": round(float(r.confidence or 0), 2),
        "proveedor": (r.provider_name or "")[:26],
        "total_amount": str(r.total_amount),
        "items": "si" if r.items else "NO",
    }


async def main() -> None:
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "/tickets")
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in SUFIJOS)

    s = capture.settings
    print(f"{len(archivos)} archivos en {carpeta}", flush=True)
    print(f"ESCALAR_A_IA_SIN_LINEAS={s.ESCALAR_A_IA_SIN_LINEAS}  OCR_PSM={s.OCR_PSM}  "
          f"AI_ENABLED={s.AI_ENABLED}  MAX_LADO_VISION={s.MAX_LADO_VISION}", flush=True)
    print(f"OLLAMA_VISION={s.OLLAMA_VISION_MODEL}  KEEP_ALIVE={s.OLLAMA_KEEP_ALIVE}", flush=True)
    print("-" * 100, flush=True)

    filas = []
    t_all = time.perf_counter()
    for ruta in archivos:
        r = await medir_un(ruta)
        filas.append(r)
        print(
            f"{r['nombre'][:20]:20} {str(r['tipo']):5} {r['total_s']:6.1f}s "
            f"lect={r['lecturas']:2d} tess={r['tesseract']:5.1f}s "
            f"VIS={r['vision']:5.1f}s{'*' if r['uso_vision'] else ' '} "
            f"{r['status']:16} {r['conf']:4} tot={r['total_amount']:>10} "
            f"items={r['items']:3} {r['proveedor']}",
            flush=True,
        )
    total_s = time.perf_counter() - t_all

    tess = sum(f["tesseract"] for f in filas)
    vis = sum(f["vision"] for f in filas)
    print("-" * 100, flush=True)
    print(f"TOTAL: {total_s:.1f}s para {len(archivos)} archivos "
          f"({total_s / max(len(archivos), 1):.1f}s por archivo)", flush=True)
    print(f"  tesseract   {tess:7.1f}s  ({tess / total_s * 100:4.1f}%)  "
          f"{sum(f['lecturas'] for f in filas)} lecturas", flush=True)
    print(f"  VISION      {vis:7.1f}s  ({vis / total_s * 100:4.1f}%)  "
          f"en {sum(f['uso_vision'] for f in filas)} de {len(archivos)} archivos", flush=True)
    print(f"  otros       {total_s - tess - vis:7.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())