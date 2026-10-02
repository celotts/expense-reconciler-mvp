"""Prueba de mutacion sobre la ruta de escaneo: cada defensa tiene un centinela.

Este archivo se ejecuta con `python3 scripts/verify_scan_mutations.py` (ver
abajo) y tambien con pytest, para que un test normal lo cubra.

La idea es la misma que en `verify_capture_mutations.py`: comprobar que las
defensas NO SE PUEDEN QUITAR EN SILENCIO. Un test que verifica que la carpeta se
valida pasa igual si alguien comenta la validacion y devuelve una constante.
Estos tests miran el TEXTO del codigo, asi que se dies si la defensa desaparece.

Lo que protege cada mutacion:

 1. la carpeta sale de `TICKETS_INPUT_DIR`, no de la peticion
 2. `is_relative_to` comprueba la contencion DESPUES de resolver
 3. el formato se detecta por bytes, no por extension
 4. el hash decide si el archivo cambio, no `mtime`
 5. un ticket revisado por una persona no se sobreescribe
 6. el piso de caracteres del OCR no sube por encima de un ticket minimo
 7. la confianza de OCR usa su propia tabla
 8. `os.walk` no sigue symlinks a directorios
 9. no se importa pytesseract al arrancar
10. el registro se escribe con la ruta RELATIVA
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

ARCHIVOS = {
    "scan_service": RAIZ / "app" / "services" / "scan_service.py",
    "ocr": RAIZ / "app" / "services" / "ocr.py",
    "capture": RAIZ / "app" / "services" / "capture.py",
    "archivo_real": RAIZ / "app" / "core" / "archivo_real.py",
    # El router se llama `scans.py` y no `scan.py` a proposito: el repo nombra
    # los routers en plural (`tickets.py`, `companies.py`, `bank_transactions.py`)
    # y los schemas en singular (`ticket.py`, `company.py`). Con `scan.py` en los
    # dos, el nombre era el unico que no seguia la convencion y seeria el primer
    # archivo con el mismo nombre en dos carpetas del mismo dominio.
    "scan_api": RAIZ / "app" / "api" / "scans.py",
    "scan_schema": RAIZ / "app" / "schemas" / "scan.py",
    "scan_model": RAIZ / "app" / "models" / "scan_file.py",
}


def fuente(clave: str) -> str:
    return ARCHIVOS[clave].read_text(encoding="utf-8")


def arbol(clave: str) -> ast.Module:
    return ast.parse(fuente(clave))


# ---------------------------------------------------------------------------
# 1. La carpeta NO viene de la peticion
# ---------------------------------------------------------------------------


class TestLaRutaNoVieneDeLaPeticion:

    def test_el_schema_no_tiene_campo_de_carpeta(self):
        """`ScanRequest` no declara ninguna ruta.

        Un `folder_path` en el body es lectura arbitraria del disco del
        servidor. Esta es la mutacion mas importante del archivo: si alguien lo
        agrega "para que el cliente elija donde escanear", el endpoint sube el
        `.env` del proyecto como si fuera un comprobante.
        """
        modulo = arbol("scan_schema")
        for nodo in ast.walk(modulo):
            if isinstance(nodo, ast.ClassDef) and nodo.name == "ScanRequest":
                campos = {
                    campo.target.id
                    for campo in nodo.body
                    if isinstance(campo, ast.AnnAssign)
                    and isinstance(campo.target, ast.Name)
                }
                assert not (campos & {"folder_path", "path", "directorio", "dir"}), (
                    f"ScanRequest no debe aceptar una carpeta: tiene {campos}"
                )
                return
        pytest.fail("no se encontro la clase ScanRequest")

    def test_el_router_no_toma_carpeta_por_query_ni_por_body(self):
        """Ningun endpoint del escaner DECLARA un parametro de carpeta.

        Se mira el AST y no el texto: los comentarios de este router mencionan
        `folder_path` precisamente para explicar por que NO existe, y una busqueda
        de texto plano las encontraria. Lo que importa es lo que el codigo
        declara.
        """
        modulo = arbol("scan_api")
        for nodo in ast.walk(modulo):
            if isinstance(nodo, ast.FunctionDef):
                argumentos = nodo.args
                todos = argumentos.args + argumentos.kwonlyargs
                for arg in todos:
                    assert arg.arg not in {"folder_path", "directorio", "carpeta_entrada"}, (
                        f"{nodo.name} acepta el parametro {arg.arg!r}: "
                        "el escaner no puede recibir una carpeta del cliente"
                    )
                # Y un `Query(default=...)` que se llame carpeta tampoco.
                for default in argumentos.defaults + [d for d in argumentos.kw_defaults if d]:
                    if isinstance(default, ast.Call):
                        nombre = getattr(default.func, "attr", getattr(default.func, "id", ""))
                        if nombre == "Query":
                            for kw in default.keywords:
                                assert kw.arg != "folder_path"

    def test_la_carpeta_se_toma_de_settings(self):
        """La unica fuente de la ruta es `settings.TICKETS_INPUT_DIR`."""
        texto = fuente("scan_service")
        assert "settings.TICKETS_INPUT_DIR" in texto


# ---------------------------------------------------------------------------
# 2. Contencion de rutas
# ---------------------------------------------------------------------------


class TestLaContencionDeRutas:

    def test_se_usa_is_relative_to_y_no_startswith(self):
        """`is_relative_to` y no `str(...).startswith(str(...))`.

        Con `startswith`, `/x/tickets_secreto` empieza con `/x/tickets` y la
        carpeta hermana se lee como si estuviera dentro. Es el error clasico de
        comparar rutas como cadenas, y no lo detecta ningun test funcional si la
        carpeta hermana no existe.
        """
        texto = fuente("scan_service")
        assert "is_relative_to" in texto, "la comparacion de contencion desaparecio"

        # Que no se compare una ruta contra otra con `startswith`. Se busca en el
        # AST y no en el texto porque `nombre.startswith(...)` en el filtro de
        # ruido de archivos es correcto y no tiene nada que ver con esto: la
        # mutacion que importa es `ruta.startswith(otra_ruta)`.
        #
        # Distinguir las dos por forma es lo que hace falta: `Path.startswith`
        # EXISTE y es tentador, y es exactamente el bug, porque
        # `/x/tickets_secreto` empieza con `/x/tickets` tambien para pathlib.
        modulo = arbol("scan_service")
        rutas_como_nombre = {"candidata", "base", "ruta", "otra", "prefijo", "otra_ruta"}
        for nodo in ast.walk(modulo):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr == "startswith"
                and nodo.args
                and isinstance(nodo.args[0], (ast.Name, ast.Attribute))
            ):
                receptor = nodo.func.value
                nombre_receptor = getattr(receptor, "id", None)
                if nombre_receptor in rutas_como_nombre:
                    pytest.fail(
                        f"linea {nodo.lineno}: se compara una ruta con startswith. "
                        "Se leeria la carpeta hermana."
                    )

    def test_la_comprobacion_viene_despues_de_resolve(self):
        """Resolver primero, comprobar despues. En ese orden, y no al reves.

        Un symlink a `/etc` no tiene un solo `..` en el texto. Lo unico que lo
        revela es `resolve()`, asi que comprobar la cadena antes de resolver
        daria una sensacion de seguridad que no existe.
        """
        texto = fuente("scan_service")
        i_resolve = texto.index("candidata = (base / relative_path).resolve()")
        i_comprueba = texto.index("is_relative_to(base)")
        assert i_resolve < i_comprueba, (
            "la comprobacion de contencion tiene que ir DESPUES de resolver"
        )

    def test_los_symlinks_a_archivo_no_se_siguen(self):
        """Un symlink dentro de la carpeta se salta sin/leerlo."""
        texto = fuente("scan_service")
        assert "islink" in texto, "el recorrido dejo de detectar symlinks"


# ---------------------------------------------------------------------------
# 3. El formato sale de los bytes
# ---------------------------------------------------------------------------


class TestElFormatoPorBytes:

    def test_la_extension_no_elige_la_ruta_de_lectura(self):
        """El escaner decide el tipo con `detectar_tipo_real`, no con `suffix`.

        `sufijo.extension` para elegir la ruta es exactamente el fallo que
        `app/core/archivo_real.py` existe para evitar: un PDF con nombre `.jpg`
        se manda a vision y queda registrado como lectura de modelo.
        """
        texto = fuente("scan_service")
        assert "detectar_tipo_real" in texto
        # `extension` se guarda, pero no debe decidir el tipo.
        assert "detected_format = " in texto or "detected_format=" in texto

    def test_el_detector_es_una_lista_cerrada_de_firmas(self):
        """Firmas comparadas byte a byte, no heuristicas de prefijo."""
        texto = fuente("archivo_real")
        assert "FIRMAS_IMAGEN" in texto
        assert "FIRMA_PDF" in texto


# ---------------------------------------------------------------------------
# 4. El hash decide, no la fecha
# ---------------------------------------------------------------------------


class TestElHashDecide:

    def test_la_comparacion_usa_sha256_del_contenido(self):
        """Un `touch` cambia la fecha sin cambiar el contenido.

        Comparar por `mtime` reprocesa en cada corrida, y el escaneo deja de ser
        idempotente: paga OCR sobre los mismos archivos para siempre.
        """
        texto = fuente("scan_service")
        assert "hashlib.sha256" in texto
        assert "mtime ==" not in texto, "comparar por mtime rompe la idempotencia"

    def test_el_contenido_se_lee_una_vez_por_corrida(self):
        """Se lee el archivo una vez y de ahi salen hash y contenido.

        Leerlo dos veces (una para el hash y otra para el OCR) duplica el IO en
        el disco, que es justo lo que hace lento un escaneo de una carpeta
        grande.
        """
        # En `escanear` el archivo se lee UNA vez y de ahi salen el hash y el
        # contenido. `reprocesar_uno` tiene su propia lectura y no cuenta: es un
        # endpoint de un archivo, no un recorrido.
        # `reprocesar_uno` va DESPUES de `escanear` en el archivo, asi que se corta
        # el cuerpo en los dos: el de `escanear` es el recorrido y no debe leer
        # cada archivo mas de una vez.
        texto = fuente("scan_service")
        cuerpo = texto.split("async def escanear", 1)[1].split("async def reprocesar_uno", 1)[0]
        assert cuerpo.count("read_bytes()") == 1, (
            "el recorrido esta leyendo cada archivo mas de una vez"
        )


# ---------------------------------------------------------------------------
# 5. El trabajo humano no se pisa
# ---------------------------------------------------------------------------


class TestElTrabajoHumano:

    def test_la_comprobacion_de_revision_humana_existe(self):
        """`reviewed_at` se consulta antes de sobreescribir.

        El operador corrigio el ticket a mano porque la lectura estaba mal. Volver
        a correr OCR lo deshace, en silencio.
        """
        texto = fuente("scan_service")
        assert "reviewed_at" in texto
        assert "_es_revisado_por_humano" in texto

    def test_la_comprobacion_se_usa_antes_de_actualizar(self):
        """No basta con que la funcion exista: tiene que ir antes del UPDATE."""
        texto = fuente("scan_service")
        i_comprueba = texto.index("if _es_revisado_por_humano(ticket):")
        i_actualiza = texto.index("await _actualizar_ticket(")
        assert i_comprueba < i_actualiza, (
            "la actualizacion esta pasando antes de preguntar si el ticket es humano"
        )

    def test_eso_avisa_en_vez_de_hacerlo_en_silencio(self):
        """Cuando no se sobreescribe, queda dicho en `last_error`.

        El estado `PROCESADO` no expresa "el archivo cambio y no lo actualice".
        Sin el aviso, el ticket queda desactualizado y nadie se entera hasta que
        el cierre no cuadra.
        """
        texto = fuente("scan_service")
        assert "revisado por una persona" in texto


# ---------------------------------------------------------------------------
# 6 y 7. La confianza del OCR
# ---------------------------------------------------------------------------


class TestLaConfianzaDelOcr:

    def test_el_piso_de_caracteres_no_esta_por_encima_de_un_ticket_minimo(self):
        """Un ticket minimo real tiene 97 caracteres. El piso no puede estar mas arriba.

        Se puso en 120 y rechazaba comprobantes de verdad: cada foto de un ticket
        chico se iba a vision y pagaba un modelo. La regresion mas cara de este
        trabajo, y la unica que se manifesto como "no falla, solo no lee".
        """
        from app.services.capture import OCR_MIN_CHARS_PARA_INTENTAR

        ticket_minimo = (
            "OXXO SA de CV\nRFC: XXXXX010101XXX\nFecha: 2025/03/15\n"
            "Subtotal: 43.10\nIVA (16%): 6.90\nTOTAL: 50.00\n"
        )
        assert len(ticket_minimo) == 97
        assert OCR_MIN_CHARS_PARA_INTENTAR < len(ticket_minimo)

    def test_ocr_tiene_su_propia_tabla_de_confianza(self):
        """La tabla de OCR existe y es distinta de la de PDF.

        Con la misma tabla, un OCR con RFC+subtotal+fecha sacaria 0.97 y se
        auto-aprobaria. Un "1" mal leido como "l" no lo detecta ningun regex, y
        un total erroneo auto-aprobado es un error financiero.
        """
        from app.core.enums import ConfidenceSource
        from app.services.capture import (
            confianza_por_campos,
            confianza_por_campos_ocr,
        )

        assert confianza_por_campos_ocr is not confianza_por_campos
        for rfc in (False, True):
            for subtotal in (False, True):
                for fecha in (False, True):
                    assert confianza_por_campos_ocr(rfc, subtotal, fecha) < confianza_por_campos(
                        rfc, subtotal, fecha
                    )

    def test_ocr_es_un_valor_del_enum_y_no_llm(self):
        """`ConfidenceSource.OCR` existe y es distinto de `LLM`.

        Si un OCR se guardara como `llm`, el reporte de exactitud sumaria al
        modelo la exactitud de Tesseract, y el numero no describe a ninguno de
        los dos.
        """
        from app.core.enums import ConfidenceSource

        assert ConfidenceSource.OCR.value == "ocr"
        assert ConfidenceSource.OCR is not ConfidenceSource.LLM


# ---------------------------------------------------------------------------
# 8 y 9. El recorrido y el arranque
# ---------------------------------------------------------------------------


class TestElRecorridoYElArranque:

    def test_os_walk_no_sigue_symlinks(self):
        """`followlinks=False`, o un enlace a `.` hace un ciclo infinito.

        `Path.rglob` sigue enlaces a directorios. Un enlace a la propia carpeta
        es un ciclo: el recorrido no termina y el escaner se queda sin memoria
        leyendo la misma foto para siempre.
        """
        texto = fuente("scan_service")
        assert "followlinks=False" in texto
        # `rglob` no debe aparecer en CODIGO. El comentario de arriba lo nombra
        # para explicar por que no se usa, asi que la busqueda es sobre el AST.
        modulo = arbol("scan_service")
        for nodo in ast.walk(modulo):
            if isinstance(nodo, ast.Attribute) and nodo.attr == "rglob":
                pytest.fail("rglob sigue symlinks a directorios y puede ciclar")

    def test_el_recorrido_ignora_un_enlace_a_directorio(self, tmp_path, monkeypatch):
        """Un symlink a una carpeta, dentro de la carpeta, no se baja.

        Es la version funcional del `followlinks=False`, y hace falta porque la
        anterior mira el TEXTO: cambiar `followlinks=False` por `followlinks=True`
        no cambia ninguna otra linea del archivo, asi que solo un symlink de
        verdad lo detecta.

        Sin esto, un enlace a la propia carpeta cuelga el escaneo: `os.walk`
        desciende, encuentra el enlace, desciende otra vez, y asi hasta que se
        acaba la memoria. No es un archivo de mas: es un escaneo que no termina.
        """
        from app.core.config import settings
        from app.services import scan_service

        base = tmp_path / "tickets"
        (base / "viaje").mkdir(parents=True)
        (base / "viaje" / "marzo.pdf").write_bytes(b"%PDF-1.7 x")
        monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(base))

        # El ciclo: un enlace que apunta a un ancestro de si mismo.
        (base / "viaje" / "arriba").symlink_to(base, target_is_directory=True)

        relativas = {v.relative_path for v in scan_service.listar_archivos()}

        assert relativas == {"viaje/marzo.pdf"}, (
            f"el recorrido bajo por un symlink a directorio: {sorted(relativas)}"
        )

    def test_pytesseract_no_se_importa_al_arrancar(self):
        """Nada de `import pytesseract` en el nivel del modulo.

        Si se importara al cargar, el API no levantaria en una maquina sin
        Tesseract, aunque no use OCR nunca. La app tiene que poder arrancar para
        poder DECIR que no tiene Tesseract, no para no poder contestar.
        """
        for clave in ("ocr", "capture", "scan_service"):
            modulo = arbol(clave)
            for nodo in modulo.body:  # solo el nivel superior
                if isinstance(nodo, ast.Import):
                    for alias in nodo.names:
                        assert "pytesseract" not in alias.name, (
                            f"{clave} importa pytesseract al arrancar"
                        )
                if isinstance(nodo, ast.ImportFrom) and nodo.module:
                    assert "pytesseract" not in nodo.module, (
                        f"{clave} importa pytesseract al arrancar"
                    )
                    assert "easyocr" not in nodo.module, (
                        f"{clave} importa easyocr al arrancar (arrastra torch)"
                    )

    def test_el_ocr_se_importa_dentro_de_la_funcion(self):
        """La import de `leer_imagen` esta dentro de `_ocr_por_defecto`."""
        texto = fuente("capture")
        assert "from app.services.ocr import leer_imagen" in texto
        cuerpo = texto.split("def _ocr_por_defecto", 1)[1]
        assert "import leer_imagen" in cuerpo


# ---------------------------------------------------------------------------
# 10. El registro guarda la ruta relativa
# ---------------------------------------------------------------------------


class TestElRegistro:

    def test_la_columna_se_llama_relative_path(self):
        """Una ruta absoluta en la base cambia al mover la carpeta.

        Ademas escribe el home de alguien 500 veces en una tabla que se exporta.
        """
        texto = fuente("scan_model")
        assert "relative_path" in texto
        assert "absolute_path" not in texto

    def test_la_ruta_se_guarda_en_posix(self):
        """`as_posix()`, para que la clave no dependa del sistema operativo.

        `a\\b` y `a/b` son dos claves distintas, y el UNIQUE de `relative_path`
        deja de proteger la idempotencia si cada sistema guarda la suya.
        """
        texto = fuente("scan_service")
        assert "as_posix()" in texto

    def test_el_modelo_declara_el_unico_de_relative_path(self):
        """Sin el UNIQUE, dos escaneos simultaneos crean dos filas por archivo."""
        texto = fuente("scan_model")
        assert "uq_scan_files_relative_path" in texto

    def test_hay_indice_por_hash_para_encontrar_duplicados(self):
        """La deduplicacion se pregunta a la tabla de archivos, no a los tickets."""
        texto = fuente("scan_model")
        assert "ix_scan_files_content_hash" in texto


# ---------------------------------------------------------------------------
# Las migraciones y el modelo no pueden divergir
# ---------------------------------------------------------------------------


class TestElEsquemaNoDiverge:

    def test_las_tablas_existen_en_la_migracion_y_en_init(self):
        """`db/init.sql` y `0008_scan_ledger.sql` tienen que decir lo mismo.

        `init.sql` solo corre con el volumen vacio. Si las dos no coinciden, una
        base nueva y una migrada se comportan distinto con el mismo codigo, que
        es la forma mas lenta de tener dos verdades.
        """
        init = (RAIZ / "db" / "init.sql").read_text(encoding="utf-8")
        migracion = (RAIZ / "db" / "migrations" / "0008_scan_ledger.sql").read_text(
            encoding="utf-8"
        )

        for tabla in ("scan_files", "scan_events"):
            assert f"CREATE TABLE IF NOT EXISTS {tabla}" in init
            assert f"CREATE TABLE IF NOT EXISTS {tabla}" in migracion

    def test_los_indices_coinciden(self):
        init = (RAIZ / "db" / "init.sql").read_text(encoding="utf-8")
        migracion = (RAIZ / "db" / "migrations" / "0008_scan_ledger.sql").read_text(
            encoding="utf-8"
        )

        for indice in (
            "ix_scan_files_status",
            "ix_scan_files_content_hash",
            "ix_scan_files_company",
            "ix_scan_events_file_created",
        ):
            assert indice in init, f"{indice} falta en init.sql"
            assert indice in migracion, f"{indice} falta en la migracion"

    def test_los_estados_validos_coinciden_con_el_enum(self):
        """La constraint del estado se construye desde el enum.

        Si `ScanStatus` gana un valor y el SQL no, el INSERT da IntegrityError en
        produccion justo en el camino nuevo. Y los tests no lo detectan porque
        corren contra SQLite, que no mira constraints.
        """
        from app.core.enums import ScanStatus

        init = (RAIZ / "db" / "init.sql").read_text(encoding="utf-8")
        migracion = (RAIZ / "db" / "migrations" / "0008_scan_ledger.sql").read_text(
            encoding="utf-8"
        )

        for sql in (init, migracion):
            for estado in ScanStatus:
                assert f"'{estado.value}'" in sql, f"{estado.value} no esta en el DDL"

    def test_las_acciones_validas_coinciden_entre_modelo_y_sql(self):
        """La constraint de `action` tiene la misma lista en los dos lados."""
        texto_modelo = fuente("scan_model")
        migracion = (RAIZ / "db" / "migrations" / "0008_scan_ledger.sql").read_text(
            encoding="utf-8"
        )

        import re

        # Del lado del MODELO se lee el AST, no el texto. La condicion y el
        # `name=` van en argumentos distintos de `CheckConstraint`, asi que el
        # texto obliga a adivinar el orden y a|Fr| regex fragil; el AST dice
        # cual es cual. Ademas asi el comentario que explica la constraint no
        # puede contaminar el resultado.
        acciones_modelo: set[str] = set()
        for nodo in ast.walk(arbol("scan_model")):
            if not (isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "CheckConstraint"):
                continue
            nombre = next(
                (kw.value.value for kw in nodo.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)),
                None,
            )
            if nombre != "ck_scan_events_action":
                continue
            # El primer argumento posicional es la condicion.
            if nodo.args:
                acciones_modelo = set(
                    re.findall(r"'([A-Z_]+)'", ast.unparse(nodo.args[0]))
                )
            break

        assert acciones_modelo, "no se encontro la constraint ck_scan_events_action"

        # Del lado del SQL si se lee el texto, porque no hay otra forma, pero se
        # acota al bloque de la constraint y no al archivo entero:
        # `scan_files.status` tiene su propia lista de valores y mezclarla haria
        # que la comparacion no significara nada.
        bloque_sql = re.search(
            r"ck_scan_events_action\s*CHECK\s*\(.*?IN \((.*?)\)", migracion, re.S
        )
        assert bloque_sql, "no se encontro ck_scan_events_action en la migracion"
        acciones_sql = set(re.findall(r"'([A-Z_]+)'", bloque_sql.group(1)))
        assert acciones_sql, "la lista de acciones del SQL salio vacia"

        assert acciones_modelo == acciones_sql, (
            f"el modelo dice {sorted(acciones_modelo)} y el SQL dice "
            f"{sorted(acciones_sql)}"
        )
