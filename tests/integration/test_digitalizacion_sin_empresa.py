"""Una instalacion sin empresa no digitaliza: ni por carpeta ni por lote.

MEDIDO: con `companies` vacia, `POST /scan/` leia la carpeta, devolvia 200 y no
creaba tickets. El operador veia "todo bien" y el comprobante se quedaba sin
dueño ni rastro. La guardia global es el unico sitio que puede cerrar las
cinco puertas a la vez; escrita en cada endpoint, la sexta nacería abierta.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.core.enums import EstadoEmpresa
from app.models.company import CompanyModel


@pytest.fixture
def carpeta_vacia(tmp_path, monkeypatch):
    destino = tmp_path / "tickets"
    destino.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    return destino


async def _escanear(client):
    return await client.post("/api/v1/scan/", json={})


async def _lote(client):
    return await client.post(
        "/api/v1/expenses/upload-batch",
        data={"company_id": str(uuid4())},
        files={"files": ("ticket.pdf", b"%PDF-1.4\n", "application/pdf")},
    )


async def _directorio(client):
    return await client.post(
        "/api/v1/expenses/digitalizar-directorio",
        data={"company_id": str(uuid4())},
    )


async def _reprocesar(client):
    return await client.post(f"/api/v1/scan/files/{uuid4()}/reprocess", json={})


async def _crear_ticket(client):
    return await client.post(
        "/api/v1/tickets/extract-and-create",
        data={"company_id": str(uuid4())},
        files={"file": ("ticket.pdf", b"%PDF-1.4\n", "application/pdf")},
    )


class TestSinEmpresaNoSeDigitaliza:

    @pytest.mark.parametrize(
        "puerta",
        ["escanear", "lote", "directorio", "reprocesar", "crear_ticket"],
    )
    async def test_sin_ninguna_empresa_las_cinco_puertas_paran(
        self, async_client, carpeta_vacia, puerta
    ):
        respuesta = await {
            "escanear": _escanear,
            "lote": _lote,
            "directorio": _directorio,
            "reprocesar": _reprocesar,
            "crear_ticket": _crear_ticket,
        }[puerta](async_client)

        assert respuesta.status_code == 403, respuesta.text
        assert "empresa" in respuesta.json()["detail"]

    async def test_solo_empresas_anuladas_tampoco_permiten_escanear(
        self, async_client, db_session, carpeta_vacia
    ):
        empresa = CompanyModel(name="Anulada", tax_id=f"BAJA{uuid4().hex[:8].upper()}")
        db_session.add(empresa)
        await db_session.commit()
        await db_session.execute(
            update(CompanyModel)
            .where(CompanyModel.id == empresa.id)
            .values(estado=EstadoEmpresa.INACTIVO.value)
        )
        await db_session.commit()

        respuesta = await _escanear(async_client)

        assert respuesta.status_code == 403, respuesta.text
        assert "empresa" in respuesta.json()["detail"]

    async def test_con_una_empresa_activa_el_escaneo_sigue_funcionando(
        self, async_client, test_company, carpeta_vacia
    ):
        """Contraprueba: el cerrojo no es un escaneo roto por otra razon."""
        respuesta = await async_client.post(
            "/api/v1/scan/", json={"company_id": str(test_company.id)}
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["archivos_vistos"] == 0
