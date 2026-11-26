#!/usr/bin/env python3
"""Corre TODOS los verificadores de mutacion, en secuencial y sin perdonar.

Un verificador que no se ejecuta cuenta como defensa no probada. Por eso este
orquestador descubre los scripts por nombre en vez de mantener una lista escrita
a mano: anadir `scripts/verify_<area>_mutations.py` nueva entra en la corrida
sinal que nadie tenga que acordarse de tocar este archivo ni el CI.

Por que secuencial
------------------
Cada verificador escribe el archivo original, corre los tests y lo restaura. Dos
corriendo a la vez se pisarian el uno al otro y el resultado no diria nada. El
orden es el alphabetico para que la salida sea reproducible.

Que cuenta como fallo
---------------------
1. El script sale con codigo distinto de cero. Ahi ya viene el detalle: una
   mutacion sobrevivio, o la linea base estaba rota.
2. El script dice que una mutacion NO se pudo aplicar ("NO SE ENCONTRO",
   "NO PROBADA", "obsoleta", ...), aunque salga con cero. Un patron obsoleto no
   probo la defensa: la dejo sin verificar y la conto como verde, que es peor
   que un test rojo porque nadie va a mirarla.

Uso:
    python3 scripts/verificar_todas_las_mutaciones.py
    python3 scripts/verificar_todas_las_mutaciones.py --lista
    python3 scripts/verificar_todas_las_mutaciones.py --solo captura

Salida: 0 si TODOS los verificadores corrieron y TODAS sus mutaciones murieron.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PATRON = "verify_*_mutations.py"

# Texto que significa "esta mutacion no llego a aplicarse". No es lo mismo que
# "sobrevivió": en el primer caso no se midio nada y en el segundo se midio y
# nadie lo vio. Los dos tienen que fallar, y este orquestador los distingue en
# el resumen para que no se arregle uno creyendo que era el otro.
NO_APLICADA = (
    "no se encontro",
    "no probada",
    "no se probaron",
    "no se pudo aplicar",
    "(obsoleta",
    "(obsoleto",
    "mutacion quedo obsoleta",
    "el patron original no aparece",
    "el patron no existe",
    "aborta",
)

SOBREVIVIO = (
    "viva ",
    "sobrevividas:",
    "sobrevivieron:",
    "sobrevivido",
    "sobrevivi\u00f3",
)


def descubrir() -> list[Path]:
    """Los scripts que existen hoy, no los que alguien recordo listar."""
    return sorted(RAIZ.glob(f"scripts/{PATRON}"))


def ambiente() -> dict[str, str]:
    """El mismo ambiente con el que se corrio la linea base en local.

    El shell de esta maquina exporta una `SECRET_KEY` corta y la `.env` enciende
    el reset del escaner. Los dos cambios hacen que la suite falle por entorno y
    no por una defensa, asi que aqui se quitan antes de llamar a cada script.
    """
    env = dict(os.environ)
    env.pop("SECRET_KEY", None)
    env["TICKETS_SCAN_RESET_HABILITADO"] = "false"
    env["PYTHONPATH"] = str(RAIZ)
    return env


def _normaliza(linea: str) -> str:
    return (
        linea.lower()
        .replace("\u00e1", "a")
        .replace("\u00e9", "e")
        .replace("\u00ed", "i")
        .replace("\u00f3", "o")
        .replace("\u00fa", "u")
    )


def revisar_salida(salida: str) -> tuple[list[str], list[str]]:
    """Separar 'no aplicada' de 'sobrevivio' mirando el texto, no el exit code."""
    no_aplicadas: list[str] = []
    sobrevivientes: list[str] = []
    for linea in salida.splitlines():
        normal = _normaliza(linea)
        if any(marca in normal for marca in NO_APLICADA):
            no_aplicadas.append(linea.strip())
        if any(marca in normal for marca in SOBREVIVIO):
            sobrevivientes.append(linea.strip())
    return no_aplicadas, sobrevivientes


def correr(script: Path, prefijo: str) -> tuple[int, str, list[str], list[str]]:
    """Un verificador, con su salida a medida que sale y el codigo final."""
    print(f"\n{'=' * 78}")
    print(f"{prefijo} {script.relative_to(RAIZ)}")
    print("=" * 78, flush=True)

    proceso = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=RAIZ,
        env=ambiente(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    lineas: list[str] = []
    assert proceso.stdout is not None
    for linea in proceso.stdout:
        lineas.append(linea)
        print(f"  | {linea.rstrip()}", flush=True)
    codigo = proceso.wait()
    salida = "".join(lineas)
    no_aplicadas, sobrevivientes = revisar_salida(salida)
    return codigo, salida, no_aplicadas, sobrevivientes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lista", action="store_true", help="solo enumerate los verificadores"
    )
    parser.add_argument(
        "--solo",
        default=None,
        help="corra solo los cuyo nombre contenga este texto (para diagnosticar)",
    )
    args = parser.parse_args(argv)

    scripts = descubrir()
    if args.solo:
        filtro = re.compile(re.escape(args.solo), re.IGNORECASE)
        scripts = [s for s in scripts if filtro.search(s.name)]

    if not scripts:
        print("No se encontro ningun verificador. Un CI que corre cero defensas "
              "no es una puerta.", file=sys.stderr)
        return 1

    if args.lista:
        for script in scripts:
            print(script.relative_to(RAIZ))
        print(f"{len(scripts)} verificadores descubiertos.")
        return 0

    fallos: list[str] = []
    print(f"Verificadores descubiertos: {len(scripts)}")
    for indice, script in enumerate(scripts, start=1):
        prefijo = f"[{indice}/{len(scripts)}]"
        codigo, _, no_aplicadas, sobrevivientes = correr(script, prefijo)

        if codigo != 0:
            fallos.append(
                f"{script.relative_to(RAIZ)}: salio con {codigo}"
            )
        if no_aplicadas:
            fallos.append(
                f"{script.relative_to(RAIZ)}: "
                f"{len(no_aplicadas)} mutacion(es) no se aplicaron"
            )
        if codigo == 0 and sobrevivientes:
            # Ya salio con 0, asi que son etiquetas informativas; se reporta
            # para que nadie confunda una linea de resumen con una defensa viva.
            print(
                f"  ! {script.relative_to(RAIZ)} imprime 'VIVA'/'sobrevivieron' "
                "pero salio con 0: revisa el verificador.",
                file=sys.stderr,
            )

    print(f"\n{'=' * 78}")
    if fallos:
        print(f"FALLARON {len(fallos)} comprobaciones en {len(scripts)} verificadores:")
        for fallo in fallos:
            print(f"  - {fallo}")
        print("\nUna defensa que no se ejecuta, o una mutacion que no se aplico, "
              "cuenta como defensa no probada.")
        return 1

    print(f"PASARON los {len(scripts)} verificadores. Toda mutacion aplicada murio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
