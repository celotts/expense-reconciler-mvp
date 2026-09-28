#!/usr/bin/env python3
"""Verifica que los tests del muestreo detecten las regresiones.

Un test que pasa no demuestra nada sobre el codigo: puede que este probando lo
que cree, o puede que no este probando nada. La unica forma de saber cual de
las dos es romper el codigo a proposito y ver si el test se da cuenta.

Las mutaciones de aqui no son inventadas para este script. Cada una deshace
algo que se ha decidido y documentado, y varias deshacen bugs que
efectivamente aparecieron al construir el muestreo:

- La cola de muestreo sin el filtro `spot_check_status IS NOT NULL` devolvia
  TODOS los tickets de la empresa. Sobrevive si nadie comprueba que lo que no
  esta en la muestra no aparece.
- La cola sin el filtro de PENDIENTE mezclaba lo ya revisado con el trabajo
  real. Sobrevive si el test solo mira los conteos, que si estaban bien.
- `subtotal` persistido como 0 en vez de NULL. Sobrevive si nadie distingue
  "no hay subtotal" de "el subtotal es cero", que es un dato falso.
- La marca de la muestra sin la condicion de AUTO_APROBADO. Sobrevive si
  nadie comprueba que lo que el gate rechazo no se cuenta como automatismo.
- El veredicto global como promedio en vez de como el peor. Sobrevive con
  pruebas de integracion cortas, porque alli el promedio da la misma respuesta
  que el peor. Por eso esa regla tiene su propia prueba unitaria.

Uso:  python3 scripts/verify_spot_check_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    # -----------------------------------------------------------------------
    # La eleccion de la muestra
    # -----------------------------------------------------------------------
    (
        "la muestra deja de depender del contenido",
        "app/services/accuracy_service.py",
        "    return (valor % 10_000) < int(tasa * 10_000)",
        "    return True",
        [
            "tests/unit/test_spot_check.py::TestLaMuestraSeEligeSinAzarNiConsentimiento",
            "tests/integration/test_spot_check_api.py::TestLaMarcaLlegaALaFila",
        ],
    ),
    (
        "la tasa se aplica a una fraccion distinta de la acordada",
        "app/services/accuracy_service.py",
        "    return (valor % 10_000) < int(tasa * 10_000)",
        "    return (valor % 1_000) < int(tasa * 100)",
        ["tests/unit/test_spot_check.py::TestLaMuestraSeEligeSinAzarNiConsentimiento"],
    ),
    (
        "un hash degenerado vuelve a romper la seleccion",
        "app/services/accuracy_service.py",
        "        valor = int(source_hash, 16)",
        "        valor = int(source_hash[:8], 16)",
        ["tests/unit/test_spot_check.py::TestLaMuestraSeEligeSinAzarNiConsentimiento"],
    ),
    (
        "la captura manual entra a la muestra",
        "app/services/accuracy_service.py",
        "    if not source_hash:\n        return False",
        "    if False:\n        return False",
        [
            "tests/unit/test_spot_check.py::TestLaMuestraSeEligeSinAzarNiConsentimiento",
            "tests/integration/test_spot_check_api.py::TestLaMarcaLlegaALaFila",
        ],
    ),
    # -----------------------------------------------------------------------
    # La condicion de entrada: solo lo que el gate aprobo solo
    # -----------------------------------------------------------------------
    (
        "se muestrea tambien lo que el gate dejo en la cola",
        "app/api/tickets.py",
        "            if decision.status == ExtractionStatus.AUTO_APROBADO\n            and en_muestra(source_hash)",
        "            if en_muestra(source_hash)",
        [
            "tests/integration/test_spot_check_api.py::TestLaMarcaLlegaALaFila",
            "tests/integration/test_spot_check_api.py::TestLaBaseRespaldaLoQueDiceElCodigo",
        ],
    ),
    (
        "se muestrea la captura manual por tener hash",
        "app/api/tickets.py",
        "            if decision.status == ExtractionStatus.AUTO_APROBADO\n            and en_muestra(source_hash)",
        "            if en_muestra(source_hash or 'x')",
        ["tests/integration/test_spot_check_api.py::TestLaMarcaLlegaALaFila"],
    ),
    # -----------------------------------------------------------------------
    # La cola: es trabajo, no historial
    # -----------------------------------------------------------------------
    (
        "la lista de la cola deja de filtrar por estado de la muestra",
        "app/api/tickets.py",
        "    base = base.where(\n        TicketModel.spot_check_status == (status_filter or SpotCheckStatus.PENDIENTE.value)\n    )",
        "    base = base.where(TicketModel.spot_check_status.is_not(None))",
        [
            "tests/integration/test_spot_check_api.py::TestLaColaEsTrabajoNoHistorial",
            "tests/integration/test_spot_check_api.py::TestLaMarcaLlegaALaFila",
        ],
    ),
    (
        "la cola mezcla lo ya revisado con el trabajo real",
        "app/api/tickets.py",
        "        TicketModel.spot_check_status == (status_filter or SpotCheckStatus.PENDIENTE.value)",
        "        TicketModel.spot_check_status.is_not(None)",
        ["tests/integration/test_spot_check_api.py::TestLaColaEsTrabajoNoHistorial"],
    ),
    (
        "el total de pendientes lo corta el limite de la lista",
        "app/api/tickets.py",
        "    pendientes = await _contar(SpotCheckStatus.PENDIENTE.value)",
        "    pendientes = min(pendientes, len(rows))",
        [
            "tests/integration/test_spot_check_api.py::TestElReporteCuentaLoQueFalta",
        ],
    ),
    # -----------------------------------------------------------------------
    # El veredicto global: el peor, no el promedio
    # -----------------------------------------------------------------------
    (
        "el veredicto global pasa a ser el promedio",
        "app/api/tickets.py",
        "    if Veredicto.NO_CUMPLE in veredictos:\n        return Veredicto.NO_CUMPLE",
        "    if Veredicto.CUMPLE in veredictos:\n        return Veredicto.CUMPLE",
        ["tests/unit/test_spot_check.py::TestElPeorVeredictoGana"],
    ),
    # -----------------------------------------------------------------------
    # La aritmetica: el intervalo tiene que seguir siendo honesto
    # -----------------------------------------------------------------------
    (
        "el intervalo de confianza se ensancha para dar mejor numero",
        "app/services/accuracy_service.py",
        "        _ajustar_al_borde(max(0.0, centro - margen)),\n        _ajustar_al_borde(min(1.0, centro + margen)),",
        "        _ajustar_al_borde(max(0.0, centro - margen * 2)),\n        _ajustar_al_borde(min(1.0, centro + margen / 2)),",
        ["tests/unit/test_spot_check.py::TestElIntervaloDiceLoQueLaMuestraSostiene"],
    ),
    (
        "un porcentaje exacto se declara suficiente sin muestra suficiente",
        "app/services/accuracy_service.py",
        "    if bajo >= objetivo:\n        return Veredicto.CUMPLE",
        "    if bajo >= objetivo or aciertos / total >= objetivo:\n        return Veredicto.CUMPLE",
        ["tests/unit/test_spot_check.py::TestUnPorcentajeSoloNoDiceNada"],
    ),
    # -----------------------------------------------------------------------
    # El subtotal: NULL y no cero
    # -----------------------------------------------------------------------
    (
        "un subtotal desconocido se guarda como 0.00",
        "app/api/tickets.py",
        "        subtotal=extracted.subtotal,",
        "        subtotal=extracted.subtotal or Decimal('0.00'),",
        [
            "tests/integration/test_spot_check_api.py::TestRegistrarElVeredicto",
        ],
    ),
    # -----------------------------------------------------------------------
    # El contrato con el frontend
    # -----------------------------------------------------------------------
    # Estas no deshacen un bug ya visto: deshacen el modo de fallo silencioso
    # mas caro que tiene esta funcionalidad, que es que el conteo de la
    # exactitud salga mejor de lo que es sin que nadie vea un error.
    (
        "la pantalla deja de ofrecer un campo que el backend acepta",
        "front/src/utils/spotcheck.ts",
        "  { campo: 'subtotal', label: 'Subtotal' },\n",
        "",
        ["tests/unit/test_contract_sync.py::TestElMuestreoHablaElMismoIdioma"],
    ),
    (
        "la pantalla ofrece un campo que el backend rechaza",
        "front/src/utils/spotcheck.ts",
        "  { campo: 'total_amount', label: 'Total' },\n",
        "  { campo: 'total_amount', label: 'Total' },\n  { campo: 'category', label: 'Categoria' },\n",
        ["tests/unit/test_contract_sync.py::TestElMuestreoHablaElMismoIdioma"],
    ),
    (
        "la pantalla anuncia un veredicto que el backend nunca emite",
        "front/src/types/api.ts",
        "export type Veredicto = 'SIN_EVIDENCIA' | 'CUMPLE' | 'NO_CUMPLE' | 'INCONCLUYENTE';",
        "export type Veredicto = 'SIN_EVIDENCIA' | 'CUMPLE' | 'NO_CUMPLE' | 'INCONCLUYENTE' | 'PERFECTO';",
        ["tests/unit/test_contract_sync.py::TestElMuestreoHablaElMismoIdioma"],
    ),
    (
        "un estado de la muestra desaparece de la pantalla",
        "front/src/types/api.ts",
        "export type SpotCheckStatus = 'PENDIENTE' | 'CORRECTO' | 'INCORRECTO';",
        "export type SpotCheckStatus = 'PENDIENTE' | 'CORRECTO';",
        ["tests/unit/test_contract_sync.py::TestElMuestreoHablaElMismoIdioma"],
    ),
    (
        "la pantalla deja de declarar un campo del ticket",
        "front/src/types/api.ts",
        "  subtotal: string | null;\n",
        "",
        ["tests/unit/test_contract_sync.py::TestLosCamposDelTicketCoinciden"],
    ),
    (
        "un campo nullable del backend se tipa sin null en el frontend",
        "front/src/types/api.ts",
        "  created_at: string | null;",
        "  created_at: string;",
        ["tests/unit/test_contract_sync.py::TestLosCamposDelTicketCoinciden"],
    ),

    # -----------------------------------------------------------------------
    # La antiguedad: una resta entre zonas distintas
    # -----------------------------------------------------------------------
    (
        "la antiguedad se calcula con una resta cruda entre zonas",
        "app/api/tickets.py",
        "            antiguedad = dias_desde(mas_viejo)",
        "            antiguedad = (utcnow() - mas_viejo).total_seconds() / 86400",
        [
            "tests/integration/test_spot_check_api.py::TestElReporteCuentaLoQueFalta",
        ],
    ),
]


def _correr(objetivos: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", *objetivos],
        capture_output=True,
        text=True,
        cwd=RAIZ,
    )
    return proc.returncode != 0, proc.stdout + proc.stderr


def main() -> int:
    if not MUTACIONES:
        print("no hay mutaciones que verificar")
        return 1

    supervivientes: list[str] = []
    for nombre, archivo, original, mutado, objetivos in MUTACIONES:
        ruta = RAIZ / archivo
        fuente = ruta.read_text(encoding="utf-8")

        if original not in fuente:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO el texto original en {archivo}.")
            print("        La mutacion esta desactualizada: el codigo cambio y")
            print("        esta mutacion ya no representa un cambio posible.")
            supervivientes.append(f"{nombre} (texto original no encontrado)")
            continue

        copia = Path(tempfile.mkdtemp()) / ruta.name
        try:
            shutil.copytree(RAIZ, copia.parent / "repo", symlinks=True)
            destino = copia.parent / "repo" / archivo
            destino.write_text(
                fuente.replace(original, mutado, 1), encoding="utf-8"
            )
            backup = ruta.read_text(encoding="utf-8")
            ruta.write_text(destino.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                murio, salida = _correr(objetivos)
            finally:
                ruta.write_text(backup, encoding="utf-8")
        finally:
            shutil.rmtree(copia.parent, ignore_errors=True)

        if murio:
            print(f"  ok   {nombre} (detectada)")
        else:
            print(f"  VIVA {nombre} (nadie se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for s in supervivientes:
            print(f"  - {s}")
        print()
        print("Cada superviviente es una pieza de logica que nadie mira. O el test")
        print("no cubre esa regla, o la cubre de una forma que el cambio no rompe.")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
