"""Un token con membresia no puede leer ni tocar resources de otra empresa.

Los tests de `test_permisos_http.py` comprueban la autoridad sobre la ACCION
(quien puede aprobar, exportar, administrar). Aqui se comprueba la autoridad
sobre el RECURSO: con un id real de la empresa B, un token que opera en A debe
recibir 404. Un token que solo puede "leer" no deberia poder leer el disco
entero por id.

Cada caso lleva su contraprueba: el MISMO endpoint con un recurso de la empresa
propia tiene quedar 200. Sin eso, un 404 por una ruta mal escrita daria verde
sin probar el filtro.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RolEmpresa
from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.models.usuario_empresa import UsuarioEmpresaModel


async def _miembro(
    db_session: AsyncSession,
    empresa: CompanyModel,
    usuario: UserModel,
    rol: RolEmpresa = RolEmpresa.ADMIN,
) -> None:
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario.id,
            company_id=empresa.id,
            rol=rol,
            activo=True,
        )
    )
    await db_session.commit()


@pytest.fixture
async def otra_empresa(db_session: AsyncSession) -> CompanyModel:
    empresa = CompanyModel(
        name="Empresa ajena", tax_id=f"AJENA{uuid4().hex[:8].upper()}"
    )
    db_session.add(empresa)
    await db_session.commit()
    await db_session.refresh(empresa)
    return empresa


@pytest.fixture
async def recursos(
    db_session: AsyncSession,
    test_company: CompanyModel,
    otra_empresa: CompanyModel,
) -> dict[str, dict[str, UUID]]:
    """Un juego completo de filas por empresa, con los mismos tipos."""

    def _ticket(empresa: CompanyModel) -> TicketModel:
        return TicketModel(
            company_id=empresa.id,
            provider_name=f"Proveedor de {empresa.name}",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 10, 1),
            # Las dos colas y el reporte leen estas columnas: marcarlas aqui
            # hace que el test pueda demostrar que no filtran la empresa ajena.
            confidence_source="ocr",
            # El constraint `ck_tickets_spot_check_only_auto` solo permite
            # muestrear lo que el automatismo aprobo solo.
            extraction_status="AUTO_APROBADO",
            spot_check_status="PENDIENTE",
        )

    def _movimiento(empresa: CompanyModel) -> BankTransactionModel:
        return BankTransactionModel(
            company_id=empresa.id,
            transaction_date=date(2026, 10, 1),
            amount=Decimal("-100.00"),
            description=f"Cargo de {empresa.name}",
        )

    def _conciliacion(
        ticket: TicketModel, movimiento: BankTransactionModel
    ) -> ReconciliationModel:
        return ReconciliationModel(
            ticket_id=ticket.id,
            bank_transaction_id=movimiento.id,
            match_status="PERFECT",
        )

    def _mapeo(empresa: CompanyModel) -> AccountingMappingModel:
        return AccountingMappingModel(
            company_id=empresa.id,
            software_name=f"Contabilidad de {empresa.name}",
            column_mappings={"Fecha": "fecha"},
        )

    propios: dict[str, UUID] = {}
    ajenos: dict[str, UUID] = {}
    for empresa, destino in ((test_company, propios), (otra_empresa, ajenos)):
        ticket = _ticket(empresa)
        movimiento = _movimiento(empresa)
        db_session.add_all([ticket, movimiento])
        await db_session.flush()

        conciliacion = _conciliacion(ticket, movimiento)
        mapeo = _mapeo(empresa)
        db_session.add_all([conciliacion, mapeo])
        await db_session.flush()

        destino.update(
            ticket=ticket.id,
            movimiento=movimiento.id,
            conciliacion=conciliacion.id,
            mapeo=mapeo.id,
        )
    await db_session.commit()
    return {"propios": propios, "ajenos": ajenos}


@pytest.mark.asyncio
async def test_ticket_ajeno_da_404_y_el_propio_200(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    ajeno = await async_client.get(
        f"/api/v1/tickets/{recursos['ajenos']['ticket']}", headers=cabeceras
    )
    assert ajeno.status_code == 404, ajeno.text

    propio = await async_client.get(
        f"/api/v1/tickets/{recursos['propios']['ticket']}", headers=cabeceras
    )
    assert propio.status_code == 200, propio.text
    assert propio.json()["company_id"] == str(test_company.id)


@pytest.mark.asyncio
async def test_movimiento_ajeno_da_404_y_el_propio_200(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    ajeno = await async_client.get(
        f"/api/v1/bank-transactions/{recursos['ajenos']['movimiento']}",
        headers=cabeceras,
    )
    assert ajeno.status_code == 404, ajeno.text

    propio = await async_client.get(
        f"/api/v1/bank-transactions/{recursos['propios']['movimiento']}",
        headers=cabeceras,
    )
    assert propio.status_code == 200, propio.text


@pytest.mark.asyncio
async def test_conciliacion_ajena_da_404_y_la_propia_200(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    ajena = await async_client.get(
        f"/api/v1/reconciliations/{recursos['ajenos']['conciliacion']}",
        headers=cabeceras,
    )
    assert ajena.status_code == 404, ajena.text

    propia = await async_client.get(
        f"/api/v1/reconciliations/{recursos['propios']['conciliacion']}",
        headers=cabeceras,
    )
    assert propia.status_code == 200, propia.text


@pytest.mark.asyncio
async def test_mapeo_ajeno_da_404_y_el_propio_200(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    ajeno = await async_client.get(
        f"/api/v1/reconciliations/mappings/{recursos['ajenos']['mapeo']}",
        headers=cabeceras,
    )
    assert ajeno.status_code == 404, ajeno.text

    propio = await async_client.get(
        f"/api/v1/reconciliations/mappings/{recursos['propios']['mapeo']}",
        headers=cabeceras,
    )
    assert propio.status_code == 200, propio.text


@pytest.mark.asyncio
async def test_las_listas_no_mezclan_empresas(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    """Sin `?company_id`, la empresa de la membresia es el unico filtro.

    Antes esas listas devolvian las dos empresas: la ausencia de filtro se leia
    como "trae todo", y en una maquina con varias empresas eso filtraba datos
    ajenos al contador que tenia la pantalla abierta.
    """
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}
    propios = recursos["propios"]
    ajenos = recursos["ajenos"]

    for ruta, clave in (
        ("/api/v1/tickets/", "ticket"),
        ("/api/v1/bank-transactions/", "movimiento"),
        ("/api/v1/reconciliations/", "conciliacion"),
        ("/api/v1/reconciliations/mappings", "mapeo"),
    ):
        respuesta = await async_client.get(ruta, headers=cabeceras)
        assert respuesta.status_code == 200, respuesta.text
        ids = {fila["id"] for fila in respuesta.json()}
        assert str(propios[clave]) in ids, (ruta, ids)
        assert str(ajenos[clave]) not in ids, (ruta, ids)


@pytest.mark.asyncio
async def test_las_colas_y_el_reporte_no_usan_la_empresa_ajena(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    """Los conteos del tablero son de la empresa activa, no del disco entero."""
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}
    propio = str(recursos["propios"]["ticket"])
    ajeno = str(recursos["ajenos"]["ticket"])

    cola = await async_client.get(
        "/api/v1/tickets/review-queue",
        headers=cabeceras,
        params={"status": "AUTO_APROBADO"},
    )
    assert cola.status_code == 200, cola.text
    ids_cola = {fila["id"] for fila in cola.json()["tickets"]}
    assert propio in ids_cola
    assert ajeno not in ids_cola

    muestra = await async_client.get("/api/v1/tickets/spot-check", headers=cabeceras)
    assert muestra.status_code == 200, muestra.text
    ids_muestra = {fila["ticket"]["id"] for fila in muestra.json()["tickets"]}
    assert propio in ids_muestra
    assert ajeno not in ids_muestra

    exactitud = await async_client.get("/api/v1/tickets/accuracy", headers=cabeceras)
    assert exactitud.status_code == 200, exactitud.text
    assert str(test_company.id) == exactitud.json()["company_id"]
    pendientes = sum(
        fila["pendientes"]
        for fila in exactitud.json()["por_origen"]
        if fila["origen"] == "ocr"
    )
    # Un pendiente por empresa, no dos: el del ticket ajeno no se presta.
    assert pendientes == 1, exactitud.text


@pytest.mark.asyncio
async def test_clasificar_lote_no_toca_el_ticket_ajeno(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    """Un lote mixto clasifica los propios y deja los ajenos como estaban."""
    await _miembro(db_session, test_company, usuario_de_prueba)
    ids = [recursos["ajenos"]["ticket"], recursos["propios"]["ticket"]]

    respuesta = await async_client.patch(
        "/api/v1/tickets/categoria",
        headers={"X-Company-ID": str(test_company.id)},
        json={"ticket_ids": [str(i) for i in ids], "category": "CLASIFICADO"},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["actualizados"] == 1, respuesta.text

    ajeno = await db_session.get(TicketModel, recursos["ajenos"]["ticket"])
    await db_session.refresh(ajeno)
    assert ajeno.category != "CLASIFICADO"


@pytest.mark.asyncio
async def test_no_se_puede_conciliar_un_ticket_ajeno(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    """Crear tambien mira la empresa: los ids del cuerpo no bastan."""
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    # Un solo id ajeno basta para tumbar la creacion: probar los dos a la vez
    # daria verde si alguien quitara solo uno de los dos filtros.
    base = {
        "ticket_id": str(recursos["propios"]["ticket"]),
        "bank_transaction_id": str(recursos["propios"]["movimiento"]),
        "match_status": "PERFECT",
    }
    for clave in ("ticket_id", "bank_transaction_id"):
        ajena = await async_client.post(
            "/api/v1/reconciliations/", headers=cabeceras, json={**base, clave: str(recursos["ajenos"][
                "ticket" if clave == "ticket_id" else "movimiento"
            ])}
        )
        assert ajena.status_code == 404, (clave, ajena.text)

    propia = await async_client.post(
        "/api/v1/reconciliations/", headers=cabeceras, json=base
    )
    assert propia.status_code == 201, propia.text


@pytest.mark.asyncio
async def test_patch_de_ticket_ajeno_da_404(
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_company: CompanyModel,
    usuario_de_prueba: UserModel,
    recursos: dict[str, dict[str, UUID]],
) -> None:
    """El filtro esta en el lookup, no solo en el GET: tambien cubre escrituras."""
    await _miembro(db_session, test_company, usuario_de_prueba)
    cabeceras = {"X-Company-ID": str(test_company.id)}

    ajeno = await async_client.patch(
        f"/api/v1/tickets/{recursos['ajenos']['ticket']}",
        headers=cabeceras,
        json={"category": "ESCRITO"},
    )
    assert ajeno.status_code == 404, ajeno.text

    propio = await async_client.patch(
        f"/api/v1/tickets/{recursos['propios']['ticket']}",
        headers=cabeceras,
        json={"category": "ESCRITO"},
    )
    assert propio.status_code == 200, propio.text
