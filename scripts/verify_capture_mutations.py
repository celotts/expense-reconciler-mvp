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
        # Un PDF con texto va a `_cascada_texto` con `exigir_items=True`. Si se
        # manda a vision, un ticket impreso pago un modelo para nada.
        "    return await _cascada_texto(\n"
        "        texto, ConfidenceSource.PDF_TEXT, extract_from_text, exigir_items=True\n"
        "    )",
        "    return await _vision_pdf(content, extract_from_image)",
        ["tests/unit/test_capture.py::TestPdfConTexto"],
    ),
    (
        "el modelo se consulta antes que las reglas",
        "app/services/capture.py",
        "    if _es_extraccion_util(reglas) and not (exigir_items and _exige_items(reglas)):\n        return reglas",
        "    if extract_from_text is not None:\n        return invoice_to_result(await extract_from_text(texto))\n    if _es_extraccion_util(reglas) and not (exigir_items and _exige_items(reglas)):\n        return reglas",
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
        # OJO: este patron se reapunto DOS veces, y las dos veces por lo mismo:
        # apunta a texto LITERAL, y Ruff reformatea el bloque sin que nadie lo
        # note. La primera version apuntaba a la forma antigua de `_exige_items`
        # (`if ...: return False` / `return True`). Esta apunta a `_es_extraccion_util`,
        # que Ruff redujo a un solo `return not (...)` y despues le anadio el
        # termino de `_nombre_parece_basura`.
        #
        # Por eso el `??` del script distingue SOBREVIVIDA de NO PROBADA: una
        # mutacion que no se aplica y sigue contando es PEOR que una que no
        # existe, porque el conteo dice 28 y son 27. Si se vuelve a tocar esta
        # funcion, hay que reapuntar el patron.
        "    return not (\n        resultado.provider_name == UNKNOWN_PROVIDER\n        or resultado.total_amount <= 0\n        or _nombre_parece_basura(resultado.provider_name)\n    )",
        "    return not (\n        resultado.provider_name == UNKNOWN_PROVIDER\n        and resultado.total_amount <= 0\n        and _nombre_parece_basura(resultado.provider_name)\n    )",
        [
            "tests/unit/test_capture.py::TestPdfConTexto",
            "tests/unit/test_capture.py::TestTiposDeArchivo",
            "tests/integration/test_capture_pipeline.py::TestLoQueSeGuardaDicenLaVerdad",
        ],
    ),
    (
        "el origen se pierde y todo se guarda como lectura de modelo",
        "app/services/ticket_persistence.py",
        "        source=extracted.confidence_source,",
        "        source=ConfidenceSource.LLM,",
        [
            "tests/integration/test_capture_pipeline.py::TestLoQueSeGuardaDicenLaVerdad",
        ],
    ),
    (
        "una fecha ausente se reemplaza por la fecha UTC",
        "app/services/ticket_persistence.py",
        "        expense_date=extracted.expense_date or date.today(),",
        "        expense_date=extracted.expense_date or utcnow().date(),",
        [
            "tests/integration/test_capture_pipeline.py::TestFechaInventadaEnLaFila",
        ],
    ),
    (
        "una fecha ausente se guarda como fecha de hoy sin avisar",
        "app/services/ticket_persistence.py",
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
        # El texto viejo de esta mutacion era `r"(?:^|\s)sub..."`. Se cambio a
        # `r"(?:^|[^\w])sub..."` porque el borde de la etiqueta tiene que aceptar
        # la puntuacion que el OCR pone delante (`“SUBTOTAL 108.31`), y sin eso
        # el script contaba la defensa como no verificada EN SILENCIO. Es el caso
        # que `AGENTS.md` avisa: un patron obsoleto no falla, desaparece.
        "el subtotal vuelve a quedarse sin extraer",
        "app/services/parser_service.py",
        "        subtotal_match = re.search(\n            r\"(?:^|[^\\w])sub[\\s-]?total[\\s:]*[$€]?\\s*([\\d.,]+)\",\n            line, re.IGNORECASE,\n        )\n        if subtotal_match:\n            try:\n                subtotal = parse_mexican_number(subtotal_match.group(1))\n            except (InvalidOperation, ValueError):\n                pass",
        "        pass",
        [
            "tests/unit/test_parser_service.py::TestExtraccionDeSubtotal",
            "tests/integration/test_capture_pipeline.py::TestVerificacionAritmeticaEnLaRutaBarata",
        ],
    ),
    (
        # ESTA la metio el arreglo del borde, y es la quefalta: el script mutaba
        # el `subtotal` pero no el `total`, que cambio en el mismo commit por el
        # mismo motivo. Sin esta entrada, "el total vuelve a exigir solo
        # whitespace" queda sin probar y parece estar cubierta.
        "el total vuelve a exigir solo whitespace antes de la etiqueta",
        "app/services/parser_service.py",
        "        total_match = re.search(r\"(?:^|[^\\w])total[\\s:]*[$€]?\\s*([\\d.,]+)\", line, re.IGNORECASE)",
        "        total_match = re.search(r\"(?:^|\\s)total[\\s:]*[$€]?\\s*([\\d.,]+)\", line, re.IGNORECASE)",
        [
            "tests/unit/test_parser_ahorro_y_bordes.py::TestElTotalTambienAceptaElBordeRelajado",
        ],
    ),
    (
        # Y la del filtro de ahorro. Sin esta, "una linea de ahorro vuelve a ser
        # el total" no esta verificada por ningun script, solo por el test unit.
        "una linea de ahorro vuelve a leerse como el total",
        "app/services/parser_service.py",
        "        if AHORRO_EN_LINEA_RE.search(line):\n            continue\n",
        "        pass\n",
        [
            "tests/unit/test_parser_ahorro_y_bordes.py::TestUnaLineaDeAhorroNoEsElTotal",
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
    (
        "se reintroduce un import tragado de fitz",
        "app/services/parser_service.py",
        "def extract_pdf_text(file_content: bytes) -> str:",
        "def extract_pdf_text(file_content: bytes) -> str:\n    import fitz  # noqa",
        ["tests/unit/test_capture.py::TestUnaSolaRutaDeCaptura"],
    ),
    (
        "se reintroduce la ruta de PDF en la capa de IA",
        "app/services/ai_extractor.py",
        '    async def extract_from_image(self, image_bytes: bytes) -> ExtractedInvoice:',
        "    async def extract_from_pdf(self, pdf_bytes: bytes) -> ExtractedInvoice:\n"
        '        return await self._extract_from_text("x", "pdf_text")\n\n'
        "    async def _pdf_to_images(self, pdf_bytes: bytes) -> list:\n        return []\n\n"
        '    async def extract_from_image(self, image_bytes: bytes) -> ExtractedInvoice:',
        ["tests/unit/test_capture.py::TestUnaSolaRutaDeCaptura"],
    ),
    (
        "la imagen vuelve a devolver un ticket de relleno",
        "app/services/parser_service.py",
        '    if file_type == "image":\n'
        "        raise ValueError(\n"
        '            "una imagen no se extrae por reglas: use "\n'
        '            "app.services.capture.capture_ticket, que consulta al modelo"\n'
        "        )",
        '    if file_type == "image":\n'
        "        return TicketExtractionResult(\n"
        '            provider_name=UNKNOWN_PROVIDER, total_amount=Decimal("0.00"),\n'
        '            raw_text="Image OCR not implemented",\n'
        "        )",
        ["tests/unit/test_capture.py::TestUnaSolaRutaDeCaptura"],
    ),
    (
        "la idempotencia vuelve a ignorar la empresa",
        "app/services/ticket_persistence.py",
        "        select(TicketModel).where(\n"
        "            TicketModel.source_hash == source_hash,\n"
        "            TicketModel.company_id == company_id,\n"
        "        )",
        "        select(TicketModel).where(TicketModel.source_hash == source_hash)",
        ["tests/integration/test_capture_pipeline.py::TestElMismoArchivoEnDosEmpresas"],
    ),
    (
        "el indice unico vuelve a ser global y no por empresa",
        "app/models/ticket.py",
        '        Index("ix_tickets_source_hash", "company_id", "source_hash", unique=True,',
        '        Index("ix_tickets_source_hash", "source_hash", unique=True,',
        ["tests/integration/test_capture_pipeline.py::TestElMismoArchivoEnDosEmpresas"],
    ),
    (
        "el tope de raw_text desaparece",
        "app/services/parser_service.py",
        '        return recortar_raw_text(v)',
        "        return v",
        ["tests/integration/test_capture_pipeline.py::TestElTopeDeRawText"],
    ),
    (
        "el recorte se hace solo por la cabeza y pierde el total",
        "app/services/parser_service.py",
        "        + texto[-cola:]",
        "        + ''",
        ["tests/integration/test_capture_pipeline.py::TestElTopeDeRawText"],
    ),
    (
        "el recorte deja de avisar que hubo recorte",
        "app/services/parser_service.py",
        '    if len(texto) <= RAW_TEXT_MAX_CHARS:\n        return texto',
        "    return texto[:RAW_TEXT_MAX_CHARS]",
        ["tests/integration/test_capture_pipeline.py::TestElTopeDeRawText"],
    ),
    (
        "el comprobante deja de guardarse con el ticket",
        "app/services/ticket_persistence.py",
        "    await guardar_documento(\n"
        "        db, ticket, content,\n"
        "        content_type=content_type,\n"
        "        nombre_archivo=source_file,\n"
        "    )",
        "    await guardar_documento(\n"
        "        db, ticket, b'',\n"
        "        content_type=content_type,\n"
        "        nombre_archivo=source_file,\n"
        "    )",
        ["tests/integration/test_documentos.py"],
    ),
    (
        "un content-type del cliente se sirve tal cual",
        "app/models/ticket_document.py",
        "    if limpio in CONTENT_TYPES_SERVIBLES:\n        return limpio",
        "    return limpio or CONTENT_TYPE_POR_DEFECTO",
        ["tests/integration/test_documento_api.py::TestElContentTypeNoLoDecideElCliente"],
    ),
    (
        "el svg vuelve a servirse como imagen",
        "app/models/ticket_document.py",
        '    "image/png",',
        '    "image/png",\n    "image/svg+xml",',
        ["tests/integration/test_documento_api.py::TestElContentTypeNoLoDecideElCliente"],
    ),
    (
        "el error de imagen deja de decir donde esta la cascada",
        "app/services/parser_service.py",
        '            "una imagen no se extrae por reglas: use "\n'
        '            "app.services.capture.capture_ticket, que consulta al modelo"',
        '            "no soportado"',
        ["tests/unit/test_capture.py::TestUnaSolaRutaDeCaptura"],
    ),
    (
        # MEDIDO el 2026-10-15 en `IMG_4398.heic`: con esta comparacion el
        # ticket se guardaba con `total 5.00` y el papel decia `193.65`. El
        # detalle que hace la mutacion sneaky: quitar la fuerza NO cambia el
        # parser para los tickets limpios, solo para los que traen una linea de
        # ruido DESPUES de la del total, que es como se manifesta en produccion.
        "el importe con centavos vuelve a dejarse pisar por un entero suelto",
        "app/services/parser_service.py",
        "            if candidato is not None and candidato[1] >= fuerza_total:\n"
        "                total_amount, fuerza_total = candidato",
        "            if candidato is not None:\n"
        "                total_amount, fuerza_total = candidato",
        ["tests/unit/test_parser_service.py::TestElTotalLoDeclaraLaEtiquetaTotal"],
    ),
    (
        # La mitad del arreglo anterior: si la fuerza no distingue, la
        # comparacion de arriba es ">=" contra una constante y no decide nada.
        "la fuerza deja de distinguir un importe completo de uno partido",
        "app/services/parser_service.py",
        '    return 2 if ("." in bruto or "," in bruto) else 1',
        "    return 1",
        [
            "tests/unit/test_parser_service.py::TestElTotalLoDeclaraLaEtiquetaTotal",
            "tests/unit/test_parser_service.py::TestAmountExtraction",
        ],
    ),
    (
        # MEDIDO con el test `test_acepta_subtotal_con_guion`, que es del repo:
        # `Sub-Total: 500.00` + `Total: 580.00` devolvia 500.00. El guardia viejo
        # comparaba contra la palabra `"subtotal"` y el guion la escondia.
        "el subtotal con guion vuelve a entrar como candidato a total",
        "app/services/parser_service.py",
        "        if not _es_linea_de_subtotal(line):",
        '        if "subtotal" not in line.lower():',
        [
            "tests/unit/test_parser_service.py::TestElSubtotalConGuionNoEsUnTotal",
            "tests/unit/test_parser_service.py::TestExtraccionDeSubtotal",
        ],
    ),
    (
        # MEDIDO el 2026-10-15: `faltan` era SIEMPRE la lista vacia con Ollama
        # (habia un `pass` y su `else` era codigo muerto), y `disponible` daba
        # `true` con cero modelos instalados. El endpoint que existe para decir
        # "que modelo falta" decia que no faltaba ninguno, y las fotos se leian
        # solo con Tesseract sin que ninguna pantalla lo dijera.
        "el diagnostico de la IA vuelve a decir que no falta nada",
        "app/services/ai_client.py",
        "    faltan: list[str] = []\n"
        "    if instalados is not None:\n"
        '        base = {n.split(":")[0] for n in instalados}\n'
        '        for clave in ("modelo_texto", "modelo_vision"):\n'
        "            nombre = configurado.get(clave)\n"
        '            if nombre and str(nombre).split(":")[0] not in base:\n'
        "                faltan.append(str(nombre))",
        "    faltan: list[str] = []",
        ["tests/unit/test_modelo_en_la_pantalla.py::TestElDiagnosticoNoDiceQueTodoEstaBien"],
    ),
    (
        # La segunda mitad del mismo defecto: que disponibilidad se pueda
        # afirmar cuando lo unico que se sabe es que el proveedor RESPONDIO. Con
        # 25 modelos instalados y ninguno de los dos configurados, `error` queda
        # en `None` y `disponible` en `true`.
        "la IA se declara disponible sin que haya un modelo que conteste",
        "app/services/ai_client.py",
        "    if error is None and not hay_alguno:\n"
        "        error = (\n"
        '            "ningun modelo configurado esta instalado en el proveedor: "\n'
        '            + (", ".join(faltan) or "no hay modelos configurados")\n'
        "        )",
        "    pass",
        ["tests/unit/test_modelo_en_la_pantalla.py::TestElDiagnosticoNoDiceQueTodoEstaBien"],
    ),
    (
        # MEDIDO el 2026-10-15: `IMG_4409.heic`, el OCR leyo el subtotal bien
        # (279.93, la verdad) y el total mal (219.93). Sin esto se guardaba un
        # numero que la propia lectura del comprobante desmentia.
        "una foto que se contradice a si misma se devuelve sin releer",
        "app/services/capture.py",
        "            if (\n"
        "                _es_extraccion_util(resultado)\n"
        "                and not _exige_items(resultado)\n"
        "                and not _necesita_relectura(resultado)\n"
        "            ):",
        "            if _es_extraccion_util(resultado) and not _exige_items(resultado):",
        ["tests/unit/test_capture.py::TestLaFotoQueSeContradiceSeRelee"],
    ),
    (
        # El otro lado, y es el que hace que la regla NO sea "vision primero":
        # sin desglose no hay nada que comprobar. Con `<= 0` se escala en cada
        # ticket de supermercado, que es donde el OCR lee bien el total.
        "se relee tambien cuando no hay nada que comprobar",
        "app/services/capture.py",
        "    return _aritmetica_se_sostiene(resultado) < 0",
        "    return _aritmetica_se_sostiene(resultado) <= 0",
        ["tests/unit/test_capture.py::TestLaFotoQueSeContradiceSeRelee"],
    ),
    (
        # MEDIDO el 2026-10-15: la llamada a vision devolvio 500 dos veces y la
        # app registraba solo la URL. El motivo del proveedor -contexto, memoria,
        # o modelo que no cabe- se perdia, y con el las tres son el mismo fallo.
        "un 500 del proveedor se registra sin decir por que",
        "app/services/ai_client.py",
        "        if response.status_code >= 400:",
        "        if False:",
        ["tests/unit/test_modelo_en_la_pantalla.py::TestUnErrorDelProveedorSeDice"],
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
    obsoletas: list[str] = []

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
                # Distinto de "sobrevivio". Una mutacion que no se puede aplicar
                # NO PROBO NADA, y reportarla como sobreviviente mezcla dos
                # cosas opuestas: una defensa que quitaste y nadie nota, y una
                # defense que sigue ahi y que nadie volvio a comprobar. Lo
                # segundo se ve como un fallo de los tests y el primero no, que
                # es justo al reves de como hay que verlos.
                #
                # Es lo que AGENTS.md avisa para `verify_capture_mutations.py`:
                # si mueves el codigo que este script muta, reapunta la
                # mutacion. El aviso salia en un `??` que se leia como ruido.
                print(f"  ??  {nombre}: NO PROBADA — el patron original no aparece")
                print(f"      en {archivo}. El codigo cambio y la mutacion quedo obsoleta.")
                print(f"      Reapuntala: una defensa sin verificar parece una defensa que funciona.")
                obsoletas.append(nombre)
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
    if obsoletas:
        print(f"{len(obsoletas)} de {len(MUTACIONES)} mutaciones NO SE PROBARON (patron obsoleto):")
        for nombre in obsoletas:
            print(f"  - {nombre}")
        print("  Reapuntalas antes de contar esto como verde.")
        print()

    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones SOBREVIVIERON:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        print("  Estas SI se aplicaron y ningun test las nota.")
        return 1

    if obsoletas:
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
