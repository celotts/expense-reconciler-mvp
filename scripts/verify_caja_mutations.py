#!/usr/bin/env python3
"""Verificacion por mutacion de caja chica (apertura, cierre, kardex y rastro).

Un test puede estar verde porque nunca ejecuta la rama que dice proteger. Aqui
se quita cada defensa a proposito y se exige que la prueba que la sostiene muera.
Una mutacion que no se aplica cuenta como fallo: un verificador que dice 10 y
prueba 8 miente sobre la superficie que cubre.

Uso:
    python3 scripts/verify_caja_mutations.py
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
        # Una mutacion con SyntaxError muere antes de correr la prueba: eso NO
        # verifica la defensa, verifica que Python sabe parsear. Se descarta aqui
        # para que el conteo no mienta.
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
        nombre="crear_caja no escribe la apertura en el kardex",
        archivo="app/services/caja_chica_service.py",
        viejo="    db.add(apertura)\n    await _auditar(\n",
        nuevo="    # MUTACION: la caja nace sin su evidencia de fondo\n    await _auditar(\n",
        prueba="test_crear_caja_escribe_la_apertura",
    ),
    Mutacion(
        nombre="la apertura directa vuelve a ser una puerta para sumar saldo",
        archivo="app/services/caja_chica_service.py",
        viejo=(
            "    if datos.tipo is TipoMovimientoCaja.APERTURA:\n"
            '        raise ErrorCaja("La apertura solo se registra al crear la caja", 422)\n'
        ),
        nuevo=(
            "    if False and datos.tipo is TipoMovimientoCaja.APERTURA:\n"
            '        raise ErrorCaja("La apertura solo se registra al crear la caja", 422)\n'
        ),
        prueba="test_apertura_directa_no_es_otra_puerta_para_sumar",
    ),
    Mutacion(
        nombre="la diferencia del cierre se fia del numero que manda el cliente",
        archivo="app/services/caja_chica_service.py",
        viejo="            caja.diferencia_cierre = _centavos(contado - saldo)\n",
        nuevo="            caja.diferencia_cierre = contado  # MUTACION: no se resta\n",
        prueba="test_diferencia_la_calcula_el_servidor_y_no_bloquea",
    ),
    Mutacion(
        nombre="el movimiento de cierre vuelve a descontar el dinero contado",
        archivo="app/services/caja_chica_service.py",
        viejo='                monto=Decimal("0.00"),\n                notas=(\n',
        nuevo=(
            "                monto=contado,  # MUTACION: el cierre mueve dinero\n"
            "                notas=(\n"
        ),
        prueba="test_diferencia_la_calcula_el_servidor_y_no_bloquea",
    ),
    Mutacion(
        nombre="la auditoria de caja no lleva a quien firmo",
        archivo="app/services/caja_chica_service.py",
        viejo="        usuario_id=str(usuario_id),\n        rol=rol.value if isinstance(rol, RolEmpresa) else str(rol),\n",
        nuevo=(
            "        usuario_id=None,  # MUTACION: nadie firma\n"
            "        rol=rol.value if isinstance(rol, RolEmpresa) else str(rol),\n"
        ),
        prueba="test_crear_caja_firma_con_el_token",
    ),
    Mutacion(
        nombre="registrar_movimiento no deja rastro con su propio nombre",
        archivo="app/services/caja_chica_service.py",
        viejo='        accion="registrar_movimiento_caja",\n',
        nuevo='        accion="movimiento_caja",  # MUTACION: nombre generico\n',
        prueba="test_movimiento_y_cierre_dejan_rastro",
    ),
    Mutacion(
        nombre="el cruce bancario exige cargo positivo y no reconoce el retiro",
        archivo="app/services/caja_chica_service.py",
        viejo="            if abs(_centavos(banco.amount)) != monto:\n                continue\n",
        nuevo=(
            "            if _centavos(banco.amount) != monto:  # MUTACION: sin abs\n"
            "                continue\n"
        ),
        prueba="test_reposicion_se_cruza_con_el_cargo_del_banco",
    ),
    Mutacion(
        nombre="dos cargos del mismo monto se cruzan aunque la referencia difiera",
        archivo="app/services/caja_chica_service.py",
        viejo=(
            "            if referencia and ref_banco and referencia != ref_banco:\n"
            "                continue\n"
        ),
        nuevo=(
            "            if False:  # MUTACION: se ignora la referencia\n"
            "                continue\n"
        ),
        prueba="test_la_referencia_distingue_dos_cargos_del_mismo_monto",
    ),
    Mutacion(
        nombre="la conciliacion de caja marca el cargo bancario como reconciliado",
        archivo="app/services/caja_chica_service.py",
        viejo="            par = banco\n            break\n",
        nuevo=(
            "            par = banco\n"
            "            banco.is_reconciled = True  # MUTACION: escribe en el banco\n"
            "            await db.commit()\n"
            "            break\n"
        ),
        prueba="test_reposicion_se_cruza_con_el_cargo_del_banco",
    ),
    Mutacion(
        nombre="una reposicion en efectivo acepta datos de banco",
        archivo="app/services/caja_chica_service.py",
        viejo=(
            "    if datos.origen_fondo is not OrigenFondo.BANCO and (\n"
            "        datos.banco_nombre or datos.referencia_bancaria\n"
            "    ):\n"
        ),
        nuevo=(
            "    if False and (\n"
            "        datos.banco_nombre or datos.referencia_bancaria\n"
            "    ):  # MUTACION: ya no se comprueba el origen\n"
        ),
        prueba="test_el_servicio_tambien_lo_rechaza_sin_pasar_por_el_schema",
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
