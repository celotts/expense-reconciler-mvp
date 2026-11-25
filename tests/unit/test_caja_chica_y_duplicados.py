"""Caja chica, movimientos y duplicados por huella fiscal.

Los tests trabajan sobre la API, no sobre el servicio a secas. El motivo es el
mismo que el resto del proyecto: una regla que solo vive en el servicio puede
estar bien mientras el router la llama mal, y el cliente ve otro resultado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.usuario_empresa import UsuarioEmpresaModel


@pytest.fixture(autouse=True)
async def membresia_de_prueba(db_session, test_company, usuario_de_prueba):
    """La cuenta con la que corre la suite opera en la empresa de la suite.

    Sin esta fila los endpoints de caja devolverian 403 y los tests existentes
    probarian la puerta nueva en vez de la regla que estan probando. Se pone
    aqui, en el modulo que toca caja y duplicados, para no meter una empresa en
    las 1800 pruebas que no tienen nada que ver con la empresa activa.
    """
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario_de_prueba.id,
            company_id=test_company.id,
            rol="admin",
            activo=True,
        )
    )
    await db_session.commit()


async def _crear_caja(cliente, company_id, usuario_id, **extra):
    body = {
        "company_id": str(company_id),
        "responsable_id": str(usuario_id),
        "nombre": "Caja de pruebas",
        "monto_inicial": "1000.00",
        "umbral_alerta": "200.00",
    }
    body.update(extra)
    respuesta = await cliente.post("/api/v1/caja-chica/", json=body)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


async def _crear_ticket(
    db_session,
    company_id,
    *,
    total="100.00",
    folio="F-1",
    rfc="AAA010101AAA",
    fecha=date(2026, 11, 20),
    estado="CONCILIADO",
):
    ticket = TicketModel(
        company_id=company_id,
        provider_name="Comercio de prueba",
        provider_tax_id=rfc,
        identificacion_fiscal=rfc,
        folio=folio,
        total_amount=Decimal(total),
        tax_amount=Decimal("0.00"),
        expense_date=fecha,
        extraction_status="AUTO_APROBADO",
        estado_conciliacion=estado,
        created_at=None,
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestEsquemas:
    def test_banco_sin_nombre_se_rechaza(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="banco_nombre"):
            MovimientoCajaCreate(
                tipo="reposicion",
                monto=Decimal("100.00"),
                origen_fondo="banco",
            )

    def test_efectivo_no_lleva_datos_de_banco(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="efectivo"):
            MovimientoCajaCreate(
                tipo="reposicion",
                monto=Decimal("100.00"),
                origen_fondo="efectivo",
                banco_nombre="Banorte",
            )

    def test_reposicion_exige_origen(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="fondo"):
            MovimientoCajaCreate(tipo="reposicion", monto=Decimal("100.00"))

    def test_gasto_ticket_exige_ticket(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="ticket_id"):
            MovimientoCajaCreate(
                tipo="gasto_ticket", monto=Decimal("100.00"), origen_fondo="efectivo"
            )

    def test_cierre_exige_hora(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="closed_at"):
            MovimientoCajaCreate(tipo="cierre", monto=Decimal("100.00"))

    def test_solo_ajuste_puede_restar(self):
        from app.schemas.caja_chica import MovimientoCajaCreate

        with pytest.raises(ValidationError, match="ajuste"):
            MovimientoCajaCreate(
                tipo="reposicion",
                monto=Decimal("100.00"),
                origen_fondo="efectivo",
                resta=True,
            )

    def test_dinero_se_redondea_con_half_up(self):
        from app.schemas.caja_chica import CajaChicaCreate

        caja = CajaChicaCreate(
            company_id="00000000-0000-0000-0000-000000000001",
            responsable_id="00000000-0000-0000-0000-000000000002",
            nombre="Caja",
            monto_inicial="10.005",
            umbral_alerta="0.00",
        )
        assert caja.monto_inicial == Decimal("10.01")


class TestCajaChicaApi:
    async def test_alta_nace_con_saldo_inicial_y_bandera_de_umbral(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id, umbral_alerta="1200.00"
        )
        assert caja["saldo_actual"] == "1000.00"
        assert caja["estado"] == "abierta"
        assert caja["saldo_bajo_umbral"] is True

    async def test_reposicion_desde_banco_actualiza_saldo(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "250.50",
                "origen_fondo": "banco",
                "banco_nombre": "Banorte",
                "referencia_bancaria": "DEP-123",
            },
        )
        assert respuesta.status_code == 201, respuesta.text
        movimiento = respuesta.json()
        assert movimiento["banco_nombre"] == "Banorte"
        assert movimiento["referencia_bancaria"] == "DEP-123"

        detalle = await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        assert detalle.json()["saldo_actual"] == "1250.50"
        assert len(detalle.json()["movimientos"]) == 1

    async def test_reposicion_banco_sin_nombre_es_422(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "10.00",
                "origen_fondo": "banco",
            },
        )
        assert respuesta.status_code == 422

    async def test_gasto_ticket_descuenta_y_vincula(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        ticket = await _crear_ticket(db_session, test_company.id)
        caja = await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "gasto_ticket",
                "monto": "40.25",
                "ticket_id": str(ticket.id),
            },
        )
        assert respuesta.status_code == 201, respuesta.text

        await db_session.refresh(ticket)
        assert ticket.caja_chica_id is not None
        detalle = await async_client.get(f"/api/v1/caja-chica/{caja['id']}")
        assert detalle.json()["saldo_actual"] == "959.75"

    async def test_gasto_sin_saldo_no_escribe_movimiento(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        ticket = await _crear_ticket(db_session, test_company.id)
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id, monto_inicial="10.00"
        )
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "gasto_ticket",
                "monto": "20.00",
                "ticket_id": str(ticket.id),
            },
        )
        assert respuesta.status_code == 409
        assert "hay 10.00" in respuesta.json()["detail"]

        movimientos = await async_client.get(f"/api/v1/caja-chica/{caja['id']}/movimientos")
        assert movimientos.json() == []

    async def test_empresa_del_movimiento_no_puede_ser_otra(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        from uuid import uuid4

        caja = await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "company_id": str(uuid4()),
                "tipo": "reposicion",
                "monto": "10.00",
                "origen_fondo": "efectivo",
            },
        )
        assert respuesta.status_code == 409

    async def test_cerrar_exige_revision_y_deja_hora(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(async_client, test_company.id, usuario_de_prueba.id)

        premature = await async_client.post(f"/api/v1/caja-chica/{caja['id']}/cerrar", json={})
        assert premature.status_code == 409

        revision = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/en-revision"
        )
        assert revision.status_code == 200
        assert revision.json()["estado"] == "en_revision"

        cerrada = await async_client.post(f"/api/v1/caja-chica/{caja['id']}/cerrar", json={})
        assert cerrada.status_code == 200
        assert cerrada.json()["estado"] == "cerrada"
        assert cerrada.json()["closed_at"] is not None

        movimiento = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={"tipo": "reposicion", "monto": "1.00", "origen_fondo": "efectivo"},
        )
        assert movimiento.status_code == 409


class TestDuplicadosFiscales:
    async def test_busca_por_campos_aunque_no_haya_hash(
        self, async_client, db_session, test_company
    ):
        await _crear_ticket(db_session, test_company.id, total="279.93", folio="F-9")
        respuesta = await async_client.post(
            "/api/v1/duplicados/buscar",
            json={
                "company_id": str(test_company.id),
                "provider_tax_id": "aaa010101aaa",
                "folio": "f-9",
                "expense_date": "2026-11-20",
                "total_amount": "279.93",
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["es_duplicado"] is True
        assert cuerpo["candidatos"][0]["folio"] == "F-9"
        assert cuerpo["hash_duplicado"] is not None

    async def test_no_confunde_otra_empresa(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        """Otra empresa real, con membresia y header: el ticket no es suyo.

        Antes se pasaba un UUID inventado en el cuerpo y la busqueda no encontraba
        nada. Eso ya no es una prueba de aislamiento: el router rechaza la empresa
        ajena. Para seguir probando el aislamiento hay que entrar COMO esa otra
        empresa, que es justo el escenario que la defensa nueva tiene que cubrir.
        """
        otra = CompanyModel(
            name="Otra empresa", tax_id=f"OTRA{uuid4().hex[:9].upper()}"
        )
        db_session.add(otra)
        # El id lo genera Python al hacer flush: antes de eso `otra.id` es None
        # y la membresia se escribe con company_id NULL.
        await db_session.flush()
        db_session.add(
            UsuarioEmpresaModel(
                user_id=usuario_de_prueba.id,
                company_id=otra.id,
                rol="admin",
                activo=True,
            )
        )
        await db_session.commit()
        await db_session.refresh(otra)

        await _crear_ticket(db_session, test_company.id)
        respuesta = await async_client.post(
            "/api/v1/duplicados/buscar",
            headers={"X-Company-ID": str(otra.id)},
            json={
                "company_id": str(otra.id),
                "provider_tax_id": "AAA010101AAA",
                "folio": "F-1",
                "expense_date": "2026-11-20",
                "total_amount": "100.00",
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["es_duplicado"] is False

    async def test_resumen_cuenta_un_solo_conciliado_por_grupo(
        self, async_client, db_session, test_company
    ):
        await _crear_ticket(db_session, test_company.id, total="50.00", folio="DUP")
        await _crear_ticket(db_session, test_company.id, total="50.00", folio="DUP")
        await _crear_ticket(db_session, test_company.id, total="70.00", folio="OTRO")
        await _crear_ticket(
            db_session, test_company.id, total="30.00", folio="PEND", estado="PENDIENTE"
        )

        respuesta = await async_client.get(
            "/api/v1/duplicados/resumen", params={"company_id": str(test_company.id)}
        )
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        # El grupo repetido cuenta 50 una vez; el ticket unico conciliado, 70.
        assert cuerpo["total_conciliado_sin_duplicados"] == "120.00"
        assert len(cuerpo["grupos"]) == 1
        assert len(cuerpo["grupos"][0]["tickets"]) == 2
        assert cuerpo["advertencia"] is not None
