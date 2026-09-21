import pytest
from httpx import AsyncClient
from uuid import uuid4


class TestCompaniesAPI:
    @pytest.mark.asyncio
    async def test_create_company(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/v1/companies/",
            json={"name": "Mi Empresa SA", "tax_id": "MES123456789"}
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Mi Empresa SA"
        assert data["tax_id"] == "MES123456789"
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_create_company_duplicate_tax_id_fails(self, async_client: AsyncClient):
        # Crear primera
        await async_client.post("/api/v1/companies/", json={"name": "Empresa 1", "tax_id": "DUP123"})
        
        # Intentar crear segunda con mismo tax_id
        response = await async_client.post(
            "/api/v1/companies/",
            json={"name": "Empresa 2", "tax_id": "DUP123"}
        )
        
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_companies(self, async_client: AsyncClient):
        await async_client.post("/api/v1/companies/", json={"name": "Empresa A", "tax_id": "TAX001"})
        await async_client.post("/api/v1/companies/", json={"name": "Empresa B", "tax_id": "TAX002"})
        
        response = await async_client.get("/api/v1/companies/")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all("id" in c and "name" in c for c in data)

    @pytest.mark.asyncio
    async def test_get_company(self, async_client: AsyncClient):
        create_resp = await async_client.post(
            "/api/v1/companies/", json={"name": "Test Get", "tax_id": "GET123"}
        )
        company_id = create_resp.json()["id"]
        
        response = await async_client.get(f"/api/v1/companies/{company_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == company_id
        assert data["name"] == "Test Get"

    @pytest.mark.asyncio
    async def test_get_company_not_found(self, async_client: AsyncClient):
        response = await async_client.get(f"/api/v1/companies/{uuid4()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_company(self, async_client: AsyncClient):
        create_resp = await async_client.post(
            "/api/v1/companies/", json={"name": "Original", "tax_id": "UPD123"}
        )
        company_id = create_resp.json()["id"]
        
        response = await async_client.patch(
            f"/api/v1/companies/{company_id}",
            json={"name": "Actualizado"}
        )
        
        assert response.status_code == 200
        assert response.json()["name"] == "Actualizado"
        assert response.json()["tax_id"] == "UPD123"  # No cambió

    @pytest.mark.asyncio
    async def test_update_company_tax_id_conflict(self, async_client: AsyncClient):
        await async_client.post("/api/v1/companies/", json={"name": "Empresa 1", "tax_id": "CONF1"})
        create_resp = await async_client.post("/api/v1/companies/", json={"name": "Empresa 2", "tax_id": "CONF2"})
        company_id = create_resp.json()["id"]
        
        response = await async_client.patch(
            f"/api/v1/companies/{company_id}",
            json={"tax_id": "CONF1"}  # Ya existe
        )
        
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_company(self, async_client: AsyncClient):
        create_resp = await async_client.post(
            "/api/v1/companies/", json={"name": "Para Borrar", "tax_id": "DEL123"}
        )
        company_id = create_resp.json()["id"]
        
        response = await async_client.delete(f"/api/v1/companies/{company_id}")
        assert response.status_code == 204
        
        # Verificar que ya no existe
        get_resp = await async_client.get(f"/api/v1/companies/{company_id}")
        assert get_resp.status_code == 404