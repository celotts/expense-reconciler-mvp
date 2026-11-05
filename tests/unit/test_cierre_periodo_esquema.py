"""La tabla de cierres tiene que existir en los dos lados del esquema.

Por que existe este archivo
---------------------------

`db/init.sql` y los modelos son el mismo esquema escrito dos veces, y **nada los
ataba**. Se comprobo: ningun test del repo compara las columnas de una tabla con
su `CREATE TABLE`. El unico rastro de que se drifted es `AGENTS.md`, que anota
como trampa conocida que `bank_transactions.company_id` es nullable en `init.sql`
y `NOT NULL` en el modelo — y los tests no lo detectan porque corren sobre SQLite
construyendo desde los modelos, no desde el DDL.

Es decir: el divergence es un hecho, no un riesgo. Este archivo lo convierte en
un fallo de test para el caso que se acaba de anadir.

Por que importa en esta tabla en concreto
-----------------------------------------

R3 del contrato depende de que `cerrado_por` sea NOT NULL y de que
`(company_id, periodo)` sea UNIQUE. Si el DDL de Postgres no los tuviera, la
base recien creada aceptaria un cierre sin firma y con periodos duplicados, y el
informe daria por cerrado algo que nadie cerro. En SQLite los tests si lo
detectarian, porque construyen desde los modelos — y por eso hace falta *esta*
comprobacion, contra el archivo que de verdad ejecuta Postgres.

Que protege
-----------

1. Que cada columna del modelo exista como columna de verdad en el `CREATE
   TABLE` de `init.sql` (y no solo como palabra suelta: ver
   `_columnas_definidas`).
2. Que lo que R3 necesita — `cerrado_por` y `periodo` NOT NULL, el indice
   UNIQUE — este en los dos lados.
3. Que el DDL de Postgres use la regex completa del contrato. La del modelo es
   mas debil a proposito (SQLite no entiende `~`), y por eso el DDL no puede
   serlo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.cierre_periodo import CierrePeriodoModel

RAIZ = Path(__file__).resolve().parents[2]
INIT_SQL = RAIZ / "db/init.sql"
TABLA = "cierres_periodo"

# El `CREATE TABLE` de la tabla, tal cual esta en init.sql. Se recorta por
# nombre de tabla y no por numero de linea, para que anadir tablas arriba no
# rompa este test.
_BLOQUE = re.compile(
    rf"CREATE TABLE IF NOT EXISTS {TABLA}\s*\((.*?)\n\);",
    re.DOTALL | re.IGNORECASE,
)


@pytest.fixture(scope="module")
def ddl() -> str:
    texto = INIT_SQL.read_text(encoding="utf-8")
    m = _BLOQUE.search(texto)
    assert m, (
        f"init.sql ya no tiene un 'CREATE TABLE {TABLA}' reconocible. Si le "
        "cambiaste el nombre de la tabla o el formato del CREATE, actualiza este "
        "test: la comprobacion que hace es contra el archivo que ejecuta "
        "Postgres, no contra los modelos."
    )
    return m.group(1)


def _columnas_definidas(ddl: str) -> set[str]:
    """Los nombres de columna REALES del CREATE TABLE, no las palabras sueltas.

    La primera version de este test hacia `re.search(rf"\\b{columna}\\b", ddl)` y
    daba VERDE con la columna `huella` borrada del DDL, porque la palabra
    "huella" sigue apareciendo en el nombre de la constraint
    `ck_cierres_periodo_huella_hex` y en los comentarios. Se comprobo con una
    mutacion: borrar la columna no mato el test.

    Es la trampa que el docstring del archivo advertia y que el test se tomo solo:
    buscar una palabra no es comprobar una columna.

    Ahora se parte el bloque en elementos de primer nivel —los parentesis de
    `VARCHAR(7)` no cuentan como separador— y de cada elemento se saca el primer
    token, que es el nombre de la columna. `CONSTRAINT`, `PRIMARY`, `UNIQUE`,
    `FOREIGN` y `CHECK` se descartan porque no son columnas.

    El orden importa: los comentarios se quitan ANTES de partir por comas. Los
    comentarios de este DDL son prosa en espanol y traen comas dentro
    ("...a partir del dia 1, y en un cierre fiscal son dos cosas distintas"),
    asi que partir primero hace que las frases salgan en trozos que empiezan
    por 'y', 'que' y 'no', y el parser crea que esas son columnas.
    """
    # Comentarios fuera primero: uno llega hasta el fin de linea.
    sin_comentarios = re.sub(r"--[^\n]*", " ", ddl)

    elementos, nivel, actual = [], 0, []
    for ch in sin_comentarios:
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        if ch == "," and nivel == 0:
            elementos.append("".join(actual))
            actual = []
        else:
            actual.append(ch)
    elementos.append("".join(actual))

    columnas: set[str] = set()
    for elemento in elementos:
        limpio = elemento.strip()
        if not limpio:
            continue
        primer_token = limpio.split()[0].upper()
        if primer_token in ("CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
            continue
        if re.fullmatch(r"\w+", primer_token):
            columnas.add(primer_token.lower())
    return columnas


class TestLaTablaExisteEnInitSql:
    @pytest.mark.parametrize(
        "columna",
        [
            "id",
            "company_id",
            "periodo",
            "cerrado_por",
            "cerrado_at",
            "pendientes_al_cerrar",
            "huella",
        ],
    )
    def test_cada_columna_del_modelo_existe_como_columna_en_el_ddl(self, ddl, columna):
        definidas = _columnas_definidas(ddl)
        assert columna in definidas, (
            f"la columna `{columna}` esta en el modelo y no como columna en el "
            f"CREATE TABLE de init.sql (las que si estan: {sorted(definidas)}).\n"
            f"Postgres crearia la tabla sin ella y la primera consulta daria "
            f"UndefinedColumnError."
        )

    def test_init_sql_no_declara_columnas_que_el_modelo_no_conoce(self, ddl):
        """La otra direccion del divergence.

        Una columna de mas en el DDL no rompe al modelo, pero hace que alguien
        escriba una consulta que funciona en la base creada de cero y falla en la
        migrada, que es el fallo mas caro de diagnosticar.
        """
        definidas = _columnas_definidas(ddl)
        en_modelo = {c.name for c in CierrePeriodoModel.__table__.columns}
        huerfanas = definidas - en_modelo
        assert not huerfanas, (
            f"init.sql define columnas que el modelo no tiene: {sorted(huerfanas)}.\n"
            f"O al reves: una columna que existe en la base creada de cero pero no "
            f"en la migrada hace que una consulta funcione en una base y falle en "
            f"la otra."
        )


class TestLoQueR3NecesitaEstaEnLosDosLados:
    """Las tres cosas de las que depende la regla, y donde fallan si divergen."""

    def test_cerrado_por_es_not_null_en_los_dos(self, ddl):
        # Si `cerrado_por` admitiera NULL, existiria un cierre sin firma y R3
        # diria que el contador lo cerro cuando no hay contador.
        assert re.search(r"cerrado_por\s+VARCHAR\(255\)\s+NOT NULL", ddl), (
            "en init.sql, `cerrado_por` tiene que ser NOT NULL"
        )
        assert CierrePeriodoModel.__table__.c.cerrado_por.nullable is False, (
            "en el modelo, `cerrado_por` tiene que ser NOT NULL"
        )

    def test_el_periodo_es_not_null_en_los_dos(self, ddl):
        assert re.search(r"periodo\s+VARCHAR\(7\)\s+NOT NULL", ddl), (
            "en init.sql, `periodo` tiene que ser NOT NULL"
        )
        assert CierrePeriodoModel.__table__.c.periodo.nullable is False

    def test_el_indice_unico_existe_en_los_dos_con_el_mismo_nombre(self):
        texto = INIT_SQL.read_text(encoding="utf-8")
        indice = re.search(
            rf"CREATE UNIQUE INDEX IF NOT EXISTS (\S+)\s+ON {TABLA}\s*"
            rf"\(\s*company_id,\s*periodo\s*\)",
            texto,
        )
        assert indice, (
            f"init.sql no tiene un indice UNIQUE (company_id, periodo) sobre "
            f"{TABLA}. Sin el se pueden meter dos cierres del mismo periodo y el "
            f"informe no sabe cual se firmo."
        )
        nombres_modelo = {i.name for i in CierrePeriodoModel.__table__.indexes}
        assert indice.group(1) in nombres_modelo, (
            f"el indice del DDL se llama {indice.group(1)} y en el modelo no "
            f"existe ese nombre. Con nombres distintos, migrar el DDL no lleva "
            f"consigo el indice."
        )


class TestLaTablaSirve:
    """Que se pueda escribir en ella, que es distinto de que exista.

    Este bloque no estaba, y su ausencia costo una hora. Los tests de arriba
    comparan el DDL con el modelo y salen verdes con una constraint que en
    SQLite rechazaba toda fila valida: `cerrado_at <= CURRENT_TIMESTAMP` compara
    '2026-09-30 18:07:24' (lo que da SQLite) contra '2026-09-30 18:07:24.704058'
    (lo que escribe SQLAlchemy), y la de microsegundos siempre gana. Ningun test
    insertaba una fila, asi que nadie lo noto: 765 tests en verde y una tabla que
    no aceptaba cierres.

    Un esquema que no se puede escribir no es un esquema verificado.
    """

    @pytest.fixture()
    def sesion_sqlite(self, tmp_path):
        """Una base SQLite real, con el esquema construido desde los modelos."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.database import Base
        import app.models  # noqa: F401  (registra todas las tablas)

        motor = create_engine(f"sqlite:///{tmp_path}/cierre.db")
        Base.metadata.create_all(motor)
        Session = sessionmaker(bind=motor)
        try:
            yield Session()
        finally:
            motor.dispose()

    def _empresa(self, sesion):
        from app.models.company import CompanyModel

        empresa = CompanyModel(name="Comercial SA", tax_id="COCE010101XX")
        sesion.add(empresa)
        sesion.commit()
        return empresa

    def test_un_cierre_valido_se_guarda(self, sesion_sqlite):
        from app.core.time import utcnow
        from app.models.cierre_periodo import CierrePeriodoModel

        empresa = self._empresa(sesion_sqlite)
        ahora = utcnow()
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo="2026-01",
                cerrado_por="contador@example.mx",
                cerrado_at=ahora,
                pendientes_al_cerrar={"sin_categoria": 0, "sin_conciliar": 3},
                huella="a" * 64,
            )
        )
        sesion_sqlite.commit()  # aqui es donde revienta si el esquema esta roto

        fila = sesion_sqlite.query(CierrePeriodoModel).one()
        assert fila.periodo == "2026-01"
        assert fila.cerrado_por == "contador@example.mx"
        assert fila.pendientes_al_cerrar == {"sin_categoria": 0, "sin_conciliar": 3}
        assert fila.huella == "a" * 64

    def test_el_json_vuelve_como_tal_cuando_nada_falta(self, sesion_sqlite):
        """`pendientes_al_cerrar` y `huella` son NULL, no cadenas vacias.

        R7: una seccion que no se pudo calcular dice "no disponible". Un `''` o un
        `0` seria rellenar el hueco con un dato falso, que es justo lo que el
        informe no debe hacer.
        """
        from app.core.time import utcnow
        from app.models.cierre_periodo import CierrePeriodoModel

        empresa = self._empresa(sesion_sqlite)
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo="2026-02",
                cerrado_por="contador@example.mx",
                cerrado_at=utcnow(),
            )
        )
        sesion_sqlite.commit()

        fila = sesion_sqlite.query(CierrePeriodoModel).one()
        assert fila.pendientes_al_cerrar is None
        assert fila.huella is None

    @pytest.mark.parametrize(
        "periodo",
        ["2026-13", "2026-00", "2026-1", "202601", "2026_01", "26-01", "2026-01-01"],
    )
    def test_un_periodo_invalido_lo_rechaza_la_base(self, sesion_sqlite, periodo):
        """La constraint de formato tiene que rejects, no avisar.

        La API devuelve 422 antes de llegar aqui, pero la base es la ultima linea
        y la unica que sobrevive a un script que escriba directo.
        """
        import pytest as _pytest
        from sqlalchemy.exc import IntegrityError

        from app.core.time import utcnow
        from app.models.cierre_periodo import CierrePeriodoModel

        empresa = self._empresa(sesion_sqlite)
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo=periodo,
                cerrado_por="contador@example.mx",
                cerrado_at=utcnow(),
            )
        )
        with _pytest.raises(IntegrityError):
            sesion_sqlite.commit()
        sesion_sqlite.rollback()

    def test_dos_cierres_del_mismo_periodo_no_caben(self, sesion_sqlite):
        """El indice UNIQUE hace su trabajo: el grano es (empresa, periodo)."""
        from sqlalchemy.exc import IntegrityError

        from app.core.time import utcnow
        from app.models.cierre_periodo import CierrePeriodoModel

        empresa = self._empresa(sesion_sqlite)
        # El primero entra bien y se confirma, para que quede UNA fila cerrada.
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo="2026-03",
                cerrado_por="ana@example.mx",
                cerrado_at=utcnow(),
            )
        )
        sesion_sqlite.commit()

        # El segundo es el que tiene que rebotar. Se agrega SIN commitear el
        # anterior en el mismo bucle: un commit dentro del `for` hacia fallar el
        # segundo por el UNIQUE antes de que el test llegara al `raises`.
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo="2026-03",
                cerrado_por="beto@example.mx",
                cerrado_at=utcnow(),
            )
        )
        with pytest.raises(IntegrityError):
            sesion_sqlite.commit()
        sesion_sqlite.rollback()

        # Y sigue habiendo uno solo, no tres.
        assert (
            sesion_sqlite.query(CierrePeriodoModel)
            .filter_by(company_id=empresa.id, periodo="2026-03")
            .count()
            == 1
        )

    def test_una_huella_de_largo_distinto_no_se_guarda(self, sesion_sqlite):
        from sqlalchemy.exc import IntegrityError

        from app.core.time import utcnow
        from app.models.cierre_periodo import CierrePeriodoModel

        empresa = self._empresa(sesion_sqlite)
        sesion_sqlite.add(
            CierrePeriodoModel(
                company_id=empresa.id,
                periodo="2026-04",
                cerrado_por="contador@example.mx",
                cerrado_at=utcnow(),
                huella="NOESUNAHUELLA",
            )
        )
        with pytest.raises(IntegrityError):
            sesion_sqlite.commit()
        sesion_sqlite.rollback()


