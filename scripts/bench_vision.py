#!/usr/bin/env python3
"""Mide que modelos de vision LEEN receipts, con el prompt real de la app.

Por que NO usa el escaner
=========================
Este script no escribe en la base, no toca `scan_files` y no llama a
`POST /scan`. Llama a `AIExtractor.extract_from_image` directamente, que es la
misma funcion que la cascada usa cuando un OCR no entiende una foto. Asi se puede
comparar modelos sin que la medicion contamine el ledger y sin que la corrida
"tome" los archivos para la prueba del usuario.

Por que usa el codigo de la app y no un prompt propio
=====================================================
Un prompt inventado mide otra cosa. Si el prompt fuera masfacil que el de la app,
el resultado favorable no diria nada sobre el sistema real. Aqui se usa
`VISION_EXTRACTION_PROMPT`, `_a_jpeg` (que resuelve los HEIC) y
`_parse_ai_response` (que aplica la limpieza y el rescue por regex). Lo unico que
cambia entre corridas es el nombre del modelo.

Como corre
==========
    python3 scripts/bench_vision.py                       # todas, 2 fotos
    python3 scripts/bench_vision.py --modelos gemma3:4b   # uno
    python3 scripts/bench_vision.py --todas-las-fotos      # las 7
    python3 scripts/bench_vision.py --base-url http://host.docker.internal:11434

Sale con codigo 1 si ningun modelo extrae un total de mas de 0, porque ese es el
resultado que hace que la herramienta haya fallado en lo que pretendia medir.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.ai_extractor import AIExtractor  # noqa: E402
from app.services.ai_client import ai_client  # noqa: E402

# Las fotos que ya se leyeron con OCR dan un valor esperado con el cual comparar.
# Las que salieron en 0.00 son las que interesting: son las que la cascada tiene
# que pasarle a un modelo, y por lo tanto las que separan un modelo bueno de uno
# que no sirve.
FOTOS = {
    "cremeria": "CremeriaHermanosCoronel.heic",      # OCR: 171.96  (control facil)
    "walmart": "FC3CB9F8-5AB1-4546-B9A9-016C69A158C5 2.JPG",  # OCR: 234.00
    "ooxo_roto": "IMG_4316.HEIC",                      # OCR:   0.00  (caso dificil)
}

# Lo que el OCR ya habia leido. No es "la verdad": es lo que hay que superar. La
# verdad es el papel, y ahi no hay quien la lea automaticamente.
CONOCIDO = {
    "cremeria": Decimal("171.96"),
    "walmart": Decimal("234.00"),
    "ooxo_roto": None,
}


def _ruta_fotos() -> Path:
    d = settings.TICKETS_INPUT_DIR
    p = Path(d)
    return p


def _a_numero(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


async def probar(modelo: str, etiqueta: str, directorio: Path) -> dict:
    extractor = AIExtractor()
    settings.OLLAMA_VISION_MODEL = modelo
    fila = {"modelo": modelo, "foto": etiqueta, "casos": {}}

    for nombre, archivo in FOTOS.items():
        ruta = directorio / archivo
        if not ruta.exists():
            fila["casos"][nombre] = {"error": f"no existe {archivo}"}
            continue

        bytes_crudos = ruta.read_bytes()
        t0 = time.monotonic()
        try:
            inv = await asyncio.wait_for(
                extractor.extract_from_image(bytes_crudos), timeout=300
            )
        except Exception as exc:  # noqa: BLE001 - aqui el fallo ES el dato
            fila["casos"][nombre] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
            continue
        elapsed = time.monotonic() - t0

        leido = _a_numero(inv.total)
        esperado = CONOCIDO[nombre]
        coincide = (
            None if esperado is None or leido is None
            else abs(leido - esperado) <= Decimal("0.01")
        )
        fila["casos"][nombre] = {
            "proveedor": inv.provider_name,
            "total": str(leido) if leido is not None else None,
            "esperado": str(esperado) if esperado is not None else "desconocido",
            "coincide": coincide,
            "rfc": inv.provider_tax_id,
            "fecha": str(inv.invoice_date) if inv.invoice_date else None,
            "confianza": inv.confidence,
            "origen": inv.extraction_method,
            "segundos": round(elapsed, 1),
        }

    return fila


def _tabla(filas: list[dict]) -> str:
    lineas = []
    for fila in filas:
        for nombre, c in fila["casos"].items():
            if "error" in c:
                lineas.append(f"  {fila['modelo']:<18} {nombre:<12} ERROR {c['error']}")
                continue
            total = c["total"] or "—"
            ok = {True: "ok", False: "DIFIERE", None: "?"}[c["coincide"]]
            lineas.append(
                f"  {fila['modelo']:<18} {nombre:<12} total={total:<10} "
                f"esperado={c['esperado']:<10} {ok:<7} "
                f"{c['segundos']:>5}s  {c['proveedor'][:28]!r}"
            )
    return "\n".join(lineas)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Banco de modelos de vision")
    ap.add_argument("--modelos", nargs="*", default=["gemma3:4b"])
    ap.add_argument("--base-url", default="http://host.docker.internal:11434")
    ap.add_argument("--out", default=None, help="archivo JSON con el detalle")
    args = ap.parse_args()

    settings.OLLAMA_BASE_URL = args.base_url
    ai_client._ollama_client = None  # se rehace con el base_url nuevo

    directorio = _ruta_fotos()
    print(f"Modelos: {', '.join(args.modelos)}")
    print(f"Base:    {args.base_url}")
    print(f"Fotos:   {directorio}\n")

    filas = []
    for modelo in args.modelos:
        print(f"  ... {modelo}", file=sys.stderr, flush=True)
        filas.append(await probar(modelo, modelo, directorio))

    print("\n=== resultado ===")
    print(_tabla(filas))

    if args.out:
        Path(args.out).write_text(json.dumps(filas, indent=2, ensure_ascii=False))
        print(f"\nDetalle en {args.out}")

    # Un modelo sirve si de los casos con total conocido saca alguno bien.
    aciertos = 0
    for fila in filas:
        for c in fila["casos"].values():
            if c.get("coincide") is True:
                aciertos += 1
                break
    print(f"\nModelos que acertaron al menos un total conocido: {aciertos}/{len(filas)}")
    return 0 if aciertos else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
