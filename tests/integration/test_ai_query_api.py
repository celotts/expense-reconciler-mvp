"""Integracion del endpoint `POST /api/v1/ai-query/ask`.

Ollama se simula con monkeypatch sobre `consulta_nl.preguntar_al_modelo`:
lo que se prueba es la ruta completa — autenticacion, resolucion del
tenant, guardia, ejecucion y respuesta — con el SQL que el modelo
"generaria". El SQL NUNCA llega a la base sin pasar el guardia, y este
test lo comprueba con las tres cerraduras:

1. resolver_empresa: la empresa del cuerpo no decide contra la del usuario
2. validar_sql_solo_lectura: SQL de escritura/multisentencia -> 400
3. ejecucion: el resultado que ve el cliente viene de la base real
"""

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.ticket import TicketModel
from app.services import consulta_nl


async def _fabricar_ticket(db_session, company_id, provider="OXXO", total="100.00"):
    ticket = TicketModel(
        company_id=company_id,
        provider_name=provider,
        total_amount=Decimal(total),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 11, 1),
        extraction_status="APROBADO",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestAskFeliz:
    @pytest.mark.asyncio
    async def test_pregunta_devuelve_filas_de_la_base(
        self, async_client: AsyncClient, db_session, test_company, monkeypatch,
    ):
        # SQLite guarda los UUID sin guiones (CHAR(32) hex de SQLAlchemy), asi
        # que `company_id = '<uuid con guiones>'` jamas devuelve filas aqui.
        # Por eso el mock hace lo que haria el modelo — devuelve una fila
        # con el mismo predicado literal que exige el guardia, sobre un CTE —
        # y lo que este test asegura es la PLOMERIA: auth, tenant, guardia,
        # ejecucion y serializacion. La lectura de `tickets WHERE company_id`
        # con UUID dashed es correcta en Postgres (produccion), no en SQLite.
        async def fake_ollama(pregunta, tenant_id):
            return (
                f"WITH t(provider_name, company_id) AS "
                f"(SELECT 'OXXO', '{tenant_id}') "
                f"SELECT provider_name FROM t WHERE company_id = '{tenant_id}'"
            )

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "cuales son mis gastos?", "tenant_id": str(test_company.id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["sql"].startswith("WITH")
        assert any(f["provider_name"] == "OXXO" for f in data["filas"])
        assert data["cantidad_filas"] == 1
        assert data["truncado"] is False


class TestAskRechazos:
    @pytest.mark.asyncio
    async def test_sql_con_escritura_devuelve_400(
        self, async_client: AsyncClient, test_company, monkeypatch,
    ):
        async def fake_ollama(pregunta, tenant_id):
            return f"UPDATE tickets SET total_amount = 0 WHERE company_id = '{tenant_id}'"

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "pon todos en cero", "tenant_id": str(test_company.id)},
        )
        assert response.status_code == 400
        assert "rechazado" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_sql_sin_predicado_de_tenant_devuelve_400(
        self, async_client: AsyncClient, test_company, monkeypatch,
    ):
        async def fake_ollama(pregunta, tenant_id):
            return "SELECT * FROM tickets"

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "todo", "tenant_id": str(test_company.id)},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_tabla_fuera_de_la_lista_blanca_devuelve_400(
        self, async_client: AsyncClient, test_company, monkeypatch,
    ):
        async def fake_ollama(pregunta, tenant_id):
            return f"SELECT email FROM users WHERE company_id = '{tenant_id}'"

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "dame los correos", "tenant_id": str(test_company.id)},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_tenant_ajeno_devuelve_404(
        self, async_client: AsyncClient, test_company, monkeypatch,
    ):
        async def fake_ollama(pregunta, tenant_id):
            raise AssertionError("el modelo no debe llamarse cuando la empresa es ajena")

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        otro_tenant = "00000000-0000-0000-0000-000000000000"
        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "datos de otra empresa", "tenant_id": otro_tenant},
        )
        assert response.status_code == 404


class TestAskFallasDelProveedor:
    @pytest.mark.asyncio
    async def test_ollama_caido_devuelve_503(
        self, async_client: AsyncClient, test_company, monkeypatch,
    ):
        async def fake_ollama(pregunta, tenant_id):
            raise RuntimeError("No se pudo contactar al servidor local de Ollama")

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "algo", "tenant_id": str(test_company.id)},
        )
        assert response.status_code == 503


class TestAskLimiteDeFilas:
    @pytest.mark.asyncio
    async def test_truncado_cuando_hay_mas_filas_que_max(
        self, async_client: AsyncClient, db_session, test_company, monkeypatch,
    ):
        # Ver el comentario en TestAskFeliz sobre el UUID en SQLite.
        async def fake_ollama(pregunta, tenant_id):
            return (
                f"WITH n(x, company_id) AS ("
                f"SELECT 1, '{tenant_id}' UNION ALL "
                f"SELECT 2, '{tenant_id}' UNION ALL "
                f"SELECT 3, '{tenant_id}') "
                f"SELECT 'P' || x AS provider_name FROM n "
                f"WHERE company_id = '{tenant_id}'"
            )

        monkeypatch.setattr(consulta_nl, "preguntar_al_modelo", fake_ollama)

        response = await async_client.post(
            "/api/v1/ai-query/ask",
            json={"pregunta": "proveedores", "tenant_id": str(test_company.id), "max_filas": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cantidad_filas"] == 2
        assert data["truncado"] is True


class TestEjecutarConsulta:
    """`ejecutar_consulta` contra la tabla `tickets` de verdad.

    En SQLite el UUID se guarda como CHAR(32) hex (SQLAlchemy lo serializa
    con `.hex`), asi que el predicado usa el hex. Esto prueba la parte que
    el mock del LLM no cubre: la ejecucion cruda, el LIMIT y la serializacion
    de los importes (str, no float).
    """

    @pytest.mark.asyncio
    async def test_lee_tickets_y_serializa_importes(
        self, db_session, test_company,
    ):
        await _fabricar_ticket(db_session, test_company.id, provider="OXXO", total="123.45")

        filas = await consulta_nl.ejecutar_consulta(
            f"SELECT provider_name, total_amount FROM tickets WHERE company_id = '{test_company.id.hex}'",
            10,
            db_session,
        )
        assert filas
        assert filas[0]["provider_name"] == "OXXO"
        # El importe viaja como str: exacto y sin redondeo binario.
        assert filas[0]["total_amount"] == "123.45"
