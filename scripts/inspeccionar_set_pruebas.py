#!/usr/bin/env python3
"""Mide que lee el API de cada archivo del set de pruebas.

No es un test: es una herramienta para responder "¿que hace este archivo?" sin
tener que subirlo a la API ni mirar la base. Muestra tres cosas que son
preguntas distintas:

* `bytes` — el formato que detecto `archivo_real` por la FIRMA, no por el nombre.
* `lector` — la clase de lector que gano la cascada (`pdf_text`, `ocr`, `xml`).
* `total` / `proveedor` / `fecha` — lo que el sistema afirmo que dice el papel.

Por omision apaga OCR y modelo para medir SOLO las reglas, que es la ruta que
siempre debe funcionar. Con `--con-ocr` se mide tambien la foto.

Uso:
    docker compose exec expense-api python scripts/inspeccionar_set_pruebas.py
    docker compose exec expense-api python scripts/inspeccionar_set_pruebas.py --con-ocr
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.archivo_real import detectar_tipo_real
from app.services import capture

RAIZ = Path(__file__).resolve().parents[1]
SET = RAIZ / "test-files" / "set-digitalizacion"


async def inspeccionar(ruta: Path, con_ocr: bool) -> dict:
    contenido = ruta.read_bytes()
    formato = detectar_tipo_real(contenido)
    if formato is None:
        return {"archivo": str(ruta.relative_to(SET)), "bytes": None, "lector": "NO_SOPORTADO"}

    def sin_ocr(_datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado en la inspeccion")

    async def sin_modelo(*_a, **_k):
        raise RuntimeError("modelo apagado en la inspeccion")

    # `--con-ocr` enciende SOLO Tesseract. Dejar `extract_from_image=None` seria
    # encender tambien el modelo, y entonces esta herramienta mediria dos cosas a
    # la vez: el OCR local y una llamada de red de minutos por foto.
    resultado = await capture.capture_ticket(
        contenido,
        formato,
        extract_from_image=sin_modelo,
        extract_from_text=sin_modelo,
        ocr_reader=None if con_ocr else sin_ocr,
    )
    return {
        "archivo": str(ruta.relative_to(SET)),
        "bytes": formato,
        "lector": resultado.confidence_source.value if hasattr(resultado.confidence_source, "value") else str(resultado.confidence_source),
        "total": str(resultado.total_amount),
        "proveedor": resultado.provider_name,
        "fecha": str(resultado.expense_date),
        # El veredicto (`extraction_status`) lo pone el gate DESPUES de la
        # captura, asi que aqui solo se puede mostrar la confianza cruda.
        "confianza": str(resultado.confidence),
    }


async def main(con_ocr: bool) -> int:
    if not SET.is_dir():
        print(f"No existe {SET}. Corre primero scripts/generar_set_pruebas_digitalizacion.py")
        return 1

    for subcarpeta in ("ideal", "borde", "fallido"):
        carpeta = SET / subcarpeta
        if not carpeta.is_dir():
            continue
        print(f"\n=== {subcarpeta} ===")
        for ruta in sorted(carpeta.rglob("*")):
            if ruta.is_symlink() or not ruta.is_file():
                if ruta.is_symlink():
                    print(f"{ruta.relative_to(SET)}: enlace simbolico (se reporta, no se lee)")
                continue
            dato = await inspeccionar(ruta, con_ocr)
            print(
                f"{dato['archivo']:<48} bytes={str(dato['bytes']):<10} "
                f"lector={dato['lector']:<12} total={dato.get('total', '-'):<12} "
                f"fecha={dato.get('fecha', '-')}"
            )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--con-ocr", action="store_true", help="usa Tesseract para las imagenes")
    raise SystemExit(asyncio.run(main(parser.parse_args().con_ocr)))
