#!/usr/bin/env python3
"""Mutaciones del explicador asistido: propone, nunca afirma ni cruza empresas.

Seis defensas, cada una con su prueba:

- los numeros citados se comprueban contra los datos literales -> test unitario.
- el flag apagado no llama al modelo -> test unitario.
- `raw_text` no viaja al modelo -> test unitario.
- un fallo del proveedor no filtra su cuerpo -> test unitario.
- un ticket ajeno por id da 404 -> test de integracion.
- la ruta nace protegida y montada -> test de integracion.

Uso:  python3 scripts/verify_ia_asistida_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

BASE = [
    "env", "-u", "SECRET_KEY", "-u", "POSTGRES_PASSWORD", "-u", "password_inicial",
    "TICKETS_SCAN_RESET_HABILITADO=false", "PYTHONPATH=.", "python3", "-m", "pytest",
]
UNIT = [*BASE, "tests/unit/test_ia_asistida.py", "-q"]
HTTP = [*BASE, "tests/integration/test_ia_asistida_api.py", "-q"]

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "el modelo puede citar numeros que no estan en el ticket",
        "app/services/ia_asistida.py",
        '        inventados = sorted(_numeros_de(texto) - _numeros_de("\\n".join(datos)))\n',
        "        inventados: list[str] = []\n",
        UNIT,
    ),
    (
        "el flag apagado ya no detiene la llamada al modelo",
        "app/services/ia_asistida.py",
        "        if not self.habilitado:\n",
        "        if False:\n",
        UNIT,
    ),
    (
        "raw_text viaja al modelo como dato verificable",
        "app/services/ia_asistida.py",
        '            f"total: {_importe(ticket.total_amount)}",\n',
        '            f"total: {_importe(ticket.total_amount)}",\n'
        '            f"texto: {getattr(ticket, \'raw_text\', \'\')}",\n',
        UNIT,
    ),
    (
        "el error del proveedor se devuelve al cliente",
        "app/services/ia_asistida.py",
        '                motivo="El modelo no respondio. La lectura del ticket no se ha cambiado.",\n',
        '                motivo=f"El modelo no respondio: {exc}",\n',
        UNIT,
    ),
    (
        "un ticket de otra empresa se puede explicar por id",
        "app/api/ia_asistida.py",
        "    if empresa is not None:\n"
        "        consulta = consulta.where(TicketModel.company_id == empresa)\n",
        "",
        HTTP,
    ),
    (
        "la ruta deja de estar montada",
        "app/api/api_router.py",
        "api_router.include_router(\n"
        "    ia_asistida_router,\n"
        '    prefix="/ia-asistida",\n'
        '    tags=["IA asistida"],\n'
        "    dependencies=PROTEGIDOS,\n"
        ")\n",
        "",
        HTTP,
    ),
]


def main() -> int:
    supervivientes: list[str] = []
    for nombre, archivo, original, mutado, comando in MUTACIONES:
        ruta = RAIZ / archivo
        fuente = ruta.read_text(encoding="utf-8")
        if original not in fuente:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO el texto original en {archivo}.")
            supervivientes.append(f"{nombre} (mutacion obsoleta)")
            continue

        try:
            ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
            proc = subprocess.run(comando, capture_output=True, text=True, cwd=RAIZ)
        finally:
            ruta.write_text(fuente, encoding="utf-8")

        if proc.returncode != 0:
            print(f"  ok   {nombre} (muere)")
        else:
            print(f"  VIVA {nombre} (la prueba no se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1
    print(f"Las {len(MUTACIONES)} mutaciones del explicador mueren. La IA propone; el codigo verifica.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
