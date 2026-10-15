"""Que lee el pipeline REAL sobre cada foto, comparado contra la verdad a mano.

Corre el `capture_ticket` de verdad —los mismos extractores que usa el
escaner— sobre los comprobantes de la carpeta y compara lo que salio con
`verdad.json`, que escribe una persona mirando el papel.

NO escribe nada en la base: solo lee y compara.

    docker compose exec expense-api python scripts/medir_exactitud.py /tickets /verdad/verdad.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "/app")

from app.core import archivo_real  # noqa: E402
from app.services import capture  # noqa: E402
from app.services.ai_extractor import ai_extractor  # noqa: E402

SUFIJOS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".txt", ".webp"}
CAMPOS = ("total_amount", "subtotal", "tax_amount", "provider_name", "expense_date")


def _dinero(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def _mismo_dinero(a, b) -> bool | None:
    """None = no hay verdad para comparar. False = leido pero distinto."""
    da, db = _dinero(a), _dinero(b)
    if db is None:
        return None
    if da is None:
        return False
    return abs(da - db) < Decimal("0.01")


def _mismo_texto(a, b) -> bool | None:
    """Compara texto sin exigir cadena exacta: es COMERCIO, no codigo."""
    if b is None or b == "":
        return None
    if a is None:
        return False
    return str(a).strip().lower() == str(b).strip().lower()


async def main() -> None:
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "/tickets")
    ruta_verdad = Path(sys.argv[2] if len(sys.argv) > 2 else "/verdad/verdad.json")

    verdad = json.loads(ruta_verdad.read_text()) if ruta_verdad.exists() else {}
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in SUFIJOS)

    print(f"{len(archivos)} archivos  |  verdad cargada: {len([k for k in verdad if not k.startswith('_')])}")
    print("=" * 104)

    # aciertos por campo: True=acierto, False=fallo, None=sin verdad
    marcas = {c: {"ok": 0, "no": 0, "sin": 0} for c in CAMPOS}
    con_items = 0
    lineas_totales = 0

    for ruta in archivos:
        datos = ruta.read_bytes()
        file_type = archivo_real.detectar_tipo_real(datos)
        t0 = time.perf_counter()
        r = await capture.capture_ticket(
            datos,
            file_type=file_type,
            extract_from_image=ai_extractor.extract_from_image,
            extract_from_text=ai_extractor.extract_from_text,
        )
        dt = time.perf_counter() - t0

        v = verdad.get(ruta.name)
        print(f"\n{ruta.name}   ({dt:.0f}s, {r.confidence_source.value if r.confidence_source else '?'})")
        print(f"   leyo:  prov={str(r.provider_name)[:34]:36} total={r.total_amount} "
              f"sub={r.subtotal} iva={r.tax_amount} fecha={r.expense_date}")
        print(f"   lineas: {len(r.items) if r.items else 0}")
        if r.items:
            con_items += 1
            lineas_totales += len(r.items)
        if r.verificacion_items:
            print(f"   avisos: {r.verificacion_items}")

        if not v:
            print("   VERDAD: sin rotular  <-- esta foto no se puede calificar")
            continue

        fallos = []
        for c in CAMPOS:
            leido = getattr(r, c, None)
            if c in ("total_amount", "subtotal", "tax_amount"):
                ok = _mismo_dinero(leido, v.get(c))
                mostrado = f"{leido} vs {v.get(c)}"
            elif c == "expense_date":
                ok = _mismo_texto(leido, v.get(c))
                mostrado = f"{leido} vs {v.get(c)}"
            else:
                ok = _mismo_texto(leido, v.get(c))
                mostrado = f"{str(leido)[:24]} vs {str(v.get(c))[:24]}"
            marcas[c]["sin" if ok is None else ("ok" if ok else "no")] += 1
            if ok is False:
                fallos.append(f"{c}: {mostrado}")
        print(f"   VERDAD: total={v.get('total_amount')} prov={str(v.get('provider_name'))[:24]} "
              f"fecha={v.get('expense_date')}")
        print(f"   -> {'TODO CORRECTO' if not fallos else 'FALLA: ' + '; '.join(fallos)}")

    print("\n" + "=" * 104)
    print("EXACTITUD (solo sobre los que TIENEN verdad rotulada)")
    print("-" * 104)
    for c in CAMPOS:
        m = marcas[c]
        medibles = m["ok"] + m["no"]
        pct = f"{m['ok'] / medibles * 100:.0f}%" if medibles else "s/d"
        detalle = f"   (sin verdad: {m['sin']})" if m["sin"] else ""
        print(f"  {c:16} {pct:>6}   acierta {m['ok']}/{medibles}{detalle}")
    print("-" * 104)
    print(f"  fotos con lineas (items): {con_items}/{len(archivos)}   "
          f"lineas totales leidas: {lineas_totales}")
    print("\nATENCION: sin verdad rotulada esto NO es una medida de exactitud.")
    print("Es una EJECUCION. Para medir hay que rotular mirando el papel.")


if __name__ == "__main__":
    asyncio.run(main())