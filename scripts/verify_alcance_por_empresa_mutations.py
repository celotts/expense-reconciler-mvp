#!/usr/bin/env python3
"""Mutaciones del alcance por empresa: un id ajeno tiene que dar 404.

`verify_permisos_http_mutations.py` comprueba la autoridad sobre la ACCION.
Aqui se comprueba la autoridad sobre el RECURSO. Cada mutacion quita una
comprobacion de empresa y tiene que hacer fallar
`tests/integration/test_alcance_por_empresa.py`.

Si una sobrevive, la defensa esta escrita pero no esta defendiendo nada. Si el
texto original ya no esta, la mutacion queda obsoleta y tambien cuenta como
fallo: un verificador que no se aplica y sigue imprimiendo "ok" es peor que no
tener verificador.

Uso:  python3 scripts/verify_alcance_por_empresa_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = "tests/integration/test_alcance_por_empresa.py"

# (nombre, archivo, original, mutado, ocurrencia)
# `ocurrencia` distingue las cuatro lineas identicas de `tickets.py`: cada una
# pertenece a un endpoint distinto y cada una tiene su propia prueba.
MUTACIONES: list[tuple[str, str, str, str, int]] = [
    (
        "el ticket por id vuelve a leer cualquier empresa",
        "app/api/tickets.py",
        "    if empresa is not None:\n"
        "        consulta = consulta.where(TicketModel.company_id == empresa)\n",
        "    if False:\n"
        "        consulta = consulta.where(TicketModel.company_id == empresa)\n",
        0,
    ),
    (
        "la lista de tickets vuelve a mezclar empresas",
        "app/api/tickets.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        0,
    ),
    (
        "la cola de revision vuelve a mezclar empresas",
        "app/api/tickets.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        1,
    ),
    (
        "la cola de muestreo vuelve a mezclar empresas",
        "app/api/tickets.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        2,
    ),
    (
        "el reporte de exactitud presta la evidencia ajena",
        "app/api/tickets.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        3,
    ),
    (
        "un lote mixto vuelve a clasificar el ticket ajeno",
        "app/api/tickets.py",
        "    if empresa is not None:\n"
        "        # Un lote con un id ajeno NO clasifica parte de los tickets: con el\n"
        "        # `WHERE` de empresa, los ajenos simplemente no cuentan como actualizados.\n"
        "        actualizacion = actualizacion.where(TicketModel.company_id == empresa)\n",
        "    if False:\n"
        "        actualizacion = actualizacion.where(TicketModel.company_id == empresa)\n",
        0,
    ),
    (
        "el movimiento bancario por id vuelve a leer cualquier empresa",
        "app/api/bank_transactions.py",
        "    if empresa is not None:\n"
        "        consulta = consulta.where(BankTransactionModel.company_id == empresa)\n",
        "    if False:\n"
        "        consulta = consulta.where(BankTransactionModel.company_id == empresa)\n",
        0,
    ),
    (
        "la lista de movimientos vuelve a mezclar empresas",
        "app/api/bank_transactions.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        0,
    ),
    (
        "la conciliacion por id vuelve a leer cualquier empresa",
        "app/api/reconciliations.py",
        "    if empresa is not None:\n"
        "        consulta = consulta.where(\n"
        "            or_(\n"
        "                TicketModel.company_id == empresa,\n",
        "    if False:\n"
        "        consulta = consulta.where(\n"
        "            or_(\n"
        "                TicketModel.company_id == empresa,\n",
        0,
    ),
    (
        "la lista de conciliaciones vuelve a mezclar empresas",
        "app/api/reconciliations.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        0,
    ),
    (
        "el mapeo contable por id vuelve a leer cualquier empresa",
        "app/api/reconciliations.py",
        "    if empresa is not None:\n"
        "        consulta = consulta.where(AccountingMappingModel.company_id == empresa)\n",
        "    if False:\n"
        "        consulta = consulta.where(AccountingMappingModel.company_id == empresa)\n",
        0,
    ),
    (
        "la lista de mapeos vuelve a mezclar empresas",
        "app/api/reconciliations.py",
        "    company_id = empresa or company_id",
        "    # company_id = empresa or company_id",
        1,
    ),
    (
        "crear una conciliacion vuelve a creer el ticket ajeno",
        "app/api/reconciliations.py",
        "        if empresa is not None:\n"
        "            consulta = consulta.where(TicketModel.company_id == empresa)\n",
        "        if False:\n"
        "            consulta = consulta.where(TicketModel.company_id == empresa)\n",
        0,
    ),
    (
        "crear una conciliacion vuelve a creer el movimiento ajeno",
        "app/api/reconciliations.py",
        "        if empresa is not None:\n"
        "            consulta = consulta.where(BankTransactionModel.company_id == empresa)\n",
        "        if False:\n"
        "            consulta = consulta.where(BankTransactionModel.company_id == empresa)\n",
        0,
    ),
]


def _reemplazar(fuente: str, original: str, mutado: str, ocurrencia: int) -> str | None:
    """Sustituye solo la ocurrencia pedida. `None` si no existe."""
    partes = fuente.split(original)
    if len(partes) - 1 <= ocurrencia:
        return None
    return mutado.join(partes[: ocurrencia + 1]) + original.join(partes[ocurrencia + 1 :])


def main() -> int:
    supervivientes: list[str] = []
    for nombre, archivo, original, mutado, ocurrencia in MUTACIONES:
        ruta = RAIZ / archivo
        fuente = ruta.read_text(encoding="utf-8")
        cambiada = _reemplazar(fuente, original, mutado, ocurrencia)
        if cambiada is None:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO la ocurrencia {ocurrencia} en {archivo}.")
            supervivientes.append(f"{nombre} (mutacion obsoleta)")
            continue

        try:
            ruta.write_text(cambiada, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", PRUEBAS],
                capture_output=True,
                text=True,
                cwd=RAIZ,
            )
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (detectada)")
        else:
            print(f"  VIVA {nombre} (nadie se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(
        f"Las {len(MUTACIONES)} mutaciones mueren. Un id ajeno no cruza la empresa."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
