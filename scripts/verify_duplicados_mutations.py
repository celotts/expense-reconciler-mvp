#!/usr/bin/env python3
"""Verificacion por mutacion de la huella fiscal y su aviso.

La huella fiscal no bloquea: avisa. Por eso un falso negativo no da error, y un
test que solo mira el código puede estar verde aunque el aviso ya no llegue a la
fila ni al JSON del escaneo. Aqui se quitan las defensas a proposito y se exige
que la prueba que las sostiene muera. Una mutacion obsoleta tambien cuenta como
fallo: no se puede decir "verificado" si el texto buscado ya no está.

Uso:
    python3 scripts/verify_duplicados_mutations.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = [
    "tests/unit/test_caja_conciliacion_y_auditoria.py",
    "tests/unit/test_caja_chica_y_duplicados.py",
]


@dataclass
class Mutacion:
    nombre: str
    archivo: str
    viejo: str
    nuevo: str
    prueba: str

    def aplicar(self) -> tuple[Path, str]:
        ruta = RAIZ / self.archivo
        original = ruta.read_text(encoding="utf-8")
        if self.viejo not in original:
            raise AssertionError(
                f"no se encontro el texto a mutar en {self.archivo}:\n"
                f"  {self.viejo[:140]!r}\n"
                "Si el codigo cambio, esta mutacion esta obsoleta y hay que "
                "actualizarla."
            )
        mutado = original.replace(self.viejo, self.nuevo, 1)
        # Un error de sintaxis no prueba nada: se descarta antes de correr la
        # suite para no contar como "detectada" una mutacion que nunca corrio.
        try:
            compile(mutado, str(ruta), "exec")
        except SyntaxError as exc:
            raise AssertionError(
                f"la mutacion produjo codigo invalido en {self.archivo}: {exc}"
            ) from exc
        ruta.write_text(mutado, encoding="utf-8")
        return ruta, original


MUTACIONES = [
    Mutacion(
        nombre="la busqueda de duplicados cruza empresas",
        archivo="app/services/duplicados_service.py",
        viejo=(
            "    condiciones = [\n"
            "        TicketModel.company_id == company_id,\n"
            "        TicketModel.expense_date == fecha,\n"
        ),
        nuevo=(
            "    condiciones = [\n"
            "        True,  # MUTACION: ya no se mira la empresa\n"
            "        TicketModel.expense_date == fecha,\n"
        ),
        prueba="test_compania_distinta_no_es_duplicado",
    ),
    Mutacion(
        nombre="dos RFC distintos cuentan como el mismo gasto",
        archivo="app/services/duplicados_service.py",
        viejo=(
            "    fiscal = _normalizar_texto(identificacion_fiscal)\n"
            "    if fiscal:\n"
            "        condiciones.append(\n"
        ),
        nuevo=(
            "    fiscal = _normalizar_texto(identificacion_fiscal)\n"
            "    if False:  # MUTACION: se ignora el RFC\n"
            "        condiciones.append(\n"
        ),
        prueba="test_otro_rfc_no_es_duplicado",
    ),
    Mutacion(
        nombre="dos folios distintos cuentan como el mismo gasto",
        archivo="app/services/duplicados_service.py",
        viejo=(
            "    folio_norm = _normalizar_texto(folio)\n"
            "    if folio_norm:\n"
            "        condiciones.append(func.upper(TicketModel.folio) == folio_norm)\n"
        ),
        nuevo=(
            "    folio_norm = _normalizar_texto(folio)\n"
            "    if False:  # MUTACION: se ignora el folio\n"
            "        condiciones.append(func.upper(TicketModel.folio) == folio_norm)\n"
        ),
        prueba="test_otro_folio_no_es_duplicado",
    ),
    Mutacion(
        nombre="se calcula huella sin fecha y se afirma un duplicado inventado",
        archivo="app/services/duplicados_service.py",
        viejo=(
            "    monto = _monto_canonico(total)\n"
            "    if fecha is None or monto is None:\n"
            "        return None\n"
        ),
        nuevo=(
            "    monto = _monto_canonico(total)\n"
            "    if fecha is None and monto is None:  # MUTACION: basta el monto\n"
            "        return None\n"
        ),
        prueba="test_sin_fecha_no_se_calcula_huella",
    ),
    Mutacion(
        nombre="el aviso se comprueba con una fecha distinta de la que se guarda",
        archivo="app/services/ticket_persistence.py",
        viejo="        fecha=fecha_ticket,\n        folio=extracted.folio,\n",
        nuevo="        fecha=None,  # MUTACION: se pregunta por otra fecha\n        folio=extracted.folio,\n",
        prueba="test_el_segundo_papel_llega_con_aviso",
    ),
    Mutacion(
        nombre="el ticket se guarda sin el aviso aunque haya duplicado",
        archivo="app/services/ticket_persistence.py",
        viejo=(
            '    if duplicados_fiscales:\n'
            '        previos = ", ".join(str(t.id) for t in duplicados_fiscales[:5])\n'
        ),
        nuevo=(
            "    if False:  # MUTACION: se descarta el aviso\n"
            '        previos = ", ".join(str(t.id) for t in duplicados_fiscales[:5])\n'
        ),
        prueba="test_el_segundo_papel_llega_con_aviso",
    ),
    Mutacion(
        nombre="el aviso no viaja en los datos del escaneo",
        archivo="app/services/scan_service.py",
        viejo='            "advertencia_duplicado_fiscal": ticket.advertencia_duplicado_fiscal,\n',
        nuevo='            "advertencia_duplicado_fiscal": None,  # MUTACION: se pierde\n',
        prueba="test_el_aviso_viaja_en_los_datos_del_escaneo",
    ),
]


def _correr_suite() -> tuple[bool, str]:
    entorno = os.environ.copy()
    entorno.pop("SECRET_KEY", None)
    entorno["TICKETS_SCAN_RESET_HABILITADO"] = "false"
    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", *PRUEBAS, "-q", "--no-header", "-x"],
        cwd=RAIZ,
        env=entorno,
        capture_output=True,
        text=True,
    )
    return proceso.returncode == 0, proceso.stdout + proceso.stderr


def main() -> int:
    paso, salida = _correr_suite()
    if not paso:
        print("La suite ya esta en rojo antes de mutar. Arregla eso primero.\n")
        print(salida[-3000:])
        return 1

    print(f"Suite base en verde. Se prueban {len(MUTACIONES)} mutaciones.\n")

    sobrevivieron: list[str] = []
    for mutacion in MUTACIONES:
        try:
            ruta, original = mutacion.aplicar()
        except AssertionError as exc:
            print(f"  [OBSOLETA] {mutacion.nombre}\n      {exc}")
            sobrevivieron.append(f"{mutacion.nombre} (obsoleta)")
            continue

        try:
            paso, salida = _correr_suite()
        finally:
            ruta.write_text(original, encoding="utf-8")

        if paso:
            print(f"  [NO DETECTADA] {mutacion.nombre}")
            sobrevivieron.append(mutacion.nombre)
        else:
            detectada = (
                "la prueba indicada"
                if mutacion.prueba in salida
                else "OTRA prueba (revisa el nombre)"
            )
            print(f"  [detectada]   {mutacion.nombre}  <- {detectada}")

    print()
    if sobrevivieron:
        print(f"{len(sobrevivieron)} mutaciones pasaron sin que nadie se diera cuenta:")
        for nombre in sobrevivieron:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones fueron detectadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
