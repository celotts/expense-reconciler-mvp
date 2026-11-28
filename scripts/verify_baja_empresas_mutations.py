#!/usr/bin/env python3
"""Verifica por MUTACION las defensas del estado de empresa.

Cada mutacion quita una defensa y comprueba que al menos un test muere. Una
defensa que sobrevive se lee como garantia y no esta defendiendo nada.

    python3 scripts/verify_baja_empresas_mutations.py
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SERVICIO = RAIZ / "app/services/empresas_service.py"
DEPS = RAIZ / "app/core/deps.py"
AUTH = RAIZ / "app/api/auth.py"
ROUTER = RAIZ / "app/api/companies.py"
INIT = RAIZ / "db/init.sql"

MUTACIONES = [
    (
        "M1 eliminar deja de comprobar datos de la empresa",
        SERVICIO,
        "    filas = await datos_de_la_empresa(db, company.id)\n    if filas:\n        raise EmpresaConDatosError(filas)",
        "    filas = await datos_de_la_empresa(db, company.id)\n    if False:\n        raise EmpresaConDatosError(filas)",
        "test_eliminar_exige_empresa_vacia_y_no_dejar_sistema_sin_activa",
    ),
    (
        "M2 desactivar deja de impedir quedarse sin empresa activa con datos",
        SERVICIO,
        "    if await cantidad_de_empresas_activas(db) == 1 and await hay_datos_en_el_sistema(db):\n        raise UltimaEmpresaActivaError()",
        "    if False:\n        raise UltimaEmpresaActivaError()",
        "test_no_se_puede_bajar_la_unica_activa_con_datos",
    ),
    (
        "M3 el banco deja de contar como dato de trabajo",
        SERVICIO,
        "    TicketModel,\n    BankTransactionModel,\n    AccountingMappingModel,",
        "    TicketModel,\n    AccountingMappingModel,",
        "test_cuenta_filas_de_trabajo",
    ),
    (
        "M4 una eliminada vuelve a ser reactivable",
        SERVICIO,
        "    if company.estado == EstadoEmpresa.ELIMINADO.value:\n        raise EmpresaEliminadaError()",
        "    if False:\n        raise EmpresaEliminadaError()",
        "test_una_eliminada_no_se_reactiva",
    ),
    (
        "M5 cambiar_estado deja de ser idempotente",
        SERVICIO,
        "    if company.estado == estado.value:\n        return False",
        "    if False:\n        return False",
        "test_cambiar_estado_es_idempotente",
    ),
    (
        "M6 `empresa_activa` vuelve a aceptar empresas cerradas",
        DEPS,
        "                    UsuarioEmpresaModel.activo.is_(True),\n                    CompanyModel.estado == EstadoEmpresa.ACTIVO.value,",
        "                    UsuarioEmpresaModel.activo.is_(True),",
        "test_una_empresa_bajada_no_autoriza_escrituras",
    ),
    (
        "M7 la guardia global deja de parar el trabajo sin empresa activa",
        DEPS,
        "        if await _tiene_membresias(usuario, db):\n            raise HTTPException(\n                status_code=status.HTTP_403_FORBIDDEN,\n                detail=await _detalle_sin_empresa_activa(request, usuario, db),\n            )\n        return\n",
        "        if False:\n            raise HTTPException(\n                status_code=status.HTTP_403_FORBIDDEN,\n                detail=await _detalle_sin_empresa_activa(request, usuario, db),\n            )\n        return\n",
        "test_la_guardia_para_a_quien_solo_tiene_empresas_eliminadas",
    ),
    (
        "M8 la recuperacion queda encerrada por el propio cerrojo",
        DEPS,
        "            if await _tiene_membresias(usuario, db) and not _es_recuperacion(request):",
        "            if await _tiene_membresias(usuario, db) and True:",
        "test_la_recuperacion_sigue_abierta_con_la_unica_eliminada",
    ),
    (
        "M9 `/auth/empresas` deja de listar la empresa anulada",
        AUTH,
        "                CompanyModel.estado.in_(\n                    (EstadoEmpresa.ACTIVO.value, EstadoEmpresa.INACTIVO.value)\n                ),",
        "                CompanyModel.estado == EstadoEmpresa.ACTIVO.value,",
        "test_la_anulada_sigue_en_la_lista_para_explicar_el_motivo",
    ),
    (
        "M10 desactivar no corre el cerrojo de ultima activa",
        ROUTER,
        "        EstadoEmpresa.INACTIVO, empresas_service.verificar_desactivar,",
        "        EstadoEmpresa.INACTIVO, empresas_service.verificar_activar,",
        "test_ultima_activa_con_datos_no_se_da_de_baja",
    ),
    (
        "M11 `init.sql` deja de declarar la columna `estado`",
        INIT,
        "    estado VARCHAR(20) NOT NULL DEFAULT 'activo'",
        "    estado_antiguo VARCHAR(20) NOT NULL DEFAULT 'activo'",
        "test_init_sql_espeja_la_columna_y_registra_la_migracion",
    ),
    (
        "M12 `init.sql` deja de registrar la migracion 0029",
        INIT,
        "    ('0029_estado_empresa.sql')",
        "    ('0029_empresas_antiguas.sql')",
        "test_init_sql_espeja_la_columna_y_registra_la_migracion",
    ),
    (
        "M13 `empresa_activa` permite escribir en una empresa anulada",
        DEPS,
        "    permiso = _permiso_de_la_peticion(request)\n    if permiso is None:\n        consultables = await _membresias_consultables(usuario, db)\n        company_ids = {company_id for _rol, company_id, _estado in consultables}",
        "    permiso = None\n    if permiso is None:\n        consultables = await _membresias_consultables(usuario, db)\n        company_ids = {company_id for _rol, company_id, _estado in consultables}",
        "test_empresa_activa_rechaza_una_anulada_para_escribir",
    ),
    (
        "M14 la guardia global clasifica una escritura como consulta",
        DEPS,
        "    filas = consultables if permiso is None else activas",
        "    filas = await _membresias_consultables(usuario, db)",
        "test_no_se_digitaliza_pero_si_se_consulta",
    ),
    (
        "M15 anular no guarda la nota declarada",
        SERVICIO,
        "        company.motivo_baja = motivo or None",
        "        company.motivo_baja = None",
        "test_anular_guarda_el_motivo_y_activar_lo_limpia",
    ),
    (
        "M16 reactivar deja la nota vieja en la fila",
        SERVICIO,
        "        company.motivo_baja = None\n        company.bajada_en = None\n        company.bajada_por = None",
        "        company.bajada_en = None\n        company.bajada_por = None",
        "test_anular_guarda_el_motivo_y_activar_lo_limpia",
    ),
    (
        "M17 `/auth/empresas` no dice el motivo de la anulacion",
        AUTH,
        "            motivo_baja=company.motivo_baja,",
        "            motivo_baja=None,",
        "test_la_anulada_sigue_en_la_lista_para_explicar_el_motivo",
    ),
    (
        "M18 una empresa anulada conserva permisos de escritura",
        AUTH,
        "                else [\"leer\"]",
        "                else list(permisos_de_rol(rol))",
        "test_la_anulada_sigue_en_la_lista_para_explicar_el_motivo",
    ),
    (
        "M19 el listado dice que cualquier empresa se puede eliminar",
        SERVICIO,
        "    return {fila[0] for fila in filas if fila[0] is not None}",
        "    return set()",
        "test_el_boton_anular_sale_del_servicio_no_de_un_conteo_del_front",
    ),
]


def correr_tests() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/unit/test_empresas_service.py",
         "tests/integration/test_baja_de_empresas.py",
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ, capture_output=True, text=True,
    )


def main() -> int:
    originales = {archivo: archivo.read_text() for archivo in
                  {m[1] for m in MUTACIONES}}
    supervivientes = []

    print("Baseline (sin mutacion, tiene que pasar)")
    base = correr_tests()
    if base.returncode != 0:
        print("  FALLA: el baseline ya esta roto, las mutaciones no dicen nada")
        print(base.stdout[-3000:])
        return 1
    print("  OK\n")

    for nombre, archivo, viejo, nuevo, test_esperado in MUTACIONES:
        actual = archivo.read_text()
        if viejo not in actual:
            print(f"  {nombre}\n    NO SE APLICA: el patron viejo no esta en el archivo")
            supervivientes.append(nombre)
            continue
        archivo.write_text(actual.replace(viejo, nuevo, 1))
        try:
            r = correr_tests()
        finally:
            archivo.write_text(actual)

        murio = r.returncode != 0
        marco = "SI" if test_esperado in r.stdout else "NO"
        if not murio:
            supervivientes.append(nombre)
            estado = "SOBREVIVIO  <<<"
        else:
            estado = "murio"
        print(f"  {nombre}")
        print(f"    {estado}  (y el test '{test_esperado}' aparecio en la salida: {marco})")

    print()
    if supervivientes:
        print(f"FALLA: {len(supervivientes)} defensa(s) sin test que muera:")
        for s in supervivientes:
            print(f"  - {s}")
        return 1
    print(f"OK: las {len(MUTACIONES)} defensas mueren si se quitan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
