"""El esquema de la administracion: init.sql contra los modelos.

POR QUE ESTE ARCHIVO EXISTE
---------------------------

Porque `db/init.sql` y los modelos son el mismo esquema escrito dos veces, y nada
los ataba. Se comprobo antes de que este trabajo empezara, y ya hay un archivo que
lo hace para `cierres_periodo`: `test_cierre_periodo_esquema.py`. Este es el mismo
chequeo para las cuatro cosas que la administracion toco.

Y no es teorico. La migracion `0013` anade `reconciliations.revisado_por` y
`revisado_at`, y RECHAZADO a `ck_compras_estado`. Si `init.sql` no los tuviera:

  - una base creada de cero (que es `init.sql`) no tendria las columnas, y
    `PATCH /reconciliations/{id}` daria UndefinedColumnError. Los tests NO lo
    detectarian, porque arman el esquema desde los MODELOS.
  - `POST /inventario/compras/{id}/rechazar` daria IntegrityError en una base nueva
    y no en una migrada. Dos entornos, dos comportamientos, sin marca de error.

QUE COMPRUEBA Y QUE NO
----------------------

Comprueba que las columnas del modelo existen como COLUMNAS en el DDL, en las dos
direcciones, y que la constraint de estados incluye lo que el codigo escribe.

NO comprueba los triggers. SQLite no tiene triggers de este tipo y los de Postgres
no se declaran en un `CREATE TABLE`: van en
`scripts/verify_postgres_inventario.py`. Decirlo aqui es lo que evita que alguien
agregue una prueba de trigger a este archivo y la vea pasar sin comprobar nada.

EL `_BLOQUE` Y POR QUE NO ES UN `re.search` DE LA TABLA ENTERA
--------------------------------------------------------------

Recorta por nombre de tabla y se queda con el parentesis que cierra. Es lo mismo
que hace el archivo de `cierres_periodo`, y por lo mismo: el DDL de Postgres tiene
`CREATE TRIGGER`, `CREATE FUNCTION` y `CREATE INDEX` que mencionan los mismos
nombres de tabla, asi que un `re.search("compras")` sobre el archivo entero matchea
el trigger y no el `CREATE TABLE`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.inventario import CompraModel, MovimientoInventarioModel, ProductoModel
from app.models.reconciliation import ReconciliationModel

RAIZ = Path(__file__).resolve().parents[2]
INIT_SQL = RAIZ / "db" / "init.sql"
MIGRACION_0013 = RAIZ / "db" / "migrations" / "0013_compras_rechazables.sql"


@pytest.fixture(scope="module")
def ddl_compras() -> str:
    return _bloque_de_tabla("compras")


@pytest.fixture(scope="module")
def ddl_reconciliations() -> str:
    return _bloque_de_tabla("reconciliations")


def _bloque_de_tabla(tabla: str) -> str:
    texto = INIT_SQL.read_text(encoding="utf-8")
    m = re.search(
        rf"CREATE TABLE IF NOT EXISTS {tabla}\s*\((.*?)\n\);",
        texto,
        re.DOTALL | re.IGNORECASE,
    )
    assert m, (
        f"init.sql ya no tiene un 'CREATE TABLE {tabla}' reconocible. Si le "
        "cambiaste el nombre o el formato del CREATE, actualiza este test: la "
        "comprobacion es contra el archivo que ejecuta Postgres, no contra los "
        "modelos."
    )
    return m.group(1)


def _columnas_definidas(ddl: str) -> set[str]:
    """Los nombres de columna REALES del CREATE TABLE.

    Copiado de `test_cierre_periodo_esquema.py` y por el mismo motivo: buscar una
    palabra no es comprobar una columna. La primera version de those tests hacia
    `re.search(rf"\\b{columna}\\b", ddl)` y daba VERDE con la columna `huella`
    borrada, porque la palabra sigue en el nombre de la constraint.

    Se parte en elementos de primer nivel —los parentesis de `VARCHAR(7)` no son
    separadores— y de cada elemento se saca el primer token, que es el nombre de
    la columna. `CONSTRAINT`, `PRIMARY`, `UNIQUE`, `FOREIGN` y `CHECK` se descartan
    porque no son columnas.

    Los comentarios se quitan ANTES de partir por comas: los de este DDL son prosa
    en espanol y traen comas dentro.
    """
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


class TestLasColumnasDeLaRevisionEstanEnLosDosLados:
    """`revisado_por` y `revisado_at`: la defensa, y necesita las dos columnas.

    Si `init.sql` no las tuviera, `PATCH /reconciliations/{id}` daria
    UndefinedColumnError en una base creada de cero. Los tests de este repo no lo
    verian, porque arman el esquema desde los modelos.
    """

    @pytest.mark.parametrize("columna", ["revisado_por", "revisado_at"])
    def test_la_columna_existe_como_columna_en_el_ddl(self, ddl_reconciliations, columna):
        definidas = _columnas_definidas(ddl_reconciliations)
        assert columna in definidas, (
            f"la columna `{columna}` esta en el modelo y no como columna en el "
            f"CREATE TABLE de init.sql (las que si estan: {sorted(definidas)})."
        )

    def test_init_sql_no_declara_columnas_que_el_modelo_no_conoce(
        self, ddl_reconciliations
    ):
        definidas = _columnas_definidas(ddl_reconciliations)
        en_modelo = {c.name for c in ReconciliationModel.__table__.columns}
        huerfanas = definidas - en_modelo
        assert not huerfanas, (
            f"init.sql define columnas de reconciliations que el modelo no tiene: "
            f"{sorted(huerfanas)}.\n"
            f"Una columna que existe en la base creada de cero pero no en la "
            f"migrada hace que una consulta funcione en una base y falle en la otra."
        )


def _constraint_de_estados(ddl: str) -> str:
    """El cuerpo del `CHECK` de `ck_compras_estado`, dentro del CREATE TABLE.

    POR QUE HACE FALTA UN HELPER Y NO UN `re.search` EN CADA TEST
    -------------------------------------------------------------

    Porque la forma del DDL es `CONSTRAINT ck_compras_estado\n        CHECK (...)`:
    hay un `CHECK` entre el nombre y el parentesis. Un patron que espere
    `ck_compras_estado\\s*\\(` no matchea nunca, y el fallo se lee como "init.sql no
    tiene la constraint", que es un diagnostico equivocado —si la tiene, y con
    RECHAZADO.

    Se busca dentro del bloque del `CREATE TABLE` y no sobre `init.sql` entero,
    porque `ck_compras_estado` no aparece en otro sitio pero `RECHAZADO` si, y un
    match de ese no diria nada de la constraint.
    """
    bloque = re.sub(r"--[^\n]*", " ", ddl)
    constraint = re.search(
        r"CONSTRAINT\s+ck_compras_estado\s+CHECK\s*\((.*?)\)", bloque, re.DOTALL
    )
    return constraint.group(1) if constraint else ""


class TestRechazadoEstaEnLosDosLados:
    """`EstadoCompra` escribia RECHAZADO y la constraint no lo permitia.

    Si `ck_compras_estado` en `init.sql` no tuviera RECHAZADO,
    `POST /inventario/compras/{id}/rechazar` daria IntegrityError con un 500 en una
    base creada de cero, y funcionaria en una migrada. Dos entornos, dos
    comportamientos, sin marca de error.

    Y el fallo es invisible desde los tests del repo entero: arman el esquema desde
    `CompraModel.__table_args__`, donde la constraint ya incluye RECHAZADO.
    """

    def test_el_ddl_de_init_sql_acepta_rechazado(self, ddl_compras):
        """La constraint, y no el archivo entero.

        Se busca dentro del bloque del `CREATE TABLE` y no sobre `init.sql`, porque
        `RECHAZADO` aparece tambien en comentarios de otros sitios y un match ahi
        no diria nada de la constraint.
        """
        constraint = _constraint_de_estados(ddl_compras)
        assert constraint, (
            "init.sql ya no tiene un 'CONSTRAINT ck_compras_estado' reconocible en "
            "el CREATE TABLE de compras."
        )
        assert "RECHAZADO" in constraint, (
            "ck_compras_estado en init.sql no acepta RECHAZADO, y el codigo lo "
            "escribe. En una base creada de cero, POST "
            "/inventario/compras/{id}/rechazar daria IntegrityError."
        )

    def test_el_enum_y_el_ddl_dicen_lo_mismo(self, ddl_compras):
        """La lista del enum y la de la constraint, comparadas.

        Es el mismo criterio que `SpotCheckStatus` con `ck_tickets_spot_check_values`
        y `EstadoCompra` con su propia constraint: si divergen, se escribe un estado
        nuevo en Python y la base lo rechaza al guardarlo, en produccion.
        """
        from app.core.enums import EstadoCompra

        declarados = set(re.findall(r"'([A-Z_]+)'", _constraint_de_estados(ddl_compras)))
        del_enum = {e.value for e in EstadoCompra}

        assert declarados == del_enum, (
            f"la constraint declara {sorted(declarados)} y el enum "
            f"{sorted(del_enum)}. Un estado del enum que la constraint no conoce se "
            f"rechaza al guardarlo, y uno que la constraint conoce y el enum no, no "
            f"se puede ni escribir desde Python."
        )

    def test_la_migracion_0013_acepta_rechazado_tambien(self):
        """La migracion tiene que decirlo, no solo `init.sql`.

        Son dos caminos para llegar al mismo esquema: `init.sql` en una base nueva,
        las migraciones en una existente. Si solo uno de los dos lo dice, el
        comportamiento depende de como se creo la base — que es el fallo mas caro
        de diagnosticar.
        """
        texto = MIGRACION_0013.read_text(encoding="utf-8")
        assert "RECHAZADO" in texto, (
            "0013 no menciona RECHAZADO. Si la constraint no se actualiza ahi, una "
            "base migrada no podra rechazar una compra."
        )
        assert "ck_compras_estado" in texto


class TestElModeloYLasColumnasNoSeHanMovido:
    """Guarda contra una refactorizacion que se lleve una columna por delante.

    No comprueba el DDL: comprueba que el modelo sigue teniendo las columnas que el
    codigo del router y del servicio tocan por atributo. Si alguien renombra
    `revisado_por` en el modelo y no en el router, esto no lo detecta, pero
    `test_la_columna_existe_como_columna_en_el_ddl` mas los tests de API si.
    """

    @pytest.mark.parametrize(
        "columna",
        ["id", "company_id", "nombre", "origen", "verificado", "activo"],
    )
    def test_productos_sigue_teniendo_lo_que_el_router_toca(self, columna):
        assert columna in {c.name for c in ProductoModel.__table__.columns}

    @pytest.mark.parametrize("columna", ["id", "company_id", "ticket_id", "estado"])
    def test_compras_sigue_teniendo_lo_que_el_router_toca(self, columna):
        assert columna in {c.name for c in CompraModel.__table__.columns}

    @pytest.mark.parametrize(
        "columna", ["id", "producto_id", "tipo", "cantidad", "referencia_tipo", "actor"]
    )
    def test_movimientos_sigue_teniendo_lo_que_registrar_movimiento_escribe(
        self, columna
    ):
        assert columna in {c.name for c in MovimientoInventarioModel.__table__.columns}