class TestLaFormaDelPeriodoNoSeDebilitaEnElDdl:
    """La version portable del modelo es mas debil. Postgres no tiene por que serlo."""

    def test_el_ddl_usa_la_regex_del_contrato(self, ddl):
        assert re.search(r"periodo\s*~\s*'\^\\d\{4\}-\(0\[1-9\]\|1\[0-2\]\)\$'", ddl), (
            "la constraint de init.sql tiene que usar la regex completa del "
            "contrato (^\\d{4}-(0[1-9]|1[0-2])$). La del modelo es mas debil a "
            "proposito, porque SQLite no entiende `~`; si el DDL de Postgres "
            "tambien se queda corto, entonces ningun motor rechaza un anio no "
            "numerico como 'abcd-01'."
        )

    def test_el_ddl_exige_la_huella_como_64_hex(self, ddl):
        assert re.search(r"huella IS NULL OR huella ~ '\^\[0-9a-f\]\{64\}\$'", ddl), (
            "la constraint de la huella en init.sql tiene que exigir 64 hex "
            "minusculas. La del modelo solo comprueba el largo, porque SQLite no "
            "tiene forma de expresar 'solo hex'; si el DDL tampoco la exige, una "
            "huella mal formada se acepta y la comparacion posterior no coincide "
            "nunca, que es un fallo silencioso: el informe siempre dira 'cerrado "
            "con otra informacion'."
        )

    def test_el_ddl_rechaza_un_sello_en_el_futuro(self, ddl):
        assert re.search(r"cerrado_at\s*<=\s*now\(\)", ddl), (
            "la constraint del sello tiene que estar en init.sql. Un cierre con "
            "fecha de 2099 no es un cierre, es una intencion, y el informe no "
            "debe reportarlo como cerrado."
        )
