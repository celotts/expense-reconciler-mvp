#!/usr/bin/env python3
"""Mutaciones del tablero por empresa: cambiar de empresa debe cambiar los datos.

Cinco defensas, cada una con su prueba:

- `X-Company-ID` gana a la query suelta -> test de integracion.
- el parametro sigue siendo una dependencia, no un adorno -> test de integracion.
- el tablero no manda `company_id` suelto -> `verify:empresa`.
- al recargar no se borra la empresa guardada -> `verify:empresa`.
- el id de la empresa se ve en pantalla -> `verify:empresa`.

Uso:  python3 scripts/verify_dashboard_empresa_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive o quedo obsoleta.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

TEST = [
    "env", "-u", "SECRET_KEY", "-u", "POSTGRES_PASSWORD", "-u", "password_inicial",
    "TICKETS_SCAN_RESET_HABILITADO=false", "PYTHONPATH=.", "python3", "-m", "pytest",
    "tests/integration/test_dashboard_por_empresa.py", "-q",
]

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "la query vuelve a mandar sobre la cabecera",
        "app/api/dashboard.py",
        "    company_id = empresa or company_id\n",
        "    company_id = company_id\n",
        TEST,
    ),
    (
        "el endpoint deja de pedir la empresa activa",
        "app/api/dashboard.py",
        "    empresa: EmpresaVisible,\n",
        "    empresa: UUID | None = None,\n",
        TEST,
    ),
    (
        "el cliente del tablero vuelve a mandar company_id",
        "front/src/services/api.ts",
        "  get: (_companyId?: string, meses = 12) => {\n"
        "    const params = new URLSearchParams({ meses: String(meses) });\n"
        "    return fetchApi<DashboardResponse>(`/dashboard/?${params.toString()}`);\n"
        "  },",
        "  get: (companyId?: string, meses = 12) => {\n"
        "    const params = new URLSearchParams({ meses: String(meses) });\n"
        "    if (companyId) params.append('company_id', companyId);\n"
        "    return fetchApi<DashboardResponse>(`/dashboard/?${params.toString()}`);\n"
        "  },",
        ["npm", "--prefix", "front", "run", "verify:empresa"],
    ),
    (
        "al recargar se borra la empresa guardada antes de cargar la lista",
        "front/src/contextos/Empresa.tsx",
        "    if (cargando) return;\n    fijarEmpresaActiva(empresaActiva?.company_id ?? null);\n",
        "    fijarEmpresaActiva(empresaActiva?.company_id ?? null);\n",
        ["npm", "--prefix", "front", "run", "verify:empresa"],
    ),
    (
        "el tablero deja de mostrar el id de la empresa",
        "front/src/pages/Dashboard.tsx",
        "          {datos.company_id && (\n"
        '            <p className="text-xs text-gray-400" title="Empresa dueña de estas cifras">\n'
        "              {`ID de empresa: ${datos.company_id}`}\n"
        "            </p>\n"
        "          )}\n",
        "",
        ["npm", "--prefix", "front", "run", "verify:empresa"],
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
    print(f"Las {len(MUTACIONES)} mutaciones del tablero mueren. Cada empresa ve solo lo suyo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
