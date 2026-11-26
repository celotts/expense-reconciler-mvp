"""El contrato de la base para la revocacion de tokens y la auditoria por empresa.

Este archivo mira la DECLARACION, no el comportamiento HTTP (ese llega despues,
cuando la guardia lea la tabla). Aun asi tiene defensas que mueren si se quita
una parte: la tabla append-only, los motivos permitidos y la ausencia de backfill
en una tabla donde un `UPDATE` esta prohibido por trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
MIGRACION_REVOCACION = RAIZ / "db" / "migrations" / "0025_tokens_revocados.sql"
MIGRACION_AUDITORIA = RAIZ / "db" / "migrations" / "0026_auditoria_company_id.sql"
INIT_SQL = RAIZ / "db" / "init.sql"


def _sin_comentarios(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"\$[A-Za-z_]*\$.*?\$[A-Za-z_]*\$", " ", sql, flags=re.S)
    return sql


@pytest.fixture(scope="module")
def revocacion_sql() -> str:
    return _sin_comentarios(MIGRACION_REVOCACION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def auditoria_sql() -> str:
    return _sin_comentarios(MIGRACION_AUDITORIA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def init_sql() -> str:
    return _sin_comentarios(INIT_SQL.read_text(encoding="utf-8"))


class TestLaTablaDeRevocaciones:
    def test_la_migracion_declara_todas_las_columnas_del_modelo(self, revocacion_sql):
        from app.models import TokenRevocado

        crea_tabla = re.search(
            r"CREATE TABLE IF NOT EXISTS tokens_revocados\s*\((.*?)\n\);",
            revocacion_sql,
            re.S,
        )
        assert crea_tabla, "falta el CREATE TABLE de tokens_revocados en 0025"
        cuerpo = crea_tabla.group(1)
        for columna in (c.name for c in TokenRevocado.__table__.columns):
            assert re.search(rf"^\s+{re.escape(columna)}\s+[A-Z]", cuerpo, re.M), (
                f"la columna {columna} la declara el modelo pero no la migracion"
            )

    def test_es_append_only_en_la_migracion_y_en_el_init(self, revocacion_sql, init_sql):
        """Sin los triggers, un `DELETE FROM tokens_revocados` revivira un token.

        Se comprueban los dos sitios: la migracion arregla una base existente y
        el init.sql una base nueva. Si solo esta en uno, el agujero depende de
        como se instalo la base.
        """
        for nombre, sql in (("migracion 0025", revocacion_sql), ("init.sql", init_sql)):
            for trigger in (
                "trg_tokens_revocados_no_actualizar",
                "trg_tokens_revocados_no_borrar",
            ):
                assert f"CREATE TRIGGER {trigger}" in sql, (
                    f"falta {trigger} en {nombre}: una revocacion podria borrarse"
                )

    def test_los_motivos_son_los_mismos_en_modelo_y_base(self, revocacion_sql):
        from app.models import MOTIVOS_REVOCACION

        check = re.search(
            r"ck_tokens_revocados_motivo\s+CHECK\s*\(motivo IN \(([^)]*)\)\)",
            revocacion_sql,
        )
        assert check, "falta el CHECK de motivos en 0025"
        en_sql = set(re.findall(r"'([^']+)'", check.group(1)))
        assert en_sql == set(MOTIVOS_REVOCACION)

    def test_la_fk_no_deja_borrar_la_revocacion_con_la_cuenta(self, revocacion_sql):
        assert "ON DELETE RESTRICT" in revocacion_sql
        assert "ON DELETE CASCADE" not in revocacion_sql


class TestLaEmpresaEnAuditoria:
    def test_la_columna_es_nullable_y_tiene_indice(self, auditoria_sql, init_sql):
        assert "ADD COLUMN IF NOT EXISTS company_id UUID" in auditoria_sql
        assert "ix_auditoria_company_fecha" in auditoria_sql
        assert "ix_auditoria_company_fecha" in init_sql

    def test_la_columna_apunta_a_una_empresa_existente(self, auditoria_sql, init_sql):
        """Sin FK, un evento podria decir `company_id` de una empresa borrada.

        Buscar solo `company_id UUID` no basta: quitar la referencia deja el
        mismo prefijo y el test anterior sigue en verde (medido).
        """
        assert (
            "company_id UUID REFERENCES companies(id) ON DELETE SET NULL"
            in auditoria_sql
        )
        assert "company_id UUID REFERENCES companies(id) ON DELETE SET NULL" in init_sql

    def test_no_hay_backfill_porque_la_tabla_es_append_only(self, auditoria_sql):
        """Un `UPDATE auditoria` revienta por trigger desde 0020.

        Si alguien anade un backfill para "arreglar" los NULL historicos, este
        test muere: la migration fallaria en Postgres y la API no arrancaria.
        """
        update = re.findall(r"UPDATE\s+auditoria\b", auditoria_sql, re.I)
        assert not update, "0026 intenta hacer UPDATE sobre una tabla append-only"

    def test_el_modelo_declara_la_columna_y_es_nullable(self):
        from app.models import Auditoria

        columna = Auditoria.__table__.c.company_id
        assert columna.nullable is True


class TestElRegistroDeMigraciones:
    def test_el_init_registra_las_dos_migraciones_nuevas(self):
        sql = INIT_SQL.read_text(encoding="utf-8")
        for nombre in (
            "0025_tokens_revocados.sql",
            "0026_auditoria_company_id.sql",
        ):
            assert f"('{nombre}')" in sql

    def test_cada_migracion_registra_su_propio_nombre(self, revocacion_sql, auditoria_sql):
        assert "VALUES ('0025_tokens_revocados.sql')" in revocacion_sql
        assert "VALUES ('0026_auditoria_company_id.sql')" in auditoria_sql
