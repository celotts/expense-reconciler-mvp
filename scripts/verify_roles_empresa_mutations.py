#!/usr/bin/env python3
"""Verificacion por mutacion de empresa activa, roles y alcance de operator.

Un test que comprueba que una caja ajena da 404 pasaria igual si el servicio
aceptara cualquier `company_id`: basta que el test nunca pida esa caja con otra
empresa. Aqui se quita cada defensa a proposito y se exige que la prueba que la
sostiene muera. Un verificador que no se aplica y sigue contando es peor que no
tener verificador, así que una mutacion obsoleta tambien cuenta como fallo.

Uso:
    python3 scripts/verify_roles_empresa_mutations.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PRUEBAS = [
    "tests/unit/test_roles_y_empresa_activa.py",
    "tests/unit/test_caja_chica_roles.py",
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
        ruta.write_text(original.replace(self.viejo, self.nuevo, 1), encoding="utf-8")
        return ruta, original


MUTACIONES = [
    Mutacion(
        nombre="la cabecera X-Company-ID se ignora y entra la primera empresa",
        archivo="app/core/deps.py",
        viejo="    if elegida not in company_ids:\n",
        nuevo="    if False:  # MUTACION: cualquiera puede elegir empresa\n",
        prueba="test_empresa_ajena_se_rechaza",
    ),
    Mutacion(
        nombre="una membresia dada de baja sigue dando acceso",
        archivo="app/core/deps.py",
        viejo=(
            "        (\n"
            "            await db.execute(\n"
            "                select(UsuarioEmpresaModel.company_id).where(\n"
            "                    UsuarioEmpresaModel.user_id == usuario.id,\n"
            "                    UsuarioEmpresaModel.activo.is_(True),\n"
        ),
        nuevo=(
            "        (\n"
            "            await db.execute(\n"
            "                select(UsuarioEmpresaModel.company_id).where(\n"
            "                    UsuarioEmpresaModel.user_id == usuario.id,\n"
            "                    True,  # MUTACION: no se mira `activo`\n"
        ),
        prueba="test_membership_inactiva_no_entra",
    ),
    Mutacion(
        nombre="con dos empresas se adivina la activa en vez de pedirla",
        archivo="app/core/deps.py",
        viejo="        if len(company_ids) > 1:\n",
        nuevo="        if False:  # MUTACION: se elige la primera\n",
        prueba="test_dos_empresas_sin_header_piden_eleccion",
    ),
    Mutacion(
        nombre="el rol se lee de `perfil` y no de la membresia",
        archivo="app/core/deps.py",
        viejo="    return RolEmpresa(rol)\n",
        nuevo=(
            "    return RolEmpresa(\n"
            "        usuario.perfil\n"
            "        if usuario.perfil in {r.value for r in RolEmpresa}\n"
            "        else rol\n"
            "    )  # MUTACION: texto libre\n"
        ),
        prueba="test_perfil_no_es_autorizacion",
    ),
    Mutacion(
        nombre="obtener_caja no comprueba la empresa",
        archivo="app/services/caja_chica_service.py",
        viejo="    if caja is None or caja.company_id != company_id:\n",
        nuevo="    if caja is None:  # MUTACION: se ignora la empresa\n",
        prueba="test_caja_de_otra_empresa_da_404",
    ),
    Mutacion(
        nombre="el operator puede operar cajas que no son suyas",
        archivo="app/services/caja_chica_service.py",
        viejo=(
            "    if rol is not None and rol.es_operador "
            "and caja.responsable_id != usuario_id:\n"
        ),
        nuevo="    if False:  # MUTACION: se ignora el alcance\n",
        prueba="test_operator_no_ve_la_caja_de_otro",
    ),
    Mutacion(
        nombre="el listado del operator incluye cajas ajenas",
        archivo="app/services/caja_chica_service.py",
        viejo="        stmt = stmt.where(CajaChicaModel.responsable_id == usuario_id)\n",
        nuevo="        pass  # MUTACION: sin filtro de responsable\n",
        prueba="test_listado_del_operator_no_incluye_cajas_ajenas",
    ),
    Mutacion(
        nombre="crear_caja acepta una empresa distinta de la activa",
        archivo="app/services/caja_chica_service.py",
        viejo=(
            "    if datos.company_id != company_id:\n"
            '        raise ErrorCaja("La empresa de la caja no coincide '
            'con la empresa activa")\n'
        ),
        nuevo="    # MUTACION: ya no se compara la empresa del cuerpo\n",
        prueba="test_cuerpo_con_otra_empresa_da_409",
    ),
    Mutacion(
        nombre="un operator puede abrir caja a nombre de otro",
        archivo="app/services/caja_chica_service.py",
        viejo="    if rol.es_operador and datos.responsable_id != usuario_id:\n",
        nuevo="    if False:  # MUTACION: ya no se mira el responsable\n",
        prueba="test_operator_no_puede_abrir_caja_para_otro",
    ),
    Mutacion(
        nombre="duplicados busca en la empresa que diga el cuerpo",
        archivo="app/api/duplicados.py",
        viejo="    if datos.company_id != company_id:\n",
        nuevo="    if False:  # MUTACION: se cree del cuerpo\n",
        prueba="test_duplicados_rechaza_empresa_del_cuerpo",
    ),
]


def _correr_suite() -> tuple[bool, str]:
    entorno = os.environ.copy()
    # En esta maquina el shell exporta una SECRET_KEY corta y el flag de reset
    # en true; ambos ganan sobre el .env. El repo no depende de eso: se quitan
    # solo para el subproceso de pruebas, igual que se hace al correr la suite.
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
