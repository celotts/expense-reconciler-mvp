"""El guardia de NL-to-SQL (`app.services.consulta_nl`).

Cada ataque escrito en este archivo esta ahi porque es el que alguien
escribiria con una cuenta buena: no se prueba que el modelo se porte bien,
se prueba que el modelo MALICIOSO (o el comprometido) no puede.
"""

from uuid import uuid4

import pytest

from app.services.consulta_nl import extraer_sql, validar_sql_solo_lectura

TENANT = uuid4()
FILTRO = f"company_id = '{TENANT}'"


def _sql_ok() -> str:
    return f"SELECT provider_name FROM tickets WHERE {FILTRO}"


class TestSQLValido:
    def test_select_simple(self):
        assert validar_sql_solo_lectura(_sql_ok(), TENANT) == _sql_ok()

    def test_punto_y_coma_final_se_tolera(self):
        sql = _sql_ok() + ";"
        assert validar_sql_solo_lectura(sql, TENANT) == _sql_ok()

    def test_with_cte(self):
        sql = f"WITH gastos AS (SELECT total_amount FROM tickets WHERE {FILTRO}) SELECT sum(total_amount) FROM gastos"
        assert validar_sql_solo_lectura(sql, TENANT)

    def test_tablas_de_la_lista_blanca(self):
        sql = f"SELECT t.id FROM tickets t JOIN bank_transactions b ON b.company_id = t.company_id WHERE t.{FILTRO}"
        assert validar_sql_solo_lectura(sql, TENANT)

    def test_reconciliations(self):
        sql = f"SELECT match_status, count(*) FROM reconciliations WHERE {FILTRO} GROUP BY match_status"
        assert validar_sql_solo_lectura(sql, TENANT)


class TestSentenciasMultiples:
    def test_select_seguido_de_drop(self):
        sql = f"{_sql_ok()}; DROP TABLE tickets"
        with pytest.raises(ValueError, match="UNA sola sentencia"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_select_seguido_de_select(self):
        sql = f"{_sql_ok()}; SELECT * FROM bank_transactions WHERE {FILTRO}"
        with pytest.raises(ValueError, match="UNA sola sentencia"):
            validar_sql_solo_lectura(sql, TENANT)


class TestEscritura:
    @pytest.mark.parametrize("verbo", [
        "insert", "update", "delete", "drop", "alter", "create",
        "truncate", "grant", "revoke", "into", "set",
    ])
    def test_palabra_de_escritura_rechaza(self, verbo):
        # Union con la palabra prohibida: el guardia la ve como token.
        sql = f"SELECT * FROM tickets WHERE {FILTRO} UNION SELECT {verbo}"
        with pytest.raises(ValueError):
            validar_sql_solo_lectura(sql, TENANT)

    def test_update_real_rechaza(self):
        sql = f"UPDATE tickets SET total_amount = 0 WHERE {FILTRO}"
        with pytest.raises(ValueError):
            validar_sql_solo_lectura(sql, TENANT)

    def test_with_insert_es_el_ataque_clasico(self):
        sql = f"WITH x AS (INSERT INTO tickets (provider_name) VALUES ('x') RETURNING *) SELECT * FROM x"
        with pytest.raises(ValueError):
            validar_sql_solo_lectura(sql, TENANT)

    def test_set_config_rechaza(self):
        sql = f"SELECT set_config('app.company_id', 'otro', false) FROM tickets WHERE {FILTRO}"
        with pytest.raises(ValueError):
            validar_sql_solo_lectura(sql, TENANT)

    def test_pg_read_file_rechaza(self):
        sql = f"SELECT pg_read_file('/etc/passwd') FROM tickets WHERE {FILTRO}"
        with pytest.raises(ValueError):
            validar_sql_solo_lectura(sql, TENANT)


class TestLiteralNoEngaña:
    def test_drop_dentro_de_un_string_no_es_un_comando(self):
        # 'DROP TABLE' como DATO, no como instruccion: se permite.
        sql = f"SELECT provider_name FROM tickets WHERE {FILTRO} AND raw_text = 'DROP TABLE tickets'"
        assert validar_sql_solo_lectura(sql, TENANT)

    def test_tenant_dentro_de_un_string_cuentan_solo_una_vez(self):
        sql = f"SELECT '{TENANT}' FROM tickets WHERE {FILTRO}"
        assert validar_sql_solo_lectura(sql, TENANT)


class TestListaBlanca:
    def test_tabla_ajena_rechaza(self):
        sql = f"SELECT * FROM companies WHERE id = '{TENANT}'"
        with pytest.raises(ValueError, match="lista blanca"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_users_rechaza(self):
        sql = f"SELECT email FROM users WHERE company_id = '{TENANT}'"
        with pytest.raises(ValueError, match="lista blanca"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_pg_catalog_rechaza(self):
        sql = f"SELECT * FROM pg_catalog.pg_tables WHERE {FILTRO}"
        with pytest.raises(ValueError, match="lista blanca"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_alias_de_cte_no_es_una_tabla_desconocida(self):
        sql = f"WITH gastos_x AS (SELECT total_amount FROM tickets WHERE {FILTRO}) SELECT * FROM gastos_x"
        assert validar_sql_solo_lectura(sql, TENANT)


class TestPredicadoDelTenant:
    def test_sin_filtro_de_tenant_rechaza(self):
        sql = "SELECT provider_name FROM tickets"
        with pytest.raises(ValueError, match="tenant"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_filtro_con_otro_uuid_rechaza(self):
        sql = f"SELECT provider_name FROM tickets WHERE company_id = '{uuid4()}'"
        with pytest.raises(ValueError, match="tenant"):
            validar_sql_solo_lectura(sql, TENANT)

    def test_filtro_con_otra_columna_no_cuenta(self):
        sql = f"SELECT provider_name FROM tickets WHERE id = '{TENANT}'"
        with pytest.raises(ValueError, match="tenant"):
            validar_sql_solo_lectura(sql, TENANT)


class TestExtraerSql:
    def test_bloque_con_fences(self):
        assert extraer_sql("```sql\nSELECT 1\n```") == "SELECT 1"

    def test_bloque_sin_lenguaje(self):
        assert extraer_sql("```\nSELECT 1\n```") == "SELECT 1"

    def test_texto_plano(self):
        assert extraer_sql("SELECT 1") == "SELECT 1"

    def test_prosa_alrededor_de_fences_gana_el_bloque(self):
        assert extraer_sql("Aqui tienes:\n```sql\nSELECT 1\n```\nlisto") == "SELECT 1"


class TestCTECdeListaDeColumnas:
    def test_cte_con_lista_de_columnas(self):
        # WITH t(a, b) AS (...) — el regex de CTE debe ver `t` como CTE,
        # no como tabla desconocida. El predicado de tenant igual va literal.
        sql = (
            f"WITH t(a, company_id) AS (SELECT 'OXXO', '{TENANT}') "
            f"SELECT a FROM t WHERE company_id = '{TENANT}'"
        )
        assert validar_sql_solo_lectura(sql, TENANT)
