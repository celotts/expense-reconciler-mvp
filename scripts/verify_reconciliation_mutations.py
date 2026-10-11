#!/usr/bin/env python3
"""Verifica que los tests de conciliacion detecten las regresiones.

Un test que pasa no dice nada sobre el codigo: puede que este probando lo que
cree, o puede que no este probando nada. La unica forma de saber cual de las
dos es romper el codigo a proposito y ver si el test se da cuenta.

Cada mutacion cambia una pieza de la logica de emparejamiento de forma sutil,
en general la linea que explica POR QUE se escribio asi. Si una sobrevive,
significa que ningun test cubre esa pieza y que se puede volver a romper sin
que nadie se entere.

El ejemplo del porque importa: una de estas mutaciones cambia `>` por `>=` en
el filtro de tolerancia de monto. La diferencia entre "dentro de la tolerancia"
y "dentro de la tolerancia menos un centavo" no se ve en la pantalla: el
operador ve la misma diferencia en el mismo numero y el ticket deja de
conciliarse igual. Es exactamente el tipo de cambio que pasa un code review.

Uso:  python3 scripts/verify_reconciliation_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import atexit
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

SERVICIO = "app/services/reconciliation_service.py"
SCHEMA = "app/schemas/reconciliation.py"
ENUMS = "app/core/enums.py"
EXPORT = "app/services/export_service.py"

UNIT = "tests/unit/test_reconciliation_service.py"
INTEG = "tests/integration/test_reconciliations_api.py"

# (nombre, archivo, texto original, texto mutado, tests que deben morir)
MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "la fecha pesa mas que el monto",
        SERVICIO,
        """        return (
            self.diff_monto,
            self.diff_dias,""",
        """        return (
            self.diff_dias,
            self.diff_monto,""",
        [f"{UNIT}::TestElDesempateEsExplicito::test_el_monto_manda_sobre_la_fecha"],
    ),
    (
        "el desempate final es el orden de la lista y no el proveedor",
        SERVICIO,
        "            0 if self.proveedor_en_descripcion else 1,\n",
        "            0,\n",
        [f"{UNIT}::TestElDesempateEsExplicito::test_a_igual_monto_y_fecha_manda_la_descripcion"],
    ),
    (
        "una mencion parcial del proveedor cuenta como el proveedor entero",
        SERVICIO,
        "    return all(token in destino for token in tokens)",
        "    return any(token in destino for token in tokens)",
        [f"{UNIT}::TestLaNormalizacionDeTextos::test_una_mencion_parcial_no_cuenta_como_mencion"],
    ),
    (
        "cualquier token cuenta como nombre de proveedor",
        SERVICIO,
        "_TOKEN_MINIMO = 3",
        "_TOKEN_MINIMO = 1",
        [f"{UNIT}::TestLaNormalizacionDeTextos::test_un_token_muy_corto_no_cierra_el_despiste"],
    ),
    (
        "la normalizacion no quita acentos ni signos",
        SERVICIO,
        '    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))\n    return _NO_ALFANUMERICO.sub(" ", sin_acentos.upper()).strip()',
        "    return descompuesto.strip()",
        [f"{UNIT}::TestLaNormalizacionDeTextos::test_compara_con_acentos_y_signos"],
    ),
    (
        "la frontera de la tolerancia se excluye",
        SERVICIO,
        "        if amount_diff > amount_tolerance:\n            continue",
        "        if amount_diff >= amount_tolerance:\n            continue",
        [f"{UNIT}::TestElDesempateEsExplicito::test_la_frontera_exacta_de_la_tolerancia_si_concilia"],
    ),
    (
        "la fecha se descarta en vez de solo ordenar",
        SERVICIO,
        "        if date_diff > date_tolerance_days:\n            continue\n\n        criterio = _criterio_de(ticket, bank_tx, amount_diff, date_diff)\n        clave = criterio.clave()\n\n        if mejor is None or clave < mejor:\n            mejor = clave\n            elegido = bank_tx\n            elegido_criterio = criterio\n\n    if elegido is None or elegido_criterio is None:",
        "        criterio = _criterio_de(ticket, bank_tx, amount_diff, date_diff)\n        clave = criterio.clave()\n\n        if mejor is None or clave < mejor:\n            mejor = clave\n            elegido = bank_tx\n            elegido_criterio = criterio\n\n    if elegido is None or elegido_criterio is None:",
        [f"{UNIT}::TestLaDiscrepanciaExiste::test_lejos_en_monto_y_en_fecha_no_hay_nada_que_reportar"],
    ),
    (
        "la discrepancia se queda con el primer movimiento que encuentra",
        SERVICIO,
        "        if mejor is None or clave < mejor:\n            mejor = clave\n            elegido = bank_tx\n            elegido_criterio = criterio\n\n    if elegido is None or elegido_criterio is None:",
        "        if elegido is None:\n            mejor = clave\n            elegido = bank_tx\n            elegido_criterio = criterio\n\n    if elegido is None or elegido_criterio is None:",
        [f"{UNIT}::TestLaDiscrepanciaExiste::test_la_discrepancia_elige_el_mas_cercano_en_fecha"],
    ),
    (
        "nada se reporta como discrepancia",
        SERVICIO,
        "        discrepancia = _find_discrepancia(\n            ticket,\n            disponibles,\n            request.date_tolerance_days,\n        )",
        "        discrepancia = None",
        [f"{INTEG}::TestReconciliationsAPI::test_run_reconciliation_discrepancy_amount_diff"],
    ),
    (
        "todo lo que concilia se marca como MANUAL",
        SERVICIO,
        "        match_status = MatchStatus.PERFECT",
        "        match_status = MatchStatus.MANUAL",
        [f"{UNIT}::TestReconciliationMatching::test_find_best_match_perfect_amount_and_date"],
    ),
    (
        "la fecha dentro de la ventana se degrada a MANUAL",
        SERVICIO,
        "    if date_diff <= date_tolerance_days:\n        match_status = MatchStatus.PERFECT\n    else:\n        match_status = MatchStatus.MANUAL",
        "    match_status = MatchStatus.PERFECT",
        [
            f"{UNIT}::TestReconciliationMatching::test_find_best_match_amount_ok_date_outside_tolerance",
            f"{UNIT}::TestElDesempateEsExplicito::test_el_monto_manda_sobre_la_fecha",
        ],
    ),
    (
        "el recorrido de los tickets vuelve a depender del plan de la base",
        SERVICIO,
        "    query = query.order_by(TicketModel.expense_date, TicketModel.id)",
        "    pass",
        [
            f"{UNIT}::TestElRecorridoEsReproducible::test_los_tickets_salen_en_orden_cronologico_aunque_se_inserten_al_reves"
        ],
    ),
    (
        "el motivo de la decision no viaja en la respuesta",
        SERVICIO,
        "            criterio=m.criterio.texto(),",
        '            criterio="conciliado",',
        [f"{INTEG}::TestReconciliationsAPI::test_run_reconciliation_discrepancy_amount_diff"],
    ),
    (
        "el estado se guarda como el nombre de la clase y no como su valor",
        SERVICIO,
        "            match_status=match.match_status.value,",
        "            match_status=str(match.match_status),",
        [f"{UNIT}::TestLoQueQuedaEscrito"],
    ),
    (
        "el patron del schema deja de aceptar la discrepancia",
        SCHEMA,
        'PATRON_MATCH_STATUS = "^(" + "|".join(s.value for s in MatchStatus) + ")$"',
        'PATRON_MATCH_STATUS = "^(PERFECT|MANUAL)$"',
        [f"{UNIT}::TestElEnumYTElPatronNoSeDivergen::test_el_patron_del_schema_armado_desde_el_enum"],
    ),
    (
        "el patron del schema acepta cualquier cosa",
        SCHEMA,
        "    match_status: str = Field(..., pattern=PATRON_MATCH_STATUS)",
        "    match_status: str = Field(...)",
        [f"{UNIT}::TestElEnumYTElPatronNoSeDivergen::test_el_patron_no_acepta_un_estado_que_no_existe"],
    ),
    (
        "la discrepancia se exporta a CONTPAQI como si estuviera cuadrada",
        EXPORT,
        "        query = query.where(\n            ReconciliationModel.match_status.in_([s.value for s in MATCHED_STATUSES])\n        )",
        '        query = query.where(ReconciliationModel.match_status.in_(["PERFECT", "MANUAL", "DISCREPANCY"]))',
        ["tests/unit/test_export_service.py"],
    ),
    (
        "el enum y la lista de exportables se separan",
        ENUMS,
        "MATCHED_STATUSES = (\n    MatchStatus.PERFECT,\n    MatchStatus.MANUAL,\n)",
        "MATCHED_STATUSES = (\n    MatchStatus.PERFECT,\n    MatchStatus.MANUAL,\n    MatchStatus.DISCREPANCY,\n)",
        [f"{UNIT}::TestElEnumYTElPatronNoSeDivergen::test_los_estados_conciliados_no_incluyen_la_discrepancia"],
    ),
]


def _correr(objetivos: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *objetivos, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    return proc.returncode != 0, proc.stdout + proc.stderr


def main() -> int:
    print("Verificacion por mutacion del motor de conciliacion\n")
    supervivientes: list[str] = []

    base_murio, salida = _correr(["tests/"])
    if base_murio:
        print("  La suite ya falla antes de mutar. Arregla eso primero:")
        print(salida[-2500:])
        return 1
    print("  Base: suite en verde\n")

    with tempfile.TemporaryDirectory() as temporal:
        for nombre, archivo, original, mutado, objetivos in MUTACIONES:
            ruta = RAIZ / archivo
            if not ruta.exists():
                print(f"  ?? {nombre}: no existe {archivo}")
                supervivientes.append(nombre)
                continue

            fuente = ruta.read_text(encoding="utf-8")
            if original not in fuente:
                print(f"  ?? {nombre}: el patron original no aparece en {archivo}")
                print("     (el codigo cambio; la mutacion esta obsoleta)")
                supervivientes.append(nombre)
                continue

            copia = Path(temporal) / ruta.name
            shutil.copy2(ruta, copia)

            def _restaurar(*_args, _ruta=ruta, _copia=copia) -> None:
                """Deja el archivo como estaba, pase lo que pase.

                Sin esto, un Ctrl-C o un timeout del harness a mitad de una
                mutacion deja el codigo de produccion roto en el arbol de
                trabajo, con el comentario diciendo una cosa y el codigo
                haciendo otra. Es lo que paso una vez: el script fue matado en su
                segunda corrida y la suite fallo por un cambio que nadie
                habia hecho. El `finally` no alcanza si al proceso lo matan.
                """
                try:
                    if _ruta.exists() and _copia.exists():
                        shutil.copy2(_copia, _ruta)
                except OSError:
                    pass

            atexit.register(_restaurar)
            for _senal in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(_senal, lambda s, f: (f(), sys.exit(1)))
                except ValueError:
                    pass

            try:
                ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
                murio, salida = _correr(objetivos)
            finally:
                shutil.copy2(copia, ruta)
                atexit.unregister(_restaurar)

            if murio:
                print(f"  murio       {nombre}")
            else:
                supervivientes.append(nombre)
                print(f"  SOBREVIVIO  {nombre}")
                print(f"              ningun test de {', '.join(objetivos)} lo nota")

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
