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
    async def test_la_empresa_creada_se_puede_seleccionar(
        self, async_client: AsyncClient
    ):
        """Crear una empresa da acceso a ella; si no, no se puede seleccionar.

        `/auth/empresas` es la unica fuente del selector y solo lista empresas
        con membresia. Mantenimiento no puede arreglarlo: asigna roles dentro
        de la empresa activa, que se valida contra esa misma lista. Sin esta
        prueba, una empresa recien creada es invisible e inusable y el front
        parece estar omitiendo la segunda empresa.
        """
        creada = await async_client.post(
            "/api/v1/companies/",
            json={"name": "Segunda empresa", "tax_id": "SEG123456789"},
        )
        assert creada.status_code == 201
        mia = (await async_client.get("/api/v1/auth/empresas")).json()
        empresa = next(m for m in mia if m["company_id"] == creada.json()["id"])
        assert empresa["rol"] == "owner"
        assert empresa["estado"] == "activo"

        # Y se puede trabajar en ella desde el primer momento: la cabecera que
        # manda el front con la eleccion pasa la validacion del servidor.
        respuesta = await async_client.get(
            "/api/v1/dashboard/",
            headers={"X-Company-ID": creada.json()["id"]},
        )
        assert respuesta.status_code == 200

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
        # El mensaje va en espanol y con tilde: es lo unico que ve el
        # usuario de un 409, y decia "Company with this tax_id already exists"
        # —en ingles, y con el nombre del campo (`tax_id`), que en la
        # pantalla no existe. Ver `docs/known-issues.md` §29.
        assert response.json()["detail"] == "Ya hay una empresa con ese RFC"

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
        """`DELETE` ya no borra en cascada: es alias del soft delete.

        El 204 se conserva por contrato, pero la fila sigue ahi con
        `estado='eliminado'` para que la evidencia no desaparezca sin rastro.
        """
        create_resp = await async_client.post(
            "/api/v1/companies/", json={"name": "Para Borrar", "tax_id": "DEL123"}
        )
        company_id = create_resp.json()["id"]
        
        response = await async_client.delete(f"/api/v1/companies/{company_id}")
        assert response.status_code == 204
        
        # La fila sigue, pero deja de ofrecerse como empresa de trabajo.
        get_resp = await async_client.get(f"/api/v1/companies/{company_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["estado"] == "eliminado"
        list_resp = await async_client.get("/api/v1/companies/")
        assert company_id not in [c["id"] for c in list_resp.json()]