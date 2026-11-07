"""Los lotes de digitalizacion: folio, cierre, reapertura y complementario.

Estos tests usan los fixtures de `tests/conftest.py` (`async_client`,
`db_session`, `test_company`, `usuario_de_prueba`): la base es SQLite en
memoria creada desde los modelos, y el cliente entra como un usuario ya
autenticado, asi que lo que se prueba aqui es el lote, no el login.

El escenario de fondo es el set de prueba real: 7 HEIC escaneados en octubre
de 2026 (Soriana $65.00 x2, Tony $23.20, Cremeria $80.50, Bodega $234.00 x2,
Del Sol $279.93). Los duplicados de ese set son los que el detalle del lote
tiene que contar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models.auditoria import Auditoria
from app.models.lote import es_extemporaneo, periodo_de_fecha
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel


def _crear_lote_payload(company_id, **cambios):
    """El cuerpo minimo de un lote de octubre de 2026, con cambios encima."""
    payload = {
        "company_id": str(company_id),
        "nombre": "Lote octubre 2026",
        "ejercicio": 2026,
        "mes": 10,
    }
    payload.update(cambios)
    return payload


async def _crear_lote(async_client, company_id, **cambios):
    respuesta = await async_client.post("/lotes", json=_crear_lote_payload(company_id, **cambios))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


class TestPeriodoDeFecha:
    def test_mensual_es_el_mes_natural(self):
        assert periodo_de_fecha(date(2026, 10, 6)) == (
            2026,
            10,
            date(2026, 10, 1),
            date(2026, 10, 31),
        )

    def test_quincenal_parte_el_mes_en_dos(self):
        assert periodo_de_fecha(date(2026, 10, 6), "quincenal") == (
            2026,
            10,
            date(2026, 10, 1),
            date(2026, 10, 15),
        )
        assert periodo_de_fecha(date(2026, 10, 20), "quincenal") == (
            2026,
            10,
            date(2026, 10, 16),
            date(2026, 10, 31),
        )

    def test_semanal_es_de_lunes_a_domingo_y_pertenece_al_mes_del_lunes(self):
        # 2026-10-06 es martes: la semana es del lunes 05 al domingo 11.
        assert periodo_de_fecha(date(2026, 10, 6), "semanal") == (
            2026,
            10,
            date(2026, 10, 5),
            date(2026, 10, 11),
        )
        # 2026-11-01 es domingo: su semana empezo el lunes 26 de octubre, asi
        # que pertenece a octubre, no a noviembre.
        assert periodo_de_fecha(date(2026, 11, 1), "semanal") == (
            2026,
            10,
            date(2026, 10, 26),
            date(2026, 11, 1),
        )

    def test_tipo_desconocido_no_inventa_un_periodo(self):
        with pytest.raises(ValueError):
            periodo_de_fecha(date(2026, 10, 6), "bimestral")  # type: ignore[arg-type]


class TestExtemporaneo:
    def test_ticket_de_octubre_escaneado_en_noviembre_es_extemporaneo(self):
        # El caso del set de prueba: el papel es de octubre y llego en
        # noviembre. Pertenece a octubre y se marca extemporaneo.
        assert es_extemporaneo(date(2026, 10, 6), date(2026, 11, 6)) is True

    def test_ticket_del_mismo_mes_no_es_extemporaneo(self):
        assert es_extemporaneo(date(2026, 10, 6), date(2026, 10, 20)) is False

    def test_una_fecha_futura_no_es_extemporanea(self):
        # Una fecha futura es otro problema (fecha futura) y no se esconde
        # bajo este nombre.
        assert es_extemporaneo(date(2026, 12, 1), date(2026, 11, 6)) is False


class TestCrearLote:
    async def test_crea_con_folio_secuencial_y_fechas_del_mes(self, async_client, test_company):
        lote = await _crear_lote(async_client, test_company.id)
        assert lote["folio"] == "LOT-2026-10-001"
        assert lote["estado"] == "ABIERTO"
        assert lote["fecha_inicio"] == "2026-10-01"
        assert lote["fecha_fin"] == "2026-10-31"
        assert lote["total_escaneado"] == 0
        assert lote["creado_por"] is not None

    async def test_el_segundo_lote_del_mes_lleva_el_002(self, async_client, test_company):
        await _crear_lote(async_client, test_company.id)
        segundo = await _crear_lote(
            async_client, test_company.id, nombre="Segundo lote"
        )
        assert segundo["folio"] == "LOT-2026-10-002"

    async def test_otro_mes_reinicia_la_secuencia(self, async_client, test_company):
        await _crear_lote(async_client, test_company.id)
        noviembre = await _crear_lote(
            async_client, test_company.id, nombre="Lote noviembre", mes=11
        )
        assert noviembre["folio"] == "LOT-2026-11-001"

    async def test_el_prefijo_sale_de_la_config_de_la_empresa(
        self, async_client, db_session, test_company
    ):
        test_company.lote_config = {"prefijo_folio": "LOTE"}
        await db_session.commit()
        lote = await _crear_lote(async_client, test_company.id)
        assert lote["folio"] == "LOTE-2026-10-001"

    async def test_mes_fuera_de_rango_es_422(self, async_client, test_company):
        respuesta = await async_client.post(
            "/lotes", json=_crear_lote_payload(test_company.id, mes=13)
        )
        assert respuesta.status_code == 422

    async def test_fecha_inicio_posterior_a_fecha_fin_es_422(
        self, async_client, test_company
    ):
        respuesta = await async_client.post(
            "/lotes",
            json=_crear_lote_payload(
                test_company.id,
                fecha_inicio="2026-10-31",
                fecha_fin="2026-10-01",
            ),
        )
        assert respuesta.status_code == 422

    async def test_una_sola_fecha_es_422(self, async_client, test_company):
        respuesta = await async_client.post(
            "/lotes",
            json=_crear_lote_payload(test_company.id, fecha_inicio="2026-10-01"),
        )
        assert respuesta.status_code == 422

    async def test_ejercicio_y_mes_tienen_que_ser_los_de_fecha_inicio(
        self, async_client, test_company
    ):
        respuesta = await async_client.post(
            "/lotes",
            json=_crear_lote_payload(
                test_company.id,
                fecha_inicio="2026-11-01",
                fecha_fin="2026-11-30",
            ),
        )
        assert respuesta.status_code == 422

    async def test_empresa_inexistente_es_404(self, async_client):
        respuesta = await async_client.post(
            "/lotes", json=_crear_lote_payload(uuid4())
        )
        assert respuesta.status_code == 404

    async def test_el_folio_no_se_acepta_del_cuerpo(self, async_client, test_company):
        respuesta = await async_client.post(
            "/lotes",
            json=_crear_lote_payload(test_company.id, folio="LOT-2026-10-999"),
        )
        assert respuesta.status_code == 422

    async def test_exige_total_esperado_cuando_la_empresa_lo_pide(
        self, async_client, db_session, test_company
    ):
        test_company.lote_config = {"exige_total_esperado": True}
        await db_session.commit()
        respuesta = await async_client.post(
            "/lotes", json=_crear_lote_payload(test_company.id)
        )
        assert respuesta.status_code == 422
        lote = await _crear_lote(async_client, test_company.id, total_esperado=7)
        assert lote["total_esperado"] == 7


class TestListarLotes:
    async def test_filtra_por_empresa_mes_y_estado(self, async_client, test_company):
        octubre = await _crear_lote(async_client, test_company.id)
        await _crear_lote(async_client, test_company.id, nombre="Noviembre", mes=11)
        await async_client.post(f"/lotes/{octubre['id']}/cerrar")

        respuesta = await async_client.get(
            "/lotes",
            params={
                "company_id": str(test_company.id),
                "ejercicio": 2026,
                "mes": 10,
                "estado": "CERRADO",
            },
        )
        assert respuesta.status_code == 200, respuesta.text
        lotes = respuesta.json()
        assert [lote["id"] for lote in lotes] == [octubre["id"]]

    async def test_estado_invalido_es_422(self, async_client):
        respuesta = await async_client.get("/lotes", params={"estado": "PERDIDO"})
        assert respuesta.status_code == 422


class TestCerrarYReabrir:
    async def test_cerrar_pone_cerrado_at_y_bloquea_un_segundo_cierre(
        self, async_client, test_company
    ):
        lote = await _crear_lote(async_client, test_company.id)
        respuesta = await async_client.post(f"/lotes/{lote['id']}/cerrar")
        assert respuesta.status_code == 200, respuesta.text
        cerrado = respuesta.json()
        assert cerrado["estado"] == "CERRADO"
        assert cerrado["cerrado_at"] is not None

        respuesta = await async_client.post(f"/lotes/{lote['id']}/cerrar")
        assert respuesta.status_code == 409

    async def test_cerrar_deja_rastro_en_auditoria(
        self, async_client, db_session, test_company
    ):
        lote = await _crear_lote(async_client, test_company.id)
        await async_client.post(f"/lotes/{lote['id']}/cerrar")
        filas = (
            await db_session.execute(
                select(Auditoria).where(
                    Auditoria.modulo == "lotes",
                    Auditoria.accion == "LOTE_CERRADO",
                )
            )
        ).scalars().all()
        assert len(filas) == 1
        assert str(filas[0].registro_afectado) == lote["id"]

    async def test_reabrir_sin_motivo_falla(self, async_client, test_company):
        lote = await _crear_lote(async_client, test_company.id)
        await async_client.post(f"/lotes/{lote['id']}/cerrar")
        respuesta = await async_client.post(f"/lotes/{lote['id']}/reabrir", json={})
        assert respuesta.status_code == 422

    async def test_reabrir_vuelve_a_abierto_y_guarda_el_motivo(
        self, async_client, test_company
    ):
        lote = await _crear_lote(async_client, test_company.id)
        await async_client.post(f"/lotes/{lote['id']}/cerrar")
        respuesta = await async_client.post(
            f"/lotes/{lote['id']}/reabrir",
            json={"motivo": "Falto un ticket de Soriana"},
        )
        assert respuesta.status_code == 200, respuesta.text
        reabierto = respuesta.json()
        assert reabierto["estado"] == "ABIERTO"
        assert reabierto["cerrado_at"] is None
        assert reabierto["motivo"] == "Falto un ticket de Soriana"

    async def test_reabrir_un_lote_abierto_es_409(self, async_client, test_company):
        lote = await _crear_lote(async_client, test_company.id)
        respuesta = await async_client.post(
            f"/lotes/{lote['id']}/reabrir", json={"motivo": "Por si acaso"}
        )
        assert respuesta.status_code == 409

    async def test_no_se_reabre_si_la_empresa_no_lo_permite(
        self, async_client, db_session, test_company
    ):
        lote = await _crear_lote(async_client, test_company.id)
        await async_client.post(f"/lotes/{lote['id']}/cerrar")
        test_company.lote_config = {"permite_reapertura": False}
        await db_session.commit()
        respuesta = await async_client.post(
            f"/lotes/{lote['id']}/reabrir", json={"motivo": "Falto un ticket"}
        )
        assert respuesta.status_code == 409


class TestComplementario:
    async def test_complementario_referencia_al_padre_y_hereda_el_periodo(
        self, async_client, test_company
    ):
        padre = await _crear_lote(async_client, test_company.id)
        await async_client.post(f"/lotes/{padre['id']}/cerrar")
        respuesta = await async_client.post(f"/lotes/{padre['id']}/complementario", json={})
        assert respuesta.status_code == 201, respuesta.text
        hijo = respuesta.json()
        assert hijo["lote_padre_id"] == padre["id"]
        assert hijo["company_id"] == padre["company_id"]
        assert hijo["ejercicio"] == 2026 and hijo["mes"] == 10
        assert hijo["fecha_inicio"] == padre["fecha_inicio"]
        assert hijo["fecha_fin"] == padre["fecha_fin"]
        assert hijo["nombre"] == "Complemento de LOT-2026-10-001"
        assert hijo["folio"] == "LOT-2026-10-002"
        assert hijo["estado"] == "ABIERTO"

    async def test_complementario_de_padre_inexistente_es_404(self, async_client):
        respuesta = await async_client.post(f"/lotes/{uuid4()}/complementario", json={})
        assert respuesta.status_code == 404


class TestDetalleConConteos:
    async def test_detalle_cuenta_archivos_tickets_importe_y_duplicados(
        self, async_client, db_session, test_company
    ):
        lote = await _crear_lote(async_client, test_company.id)
        lote_id = UUID(lote["id"])
        # El set de prueba: Soriana $65 dos veces (duplicado) y Tony $23.20.
        for total in ("65.00", "65.00", "23.20"):
            db_session.add(
                TicketModel(
                    company_id=test_company.id,
                    provider_name="Soriana" if total == "65.00" else "Tony",
                    total_amount=Decimal(total),
                    expense_date=date(2026, 10, 6),
                    lote_id=lote_id,
                )
            )
        for nombre in ("soriana-1.heic", "soriana-2.heic"):
            db_session.add(
                ScanFileModel(
                    relative_path=nombre,
                    content_hash=f"hash-{nombre}",
                    lote_id=lote_id,
                    company_id=test_company.id,
                )
            )
        await db_session.commit()

        respuesta = await async_client.get(f"/lotes/{lote_id}")
        assert respuesta.status_code == 200, respuesta.text
        detalle = respuesta.json()
        assert detalle["total_archivos"] == 2
        assert detalle["total_tickets"] == 3
        assert Decimal(detalle["importe_total_vivo"]) == Decimal("153.20")
        assert detalle["duplicados"] == 1

    async def test_detalle_de_lote_inexistente_es_404(self, async_client):
        respuesta = await async_client.get(f"/lotes/{uuid4()}")
        assert respuesta.status_code == 404
