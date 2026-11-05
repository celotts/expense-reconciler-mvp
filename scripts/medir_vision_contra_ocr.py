"""Mide un motor de vision contra el OCR actual, campo por campo.

Por que existe
--------------

El sistema tiene dos motores para leer una foto: Tesseract (OCR local) y el modelo
de vision de Ollama. El segundo estaba configurado como `moondream`, que es un
modelo de ~1.8B para DESCRIBIR imagenes y no para extraer campos de un formato
fijo. Medido sobre `IMG_4220.jpeg`: devolvio `Unknown Provider` y `total 0.00`, y
su unico campo acertado fue el subtotal.

Este script no arregla eso: **mide**. La pregunta que responde es "que leeria
mejor un modelo de vision de verdad sobre los comprobantes REALES de esta
maquina", y la respuesta no se puede deducir de una ficha de modelo.

POR QUE COMPARA Y NO SOLO EJECUTA
---------------------------------

Porque "el modelo devolvio algo" no dice si esta bien. Un JSON lleno de campos
plausibles y equivocados es PEOR que un `total 0.00` que al menos se ve que
fallo. Por eso el criterio es la CONCORDANCIA con el OCR ya medido: no es la
verdad absoluta (los dos pueden fallar) pero si es una referencia independiente,
y de los dos se puede pedir que sen el mismo.

Y por que la CONCORDANCIA no se lee como exactitud
--------------------------------------------------

Porque dos motores pueden coincidir en lo equivocado. Si Tesseract lee
`AAA AI` y el modelo tambien, la concordancia es 100% y los dos estan mal. Por
eso el script separa los dos numeros y solo afirma el primero cuando hay campo
de texto que comparar sin ambiguedad: el **RFC**, que es alfanumerico de
longitud fija y no admite conjetas.

Como se lee la salida
---------------------

    CONCORDANCIA     de los dos motores, campo por campo. No es exactitud.
    FIABLES          solo RFC. Es el unico campo donde "coincidir" quiere decir
                     algo: longitud fija, sin decimales, no inventable.
    DISCREPANCIA     donde los motores NO coinciden. Es la lista de trabajo:
                     ahi hay que mirar el papel.

    CONFIANZA DEL MODELO, DECLARADA POR EL
    --------------------------------------
    Un modelo de vision contesta un JSON y tu lo llenaste con algo inventado si
    no encaja. Es el mismo problema que ya tiene el gate con la confianza alta, y
    por eso aqui NO se mezcla con la evidencia: va en su propia linea, y el
    script no la usa para decidir nada. La decision la toman los dos numeros de
    arriba.

Como se usa
-----------

    docker compose exec expense-api python scripts/medir_vision_contra_ocr.py /tickets
    docker compose exec expense-api python scripts/medir_vision_contra_ocr.py /tickets \\
        --model qwen2.5vl:3b

Necesita Tesseract y un modelo de vision en Ollama, y corre DENTRO del
contenedor por las dos razones. No escribe nada en la base: es un medidor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.core.archivo_real import detectar_tipo_real
from app.core.enums import ConfidenceSource
from app.services.ai_extractor import ai_extractor
from app.services.capture import (
    _marcar_por_reglas, _parse_receipt_text, _vision_de_imagen,
)
from app.services.ocr import OCRNoDisponible, leer_imagen
from app.services.parser_service import TicketExtractionResult

logger = logging.getLogger("medir_vision")

# Campos que se comparan. `raw_text` y `confidence` NO: el primero es evidencia y
# no un campo, el segundo lo pone cada motor segun su propia regla y compararlo
# seria comparar politicas, no lecturas.
CAMPOS_TEXTO = ("provider_name", "provider_tax_id")
CAMPOS_DINERO = ("total_amount", "subtotal", "tax_amount")

# El RFC es el unico campo donde la concordancia significa algo. Longitud fija,
# alfanumerico, y un motor no lo inventa sin que se note: si los dos dicen
# `NWM970924QW4` es que lo leyeron, y si dicen cosas distintas, uno de los dos
# esta inventando.
CAMPOS_FIABLES = ("provider_tax_id",)


def _dinero(valor: Decimal | None) -> Decimal | None:
    """Compara dinero a centimos, no a strings.

    `Decimal("97.56")` y `Decimal("97.560")` son el mismo total con distinta
    escala. Sin esto, dos motores que leen bien se verian en desacuerdo.
    """
    if valor is None:
        return None
    return valor.quantize(Decimal("0.01"))


def _texto(valor: str | None) -> str | None:
    """Compara texto sin que el espaciado decida el resultado.

    La mayusculas NO se iguala: `MERCADO LOCAL DON PEPE` y `Mercado Local Don
    Pepe` son la misma razon social y en un ticket mexicano la caja suele venir
    toda en mayusculas, asi que un motor que respectala y otro que no estan
    diciendo lo mismo sobre el mismo papel.
    """
    if valor is None:
        return None
    return " ".join(valor.split())


def _veredicto(a: object, b: object) -> str:
    """El veredicto de un campo, y distingue "los dosloe leyeron" de "los dos dicen lo mismo".

    ESTA FUNCION CORRIGE UN ERROR QUE EL PRIMER INTENTO MIDIO MAL
    -----------------------------------------------------------
    Comparar `None == None` como "igual".reporta 100% de concordancia de RFC
    cuando los dos motores no leyeron NINGUN RFC. Medido: los dos motores
    devolvieron `provider_tax_id=None` en los 7 comprobantes, y el script
   stitutions76announced "RFC 100% (7/7) con el mismo RFC".

    Eso es lo que hace este medidor peligroso si se lee mal: un `100%` que no
    significa nada, y en un ticket el RFC es el campo que mas se Podria suponer
    leido porque parece oficial.

    "Los dos vacios" no es concordancia: es dos ausencias. Se cuenta aparte como
    `NINGUNO`, y el resumen lo dice en voz alta para que el numero no se pueda
    leer como "el RFC esta bien".
    """
    if a is None and b is None:
        return "NINGUNO"
    if a is None or b is None:
        return "UNO"
    return "igual" if a == b else "DIFIERE"


@dataclass
class Lectura:
    motor: str
    segundos: float
    resultado: TicketExtractionResult | None
    error: str | None = None


@dataclass
class Fila:
    archivo: str
    ocr: Lectura | None = None
    vision: Lectura | None = None
    campos: dict[str, str] = field(default_factory=dict)


async def _leer_con_ocr(datos: bytes) -> Lectura:
    t0 = time.time()
    try:
        leido = await asyncio.to_thread(leer_imagen, datos)
    except OCRNoDisponible as exc:
        return Lectura("ocr", time.time() - t0, None, error=str(exc))
    texto = (leido.texto or "").strip()
    resultado = _marcar_por_reglas(_parse_receipt_text(texto), ConfidenceSource.OCR)
    return Lectura("ocr", time.time() - t0, resultado)


async def _leer_con_vision(datos: bytes) -> Lectura:
    t0 = time.time()
    try:
        resultado = await _vision_de_imagen(datos, ai_extractor.extract_from_image)
    except Exception as exc:  # noqa: BLE001
        # Un motor que revienta es un dato: se registra como lectura fallida y el
        # script sigue. Perder toda la medicion por un archivo es peor que
        # reportar que ese archivo no se pudo leer con ese motor.
        logger.warning("vision fallo: %s", exc)
        return Lectura("vision", time.time() - t0, None, error=str(exc))
    return Lectura("vision", time.time() - t0, resultado)


def _comparar(fila: Fila) -> None:
    """Anota campo por campo si los dos motores dicen lo mismo."""
    if fila.ocr is None or fila.vision is None:
        return
    if fila.ocr.resultado is None or fila.vision.resultado is None:
        return

    a, b = fila.ocr.resultado, fila.vision.resultado
    for campo in CAMPOS_TEXTO:
        fila.campos[campo] = _veredicto(_texto(getattr(a, campo)), _texto(getattr(b, campo)))
    for campo in CAMPOS_DINERO:
        fila.campos[campo] = _veredicto(_dinero(getattr(a, campo)), _dinero(getattr(b, campo)))
    # Las lineas no se comparan campo a campo porque son dicts crudos con forma
    # distinta en cada motor. Lo que se pregunta es si ambos las trajeron, que es
    # la distincion que importa para inventario: [] y None significan cosas
    # distintas (ver parser_service.TicketExtractionResult.items).
    fila.campos["items"] = (
        "ambos" if (a.items and b.items)
        else "solo_ocr" if a.items
        else "solo_vision" if b.items
        else "ninguno"
    )


def _pct(parte: int, total: int) -> str:
    return f"{(parte / total * 100):5.1f}%" if total else "  n/d"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("carpeta", help="Carpeta con los comprobantes")
    ap.add_argument("--model", default=None, help="Modelo de vision a probar")
    ap.add_argument("--json", action="store_true", help="Salida en JSON")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.model:
        from app.core.config import settings
        settings.OLLAMA_VISION_MODEL = args.model

    from app.core.config import settings
    print(f"Motor de vision: {settings.OLLAMA_VISION_MODEL}")
    print(f"Carpeta:         {args.carpeta}")

    carpeta = Path(args.carpeta)
    archivos = sorted(p for p in carpeta.rglob("*") if p.is_file() and not p.name.startswith("."))
    if not archivos:
        print("No hay archivos. Revisa que la carpeta sea la de entrada del escaner.")
        return 1

    filas: list[Fila] = []
    for ruta in archivos:
        datos = ruta.read_bytes()
        # Solo se miden los que la cascada trata como foto o escaneo. Un PDF con
        # capa de texto no pasa por OCR ni por vision, y meterlo aqui daria un
        # cero que no mide el motor: mide un camino que no se tomo.
        if detectar_tipo_real(datos) not in ("image",):
            print(f"  (se omite {ruta.name}: no es imagen)")
            continue

        print(f"\n=== {ruta.name}")
        ocr = asyncio.run(_leer_con_ocr(datos))
        print(f"  OCR     {ocr.segundos:5.1f}s  {ocr.error or ''}")
        vision = asyncio.run(_leer_con_vision(datos))
        print(f"  vision  {vision.segundos:5.1f}s  {vision.error or ''}")

        fila = Fila(archivo=ruta.name)
        fila.ocr, fila.vision = ocr, vision
        _comparar(fila)
        filas.append(fila)

        if ocr.resultado:
            r = ocr.resultado
            print(f"    OCR    : {r.provider_name!r} total={r.total_amount} rfc={r.provider_tax_id}")
        if vision.resultado:
            r = vision.resultado
            print(f"    vision : {r.provider_name!r} total={r.total_amount} rfc={r.provider_tax_id}")
        for campo, veredicto in sorted(fila.campos.items()):
            if veredicto in ("igual", "ambos"):
                marca = "  "
            elif veredicto == "NINGUNO":
                # `n` y no `!`: no es discrepancia, es que nadie lo leyo. Marcado
                # distinto porque "los dos fallaron" y "los dos dicen distinto" son
                # problemas diferentes con arreglos diferentes.
                marca = "n "
            else:
                marca = "! "
            print(f"    {marca}{campo}: {veredicto}")

    # --- El resumen ---
    #
    # Los tres numeros se calculan solo sobre las filas donde LOS DOS motores
    # devolvieron algo. Contar una fila donde el vision fallo como "no coincidio"
    # seria medir la disponibilidad del motor y no su lectura.
    comparables = [f for f in filas if f.ocr and f.vision and f.ocr.resultado and f.vision.resultado]

    print("\n" + "=" * 72)
    print("CONCORDANCIA entre motores  (no es exactitud: dos motores pueden")
    print("coincidir en lo equivocado)")
    print("=" * 72)

    total_campos = 0
    iguales = 0
    ninguno = 0
    uno = 0
    for campo in CAMPOS_TEXTO + CAMPOS_DINERO:
        con_dato = [f for f in comparables if f.campos.get(campo) is not None]
        ok = [f for f in con_dato if f.campos[campo] == "igual"]
        ninguno += len([f for f in con_dato if f.campos[campo] == "NINGUNO"])
        uno += len([f for f in con_dato if f.campos[campo] == "UNO"])
        total_campos += len(con_dato)
        iguales += len(ok)
        etiqueta = campo.ljust(16)
        print(f"  {etiqueta} {_pct(len(ok), len(con_dato))}  ({len(ok)}/{len(con_dato)})")

    print(f"\n  TOTAL         {_pct(iguales, total_campos)}  ({iguales}/{total_campos} campos)")
    print(f"    de los cuales, NINGUNO (los dos motores dejaron el campo vacio): {ninguno}")
    print(f"                     UNO (solo un motor lo leyo): {uno}")

    print("\nFIABLES  (solo RFC: longitud fija, no inventable)")
    total_fiables = 0
    fiables_ok = 0
    fiables_ninguno = 0
    for campo in CAMPOS_FIABLES:
        con_dato = [f for f in comparables if f.campos.get(campo) is not None]
        ok = [f for f in con_dato if f.campos[campo] == "igual"]
        fiables_ninguno += len([f for f in con_dato if f.campos[campo] == "NINGUNO"])
        total_fiables += len(con_dato)
        fiables_ok += len(ok)
    print(f"  RFC            {_pct(fiables_ok, total_fiables)}  ({fiables_ok}/{total_fiables} tickets lo LEYERON igual)")
    print(f"    sin RFC en ninguno de los dos motores: {fiables_ninguno}/{total_fiables}")
    if fiables_ninguno == total_fiables and total_fiables:
        print()
        print("  >>> NINGUNO de los dos motores leyo un solo RFC. El 100% de arriba")
        print("      no es acuerdo: son dos ausencias. Este numero NO se puede leer")
        print("      como 'el RFC se lee bien'.")

    print("\nDISCREPANCIA  (aqui hay que mirar el papel)")
    discrep = [f for f in comparables if any(v == "DIFIERE" for v in f.campos.values())]
    if not discrep:
        print("  ninguna")
    for f in discrep:
        campos = ", ".join(k for k, v in sorted(f.campos.items()) if v == "DIFIERE")
        print(f"  {f.archivo[:44]:<44} {campos}")

    print("\nTIEMPO por comprobante")
    for nombre in ("ocr", "vision"):
        tiempos = [getattr(f, nombre).segundos for f in filas if getattr(f, nombre)]
        if tiempos:
            print(f"  {nombre:<8} media {sum(tiempos) / len(tiempos):5.1f}s   total {sum(tiempos):6.1f}s")

    if args.json:
        print("\n" + json.dumps(
            {
                "modelo": settings.OLLAMA_VISION_MODEL,
                "concordancia": f"{iguales}/{total_campos}",
                "rfc_concordante": f"{fiables_ok}/{total_fiables}",
                "discrepancias": [
                    {"archivo": f.archivo, "campos": [k for k, v in f.campos.items() if v == "DIFIERE"]}
                    for f in discrep
                ],
            },
            indent=2, ensure_ascii=False,
        ))

    print("\nLO QUE ESTO NO DICE")
    print("  - No dice exactitud. Solo si dos motores coinciden.")
    print("  - No dice que un total coincidente sea el correcto: los dos pueden")
    print("    leer el mismo numero mal, y el RFC es el unico control.")
    print("  - No decide si hay que cambiar el motor. Eso se lee con la")
    print("    CONCORDANCIA de RFC junto con la DISCREPANCIA de totales.")

    return 0


if __name__ == "__main__":
    sys.exit(main())