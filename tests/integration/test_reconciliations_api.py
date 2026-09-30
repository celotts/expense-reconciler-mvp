import pytest
from httpx import AsyncClient
from uuid import uuid4
from decimal import Decimal
from datetime import date
from io import BytesIO
import pandas as pd


class TestReconciliationsAPI:
    @pytest.fixture
    async def setup_reconciliation_data(self, async_client: AsyncClient, test_company):
        """Crear tickets y movimientos para conciliar"""
        # Crear tickets
        tickets = []
        for i, (provider, amount, day) in enumerate([
            ("WALMART", "100.00", 15),
            ("AMAZON", "250.00", 16),
            ("COSTCO", "500.00", 17)
        ]):
            resp = await async_client.post(
                "/api/v1/tickets/",
                json={
                    "company_id": str(test_company.id),
                    "provider_name": provider,
                    "total_amount": amount,
                    "tax_amount": str(Decimal(amount) * Decimal("0.16")),
                    "expense_date": f"2025-01-{day}",
                    "category": "TEST"
                }
            )
            tickets.append(resp.json())
        
        # Crear movimientos bancarios coincidentes
        banks = []
        for i, (desc, amount, day) in enumerate([
            ("PAGO WALMART", "-100.00", 15),
            ("COMPRA AMAZON", "-250.00", 16),
            ("PAGO COSTCO", "-500.00", 17)
        ]):
            resp = await async_client.post(
                "/api/v1/bank-transactions/",
                json={
                    "company_id": str(test_company.id),
                    "transaction_date": f"2025-01-{day}",
                    "amount": amount,
                    "description": desc
                }
            )
            banks.append(resp.json())
        
        return tickets, banks

    @pytest.mark.asyncio
    async def test_run_reconciliation_perfect_matches(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        response = await async_client.post(
            "/api/v1/reconciliations/run",
            json={
                "company_id": str(test_company.id),
                "amount_tolerance": "0.01",
                "date_tolerance_days": 3
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["total_tickets"] == 3
        assert data["total_bank_transactions"] == 3
        assert data["perfect_matches"] == 3
        assert data["manual_review"] == 0
        assert data["discrepancies"] == 0
        assert data["unmatched_tickets"] == 0
        assert data["unmatched_bank_transactions"] == 0
        assert len(data["matches"]) == 3
        
        # Verificar detalle de matches
        for match in data["matches"]:
            assert match["match_status"] == "PERFECT"
            assert match["amount_diff"] == "0.00"
            assert match["date_diff_days"] == 0

    @pytest.mark.asyncio
    async def test_run_reconciliation_manual_match_date_diff(self, async_client: AsyncClient, test_company):
        # Ticket y banco con misma cantidad pero fecha diferente (> tolerancia)
        await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "PROVEEDOR",
                "total_amount": "300.00",
                "expense_date": "2025-01-10"
            }
        )
        await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-20",  # 10 días después
                "amount": "-300.00",
                "description": "PAGO PROVEEDOR"
            }
        )
        
        response = await async_client.post(
            "/api/v1/reconciliations/run",
            json={
                "company_id": str(test_company.id),
                "amount_tolerance": "0.01",
                "date_tolerance_days": 3  # Menor a 10 días
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["manual_review"] == 1
        assert data["matches"][0]["match_status"] == "MANUAL"
        assert data["matches"][0]["date_diff_days"] == 10

    @pytest.mark.asyncio
    async def test_run_reconciliation_discrepancy_amount_diff(self, async_client: AsyncClient, test_company):
        # Mismo día pero montos diferentes
        await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "TIENDA",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-150.00",  # Diferencia de 50
                "description": "PAGO TIENDA"
            }
        )
        
        response = await async_client.post(
            "/api/v1/reconciliations/run",
            json={
                "company_id": str(test_company.id),
                "amount_tolerance": "1.00",  # Menor a 50
                "date_tolerance_days": 3
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        # No concilia porque la diferencia de monto se pasa de la tolerancia,
        # pero el banco SI movio dinero ese dia. Eso es una discrepancia y se
        # reporta.
        #
        # Antes este test afirmaba `discrepancies == 0` con este mismo caso:
        # el nombre del test prometia la categoria y la asercion decia
        # que la categoria no existe. Era la rama muerta de `_find_best_match`
        # hecha test. Lo que faltaba era que se notara que el dinero salio por
        # otra cantidad, que es justo lo que hay que ver al cerrar el mes.
        assert data["perfect_matches"] == 0
        assert data["manual_review"] == 0
        assert data["discrepancies"] == 1
        # Queda contabilizado, no perdido: no vuelve a "sin nada".
        assert data["unmatched_tickets"] == 0
        assert data["unmatched_bank_transactions"] == 0
        assert data["matches"][0]["match_status"] == "DISCREPANCY"
        assert data["matches"][0]["amount_diff"] == "50.00"
        # Y dice POR QUE. Sin esto la fila dice que se reporto, pero no deja
        # reconstruir la decision, y "lo decidio el motor" no es una razon que
        # se pueda auditar en un cierre.
        assert "50.00" in data["matches"][0]["criterio"]

    @pytest.mark.asyncio
    async def test_list_reconciliations(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        # Ejecutar conciliación primero
        await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(test_company.id)}
        )
        
        response = await async_client.get(f"/api/v1/reconciliations/?company_id={test_company.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert all("match_status" in r for r in data)
        assert all("ticket" in r and "bank_transaction" in r for r in data)

    @pytest.mark.asyncio
    async def test_get_reconciliation(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(test_company.id)}
        )
        
        list_resp = await async_client.get(f"/api/v1/reconciliations/?company_id={test_company.id}")
        recon_id = list_resp.json()[0]["id"]
        
        response = await async_client.get(f"/api/v1/reconciliations/{recon_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == recon_id
        assert "ticket" in data
        assert "bank_transaction" in data

    @pytest.mark.asyncio
    async def test_create_manual_reconciliation(self, async_client: AsyncClient, test_company):
        # Crear ticket y banco sin conciliar
        ticket_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "MANUAL TICKET",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        bank_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-100.00",
                "description": "MANUAL BANK"
            }
        )
        
        ticket_id = ticket_resp.json()["id"]
        bank_id = bank_resp.json()["id"]
        
        response = await async_client.post(
            "/api/v1/reconciliations/",
            json={
                "ticket_id": ticket_id,
                "bank_transaction_id": bank_id,
                "match_status": "PERFECT"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["ticket_id"] == ticket_id
        assert data["bank_transaction_id"] == bank_id
        assert data["match_status"] == "PERFECT"
        
        # Verificar que banco marcado como reconciliado
        get_bank = await async_client.get(f"/api/v1/bank-transactions/{bank_id}")
        assert get_bank.json()["is_reconciled"] is True

    @pytest.mark.asyncio
    async def test_create_reconciliation_bank_already_reconciled_fails(self, async_client: AsyncClient, test_company):
        ticket_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "TICKET 1",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        bank_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-100.00",
                "description": "BANK 1"
            }
        )
        
        # Primera conciliación
        await async_client.post(
            "/api/v1/reconciliations/",
            json={
                "ticket_id": ticket_resp.json()["id"],
                "bank_transaction_id": bank_resp.json()["id"],
                "match_status": "PERFECT"
            }
        )
        
        # Segundo ticket intentando usar mismo banco
        ticket2_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "TICKET 2",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        
        response = await async_client.post(
            "/api/v1/reconciliations/",
            json={
                "ticket_id": ticket2_resp.json()["id"],
                "bank_transaction_id": bank_resp.json()["id"],
                "match_status": "PERFECT"
            }
        )
        
        assert response.status_code == 409
        assert "already reconciled" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_reconciliation_unreconciles_bank(self, async_client: AsyncClient, test_company):
        ticket_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "DELETE RECON",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        bank_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-100.00",
                "description": "BANK DELETE"
            }
        )
        
        recon_resp = await async_client.post(
            "/api/v1/reconciliations/",
            json={
                "ticket_id": ticket_resp.json()["id"],
                "bank_transaction_id": bank_resp.json()["id"],
                "match_status": "PERFECT"
            }
        )
        recon_id = recon_resp.json()["id"]
        
        # Eliminar conciliación
        response = await async_client.delete(f"/api/v1/reconciliations/{recon_id}")
        assert response.status_code == 204
        
        # Verificar banco ya no reconciliado
        get_bank = await async_client.get(f"/api/v1/bank-transactions/{bank_resp.json()['id']}")
        assert get_bank.json()["is_reconciled"] is False

    @pytest.mark.asyncio
    async def test_export_excel(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(test_company.id)}
        )
        
        response = await async_client.get(
            f"/api/v1/reconciliations/export/excel?company_id={test_company.id}"
        )
        
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert "attachment; filename=conciliacion.xlsx" in response.headers["content-disposition"]
        
        # Verificar contenido
        df = pd.read_excel(BytesIO(response.content))
        assert len(df) == 3
        assert "Estatus Conciliacion" in df.columns

    @pytest.mark.asyncio
    async def test_export_contpaqi(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(test_company.id)}
        )
        
        response = await async_client.get(
            f"/api/v1/reconciliations/export/contpaqi?company_id={test_company.id}"
        )
        
        assert response.status_code == 200
        assert "contpaqi_polizas.xlsx" in response.headers["content-disposition"]
        
        df = pd.read_excel(BytesIO(response.content))
        assert len(df) == 3
        assert "RFC" in df.columns
        assert "Nombre" in df.columns
        assert "Total" in df.columns
        assert "TipoComprobante" in df.columns

    @pytest.mark.asyncio
    async def test_export_generic_custom_columns(self, async_client: AsyncClient, test_company, setup_reconciliation_data):
        await async_client.post(
            "/api/v1/reconciliations/run",
            json={"company_id": str(test_company.id)}
        )
        
        response = await async_client.get(
            f"/api/v1/reconciliations/export/generic?company_id={test_company.id}&columns=Fecha,Proveedor,Total,Estatus Conciliacion"
        )
        
        assert response.status_code == 200
        df = pd.read_excel(BytesIO(response.content))
        assert list(df.columns) == ["Fecha", "Proveedor", "Total", "Estatus Conciliacion"]

    @pytest.mark.asyncio
    async def test_accounting_mapping_crud(self, async_client: AsyncClient, test_company):
        # Crear mapping
        response = await async_client.post(
            "/api/v1/reconciliations/mappings",
            json={
                "company_id": str(test_company.id),
                "software_name": "CONTPAQI_TEST",
                "column_mappings": {"Fecha": "Fecha", "Concepto": "Concepto", "Total": "Total"}
            }
        )
        
        assert response.status_code == 201
        mapping = response.json()
        assert mapping["software_name"] == "CONTPAQI_TEST"
        mapping_id = mapping["id"]
        
        # Listar
        list_resp = await async_client.get(f"/api/v1/reconciliations/mappings?company_id={test_company.id}")
        # El codigo de estado va explicito y ANTES de mirar el cuerpo. Antes
        # esta linea solo afirmaba len(json) == 1, y el {"detail": [...]} de un
        # 422 tambien cumple: el test pasaba en verde sobre un endpoint roto.
        # Un 422 aqui significa que una ruta literal quedo atrapada detras de
        # un path con placeholder. Ver el comentario de /{reconciliation_id}.
        assert list_resp.status_code == 200, f"/mappings devolvio {list_resp.status_code}"
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["id"] == mapping_id

        # Obtener
        get_resp = await async_client.get(f"/api/v1/reconciliations/mappings/{mapping_id}")
        assert get_resp.status_code == 200, f"/mappings/{{id}} devolvio {get_resp.status_code}"
        assert get_resp.json()["id"] == mapping_id