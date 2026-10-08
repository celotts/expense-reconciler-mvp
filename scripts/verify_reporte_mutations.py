#!/usr/bin/env python3
"""Verifica que los tests del informe de cierre detecten las regresiones.

Un test que pasa no demuestra nada sobre el codigo: puede que este probando lo
que cree, o puede que no este probando nada. La unica forma de saber cual de las
dos es romper el codigo a proposito y ver si el test se da cuenta.

Las mutaciones de aqui no son inventadas para este script. Cada una deshace
algo que se ha decidido y documentado en `docs/contrato-producto.md` §5, y varias
deshacen bugs que aparecieron **mientras se escribia este codigo** y que
quedarian en silencio si nadie los mira:

- El PDF imprimia literalmente "El periodo NO se puede declarar cerrado: None."
  cuando el motivo era `None`. Sobrevive si nadie comprueba que el documento no
  lleva datos inventados (R6).
- `puede_cerrarse` era un campo aparte de `pendientes`, y los dos se podian
  desincronizar. Sobrevive si se vuelve a separar.
- `bytes(pdf.output())` en vez de `pdf.output()`: en fpdf2 2.8 lo segundo es un
  `bytearray` y starlette revienta al codificar. Sobrevive porque el JSON del
  mismo router funciona bien y el fallo solo aparece en el `.pdf`.
- El filtro del extractor de pruebas descartaba el flujo comprimido porque el
  byte magico de zlib (`0x78`) es la letra `x`. Sobrevive si el test de "el JSON
  y el PDF dicen lo mismo" se relaja.

Uso:  python3 scripts/verify_reporte_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    # -----------------------------------------------------------------------
    # El DDL y los modelos tienen que decir lo mismo
    # -----------------------------------------------------------------------
    # Esta mutacion no es del informe: es de la clase de fallo que hace que el
    # informe no pueda ni empezar a leer un comprobante en una base nueva. Se
    # verifica aqui porque lo que se rompe es `db/init.sql`, que es donde el
    # gate lee sus columnas.
    (
        "el init.sql pierde las columnas que el modelo escribe",
        "db/init.sql",
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ieps_amount NUMERIC(12, 2);",
        "-- (columna borrada a proposito)",
        ["tests/unit/test_init_sql_espeja_los_modelos.py"],
    ),
    (
        "una columna sale solo en un comentario y no en el DDL",
        "db/init.sql",
        "ALTER TABLE compra_items ADD COLUMN IF NOT EXISTS iva_linea NUMERIC(12, 2);",
        "-- iva_linea se documento en el comentario de arriba",
        ["tests/unit/test_init_sql_espeja_los_modelos.py"],
    ),
    # -----------------------------------------------------------------------
    # R7 / R6: lo que no se pudo calcular se declara, y no se inventa
    # -----------------------------------------------------------------------
    (
        "el PDF imprime el motivo aunque no exista (dato inventado: None)",
        "app/services/reporte_cierre.py",
        "    if reporte.pendientes.motivo:",
        "    if True:",
        [
            "tests/unit/test_reporte_cierre.py::TestElDocumentoNoSeInventaDatos",
            "tests/unit/test_reporte_cierre.py::TestR3LosEstadosDelPeriodo",
            "tests/unit/test_reporte_cierre.py::TestLasSieteSeccionesEnOrden",
        ],
    ),
    (
        "la exactitud ausente se rellena con un cero en vez de declararse ausente",
        "app/services/reporte_cierre.py",
        "        exactitud = None\n        advertencia = (",
        '        exactitud = None\n        advertencia = (  # noqa\n        _ = ("',
        [
            "tests/unit/test_reporte_cierre.py::TestR7LoQueNoSePudoCalcularSeDeclara",
        ],
    ),
    # -----------------------------------------------------------------------
    # R3: los pendientes deciden si el periodo cierra
    # -----------------------------------------------------------------------
    (
        "los pendientes dejan de bloquear el cierre",
        "app/schemas/reporte.py",
        "        return not self.pendientes.hay_pendientes",
        "        return True",
        [
            "tests/integration/test_reporte_cierre.py::TestA8PendientesBloqueanElCierre",
            "tests/unit/test_reporte_cierre.py::TestR3LosEstadosDelPeriodo",
        ],
    ),
    (
        "el estado del periodo se aparta de los pendientes",
        "app/schemas/reporte.py",
        "    @computed_field  # type: ignore[prop-decorator]\n    @property\n    def puede_cerrarse(self) -> bool:\n        return not self.pendientes.hay_pendientes",
        "    puede_cerrarse: bool = True",
        [
            "tests/integration/test_reporte_cierre.py::TestA8PendientesBloqueanElCierre",
        ],
    ),
    (
        "una categoria en blanco cuenta como clasificada",
        "app/services/reporte_cierre.py",
        "                    func.trim(TicketModel.category) == \"\",",
        "                    TicketModel.category == \"\",",
        [
            "tests/integration/test_reporte_cierre.py::TestA8PendientesBloqueanElCierre"
            "::test_una_categoria_en_blanco_cuenta_como_sin_categoria",
        ],
    ),
    (
        "el pendiente sin categoria se cuenta sobre todo el historico",
        "app/services/reporte_cierre.py",
        "                TicketModel.expense_date >= inicio.date(),\n"
        "                TicketModel.expense_date < fin.date(),\n"
        "                or_(",
        "                or_(",
        [
            "tests/integration/test_reporte_cierre.py::TestA8PendientesBloqueanElCierre"
            "::test_el_sin_categoria_del_informe_es_solo_del_periodo",
        ],
    ),
    # -----------------------------------------------------------------------
    # R4: determinismo
    # -----------------------------------------------------------------------
    (
        "el PDF deja de sellar la fecha del periodo y usa el reloj",
        "app/services/reporte_cierre.py",
        "    pdf.set_creation_date(_fecha_sello(reporte.periodo_fin))",
        "    pdf.set_creation_date(datetime.now())",
        [
            "tests/integration/test_reporte_cierre.py::TestA4Determinismo",
            "tests/unit/test_reporte_cierre.py::TestElSelloDeFecha",
        ],
    ),
    (
        "el sello sale de una fecha fija, no del periodo",
        "app/services/reporte_cierre.py",
        "    return datetime(periodo_fin.year, periodo_fin.month, periodo_fin.day)",
        "    return datetime(2020, 1, 1)",
        ["tests/unit/test_reporte_cierre.py::TestElSelloDeFecha"],
    ),
    # -----------------------------------------------------------------------
    # R5: "leido por el sistema" y "verificado por una persona"
    # -----------------------------------------------------------------------
    (
        "las dos afirmaciones se vuelven una sola",
        "app/schemas/reporte.py",
        "    leido_por_el_sistema: int = 0\n    verificado_por_una_persona: int = 0",
        "    revisados: int = 0",
        [
            "tests/integration/test_reporte_cierre.py::TestR5LasDosAfirmacionesNoSeConfunden",
            "tests/unit/test_reporte_cierre.py::TestR5LasDosEtiquetas",
        ],
    ),
    # -----------------------------------------------------------------------
    # A6: nada de float en dinero
    # -----------------------------------------------------------------------
    (
        "un importe pasa por float",
        "app/services/reporte_cierre.py",
        "    return f\"${monto:,.2f}\"",
        "    return f\"${float(monto):,.2f}\"",
        ["tests/unit/test_reporte_cierre.py::TestA6NadaDeFloatEnDinero"],
    ),
    # -----------------------------------------------------------------------
    # El sanitizador latin-1
    # -----------------------------------------------------------------------
    (
        "los caracteres fuera de latin-1 revientan el PDF",
        "app/services/reporte_cierre.py",
        '    limpio = texto.translate(_TRADUCCIONES)\n'
        '    return limpio.encode("latin-1", errors="replace").decode("latin-1")',
        '    limpio = texto.translate(_TRADUCCIONES)\n'
        '    return limpio',
        [
            "tests/unit/test_reporte_cierre.py::TestElSanitizadorLatin1",
            "tests/unit/test_reporte_cierre.py::TestA10ElPdfNoFiltrada",
        ],
    ),
    # -----------------------------------------------------------------------
    # El periodo: la ventana que decide de que periodo se habla
    # -----------------------------------------------------------------------
    (
        "el fin del periodo se vuelve inclusivo y pierde el ultimo dia",
        "app/services/reporte_cierre.py",
        "    if mes == 12:\n        fin = datetime(anio + 1, 1, 1)\n"
        "    else:\n        fin = datetime(anio, mes + 1, 1)",
        "    if mes == 12:\n        fin = datetime(anio + 1, 1, 1)\n"
        "    else:\n        fin = datetime(anio, mes, ultimo_dia_aprox) + timedelta(days=1)\n"
        "    ultimo_dia_aprox = 28",
        [
            "tests/unit/test_reporte_cierre.py::TestElPeriodo",
            "tests/integration/test_reporte_cierre.py::TestA2ElTotalCuadra",
        ],
    ),
    (
        "un periodo invalido se acepta en vez de rechazarse",
        "app/services/reporte_cierre.py",
        '    if not periodo or not PATRON_PERIODO.match(periodo):',
        '    if not periodo:\n        return 2026, 1\n    if not PATRON_PERIODO.match(periodo):',
        [
            "tests/unit/test_reporte_cierre.py::TestElPeriodo",
            "tests/integration/test_reporte_cierre.py::TestA5ElPeriodoSeValida",
        ],
    ),
    # -----------------------------------------------------------------------
    # El tipo de la respuesta
    # -----------------------------------------------------------------------
    (
        "el PDF devuelve el bytearray crudo de fpdf2",
        "app/services/reporte_cierre.py",
        "    return bytes(pdf.output())",
        "    return pdf.output()",
        [
            "tests/integration/test_reporte_cierre.py::TestA1ElPdfSeDescarga",
        ],
    ),
    # -----------------------------------------------------------------------
    # A7: reutilizar el calculo de exactitud
    # -----------------------------------------------------------------------
    (
        "el informe deja de usar el servicio de exactitud y no avisa",
        "app/services/reporte_cierre.py",
        "        exactitud = await accuracy_service.compute_accuracy_report(db, company_id)",
        "        exactitud = None",
        ["tests/unit/test_reporte_cierre.py::TestA7ReutilizaElCalculoDeExactitud"],
    ),
]


def _correr(objetivos: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", *objetivos],
        capture_output=True,
        text=True,
        cwd=RAIZ,
    )
    return proc.returncode != 0, proc.stdout + proc.stderr


def main() -> int:
    if not MUTACIONES:
        print("no hay mutaciones que verificar")
        return 1

    supervivientes: list[str] = []
    for nombre, archivo, original, mutado, objetivos in MUTACIONES:
        ruta = RAIZ / archivo
        fuente = ruta.read_text(encoding="utf-8")

        if original not in fuente:
            print(f"  ?    {nombre}")
            print(f"        NO SE ENCONTRO el texto original en {archivo}.")
            print("        La mutacion esta desactualizada: el codigo cambio y")
            print("        esta mutacion ya no representa un cambio posible.")
            supervivientes.append(f"{nombre} (texto original no encontrado)")
            continue

        backup = fuente
        try:
            ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
            murio, salida = _correr(objetivos)
        finally:
            ruta.write_text(backup, encoding="utf-8")

        if murio:
            print(f"  ok   {nombre} (detectada)")
        else:
            print(f"  VIVA {nombre} (nadie se dio cuenta)")
            supervivientes.append(nombre)

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for s in supervivientes:
            print(f"  - {s}")
        print()
        print("Cada superviviente es una pieza de logica que nadie mira. O el test")
        print("no cubre esa regla, o la cubre de una forma que el cambio no rompe.")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())