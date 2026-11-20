#!/usr/bin/env python3
"""Verificacion por mutacion de la carga explicita por lote.

`POST /api/v1/expenses/upload-batch` estuvo muerto hasta que se reescribio como
adaptador de `procesar_archivo`. Un test que comprueba que el periodo cerrado se
respeta pasaria igual si alguien desconectara el callable de validacion y el
router devolviera 200 para todo: por eso aqui se quita cada defensa a proposito
y se exige que la prueba correspondiente muera.

Uso:
    python3 scripts/verify_expenses_batch_mutations.py

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
    # --- 1. La politica de periodo cerrado se inyecta al pipeline ------------
    Mutacion(
        nombre="la validacion de periodo cerrado se desconecta del pipeline",
        archivo="app/routers/expenses.py",
        viejo="                validar_antes_de_guardar=validar_periodo,\n",
        nuevo="                validar_antes_de_guardar=None,\n",
        prueba="test_sin_override_no_crea_ticket",
    ),
    # --- 2. La firma del override no se escribe en el ticket ------------------
    Mutacion(
        nombre="la traza del override se descarta al guardar el ticket",
        archivo="app/services/scan_service.py",
        viejo="            aplicar_override_en_ticket(ticket, traza_periodo)\n",
        nuevo="            pass  # MUTACION: se descarta la firma del override\n",
        prueba="test_override_con_motivo_firma_con_el_token",
    ),
    # --- 3. El origen de la carga se pierde -----------------------------------
    Mutacion(
        nombre="la carga por lote se registra como escaneo de carpeta",
        archivo="app/routers/expenses.py",
        viejo="                source_type=SourceType.BULK,\n",
        nuevo="                source_type=SourceType.DIRECTORY,\n",
        prueba="test_sube_y_devuelve_datos_con_veredicto",
    ),
    # --- 4. El dedup deriva del nombre, no de los bytes -----------------------
    Mutacion(
        nombre="la clave de dedup sale del nombre que mando el cliente",
        archivo="app/routers/expenses.py",
        viejo='            relative_path=f"{sha256}.{extension}",\n',
        nuevo="            relative_path=nombre,\n",
        prueba="test_mismo_bytes_no_crea_dos_tickets",
    ),
    # --- 5. La empresa inexistente no se comprueba ----------------------------
    Mutacion(
        nombre="un company_id inventado crea tickets huerfanos",
        archivo="app/routers/expenses.py",
        viejo="    if empresa is None:\n",
        nuevo="    if False:  # MUTACION: ya no se comprueba la empresa\n",
        prueba="test_company_inexistente_es_404",
    ),
    # --- 6. La respuesta pierde los datos del papel --------------------------
    Mutacion(
        nombre="la respuesta devuelve el veredicto sin los datos del papel",
        archivo="app/api/scans.py",
        viejo="        datos=detalle.datos,\n",
        nuevo="        datos=None,\n",
        prueba="test_sube_y_devuelve_datos_con_veredicto",
    ),
    # --- 7. El veredicto se lee de la lectura cruda, no de la fila -------------
    # El veredicto citable vive en `datos`, leido de la FILA del ticket en
    # `resolver_datos_de_tickets`. Si se leyera del resultado del lector, un
    # numero que el gate mando a revision llegaria sin su marca. El campo
    # `ScanItemResponse.extraction_status` de arriba es otra pregunta: solo se
    # rellena en la bandeja del escaner, no en un `CREADO` normal.
    Mutacion(
        nombre="el veredicto de `datos` sale del lector y no de la fila del ticket",
        archivo="app/services/scan_service.py",
        viejo='            "extraction_status": ticket.extraction_status,\n',
        nuevo='            "extraction_status": None,  # MUTACION: veredicto inventado\n',
        prueba="test_sube_y_devuelve_datos_con_veredicto",
    ),
]


def _correr_suite() -> tuple[bool, str]:
    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_expenses_batch.py", "-q", "--no-header", "-x"],
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
