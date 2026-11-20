#!/usr/bin/env python3
"""Verificacion por mutacion de la digitalizacion de un DIRECTORIO.

`POST /api/v1/expenses/digitalizar-directorio` abre una puerta que ningun otro
endpoint tiene: el operador escribe una RUTA. Un test que comprueba que responde
200 pasaria igual si la lista blanca se desconectara y el servidor leyera `/etc`.
Aqui se quita cada defensa a proposito y se exige que la prueba correspondiente
muera.

Uso:
    python3 scripts/verify_expenses_directorio_mutations.py

Sale con 0 si todas las mutaciones fueron detectadas, con 1 si alguna paso
desapercibida o si una mutacion quedo obsoleta (no se pudo aplicar).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


@dataclass
class Mutacion:
    nombre: str
    archivo: str
    viejo: str
    nuevo: str
    prueba: str = ""

    def aplicar(self) -> tuple[Path, str]:
        ruta = RAIZ / self.archivo
        original = ruta.read_text(encoding="utf-8")
        if self.viejo not in original:
            raise AssertionError(
                f"no se encontro el texto a mutar en {self.archivo}:\n"
                f"  {self.viejo[:120]!r}\n"
                "Si el codigo cambio, esta mutacion esta obsoleta y hay que "
                "actualizarla. Un verificador que no se aplica y sigue contando "
                "es peor que uno que no existe."
            )
        ruta.write_text(original.replace(self.viejo, self.nuevo, 1), encoding="utf-8")
        return ruta, original


MUTACIONES = [
    # --- 1. La ruta escrita deja de estar acotada a las raices declaradas -----
    Mutacion(
        nombre="cualquier ruta del servidor se acepta como carpeta",
        archivo="app/services/digitalizacion_directorio.py",
        viejo="        candidata == raiz or candidata.is_relative_to(raiz) for raiz in raices\n",
        nuevo="        True for raiz in raices  # MUTACION: sin lista blanca\n",
        prueba="test_ruta_fuera_de_la_lista_blanca_es_400",
    ),
    # --- 2. El symlink deja de ser un rechazo ---------------------------------
    Mutacion(
        nombre="un enlace simbolico se sigue fuera de la carpeta permitida",
        archivo="app/services/digitalizacion_directorio.py",
        viejo="            if ruta.is_symlink():\n",
        nuevo="            if False:  # MUTACION: se siguen los enlaces\n",
        prueba="test_enlace_simbolico_se_reporta_y_no_se_sigue",
    ),
    # --- 3. El filtro de extension desaparece ---------------------------------
    Mutacion(
        nombre="se manda a leer tambien lo que no tiene extension permitida",
        archivo="app/services/digitalizacion_directorio.py",
        viejo="            if extension not in EXTENSIONES_PERMITIDAS:\n",
        nuevo="            if False:  # MUTACION: todo entra\n",
        prueba="test_mezcla_pdf_jpg_y_xml_y_nombra_los_rechazados",
    ),
    # --- 4. El nombre del rechazado se sustituye por un numero ----------------
    Mutacion(
        nombre="el archivo rechazado se reporta sin su nombre",
        archivo="app/services/digitalizacion_directorio.py",
        viejo='                        "nombre": ruta.name,\n                        "extension": extension or "(sin extension)",\n                        "motivo": (\n                            "extension no permitida',
        nuevo='                        "nombre": "(sin nombre)",\n                        "extension": extension or "(sin extension)",\n                        "motivo": (\n                            "extension no permitida',
        prueba="test_mezcla_pdf_jpg_y_xml_y_nombra_los_rechazados",
    ),
    # --- 5. La clave de dedup vuelve a ser el nombre --------------------------
    Mutacion(
        nombre="la clave de dedup es el nombre y no el hash de los bytes",
        archivo="app/services/digitalizacion_directorio.py",
        viejo='        relative_path=f"{sha256}.{extension}",\n',
        nuevo="        relative_path=ruta.name,  # MUTACION: colisionan dos carpetas\n",
        prueba="test_mismo_nombre_en_dos_carpetas_no_colisiona",
    ),
    # --- 6. El periodo cerrado no se comprueba en esta puerta -----------------
    Mutacion(
        nombre="la validacion de periodo cerrado se desconecta del directorio",
        archivo="app/services/digitalizacion_directorio.py",
        viejo="                validar_antes_de_guardar=validar_antes_de_guardar,\n",
        nuevo="                validar_antes_de_guardar=None,\n",
        prueba="test_sin_override_no_crea_ticket",
    ),
    # --- 7. Los archivos que quedan fuera del tope se callan ------------------
    Mutacion(
        nombre="el tope de corrida se reporta como cero",
        archivo="app/services/digitalizacion_directorio.py",
        viejo="    omitidos = max(0, len(admitidos) - tope)\n",
        nuevo="    omitidos = 0  # MUTACION: se calla lo que quedo fuera\n",
        prueba="test_el_tope_no_callos_rechazados",
    ),
    # --- 8. La empresa inexistente no se comprueba ----------------------------
    Mutacion(
        nombre="un company_id inventado crea tickets huerfanos desde el directorio",
        archivo="app/routers/expenses.py",
        viejo="    await _empresa_o_404(db, company_id)\n    actor = usuario.email or \"sistema\"\n\n    try:\n        ruta = directorio.resolver_carpeta(carpeta)\n",
        nuevo="    actor = usuario.email or \"sistema\"\n\n    try:\n        ruta = directorio.resolver_carpeta(carpeta)\n",
        prueba="test_company_inexistente_es_404",
    ),
    # --- 9. El tipo de los bytes deja de viajar en la respuesta ----------------
    Mutacion(
        nombre="la respuesta calla que tipo son los bytes",
        archivo="app/services/digitalizacion_directorio.py",
        viejo='        formatos.append(detectar_tipo_real(contenido) or "desconocido")\n',
        nuevo='        formatos.append("desconocido")  # MUTACION: no se detectan los bytes\n',
        prueba="test_mezcla_pdf_jpg_y_xml_y_nombra_los_rechazados",
    ),
    # --- 10. La configuracion que ve el front deja de ser la del back ----------
    Mutacion(
        nombre="el front no recibe las raices permitidas reales",
        archivo="app/api/scans.py",
        viejo='        "rutas_permitidas": [r.as_posix() for r in raices_permitidas()],\n',
        nuevo='        "rutas_permitidas": [],  # MUTACION: el campo de ruta se queda ciego\n',
        prueba="test_config_dice_rutas_y_extensiones_permitidas",
    ),
]


def _correr_suite() -> tuple[bool, str]:
    proceso = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            # Los dos archivos: la defensa de ruta se prueba por HTTP aqui y la
            # configuracion que la pantalla consume vive en `/scan/config`.
            "tests/integration/test_expenses_directorio.py",
            "tests/integration/test_scan_api.py",
            "-q", "--no-header", "-x",
        ],
        cwd=RAIZ,
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
            sobrevivieron.append(f"{mutacion.nombre} (obsoleta, no se pudo aplicar)")
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
                "sin valor" if not mutacion.prueba
                else ("la prueba indicada" if mutacion.prueba in salida else "OTRA prueba")
            )
            print(f"  [detectada]   {mutacion.nombre}  <- {detectada}")

    print()
    if sobrevivieron:
        print(f"{len(sobrevivieron)} mutaciones pasaron sin que nadie se di cuenta:")
        for nombre in sobrevivieron:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones fueron detectadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
