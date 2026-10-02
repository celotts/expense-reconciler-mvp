#!/usr/bin/env python3
"""Verificacion por mutacion de la ruta de escaneo de carpeta.

Como los demas `scripts/verify_*_mutations.py`: comprueba que las defensas NO SE
PUEDEN QUITAR EN SILENCIO. Un test que verifica que la carpeta se valida pasa
igual si alguien comenta la validacion y devuelve una constante. Este script
muta el codigo de verdad, corre la suite, y falla si la suite sigue en verde.

Que es la diferencia entre "hay un test" y "el test muerde": aqui se quita la
defensa a proposito y se ve si algo se da cuenta.

Uso:
    python3 scripts/verify_scan_mutations.py

Sale con 0 si todas las mutaciones fueron detectadas, con 1 si alguna paso
desapercibida.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


@dataclass
class Mutacion:
    """Un cambio que DEBERIA romper algo."""

    nombre: str
    archivo: str
    viejo: str
    nuevo: str
    # Que prueba tiene que morir. Se busca por nombre en la salida de pytest; si
    # el nombre esta vacio, basta con que la suite falle.
    prueba: str = ""

    def aplicar(self) -> tuple[Path, str]:
        ruta = RAIZ / self.archivo
        original = ruta.read_text(encoding="utf-8")
        if self.viejo not in original:
            raise AssertionError(
                f"no se encontro el texto a mutar en {self.archivo}:\n"
                f"  {self.viejo[:120]!r}\n"
                "Si el codigo cambio, esta mutacion esta obsoleta y hay que "
                "actualizarla. No la borres sin pensarlo: es la que hacia que "
                "quitar la defensa se notara."
            )
        ruta.write_text(original.replace(self.viejo, self.nuevo, 1), encoding="utf-8")
        return ruta, original


MUTACIONES = [
    # --- 1. La carpeta sale de la configuracion, no de la peticion ---------
    Mutacion(
        nombre="el schema del escaner acepta una carpeta del cliente",
        archivo="app/schemas/scan.py",
        viejo="    reprocesar: bool = Field(",
        nuevo="    folder_path: str | None = Field(None)\n    reprocesar: bool = Field(",
        prueba="test_el_schema_no_tiene_campo_de_carpeta",
    ),
    # --- 2. Contencion de rutas -------------------------------------------
    Mutacion(
        nombre="la contencion se comprueba con startswith en vez de is_relative_to",
        archivo="app/services/scan_service.py",
        viejo="    if not candidata.is_relative_to(base):",
        nuevo="    if not str(candidata).startswith(str(base)) and False:",
        prueba="test_se_usa_is_relative_to_y_no_startswith",
    ),
    Mutacion(
        nombre="la contencion se comprueba ANTES de resolver (se la salta un symlink)",
        archivo="app/services/scan_service.py",
        viejo="    candidata = (base / relative_path).resolve()",
        nuevo="    candidata = base / relative_path",
        prueba="test_la_comprobacion_viene_despues_de_resolve",
    ),
    Mutacion(
        nombre="el recorrido vuelve a seguir symlinks",
        archivo="app/services/scan_service.py",
        viejo="os.walk(base, followlinks=False)",
        nuevo="os.walk(base, followlinks=True)",
        prueba="test_el_recorrido_ignora_un_enlace_a_directorio",
    ),
    # --- 3. El formato por bytes, no por extension -------------------------
    Mutacion(
        nombre="la extension vuelve a elegir la ruta de lectura",
        archivo="app/services/scan_service.py",
        viejo="    tipo = detectar_tipo_real(contenido)",
        nuevo='    tipo = "image" if vista.extension in {".jpg", ".png", ".jpeg"} else "pdf"',
        prueba="test_la_extension_no_elige_la_ruta_de_lectura",
    ),
    # --- 4. El hash decide, no la fecha ------------------------------------
    Mutacion(
        nombre="la idempotencia se decide por mtime en vez de por hash",
        archivo="app/services/scan_service.py",
        viejo="        if hash_actual == fila.content_hash and not forzar:",
        nuevo="        if True:",
        prueba="test_la_comparacion_usa_sha256_del_contenido",
    ),
    # --- 5. El trabajo humano no se pisa -----------------------------------
    Mutacion(
        nombre="se sobreescribe un ticket ya revisado por una persona",
        archivo="app/services/scan_service.py",
        viejo="        if _es_revisado_por_humano(ticket):",
        nuevo="        if False:",
        prueba="test_la_comprobacion_se_usa_antes_de_actualizar",
    ),
    Mutacion(
        nombre="la regla del trabajo humano desaparece por completo",
        archivo="app/services/scan_service.py",
        viejo='    return ticket.reviewed_at is not None',
        nuevo="    return False",
        prueba="test_la_comprobacion_de_revision_humana_existe",
    ),
    # --- 6 y 7. La confianza del OCR ---------------------------------------
    Mutacion(
        nombre="OCR usa la confianza de PDF (un total mal leido se auto-aprueba)",
        archivo="app/services/capture.py",
        viejo="    return confianza_por_campos_ocr if source is ConfidenceSource.OCR else confianza_por_campos",
        nuevo="    return confianza_por_campos",
        prueba="test_ocr_tiene_su_propia_tabla_de_confianza",
    ),
    Mutacion(
        nombre="el piso de caracteres del OCR sube y rechaza tickets reales",
        archivo="app/services/capture.py",
        viejo="OCR_MIN_CHARS_PARA_INTENTAR = 60",
        nuevo="OCR_MIN_CHARS_PARA_INTENTAR = 400",
        prueba="test_el_piso_de_caracteres_no_esta_por_encima_de_un_ticket_minimo",
    ),
    Mutacion(
        nombre="pytesseract se importa al arrancar (la app no levanta sin Tesseract)",
        archivo="app/services/capture.py",
        viejo="from app.services.ocr import OCRNoDisponible",
        nuevo="import pytesseract\nfrom app.services.ocr import OCRNoDisponible",
        prueba="test_pytesseract_no_se_importa_al_arrancar",
    ),
    # --- 10. El registro guarda la ruta relativa --------------------------
    Mutacion(
        nombre="el registro guarda la ruta absoluta",
        archivo="app/services/scan_service.py",
        viejo="    return ruta.relative_to(raiz()).as_posix()",
        nuevo="    return str(ruta)",
        prueba="test_la_ruta_se_guarda_en_posix",
    ),
    Mutacion(
        nombre="el modelo pierde el UNIQUE de relative_path",
        archivo="app/models/scan_file.py",
        viejo='        UniqueConstraint("relative_path", name="uq_scan_files_relative_path"),',
        nuevo="        # quitado a proposito para esta mutacion",
        prueba="test_el_modelo_declara_el_unico_de_relative_path",
    ),
    Mutacion(
        nombre="las acciones validas divergen entre el modelo y el SQL",
        archivo="db/migrations/0005_scan_ledger.sql",
        viejo="'REINTENTO', 'BORRADO'",
        nuevo="'REINTENTO', 'BORRADO', 'INVENTADO'",
        prueba="test_las_acciones_validas_coinciden_entre_modelo_y_sql",
    ),
]


def _correr_suite() -> tuple[bool, str]:
    """Corre los tests del escaneo. Devuelve (paso, salida)."""
    proceso = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "tests/integration/test_scan_api.py", "-q", "--no-header", "-x"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    return proceso.returncode == 0, proceso.stdout + proceso.stderr


def main() -> int:
    # La suite tiene que estar verde ANTES de mutar. Si no lo esta, no hay forma
    # de saber si una mutacion fue detectada o si la base ya estaba rota.
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
            print("      La suite sigue en verde con la defensa quitada.")
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
