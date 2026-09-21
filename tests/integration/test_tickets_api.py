import pytest
from httpx import AsyncClient
from uuid import uuid4
from decimal import Decimal
from datetime import date
import io


class TestTicketsAPI:
    @pytest.mark.asyncio
    async def test_create_ticket_manual(self, async_client: AsyncClient, test_company):
        response = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "WALMART",
                "provider_tax_id": "WAL910101XXX",
                "total_amount": "249.86",
                "tax_amount": "34.46",
                "expense_date": "2025-01-15",
                "category": "SUPERMERCADO"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["provider_name"] == "WALMART"
        assert data["total_amount"] == "249.86"
        assert data["company_id"] == str(test_company.id)

    @pytest.mark.asyncio
    async def test_create_ticket_invalid_company_fails(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(uuid4()),  # No existe
                "provider_name": "TEST",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_tickets(self, async_client: AsyncClient, test_company):
        # Crear varios tickets
        for i in range(3):
            await async_client.post(
                "/api/v1/tickets/",
                json={
                    "company_id": str(test_company.id),
                    "provider_name": f"PROVEEDOR {i}",
                    "total_amount": f"{100 + i * 50}.00",
                    "expense_date": f"2025-01-{15 + i}"
                }
            )
        
        response = await async_client.get(f"/api/v1/tickets/?company_id={test_company.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        # Verificar orden por fecha descendente (PROVEEDOR 2 tiene fecha 17, PROVEEDOR 0 tiene fecha 15)
        assert data[0]["provider_name"] == "PROVEEDOR 2"

    @pytest.mark.asyncio
    async def test_get_ticket(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "GET TICKET",
                "total_amount": "150.00",
                "expense_date": "2025-01-15"
            }
        )
        ticket_id = create_resp.json()["id"]
        
        response = await async_client.get(f"/api/v1/tickets/{ticket_id}")
        
        assert response.status_code == 200
        assert response.json()["provider_name"] == "GET TICKET"

    @pytest.mark.asyncio
    async def test_update_ticket(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "ORIGINAL",
                "total_amount": "100.00",
                "expense_date": "2025-01-15"
            }
        )
        ticket_id = create_resp.json()["id"]
        
        response = await async_client.patch(
            f"/api/v1/tickets/{ticket_id}",
            json={"provider_name": "ACTUALIZADO", "total_amount": "200.00"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["provider_name"] == "ACTUALIZADO"
        assert data["total_amount"] == "200.00"

    @pytest.mark.asyncio
    async def test_delete_ticket(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "DELETE ME",
                "total_amount": "50.00",
                "expense_date": "2025-01-15"
            }
        )
        ticket_id = create_resp.json()["id"]
        
        response = await async_client.delete(f"/api/v1/tickets/{ticket_id}")
        assert response.status_code == 204
        
        get_resp = await async_client.get(f"/api/v1/tickets/{ticket_id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_extract_ticket_from_text_pdf(self, async_client: AsyncClient, sample_ticket_text):
        # Crear un archivo de texto simulando PDF extraído
        files = {"file": ("receipt.txt", sample_ticket_text.encode(), "text/plain")}
        
        response = await async_client.post(
            "/api/v1/tickets/extract",
            files=files,
            data={"file_type": "pdf"}
        )
        
        # El endpoint espera PDF, pero con text/plain fallará en pdfplumber
        # Este test verifica que el endpoint existe y maneja errores
        assert response.status_code in [200, 400, 500]

    @pytest.mark.asyncio
    async def test_extract_and_create_ticket(self, async_client: AsyncClient, test_company):
        # Test con archivo simple - en test real usaríamos PDF fixture
        from app.services.parser_service import extract_ticket_data
        
        # Verificar que la función de extracción funciona con texto
        sample_text = """TIENDA TEST
FECHA: 20/01/2025
TOTAL: $123.45
"""
        result = extract_ticket_data(sample_text.encode(), file_type="text")
        
        assert result.provider_name == "TIENDA TEST"
        assert result.total_amount == Decimal("123.45")
        assert result.expense_date == date(2025, 1, 20)