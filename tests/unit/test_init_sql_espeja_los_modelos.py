"""`db/init.sql` tiene que decir lo mismo que los modelos.

**POR QUE ESTE ARCHIVO EXISTE.** La suite construye el esquema desde los modelos
de SQLAlchemy (`tests/conftest.py`), **nunca** desde `db/init.sql`. Ese archivo
solo corre una vez, cuando Postgres arranca con el volumen vacio.

La consecuencia es que los dos pueden divergir **y la suite sigue en verde**: los
tests no pasan por el DDL que corre en una instalacion nueva. No hay ningun test
que lo cagara hasta que un comprobante real se perdio en produccion con:

    ERROR: column "ieps_amount" of relation "tickets" does not exist

que es un 500 por cada ticket, porque `ticket_persistence.py:114` escribe
`ieps_amount` en cada INSERT y el modelo declara la columna
(`app/models/ticket.py:136`).

**Que este test no existiera antes es el punto, y no es una casualidad.** El fallo
es de los que no se ven leyendo codigo: los dos archivos estan "bien" por
separado. La unica forma de verlo es ponerlos uno al lado del otro, y eso es lo
que hace el test.

Lo que hace: recorre las columnas que declara cada modelo y comprueba que
`db/init.sql` las menciona. No valida tipos ni nullability —para eso estan los
`verify_postgres_*.py`— sino **existencia**, que es lo que se rompió y lo que un
`CREATE TABLE` cambia en silencio.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
INIT_SQL = RAIZ / "db" / "init.sql"

# Las tablas de la aplicacion. La ultima se agrega al MODELO y al DDL a la vez,
# o este archivo vuelve a quejarse sin que nadie sepa de donde salio el nombre.
TABLAS = [
    "companies",
    "users",
    "tickets",
    "bank_transactions",
    "reconciliations",
    "accounting_mappings",
    "scan_files",
    "scan_events",
    "ticket_documents",
    "cierres_periodo",
    "compras",
    "compra_items",
    "productos",
    "movimientos_inventario",
]


def _declara_columna(ddl: str, columna: str) -> bool:
    """¿El DDL CREA esta columna, o solo la menciona?

    **El bug que hizo inutil la primera version de este test.** Buscar el nombre
    de la columna en el archivo entero —incluso ya sin comentarios— decia que
    `ieps_amount` estaba, porque dos lineas mas abajo hay un
    `COMMENT ON COLUMN tickets.ieps_amount IS ...` que el modelo no necesita para
    escribir. Ese `COMMENT` no crea nada.

    Con la mutacion que borra el `ALTER` y deja el `COMMENT` intacto, el test daba
    verde con la columna ausente. Peor que no tener test: alguien lee "todo en
    verde" y concluye que el DDL esta bien.

    Por eso se busca la DECLARACION y no la palabra. Las tres formas que la
    producen en este archivo:
      - dentro de un `CREATE TABLE (...)`, como columna de la tabla
      - `ALTER TABLE <tabla> ADD COLUMN ... <columna>`
      - una que ya exista en el `CREATE TABLE` de otra tabla (no aplica: los
        nombres de columna se comprueban por archivo, no por tabla)

    Lo que NO cuenta: `COMMENT ON COLUMN`, `CREATE INDEX` sobre la columna, ni
    una mencion en un `IF NOT EXISTS` de otra cosa.
    """
    # `ADD COLUMN [IF NOT EXISTS] <tipo> <nombre>`
    if re.search(
        rf"ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?[\w()\s\[\]\",']*?\b{re.escape(columna)}\b",
        ddl,
        re.IGNORECASE,
    ):
        return True
    # Columna dentro de un `CREATE TABLE`: al principio de una linea, con tipo
    # detras. Exige el espacio al inicio para no capturar continuaciones.
    if re.search(rf"^\s{{4}}{re.escape(columna)}\s+[A-Z]", ddl, re.MULTILINE):
        return True
    return False


def _sin_comentarios(sql: str) -> str:
    """El SQL EJECUTABLE, sin los comentarios.

    **Por que esto existe, y por que el test es inicialmente inutil sin ello.**
    La primera version de este test buscaba el nombre de la columna en el archivo
    entero, y daba verde con las columnas BORRADAS: los comentarios de arriba las
    mencionan todas (`tickets.ieps_amount`, `iva_linea = 8.14`), y un substring
    encontra la palabra en un `--` igual que en un `ALTER TABLE`.

    Es la trampa de probar sobre texto en vez de sobre comportamiento, y aqui es
    especialmente facile porque este archivo esta lleno de comentarios largos que
    explican el **por que** de cada columna — que es justo lo que hace util el
    archivo y lo que hace que el test no sirva.

    **EL ORDEN IMPORTA, y la primera version lo tenia al reves.** Se quitan los
    comentarios primero y los cuerpos `plpgsql` despues. Al reves, un `$` que
    aparece DENTRO de un comentario desalinea todo el emparejamiento de dollar
    quotes:

        -- un comprobante dice que se gastó $4,093.80, no qué se compró

    Ese `$4,093.80` se leia como apertura de dollar quote, y todo lo que venía
    despues se consumia como si fuera el cuerpo de una funcion. El archivo se
    encogia de 48 KB a 5 KB y `CREATE TABLE compra_items` desaparecia. Doce tests
    en verde con el DDL intacto, y todos en rojo con las columnas borradas: un
    test que no sabe distinguir "esta columna no existe" de "no se donde esta",
    que es peor que no tener test.

    Se quitan las tres formas de comentario de Postgres:
      - `--` hasta el fin de linea
      - `/* ... */`
      - `$tag$ ... $tag$`, los cuerpos de `plpgsql`, donde un `--` es una cadena
    """
    # 1. Comentarios de linea y de bloque. Primero, siempre.
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    # 2. Cuerpos delimitados por dollar. Despues, porque un `--` DENTRO de un
    #    cuerpo es una cadena de plpgsql y borrarlo partiria el bloque por la
    #    mitad en vez de quitar el comentario.
    sql = re.sub(r"\$[A-Za-z_]*\$.*?\$[A-Za-z_]*\$", " ", sql, flags=re.S)
    return sql


def _columnas_de_los_modelos() -> dict[str, set[str]]:
    """`{tabla: {columna}}` desde los modelos, que es la fuente de verdad."""
    from app.models import (
        AccountingMappingModel,
        BankTransactionModel,
        CierrePeriodoModel,
        CompanyModel,
        CompraItemModel,
        CompraModel,
        MovimientoInventarioModel,
        ProductoModel,
        ReconciliationModel,
        ScanEventModel,
        ScanFileModel,
        TicketDocumentModel,
        TicketModel,
        UserModel,
    )

    por_tabla = [
        (CompanyModel, "companies"),
        (UserModel, "users"),
        (TicketModel, "tickets"),
        (BankTransactionModel, "bank_transactions"),
        (ReconciliationModel, "reconciliations"),
        (AccountingMappingModel, "accounting_mappings"),
        (ScanFileModel, "scan_files"),
        (ScanEventModel, "scan_events"),
        (TicketDocumentModel, "ticket_documents"),
        (CierrePeriodoModel, "cierres_periodo"),
        (CompraModel, "compras"),
        (CompraItemModel, "compra_items"),
        (ProductoModel, "productos"),
        (MovimientoInventarioModel, "movimientos_inventario"),
    ]
    return {
        tabla: {c.name for c in modelo.__table__.columns}
        for modelo, tabla in por_tabla
    }


class TestElInitSqlDiceLoMismoQueLosModelos:
    @pytest.fixture(scope="class")
    def init_sql(self) -> str:
        """El DDL sin comentarios. Ver `_sin_comentarios`: buscar el nombre de una
        columna en el archivo entero la daria por presente con solo un comentario
        que la mencione."""
        return _sin_comentarios(INIT_SQL.read_text(encoding="utf-8"))

    @pytest.fixture(scope="class")
    def init_crudo(self) -> str:
        """El archivo entero, para lo que SI tiene que mirar comentarios."""
        return INIT_SQL.read_text(encoding="utf-8")

    def test_el_archivo_existe(self, init_crudo):
        assert INIT_SQL.exists(), "db/init.sql no esta"
        assert len(init_crudo) > 1000

    def test_ninguna_columna_del_modelo_falta_en_el_init_sql(self, init_sql):
        """LA DEFENSA. Sin esto, el divergence no la ve nadie.

        El mensaje dice que hacer, porque un `ALTER TABLE ... ADD COLUMN IF NOT
        EXISTS` que replica la migracion es la respuesta y no solo el
        diagnostico.
        """
        faltantes: dict[str, list[str]] = {}
        for tabla, columnas in _columnas_de_los_modelos().items():
            # `_declara_columna`, no `c not in init_sql`: un
            # `COMMENT ON COLUMN` menciona la columna sin crearla, y asi este
            # test daba verde con el `ALTER` borrado.
            faltan = sorted(c for c in columnas if not _declara_columna(init_sql, c))
            if faltan:
                faltantes[tabla] = faltan

        detalle = "\n".join(
            f"    {t}: {', '.join(c)}" for t, c in sorted(faltantes.items())
        )
        assert not faltantes, (
            f"estas columnas las declara el modelo y NO estan en db/init.sql:\n"
            f"{detalle}\n\n"
            "Una base creada desde cero (que es la unica forma de que corra el "
            "init.sql) no podria aceptar ni un INSERT de esas tablas.\n\n"
            "El arreglo es replicar la migracion correspondiente. Si la columna "
            "viene de db/migrations/, copia su `ALTER TABLE ... ADD COLUMN` al "
            "final del init.sql. Si la acabas de inventar, agregala a la "
            "migracion que le toque Y al init.sql.\n\n"
            "Este fallo no lo caza ningun otro test: la suite construye el "
            "esquema desde los modelos, no desde el DDL."
        )

    @pytest.mark.parametrize("tabla", TABLAS)
    def test_cada_tabla_esta_creada(self, init_sql, tabla):
        """Que exista el `CREATE TABLE`, y no solo la mencion de la tabla.

        Una tabla que solo se nombra en un indice pasaria el test de columnas
        (sus columnas estan en el archivo, en el `ALTER`) sin que exista.
        """
        assert (
            f"CREATE TABLE IF NOT EXISTS {tabla} (" in init_sql
        ), f"{tabla} no tiene CREATE TABLE en db/init.sql"

    def test_las_tres_columnas_del_impuesto_estan(self, init_sql):
        """El caso concreto que se rompió, fijado por su nombre.

        El test de arriba ya lo cubre, y este parece redundante. No lo es: el de
        arriba falla diciendo *qué* falta, y este dice *por qué importa* en el
        sitio donde alguien va a leerlo. Y si alguien borra el bloque de 0012
        para "simplificar" el archivo, los dos se quieren.

        Comprueba `init_sql`, que ya viene SIN comentarios: un `--` que mencione
        `ieps_amount` no cuenta como columna.
        """
        for col, por_que in (
            ("ieps_amount", "el gate la necesita para `subtotal + IVA + IEPS == total`"),
            ("iva_linea", "el impuesto vive en la partida, no en el comprobante"),
            ("ieps_linea", "el IEPS de un ticket de supermercado es por linea"),
        ):
            assert _declara_columna(init_sql, col), f"falta {col}: {por_que}"

    def test_el_indice_de_los_impuestos_por_linea_existe(self, init_sql):
        """Sin el indice, cada confirmacion de compra recorre `compra_items` entera.

        Es el mismo criterio de los demas indices del archivo: si el indice no esta
        aqui, tampoco esta en la migracion, y la consulta depende de el.
        """
        assert "ix_compra_items_impuestos" in init_sql

    def test_los_comentarios_de_la_base_no_faltan(self, init_sql):
        """`COMMENT ON COLUMN` existe por una razon concreta: el nombre de la
        columna no dice que el valor es un IMPORTE y no una tasa.

        `iva_linea = 8.14` se lee como 8.14% si nadie lo dice, y un porcentaje
        donde debería haber pesos es un error que no da ningún error.
        """
        assert "COMMENT ON COLUMN compra_items.iva_linea IS" in init_sql
        assert "COMMENT ON COLUMN tickets.ieps_amount IS" in init_sql

    def test_no_hay_una_tabla_que_el_modelo_no_conozca(self, init_sql):
        """Al reves: una tabla en el DDL que ningun modelo declara.

        El docstring de antes decia "esto AVISA en vez de fallar" y el codigo
        hacia `assert huerfanas == set()`: avisaba y fallaba a la vez. Lo que
        hace falta no es avisar, es una lista de excepciones con su motivo, para
        que la proxima tabla huerfana siga sobrando.

        ## POR QUE HACE FALTA UNA EXCEPCION

        `schema_migrations` la escribe `scripts/migrar.sh`, no la aplicacion. Es
        el registro de que migraciones se aplicaron, y el registro lo escribe la
        herramienta que migra.

        Decirla en el codigo con un modelo seria mentir sobre como funciona: un
        `SchemaMigration` de SQLAlchemy inviting a `session.add(...)` desde un
        router, que es exactamente lo que NO debe pasar — el registro tiene que
        ser cosa de la herramienta, porque su unico modo de escribir es "el
        archivo de migracion salio con 0".
        """
        creadas = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", init_sql))
        conocidas = set(_columnas_de_los_modelos())

        # Tablas que existen en el DDL y sin modelo, con el motivo. La lista es
        # corta a proposito: cada renglon es una decision, y una decision que no
        # tiene porque anotada no es una decision.
        #
        # Anadir una aqui es facil y por eso hay un test aparte que comprueba que
        # cada una sigue teniendo su `porque`.
        sin_modelo = {
            # El registro de migraciones aplicadas. La escribe `scripts/migrar.sh`
            # despues de que `psql` salga con 0, y NADIE mas: por eso no es un
            # modelo. Sin ella no hay forma de saber si una base ya esta al dia,
            # porque `init.sql` replica las 12 migraciones y una base nueva se ve
            # igual que una migrada. Ver `scripts/migrar.sh`.
            "schema_migrations": "la escribe scripts/migrar.sh, no la aplicacion",
        }

        huerfanas = creadas - conocidas - set(sin_modelo)
        assert huerfanas == set(), (
            f"estas tablas estan en db/init.sql y ningun modelo las declara: "
            f"{sorted(huerfanas)}. Si es intencional, agregala a `sin_modelo` "
            f"en este test con su motivo."
        )

    def test_cada_excepcion_sin_modelo_explica_por_que(self):
        """La lista de excepciones no puede crecer sin motivo escrito.

        Una lista de excepciones sin justificacion es una lista de.webos: la
        proxima persona que encuentre una tabla huerfana la agrega "para que
        pase el test" y el test deja de decir algo. Este comprueba que cada
        renglon diga algo mas que el nombre de la tabla.
        """
        # Se relee el fuente del test anterior: la constante vive ahi dentro, y
        # duplicarla aqui seria tener dos listas que pueden divergir.
        fuente = Path(__file__).read_text(encoding="utf-8")
        bloque = re.search(r"sin_modelo = \{(.*?)\n        \}", fuente, re.S)
        assert bloque, "no se encontro el diccionario `sin_modelo` en el test anterior"
        for entrada in re.findall(r'"(\w+)":\s*"([^"]*)"', bloque.group(1)):
            tabla, motivo = entrada
            assert len(motivo) > 20, (
                f"la excepcion para `{tabla}` dice \"{motivo}\", que no explica "
                "nada. Lo minimo es de donde viene la tabla y por que no tiene modelo"
            )