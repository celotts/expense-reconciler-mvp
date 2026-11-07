"""El escaneo con lotes: a que lote va cada archivo, y que no se cuenta dos veces.

LA DECISION QUE ESTOS TESTS FIJAN
----------------------------------
El LOTE de un ticket es el de la CORRIDA (la fecha de escaneo): un papel de
octubre escaneado en noviembre entra en el lote de noviembre. El PERIODO
CONTABLE del ticket sigue siendo el de su fecha de papel (octubre), y por
eso el ticket se marca EXTEMPORANEO: llego tarde, pero el gasto es de
octubre. La marca no es una columna nueva en `tickets` (no hay columna
libre adecuada): queda en la respuesta del escaneo (`extemporaneo` por
archivo y la lista `extemporaneos`), en el detalle del evento CREADO o
ACTUALIZADO de `scan_events` y en `auditoria` (`TICKET_EXTEMPORANEO`),
y se vuelve a derivar de `expense_date` contra `lote.fecha_inicio`. Sin
fecha en el papel no hay extemporaneo: el ticket pertenece al periodo
del lote de la corrida.

Estos tests usan los fixtures de `tests/conftest.py` (`db_session`,
`test_company`, `async_client`): la base es SQLite en memoria creada desde
los modelos. Las fechas se calculan desde `date.today()` y no se fijan en
noviembre de 2026: un test que solo pasa en noviembre es un test que miente
en diciembre. "El mes pasado" es siempre el mes anterior a hoy, y "este
mes" es el mes de hoy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.lote import LoteModel, periodo_de_fecha
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.schemas.scan import ScanRequest
from app.services import scan_service
from app.services.lote_service import ErrorLote


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------


def _hoy() -> date:
    return date.today()


def _mes_pasado() -> date:
    """El dia 6 del mes anterior a hoy: siempre pasado, nunca futuro."""
    hoy = _hoy()
    if hoy.month == 1:
        return date(hoy.year - 1, 12, 6)
    return date(hoy.year, hoy.month - 1, 6)


def _este_mes() -> date:
    """El dia 1 de este mes: nunca futuro, siempre del periodo de escaneo."""
    hoy = _hoy()
    return date(hoy.year, hoy.month, 1)


def _comprobante(
    *,
    fecha: date | None,
    total: str = "1100.00",
    subtotal: str = "948.28",
    iva: str = "151.72",
    proveedor: str = "Tiendas Ramirez SA de CV",
) -> str:
    """Un comprobante con aritmetica que cuadra, con o sin fecha.

    Sin `fecha` no hay linea de fecha: es el caso de Bodega Aurrera, que
    no trae fecha visible en la foto y se guarda con `fecha_provisional`.
    """
    lineas = [proveedor, "RFC: TRAM910101XXX"]
    if fecha is not None:
        lineas.append(f"Fecha: {fecha:%Y/%m/%d}")
    lineas += [f"Subtotal: {subtotal}", f"IVA (16%): {iva}", f"TOTAL: {total}"]
    return "\n".join(lineas) + "\n"


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Una carpeta de tickets de verdad, apuntada por `TICKETS_INPUT_DIR`."""
    from app.core.config import settings

    destino = tmp_path / "tickets"
    destino.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    monkeypatch.setattr(settings, "TICKETS_SCAN_OUTPUT_DIR", str(tmp_path / "escaneados"))
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR", False)
    return destino


