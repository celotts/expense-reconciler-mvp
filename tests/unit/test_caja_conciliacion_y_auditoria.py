"""Kardex completo, cierre con conteo, cruce bancario y rastro de caja.

Cada defensa nueva tiene un test que muere si se quita: la apertura en el
kardex, la diferencia calculada en servidor, la conciliacion de solo lectura,
la auditoria con firma del token y el aviso de huella fiscal antes de persistir.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.enums import OrigenFondo, RolEmpresa, SourceType, TipoMovimientoCaja
from app.models import Auditoria
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.schemas.caja_chica import MovimientoCajaCreate
from app.services.caja_chica_service import ErrorCaja, registrar_movimiento
from app.services.duplicados_service import (
    buscar_duplicados_fiscales, calcular_hash_duplicado,
)
from app.services.parser_service import TicketExtractionResult
from app.services.scan_service import ResumenArchivo, resolver_datos_de_tickets
from app.services.ticket_persistence import persistir_extraccion


@pytest.fixture(autouse=True)
async def membresia_de_prueba(db_session, test_company, usuario_de_prueba):
    db_session.add(
        UsuarioEmpresaModel(
            user_id=usuario_de_prueba.id,
            company_id=test_company.id,
            rol="admin",
            activo=True,
        )
    )
    await db_session.commit()


async def _crear_caja(cliente, company_id, usuario_id, monto="1000.00"):
    respuesta = await cliente.post(
        "/api/v1/caja-chica/",
        json={
            "company_id": str(company_id),
            "responsable_id": str(usuario_id),
            "nombre": "Caja de conteo",
            "monto_inicial": monto,
            "umbral_alerta": "100.00",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


class TestAperturaEnElKardex:
    async def test_crear_caja_escribe_la_apertura(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        movimientos = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}/movimientos"
        )
        assert [m["tipo"] for m in movimientos.json()] == ["apertura"]
        apertura = movimientos.json()[0]
        assert apertura["monto"] == "1000.00"
        assert apertura["created_by"] == str(usuario_de_prueba.id)
        # El saldo NO se suma dos veces: la apertura es la evidencia del fondo,
        # no una entrada nueva.
        assert caja["saldo_actual"] == "1000.00"

    async def test_apertura_directa_no_es_otra_puerta_para_sumar(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={"tipo": "apertura", "monto": "500.00", "origen_fondo": "efectivo"},
        )
        assert respuesta.status_code == 422
        assert "crear la caja" in respuesta.json()["detail"]

    async def test_la_apertura_de_cero_tambien_queda_registrada(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id, monto="0.00"
        )
        movimientos = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}/movimientos"
        )
        assert [m["tipo"] for m in movimientos.json()] == ["apertura"]
        assert movimientos.json()[0]["monto"] == "0.00"


class TestAuditoriaDeCaja:
    async def test_crear_caja_firma_con_el_token(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        filas = (
            await db_session.execute(
                select(Auditoria).where(Auditoria.accion == "crear_caja")
            )
        ).scalars().all()
        assert len(filas) == 1
        assert filas[0].usuario_id == usuario_de_prueba.id
        assert filas[0].rol == "admin"
        assert filas[0].modulo == "caja_chica"
        assert str(filas[0].registro_afectado) == caja["id"]

    async def test_movimiento_y_cierre_dejan_rastro(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={"tipo": "ajuste", "monto": "20.00"},
        )
        await async_client.post(f"/api/v1/caja-chica/{caja['id']}/en-revision")
        await async_client.post(f"/api/v1/caja-chica/{caja['id']}/cerrar")

        acciones = {
            fila.accion
            for fila in (
                await db_session.execute(select(Auditoria))
            ).scalars().all()
        }
        assert {
            "crear_caja",
            "registrar_movimiento_caja",
            "enviar_caja_a_revision",
            "cerrar_caja",
        } <= acciones


class TestCierreConConteo:
    async def test_diferencia_la_calcula_el_servidor_y_no_bloquea(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await async_client.post(f"/api/v1/caja-chica/{caja['id']}/en-revision")
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/cerrar",
            json={"efectivo_contado": "990.00"},
        )
        assert respuesta.status_code == 200, respuesta.text
        cerrada = respuesta.json()
        assert cerrada["estado"] == "cerrada"
        assert cerrada["efectivo_contado"] == "990.00"
        assert cerrada["diferencia_cierre"] == "-10.00"

        movimientos = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}/movimientos"
        )
        cierres = [
            m for m in movimientos.json() if m["tipo"] == "cierre"
        ]
        assert len(cierres) == 1
        # El cierre no mueve dinero: solo documenta lo contado.
        assert cierres[0]["monto"] == "0.00"
        assert "diferencia -10.00" in cierres[0]["notas"]

    async def test_cerrar_sin_conteo_sigue_funcionando(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await async_client.post(f"/api/v1/caja-chica/{caja['id']}/en-revision")
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/cerrar", json={}
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["efectivo_contado"] is None
        assert respuesta.json()["diferencia_cierre"] is None


class TestConciliacionBancaria:
    async def test_reposicion_se_cruza_con_el_cargo_del_banco(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        cargo = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2026, 11, 20),
            amount=Decimal("-250.50"),
            description="Retiro para caja chica",
            reference="DEP-123",
        )
        db_session.add(cargo)
        await db_session.commit()

        await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "250.50",
                "origen_fondo": "banco",
                "banco_nombre": "Banorte",
                "referencia_bancaria": "DEP-123",
            },
        )
        respuesta = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}/conciliacion-bancaria"
        )
        assert respuesta.status_code == 200, respuesta.text
        datos = respuesta.json()
        assert datos["cruzadas"] == 1
        assert datos["sin_cruzar"] == 0
        assert datos["total_cruzado"] == "250.50"
        fila = datos["reposiciones"][0]
        assert fila["resultado"] == "CRUZADA"
        assert fila["bank_transaction_id"] == str(cargo.id)

        # Solo lectura: la conciliacion de tickets sigue siendo la unica que
        # puede marcar el cargo bancario como reconciliado.
        await db_session.refresh(cargo)
        assert cargo.is_reconciled is False

    async def test_sin_cargo_que_cuadre_se_reporta_no_se_inventa(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "100.00",
                "origen_fondo": "banco",
                "banco_nombre": "Banorte",
            },
        )
        datos = (
            await async_client.get(
                f"/api/v1/caja-chica/{caja['id']}/conciliacion-bancaria"
            )
        ).json()
        assert datos["cruzadas"] == 0
        assert datos["sin_cruzar"] == 1
        assert datos["reposiciones"][0]["bank_transaction_id"] is None
        assert "no tienen cargo" in datos["advertencia"]

    async def test_la_referencia_distingue_dos_cargos_del_mismo_monto(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        """Dos retiros de 250.50 no son el mismo dinero si la referencia difiere."""
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        for referencia in ("DEP-123", "DEP-999"):
            db_session.add(
                BankTransactionModel(
                    company_id=test_company.id,
                    transaction_date=date(2026, 11, 20),
                    amount=Decimal("-250.50"),
                    description=f"Retiro {referencia}",
                    reference=referencia,
                )
            )
        await db_session.commit()
        await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "250.50",
                "origen_fondo": "banco",
                "banco_nombre": "Banorte",
                "referencia_bancaria": "DEP-999",
            },
        )
        datos = (
            await async_client.get(
                f"/api/v1/caja-chica/{caja['id']}/conciliacion-bancaria"
            )
        ).json()
        assert datos["cruzadas"] == 1
        fila = datos["reposiciones"][0]
        assert fila["resultado"] == "CRUZADA"
        assert fila["descripcion_banco"] == "Retiro DEP-999"

    async def test_otra_empresa_no_puede_leer_la_conciliacion(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        otra = CompanyModel(name="Otra empresa", tax_id=f"OTP{uuid4().hex[:7].upper()}")
        db_session.add(otra)
        await db_session.commit()
        db_session.add(
            UsuarioEmpresaModel(
                user_id=usuario_de_prueba.id,
                company_id=otra.id,
                rol="admin",
                activo=True,
            )
        )
        await db_session.commit()

        respuesta = await async_client.get(
            f"/api/v1/caja-chica/{caja['id']}/conciliacion-bancaria",
            headers={"X-Company-ID": str(otra.id)},
        )
        assert respuesta.status_code == 404


class TestCamposBancariosEnEfectivo:
    async def test_referencia_bancaria_en_efectivo_es_422(
        self, async_client, test_company, usuario_de_prueba
    ):
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        respuesta = await async_client.post(
            f"/api/v1/caja-chica/{caja['id']}/movimientos",
            json={
                "tipo": "reposicion",
                "monto": "10.00",
                "origen_fondo": "efectivo",
                "referencia_bancaria": "DEP-123",
            },
        )
        assert respuesta.status_code == 422
        assert "efectivo" in respuesta.text

    async def test_el_servicio_tambien_lo_rechaza_sin_pasar_por_el_schema(
        self, async_client, db_session, test_company, usuario_de_prueba
    ):
        """El schema es la primera capa; el servicio no puede fiarse de ella.

        `model_construct` salta las validaciones de Pydantic a proposito: una
        llamada interna puede construir el schema sin pasar por el request, y
        una capa que solo existe cuando viene el JSON no defiende nada.
        """
        caja = await _crear_caja(
            async_client, test_company.id, usuario_de_prueba.id
        )
        datos = MovimientoCajaCreate.model_construct(
            company_id=test_company.id,
            tipo=TipoMovimientoCaja.REPOSICION,
            monto=Decimal("10.00"),
            origen_fondo=OrigenFondo.EFECTIVO,
            referencia_bancaria="DEP-123",
        )
        with pytest.raises(ErrorCaja) as exc:
            await registrar_movimiento(
                db_session,
                UUID(caja["id"]),
                datos,
                usuario_de_prueba.id,
                company_id=test_company.id,
                rol=RolEmpresa.ADMIN,
            )
        assert exc.value.status_code == 422
        assert "banco" in exc.value.detail


def _resultado_fiscal(folio: str, contenido: bytes) -> TicketExtractionResult:
    return TicketExtractionResult(
        provider_name="ABARROTES DE PRUEBA",
        provider_tax_id="AAA010101AAA",
        identificacion_fiscal="AAA010101AAA",
        folio=folio,
        total_amount=Decimal("123.45"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 11, 21),
        raw_text="ABARROTES DE PRUEBA\nFOLIO " + folio + "\nTOTAL 123.45",
    )


class TestHuellaFiscalAntesDePersistir:
    def test_sin_fecha_no_se_calcula_huella(self):
        """Sin fecha no se afirma identidad: inventarla es peor que no tenerla."""
        assert calcular_hash_duplicado(
            identificacion_fiscal="AAA010101AAA",
            fecha=None,
            folio="F-1",
            total=Decimal("123.45"),
        ) is None

    async def test_otro_rfc_no_es_duplicado(
        self, db_session, test_company
    ):
        await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado_fiscal("F-1234", b"papel con RFC propio"),
            content=b"papel con RFC propio",
            source_type=SourceType.DIRECTORY,
            source_file="rfc-original.pdf",
        )
        await db_session.commit()
        candidatos = await buscar_duplicados_fiscales(
            db_session,
            company_id=test_company.id,
            identificacion_fiscal="BBB010101BBB",
            fecha=date(2026, 11, 21),
            folio="F-1234",
            total=Decimal("123.45"),
        )
        assert candidatos == []

    async def test_otro_folio_no_es_duplicado(
        self, db_session, test_company
    ):
        await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado_fiscal("F-2222", b"papel con folio propio"),
            content=b"papel con folio propio",
            source_type=SourceType.DIRECTORY,
            source_file="folio-original.pdf",
        )
        await db_session.commit()
        candidatos = await buscar_duplicados_fiscales(
            db_session,
            company_id=test_company.id,
            identificacion_fiscal="AAA010101AAA",
            fecha=date(2026, 11, 21),
            folio="F-3333",
            total=Decimal("123.45"),
        )
        assert candidatos == []

    async def test_el_segundo_papel_llega_con_aviso(
        self, db_session, test_company
    ):
        primero = await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado_fiscal("F-4891", b"primer papel"),
            content=b"primer papel",
            source_type=SourceType.DIRECTORY,
            source_file="uno.pdf",
        )
        await db_session.commit()
        assert primero.advertencia_duplicado_fiscal is None

        segundo = await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado_fiscal("F-4891", b"otro papel"),
            content=b"otro papel",
            source_type=SourceType.DIRECTORY,
            source_file="dos.pdf",
        )
        await db_session.commit()
        assert segundo.advertencia_duplicado_fiscal is not None
        assert str(primero.id) in segundo.advertencia_duplicado_fiscal
        # El aviso NO bloquea: el comprobante se guarda para que alguien lo mire.
        assert segundo.id is not None
        assert (
            await db_session.execute(
                select(TicketModel).where(TicketModel.id == segundo.id)
            )
        ).scalar_one_or_none() is not None

    async def test_el_aviso_viaja_en_los_datos_del_escaneo(
        self, db_session, test_company
    ):
        """El aviso nace en la fila y llega al JSON del escaneo sin copias."""
        for nombre in ("primero.pdf", "reenvio.pdf"):
            await persistir_extraccion(
                db_session,
                company_id=test_company.id,
                extracted=_resultado_fiscal("F-9898", nombre.encode()),
                content=nombre.encode(),
                source_type=SourceType.DIRECTORY,
                source_file=nombre,
            )
            await db_session.commit()
        reenvio = (
            await db_session.execute(
                select(TicketModel).where(TicketModel.source_file == "reenvio.pdf")
            )
        ).scalar_one()
        assert reenvio.advertencia_duplicado_fiscal is not None

        detalle = ResumenArchivo(
            relative_path="reenvio.pdf", ticket_id=reenvio.id
        )
        await resolver_datos_de_tickets(db_session, [detalle])
        assert detalle.datos is not None
        assert detalle.datos["advertencia_duplicado_fiscal"] == (
            reenvio.advertencia_duplicado_fiscal
        )

    async def test_compania_distinta_no_es_duplicado(
        self, db_session, test_company
    ):
        otra = CompanyModel(name="Otra empresa fiscal", tax_id=f"OTR{uuid4().hex[:7].upper()}")
        db_session.add(otra)
        await db_session.commit()
        await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado_fiscal("F-7777", b"papel original"),
            content=b"papel original",
            source_type=SourceType.DIRECTORY,
            source_file="original.pdf",
        )
        await db_session.commit()

        ajeno = await persistir_extraccion(
            db_session,
            company_id=otra.id,
            extracted=_resultado_fiscal("F-7777", b"mismo papel, otra empresa"),
            content=b"mismo papel, otra empresa",
            source_type=SourceType.DIRECTORY,
            source_file="ajeno.pdf",
        )
        await db_session.commit()
        assert ajeno.advertencia_duplicado_fiscal is None
