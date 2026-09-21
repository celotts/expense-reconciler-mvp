import pytest
from httpx import AsyncClient
from uuid import uuid4
from decimal import Decimal
from datetime import date


class TestBankTransactionsAPI:
    @pytest.mark.asyncio
    async def test_create_bank_transaction_manual(self, async_client: AsyncClient, test_company):
        response = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-125.50",
                "description": "PAGO WALMART",
                "reference": "REF001"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["amount"] == "-125.50"
        assert data["description"] == "PAGO WALMART"
        assert data["is_reconciled"] is False

    @pytest.mark.asyncio
    async def test_import_csv_preview(self, async_client: AsyncClient, sample_bank_csv_content):
        files = {"file": ("bank.csv", sample_bank_csv_content, "text/csv")}
        
        response = await async_client.post(
            "/api/v1/bank-transactions/import-csv",
            files=files,
            data={
                "date_column": "fecha",
                "amount_column": "importe",
                "description_column": "concepto",
                "reference_column": "referencia",
                "date_format": "%d/%m/%Y",
                "decimal_separator": ".",
                "thousands_separator": ","
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 4
        assert all("transaction_date" in tx for tx in data)
        assert all("amount" in tx for tx in data)
        assert all("description" in tx for tx in data)

    @pytest.mark.asyncio
    async def test_import_csv_and_create(self, async_client: AsyncClient, test_company, sample_bank_csv_content):
        files = {"file": ("bank.csv", sample_bank_csv_content, "text/csv")}
        
        response = await async_client.post(
            "/api/v1/bank-transactions/import-csv-and-create",
            files=files,
            data={
                "company_id": str(test_company.id),
                "date_column": "fecha",
                "amount_column": "importe",
                "description_column": "concepto",
                "reference_column": "referencia",
                "date_format": "%d/%m/%Y",
                "decimal_separator": ".",
                "thousands_separator": ","
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert len(data) == 4
        assert all(tx["company_id"] == str(test_company.id) for tx in data)
        assert all(tx["is_reconciled"] is False for tx in data)

    @pytest.mark.asyncio
    async def test_import_csv_invalid_format_fails(self, async_client: AsyncClient):
        invalid_csv = b"""col1,col2
valor1,valor2
"""
        files = {"file": ("bad.csv", invalid_csv, "text/csv")}
        
        response = await async_client.post(
            "/api/v1/bank-transactions/import-csv",
            files=files,
            data={
                "date_column": "fecha",
                "amount_column": "importe",
                "description_column": "concepto"
            }
        )
        
        assert response.status_code == 400
        assert "Missing required columns" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_bank_transactions(self, async_client: AsyncClient, test_company):
        # Crear algunos movimientos
        for i in range(3):
            await async_client.post(
                "/api/v1/bank-transactions/",
                json={
                    "company_id": str(test_company.id),
                    "transaction_date": "2025-01-15",
                    "amount": f"-{100 + i * 10}.00",
                    "description": f"MOVIMIENTO {i}"
                }
            )
        
        response = await async_client.get(f"/api/v1/bank-transactions/?company_id={test_company.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_get_bank_transaction(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-200.00",
                "description": "GET TRANSACTION"
            }
        )
        tx_id = create_resp.json()["id"]
        
        response = await async_client.get(f"/api/v1/bank-transactions/{tx_id}")
        
        assert response.status_code == 200
        assert response.json()["description"] == "GET TRANSACTION"

    @pytest.mark.asyncio
    async def test_update_bank_transaction(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-100.00",
                "description": "ORIGINAL"
            }
        )
        tx_id = create_resp.json()["id"]
        
        response = await async_client.patch(
            f"/api/v1/bank-transactions/{tx_id}",
            json={"description": "ACTUALIZADO", "is_reconciled": True}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "ACTUALIZADO"
        assert data["is_reconciled"] is True

    @pytest.mark.asyncio
    async def test_delete_bank_transaction(self, async_client: AsyncClient, test_company):
        create_resp = await async_client.post(
            "/api/v1/bank-transactions/",
            json={
                "company_id": str(test_company.id),
                "transaction_date": "2025-01-15",
                "amount": "-50.00",
                "description": "DELETE ME"
            }
        )
        tx_id = create_resp.json()["id"]
        
        response = await async_client.delete(f"/api/v1/bank-transactions/{tx_id}")
        assert response.status_code == 204
        
        get_resp = await async_client.get(f"/api/v1/bank-transactions/{tx_id}")
        assert get_resp.status_code == 404