@pytest.fixture
def sin_modelo(monkeypatch):
    """Apaga OCR y modelo: ninguna prueba de esta suite toca la red."""
    from app.services import capture
    from app.services.ai_extractor import ai_extractor

    def _no_disponible(datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado para esta prueba")

    async def _sin_modelo(*_args, **_kwargs):
        raise RuntimeError("el modelo esta apagado en esta prueba")

    monkeypatch.setattr(capture, "_ocr_por_defecto", _no_disponible)
    monkeypatch.setattr(ai_extractor, "extract_from_image", _sin_modelo)
    monkeypatch.setattr(ai_extractor, "extract_from_text", _sin_modelo)


def _pdf_con_texto(monkeypatch, texto: str):
    """Fuerza la rama de PDF con texto, sin modelo y sin OCR."""
    from app.services import capture

    monkeypatch.setattr(capture, "extract_pdf_text", lambda _bytes: texto)
    monkeypatch.setattr(
        capture, "_vision_pdf", _no_deberia_llamarse("un PDF con texto no se renderiza")
    )


def _pdf_por_marca(monkeypatch, textos: dict[bytes, str]):
    """Como `_pdf_con_texto`, pero el texto depende de una marca en los bytes."""
    from app.services import capture

    def _extraer(datos: bytes) -> str:
        for marca, texto in textos.items():
            if marca in datos:
                return texto
        raise AssertionError("sin texto de prueba para estos bytes")

    monkeypatch.setattr(capture, "extract_pdf_text", _extraer)
    monkeypatch.setattr(
        capture, "_vision_pdf", _no_deberia_llamarse("un PDF con texto no se renderiza")
    )


def _no_deberia_llamarse(que):
    async def _explota(*_args, **_kwargs):
        raise AssertionError(que)

    return _explota


async def _lote_en_db(
    db_session,
    company_id,
    *,
    ejercicio: int,
    mes: int,
    estado: str = "ABIERTO",
    folio: str | None = None,
):
    """Un lote en la base, sin pasar por el endpoint."""
    _, _, inicio, fin = periodo_de_fecha(date(ejercicio, mes, 1), "mensual")
    lote = LoteModel(
        company_id=company_id,
        folio=folio or f"LOT-{ejercicio:04d}-{mes:02d}-001",
        nombre=f"Lote {ejercicio}-{mes:02d}",
        ejercicio=ejercicio,
        mes=mes,
        tipo_periodo="mensual",
        fecha_inicio=inicio,
        fecha_fin=fin,
        estado=estado,
        total_escaneado=0,
        importe_total=Decimal("0.00"),
    )
    db_session.add(lote)
    await db_session.commit()
    await db_session.refresh(lote)
    return lote


async def _lote_fresco(db_session, lote_id):
    return await db_session.get(LoteModel, lote_id)


# ---------------------------------------------------------------------------
# 1. El lote de la corrida
# ---------------------------------------------------------------------------


class TestEscanearConLote:
    async def test_escanear_con_lote_abierto_asigna_lote_id_y_actualiza_cache(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        lote = await _lote_en_db(
            db_session, test_company.id, ejercicio=hoy.year, mes=hoy.month
        )
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        resumen = await scan_service.escanear(
            db_session, test_company.id, "ana", lote_id=lote.id
        )

        assert resumen.lote_id == lote.id
        assert resumen.lote_folio == lote.folio
        assert resumen.nuevos == 1

        ticket = (await db_session.execute(select(TicketModel))).scalar_one()
        assert ticket.lote_id == lote.id
        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        assert fila.lote_id == lote.id

        lote = await _lote_fresco(db_session, lote.id)
        assert lote.total_escaneado == 1
        assert lote.importe_total == Decimal("1100.00")

    async def test_escanear_sin_lote_id_crea_el_lote_del_periodo_y_lo_reutiliza(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        primera = await scan_service.escanear(db_session, test_company.id, "ana")
        segunda = await scan_service.escanear(db_session, test_company.id, "ana")

        assert primera.lote_id is not None
        assert segunda.lote_id == primera.lote_id
        assert primera.lote_folio == (
            f"LOT-{hoy.year:04d}-{hoy.month:02d}-001"
        )

        lotes = (await db_session.execute(select(LoteModel))).scalars().all()
        assert len(lotes) == 1, "dos corridas no pueden crear dos lotes del periodo"
        assert lotes[0].estado == "ABIERTO"
        assert lotes[0].total_escaneado == 1

    async def test_escanear_con_lote_cerrado_rechaza_409(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        lote = await _lote_en_db(
            db_session,
            test_company.id,
            ejercicio=hoy.year,
            mes=hoy.month,
            estado="CERRADO",
        )
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        with pytest.raises(ErrorLote) as excinfo:
            await scan_service.escanear(
                db_session, test_company.id, "ana", lote_id=lote.id
            )
        assert excinfo.value.status_code == 409
        assert "CERRADO" in excinfo.value.detail

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert tickets == [], "un lote cerrado no escanea nada"

    async def test_escanear_con_lote_de_otra_empresa_rechaza_409(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        from app.models.company import CompanyModel

        hoy = _hoy()
        otra = CompanyModel(name="Otra", tax_id=f"OTRA{uuid4().hex[:8].upper()}")
        db_session.add(otra)
        await db_session.commit()
        lote = await _lote_en_db(
            db_session, otra.id, ejercicio=hoy.year, mes=hoy.month
        )
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        with pytest.raises(ErrorLote) as excinfo:
            await scan_service.escanear(
                db_session, test_company.id, "ana", lote_id=lote.id
            )
        assert excinfo.value.status_code == 409

    async def test_crear_lote_desde_el_escaneo(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        resumen = await scan_service.escanear(
            db_session,
            test_company.id,
            "ana",
            crear_lote={"nombre": "Lote prueba", "total_esperado": 5},
        )

        lote = await _lote_fresco(db_session, resumen.lote_id)
        assert lote.nombre == "Lote prueba"
        assert lote.total_esperado == 5
        assert lote.folio == f"LOT-{hoy.year:04d}-{hoy.month:02d}-001"
        assert lote.total_escaneado == 1

    def test_lote_id_y_crear_lote_juntos_es_422(self):
        with pytest.raises(ValidationError):
            ScanRequest(lote_id=uuid4(), crear_lote={"nombre": "Lote prueba"})


# ---------------------------------------------------------------------------
# 2. Idempotencia del conteo
# ---------------------------------------------------------------------------


class TestElLoteNoSeCuentaDosVeces:
    async def test_segunda_pasada_sin_cambios_no_duplica_conteo_del_lote(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        primera = await scan_service.escanear(db_session, test_company.id, "ana")
        segunda = await scan_service.escanear(db_session, test_company.id, "ana")

        assert primera.nuevos == 1
        assert segunda.sin_cambios == 1
        assert segunda.lote_id == primera.lote_id

        lote = await _lote_fresco(db_session, primera.lote_id)
        assert lote.total_escaneado == 1
        assert lote.importe_total == Decimal("1100.00")
        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1

    async def test_duplicado_por_hash_no_suma_al_lote_pero_su_archivo_lleva_lote(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        texto = _comprobante(fecha=_este_mes())
        _pdf_con_texto(monkeypatch, texto)
        contenido = b"%PDF-1.7 " + texto.encode()
        (carpeta / "original.pdf").write_bytes(contenido)
        (carpeta / "copia.pdf").write_bytes(contenido)

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.duplicados == 1
        assert resumen.nuevos == 1
        lote = await _lote_fresco(db_session, resumen.lote_id)
        assert lote.total_escaneado == 1
        assert lote.importe_total == Decimal("1100.00")

        filas = {
            f.relative_path: f
            for f in (await db_session.execute(select(ScanFileModel))).scalars().all()
        }
        assert filas["original.pdf"].lote_id == lote.id
        assert filas["copia.pdf"].lote_id == lote.id
        assert filas["original.pdf"].ticket_id == filas["copia.pdf"].ticket_id

    async def test_duplicado_por_contenido_no_suma_al_lote(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        texto = _comprobante(fecha=_este_mes())
        _pdf_por_marca(monkeypatch, {b"MARCA-A": texto, b"MARCA-B": texto})
        (carpeta / "a.pdf").write_bytes(b"%PDF-1.7 MARCA-A")
        (carpeta / "b.pdf").write_bytes(b"%PDF-1.7 MARCA-B")

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.duplicados == 1
        assert len(resumen.duplicados_por_contenido) == 1
        lote = await _lote_fresco(db_session, resumen.lote_id)
        assert lote.total_escaneado == 1
        assert lote.importe_total == Decimal("1100.00")
        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1


# ---------------------------------------------------------------------------
# 3. Extemporaneidad
# ---------------------------------------------------------------------------


class TestExtemporaneo:
    async def test_ticket_del_mes_pasado_es_extemporaneo_y_pertenece_a_ese_mes(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """El papel es del mes pasado y llego en este: va al lote de la
        corrida, marcado extemporaneo, y su periodo contable es el pasado.

        Es el caso del set de prueba: un ticket de octubre escaneado en
        noviembre pertenece a octubre y es extemporaneo.
        """
        papel = _mes_pasado()
        hoy = _hoy()
        _pdf_con_texto(monkeypatch, _comprobante(fecha=papel))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=papel).encode()
        )

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        detalle = resumen.detalles[0]
        assert detalle.extemporaneo is True
        assert detalle.lote_id == resumen.lote_id
        assert len(resumen.extemporaneos) == 1
        entrada = resumen.extemporaneos[0]
        assert entrada["fecha_papel"] == papel.isoformat()
        assert entrada["periodo_contable"] == f"{papel.year}-{papel.month:02d}"
        assert entrada["lote_folio"] == resumen.lote_folio

        ticket = (await db_session.execute(select(TicketModel))).scalar_one()
        assert ticket.expense_date == papel
        # Pertenece al mes pasado: el periodo es el de la fecha del papel.
        ejercicio, mes, _, _ = periodo_de_fecha(ticket.expense_date)
        assert (ejercicio, mes) == (papel.year, papel.month)
        # Pero el lote es el de la corrida (este mes), no el del papel.
        lote = await _lote_fresco(db_session, resumen.lote_id)
        assert (lote.ejercicio, lote.mes) == (hoy.year, hoy.month)
        assert ticket.lote_id == lote.id
        # Y aun asi cuenta en el lote: llego tarde, pero se digitalizo.
        assert lote.total_escaneado == 1
        assert lote.importe_total == Decimal("1100.00")

        # La marca queda en el detalle del evento CREADO del archivo
        # (append-only). No es una accion nueva: la CHECK de `scan_events`
        # no permite otra accion que las ocho que ya tiene, y la marca en
        # `auditoria` (`TICKET_EXTEMPORANEO`) no se puede verificar aqui:
        # `registrar_auditoria` sin sesion abre la suya, que en la suite
        # es la conexion de `DATABASE_URL`, no el SQLite en memoria.
        eventos = (
            await db_session.execute(
                select(ScanEventModel).where(
                    ScanEventModel.action == "CREADO"
                )
            )
        ).scalars().all()
        assert len(eventos) == 1
        assert "extemporaneo" in (eventos[0].detail or "")
        assert papel.isoformat() in (eventos[0].detail or "")

    async def test_ticket_sin_fecha_pertenece_al_lote_de_escaneo_y_no_es_extemporaneo(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Sin fecha en el papel (Bodega $234): el ticket pertenece al
        periodo del lote de la corrida y NO se marca extemporaneo."""
        _pdf_con_texto(monkeypatch, _comprobante(fecha=None))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=None).encode()
        )

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        detalle = resumen.detalles[0]
        assert detalle.extemporaneo is False
        assert resumen.extemporaneos == []

        ticket = (await db_session.execute(select(TicketModel))).scalar_one()
        assert ticket.fecha_provisional is True
        assert ticket.lote_id == resumen.lote_id
        lote = await _lote_fresco(db_session, resumen.lote_id)
        assert lote.total_escaneado == 1

    async def test_ticket_de_este_mes_no_es_extemporaneo(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.detalles[0].extemporaneo is False
        assert resumen.extemporaneos == []


# ---------------------------------------------------------------------------
# 4. El endpoint
# ---------------------------------------------------------------------------


class TestPostScanConLote:
    async def test_post_scan_con_lote_cerrado_responde_409(
        self, async_client, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        lote = await _lote_en_db(
            db_session,
            test_company.id,
            ejercicio=hoy.year,
            mes=hoy.month,
            estado="CERRADO",
        )
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        respuesta = await async_client.post(
            "/scan/",
            json={"company_id": str(test_company.id), "lote_id": str(lote.id)},
        )

        assert respuesta.status_code == 409, respuesta.text
        assert "CERRADO" in respuesta.json()["detail"]

    async def test_post_scan_con_lote_abierto_responde_200_con_lote(
        self, async_client, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        hoy = _hoy()
        lote = await _lote_en_db(
            db_session, test_company.id, ejercicio=hoy.year, mes=hoy.month
        )
        _pdf_con_texto(monkeypatch, _comprobante(fecha=_este_mes()))
        (carpeta / "ocho.pdf").write_bytes(
            b"%PDF-1.7 " + _comprobante(fecha=_este_mes()).encode()
        )

        respuesta = await async_client.post(
            "/scan/",
            json={"company_id": str(test_company.id), "lote_id": str(lote.id)},
        )

        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["lote_id"] == str(lote.id)
        assert cuerpo["lote_folio"] == lote.folio
        assert cuerpo["nuevos"] == 1
        assert cuerpo["detalles"][0]["lote_id"] == str(lote.id)
        assert cuerpo["detalles"][0]["extemporaneo"] is False
        assert cuerpo["resumen"]["lote_id"] == str(lote.id)
        assert cuerpo["resumen"]["lote_folio"] == lote.folio

    async def test_post_scan_con_lote_y_crear_lote_juntos_es_422(
        self, async_client, test_company
    ):
        respuesta = await async_client.post(
            "/scan/",
            json={
                "company_id": str(test_company.id),
                "lote_id": str(uuid4()),
                "crear_lote": {"nombre": "Lote prueba"},
            },
        )
        assert respuesta.status_code == 422
