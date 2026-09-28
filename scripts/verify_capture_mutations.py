#!/usr/bin/env python3
"""Verifica que los tests de la ruta de captura detecten las regresiones.

Un test que pasa no demuestra nada sobre el codigo: puede que este probando lo
que cree, o puede que no este probando nada. La unica forma de saber cual de
las dos es romper el codigo a proposito y ver si el test se da cuenta.

Se hace con mutaciones: cada una cambia una pieza de la logica de produccion de
forma sutil, en general un comentario que explica por que se hizo asi. Si una
mutacion sobrevive, significa que ningun test cubre esa pieza, y el codigo
puede volver a romperse sin que nadie se entere.

El ejemplo del porque importa: una mutacion de este conjunto cambio
`date.today()` por `utcnow().date()`. Es un cambio de una palabra,
que no se nota en la mayoria de las revisiones, y hace que un gasto quede
fechado manana. Los
tests de fecha de negocio lo detectaron.

Uso:  python3 scripts/verify_capture_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# (nombre, archivo, texto original, texto mutado, tests que deben morir)
MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "un PDF con texto se manda a vision igualmente",
        "app/services/capture.py",
        "    return await _cascada_texto(texto, ConfidenceSource.PDF_TEXT, extract_from_text)",
        "    return await _vision_pdf(content, extract_from_image)",
        ["tests/unit/test_capture.py::TestPdfConTexto"],
    ),
    (
        "el modelo se consulta antes que las reglas",
        "app/services/capture.py",
        "    reglas = _marcar_por_reglas(_parse_receipt_text(texto), source)\n\n    if _es_extraccion_util(reglas):\n        return reglas",
        "    if extract_from_text is not None:\n        return invoice_to_result(await extract_from_text(texto))\n    reglas = _marcar_por_reglas(_parse_receipt_text(texto), source)\n\n    if _es_extraccion_util(reglas):\n        return reglas",
        ["tests/unit/test_capture.py::TestPdfConTexto"],
    ),
    (
        "la confianza no depende de la evidencia encontrada",
        "app/services/capture.py",
        "    for (rfc, subtotal, fecha), confianza in CONFIANZA_POR_CAMPOS:\n        if (rfc, subtotal, fecha) == (tiene_rfc, tiene_subtotal, tiene_fecha):\n            return confianza",
        "    return 0.90",
        [
            "tests/unit/test_capture.py::TestTablaDeConfianza",
            "tests/unit/test_capture.py::TestPdfConTexto",
        ],
    ),
    (
        "los centinelas de la IA llegan como nombre de comercio",
        "app/services/capture.py",
        "    if nombre in SENTINELS_SIN_PROVEEDOR or not nombre:\n        nombre = UNKNOWN_PROVIDER",
        "    pass",
        [
            "tests/unit/test_capture.py::TestNormalizacionDeCentinelas",
            "tests/integration/test_capture_pipeline.py::TestLoQueLaIaNoPudoLeer",
        ],
    ),
    (
        "el fallo del sistema se reporta como documento ilegible",
        "app/services/capture.py",
        "    return FALLOS_DEL_MODELO.get((invoice.provider_name or \"\").strip())",
        "    return None",
        [
            "tests/unit/test_capture.py::TestFallosDelSistema",
            "tests/integration/test_capture_pipeline.py::TestLoQueLaIaNoPudoLeer",
        ],
    ),
    (
        "el error concreto de pagina no llega a la cola",
        "app/services/capture.py",
        "        return _ilegible(f\"ninguna pagina del PDF se pudo leer con vision ({detalle})\")",
        "        return _ilegible(\"ninguna pagina del PDF se pudo leer con vision\")",
        [
            "tests/unit/test_capture.py::TestFallosDelSistema",
            "tests/integration/test_capture_pipeline.py::TestLoQueLaIaNoPudoLeer",
        ],
    ),
    (
        "un documento ilegible se pierde en vez de ir a la cola",
        "app/services/capture.py",
        "    return resultado.provider_name != UNKNOWN_PROVIDER and resultado.total_amount > 0",
        "    return resultado.provider_name != UNKNOWN_PROVIDER and resultado.total_amount > 1000000",
        [
            "tests/unit/test_capture.py::TestPdfConTexto",
            "tests/unit/test_capture.py::TestTiposDeArchivo",
            "tests/integration/test_capture_pipeline.py::TestLoQueSeGuardaDicenLaVerdad",
        ],
    ),
    (
        "el origen se pierde y todo se guarda como lectura de modelo",
        "app/api/tickets.py",
        "        source=extracted.confidence_source,",
        "        source=ConfidenceSource.LLM,",
        [
            "tests/integration/test_capture_pipeline.py::TestLoQueSeGuardaDicenLaVerdad",
        ],
    ),
    (
        "una fecha ausente se reemplaza por la fecha UTC",
        "app/api/tickets.py",
        "        expense_date=extracted.expense_date or date.today(),",
        "        expense_date=extracted.expense_date or utcnow().date(),",
        [
            "tests/integration/test_capture_pipeline.py::TestFechaInventadaEnLaFila",
        ],
    ),
    (
        "una fecha ausente se guarda como fecha de hoy sin avisar",
        "app/api/tickets.py",
        "        expense_date=extracted.expense_date or date.today(),",
        "        expense_date=extracted.expense_date or extracted.expense_date,",
        [
            "tests/integration/test_capture_pipeline.py::TestFechaInventadaEnLaFila",
        ],
    ),
    (
        "el parser vuelve a exigir mayusculas sostenidas",
        "app/services/parser_service.py",
        "        if re.search(r\"[A-Z]{3,}\", line):\n            return True\n        palabras = [\n            p\n            for p in re.findall(\n                r\"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]+\", line\n            )\n            if len(p) >= 3 and p[0].isupper()\n        ]\n        return len(palabras) >= 2",
        "        return bool(re.search(r\"[A-Z]{3,}\", line))",
        ["tests/unit/test_parser_service.py::TestProveedorEnMinusculasSostenidas"],
    ),
    (
        "el subtotal vuelve a quedarse sin extraer",
        "app/services/parser_service.py",
        "        subtotal_match = re.search(\n            r\"(?:^|\\s)sub[\\s-]?total[\\s:]*[$€]?\\s*([\\d.,]+)\",\n            line, re.IGNORECASE,\n        )\n        if subtotal_match:\n            try:\n                subtotal = parse_mexican_number(subtotal_match.group(1))\n            except (InvalidOperation, ValueError):\n                pass",
        "        pass",
        [
            "tests/unit/test_parser_service.py::TestExtraccionDeSubtotal",
            "tests/integration/test_capture_pipeline.py::TestVerificacionAritmeticaEnLaRutaBarata",
        ],
    ),
    (
        "el render de paginas falla en silencio como antes",
        "app/services/parser_service.py",
        "    documento = pdfium.PdfDocument(file_content)\n    try:",
        "    return []\n    documento = pdfium.PdfDocument(file_content)\n    try:",
        ["tests/unit/test_capture.py::TestPdfEscaneado"],
    ),
    (
        "el tope de paginas a vision desaparece",
        "app/services/capture.py",
        "            max_pages=PDF_MAX_PAGINAS_A_VISION,",
        "            max_pages=100,",
        ["tests/unit/test_capture.py::TestPdfEscaneado"],
    ),
]


def _correr(objetivos: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *objetivos, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    return proc.returncode == 0, proc.stdout


def main() -> int:
    print("Verificacion por mutacion de la ruta de captura\n")
    supervivientes: list[str] = []

    base_ok, salida = _correr(["tests/"])
    if not base_ok:
        print("  La suite ya falla antes de mutar. Arregla eso primero:")
        print(salida[-2500:])
        return 1
    print(f"  Base: suite en verde\n")

    with tempfile.TemporaryDirectory() as temporal:
        for nombre, archivo, original, mutado, objetivos in MUTACIONES:
            ruta = RAIZ / archivo
            if not ruta.exists():
                print(f"  ?? {nombre}: no existe {archivo}")
                supervivientes.append(nombre)
                continue

            fuente = ruta.read_text(encoding="utf-8")
            if original not in fuente:
                print(f"  ?? {nombre}: el patron original no aparece en {archivo}")
                print(f"     (el codigo cambio; la mutacion esta obsoleta)")
                supervivientes.append(nombre)
                continue

            copia = Path(temporal) / ruta.name
            shutil.copy2(ruta, copia)
            try:
                ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
                paso, salida = _correr(objetivos)
            finally:
                shutil.copy2(copia, ruta)

            if paso:
                supervivientes.append(nombre)
                print(f"  SOBREVIVIO  {nombre}")
                print(f"              ningun test de {', '.join(objetivos)} lo nota")
            else:
                muertos = salida.count("failed")
                print(f"  murio       {nombre}")

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
