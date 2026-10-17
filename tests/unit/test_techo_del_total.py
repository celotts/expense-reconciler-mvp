"""## Que un comprobante no se pierda en silencio por un total absurdo.

ESTOS TESTS EXISTEN POR UN INCIDENTE MEDIDO, NO POR UNA PREOCUPACION
=====================================================================

El 2026-10-12, con `IMG_4415.heic` de esta maquina:

    OCR devolvio  total_amount = Decimal('750047801456')

Son 750 mil millones de pesos. El `INSERT` en una columna `NUMERIC(12,2)` no
admite el valor, PostgreSQL levanta el error, la transaccion **entera** hace
rollback —con ella la fila de `scan_files`—, y `POST /api/v1/scan/` responde
**200 OK**.

El modo de fallo NO es "un total equivocado". Es **un comprobante perdido y un
200 que dice que todo bien**. Quien digitalizo abre la pantalla, ve que el
escaneo funciono, y no vuelve a subir el archivo porque no tiene ninguna razon
para sospechar que se perdio.

`capture.capture_ticket` declara que perder el archivo es "el unico resultado
inaceptable". Antes de estos cambios eso estaba pasando por la puerta de atras.

LOS TRES CAPAS, Y POR QUE HACE FALTAN LAS TRES
================================================

  1. `confidence_gate`: marca `total_out_of_range` y NO lo aprueba solo. Sin
     esto, un total de 750 millones podria salir `AUTO_APROBADO` si la confianza
     fuera alta.
  2. `ticket_persistence`: acota el importe a lo que la columna aguanta. Sin
     esto, el gate lo rechaza pero el `INSERT` sigue reventando —el rechazo no
     evita la excepcion— y el comprobante se pierde igual.
  3. La fila sobrevive con `0.00` y el motivo visible, para que una persona
     escriba lo que dice el papel.

Una sola de las tres no arregla nada: (1) sin (2) revienta igual, y (2) sin (1)
guardaria un numero absurdo sin avisar.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.enums import ExtractionStatus
from app.services.confidence_gate import MAX_TOTAL_PLAUSIBLE, validate_extraction
from app.services.parser_service import TicketExtractionResult
from app.services.ticket_persistence import _al_rango_de_la_columna, persistir_extraccion

# El valor exacto del incidente. No un numero redondo inventado: si la defensa
# funciona con este, funciona con el que la produjo.
TOTAL_DEL_INCIDENTE = Decimal("750047801456")


def _resultado(total: Decimal | None) -> TicketExtractionResult:
    return TicketExtractionResult(
        provider_name="ABARROTES B",
        total_amount=total if total is not None else Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 10, 12),
        raw_text="ABARROTES B\nTOTAL 750047801456",
    )


class TestElGateNoSeAtragaElNumero:
    """Capa 1: el total absurdo no se aprueba solo."""

    def test_el_total_del_incidente_es_un_fallo(self):
        out = validate_extraction(
            provider_name="ABARROTES B",
            total_amount=TOTAL_DEL_INCIDENTE,
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 10, 12),
        )

        fallos = out.failures
        assert any("total_out_of_range" in f for f in fallos), (
            f"un total de {TOTAL_DEL_INCIDENTE} paso el gate: {fallos}"
        )

    def test_el_motivo_dice_cual_numero_se_leyo(self):
        """El motivo lleva el numero, y eso no es decorativo.

        Quien revisa tiene que ver QUE leyo el sistema para saber si el problema
        es el papel o la lectura. Un `total_out_of_range` a secas no dice si se
        leyeron 750 millones o 75 millones, y son dos papeles distintos.
        """
        out = validate_extraction(
            provider_name="ABARROTES B",
            total_amount=TOTAL_DEL_INCIDENTE,
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 10, 12),
        )

        motivo = next(f for f in out.failures if "total_out_of_range" in f)
        assert "750047801456" in motivo

    def test_un_gasto_normal_no_pasa_por_ahi(self):
        """La contraparte: si esto marcara de mas, no habria freno real.

        El techo esta en 50 millones. Un comprobante de 250,000 —la renta de un
        despacho, un equipo de computo— tiene que pasar sin complaint.
        """
        out = validate_extraction(
            provider_name="INMOBILIARIA",
            total_amount=Decimal("250000.00"),
            tax_amount=Decimal("43103.45"),
            expense_date=date(2026, 10, 12),
        )

        assert not any("total_out_of_range" in f for f in out.failures)

    def test_un_total_grande_pero_creible_tambien_pasa(self):
        """El techo tiene que estar lejos de donde viven los gastos reales.

        Si el freno estuviera en 10,000 marcaría una compra de 不能 y seria un
        veto, no un filtro de basura.
        """
        out = validate_extraction(
            provider_name="CONSTRUCTORA",
            total_amount=Decimal("12000000.00"),
            tax_amount=Decimal("2068965.52"),
            expense_date=date(2026, 10, 12),
        )

        assert not any("total_out_of_range" in f for f in out.failures)

    def test_el_techo_no_tiene_nada_que_ver_con_la_columna(self):
        """El techo es de CREDIBILIDAD, no de capacidad de almacenamiento.

        La columna admite casi 10 mil millones y el techo esta en 50 millones. No
        es un descuido: si el filtro estuviera en el maximo de la columna, un
        total de 9 mil millones —igualmente basura— pasaria, y lo unico que
        protege al gate es que el numero tenga sentido. Este test falla si alguien
        "optimiza" el techo subiendo o bajandolo hacia el limite de la columna.
        """
        assert MAX_TOTAL_PLAUSIBLE == Decimal("50000000")
        assert MAX_TOTAL_PLAUSIBLE < Decimal("9999999999.99")


class TestElImporteNoRevientaLaColumna:
    """Capa 2: lo que se guarda cabe en `NUMERIC(12,2)`."""

    def test_un_total_absurdo_no_se_escribe_como_absurdo(self):
        assert _al_rango_de_la_columna(TOTAL_DEL_INCIDENTE) is None

    def test_un_total_normal_pasa_intacto(self):
        valor = Decimal("234.00")
        assert _al_rango_de_la_columna(valor) == valor

    def test_el_impuesto_obligatorio_no_puede_ser_none(self):
        """`tax_amount` es NOT NULL: ahi `None` es un error de base de datos.

        Y `0.00` es correcto ahi por una razon que no aplica a los otros: el IVA
        ausente de un comprobante sin IVA es cero de hecho, no "no leido".
        """
        assert _al_rango_de_la_columna(None, cero_si_no_aplica=True) == Decimal("0.00")

    def test_el_subtotal_sin_leer_es_none_y_no_cero(self):
        """Un `0.00` de subtotal es un dato FALSO con apariencia de dato.

        Haria que `subtotal + IVA == total` cuadrara sin comprobar nada. `None`
        dice "el papel no lo trae", que es distinto y es verdad.
        """
        assert _al_rango_de_la_columna(None) is None


class TestElComprobanteNoSePierde:
    """Capa 3: la fila sobrevive y queda en la cola con el motivo."""

    @pytest.mark.asyncio
    async def test_el_ticket_se_crea_aunque_el_total_sea_absurdo(self, db_session, test_company):
        """ESTE ES EL TEST QUE CIERRA EL INCIDENTE.

        Con el techo SOLO en el gate, este test fallaria con un `IntegrityError`:
        el gate rechaza el total pero el INSERT sigue intentando escribir
        750047801456 en una NUMERIC(12,2). El comprobante se pierde, que es
        exactamente el fallo que se vino a arreglar.
        """
        from app.core.enums import SourceType

        ticket = await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(TOTAL_DEL_INCIDENTE),
            content=b"contenido del comprobante",
            source_type=SourceType.DIRECTORY,
            source_file="IMG_4415.heic",
        )
        await db_session.commit()

        assert ticket is not None, "el comprobante se perdio: no se creo la fila"
        assert ticket.id is not None
        # Y no sale aprobado: el total no se creyo.
        assert ticket.extraction_status != ExtractionStatus.AUTO_APROBADO.value
        # El motivo queda a la vista, que es lo que convierte "no se" en
        # "alguien tiene que ir a buscarlo".
        assert "total_out_of_range" in (ticket.validation_errors or "")

    @pytest.mark.asyncio
    async def test_el_importe_guardado_cabe_en_la_columna(self, db_session, test_company):
        from app.core.enums import SourceType

        ticket = await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(TOTAL_DEL_INCIDENTE),
            content=b"contenido",
            source_type=SourceType.DIRECTORY,
            source_file="IMG_4415.heic",
        )
        await db_session.commit()

        assert abs(ticket.total_amount) <= Decimal("9999999999.99")

    @pytest.mark.asyncio
    async def test_el_documento_queda_guardado_para_revisarlo(self, db_session, test_company):
        """La fila sin su comprobante no sirve de nada: quien tiene que corregir
        el total necesita VER el papel, no un numero en un formulario."""
        from sqlalchemy import select

        from app.core.enums import SourceType
        from app.models.ticket_document import TicketDocumentModel

        await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(TOTAL_DEL_INCIDENTE),
            content=b"contenido del comprobante",
            source_type=SourceType.DIRECTORY,
            source_file="IMG_4415.heic",
        )
        await db_session.commit()

        documentos = (await db_session.execute(
            select(TicketDocumentModel)
        )).scalars().all()
        assert len(documentos) == 1, (
            "el ticket se guardo sin su comprobante: nadie podria corregir el total"
        )


class TestUnaPersonaLoArregla:
    """La salida: el total se puede escribir a mano como en cualquier otro."""

    @pytest.mark.asyncio
    async def test_corregir_el_importe_lo_deja_aprobable(self, async_client, db_session, test_company):
        """El sistema no puede leer el total, pero una persona si.

        Y cuando lo escribe, el ticket debe poder aprobarse: si el techo lo
        bloqueara para siempre, el sistema habria convertido un problema de
        lectura en uno permanente.
        """
        from app.core.enums import SourceType

        await persistir_extraccion(
            db_session,
            company_id=test_company.id,
            extracted=_resultado(TOTAL_DEL_INCIDENTE),
            content=b"contenido",
            source_type=SourceType.DIRECTORY,
            source_file="IMG_4415.heic",
        )
        await db_session.commit()

        from sqlalchemy import select

        from app.models.ticket import TicketModel

        ticket = (await db_session.execute(
            select(TicketModel)
        )).scalars().one()

        resp = await async_client.patch(
            f"/api/v1/tickets/{ticket.id}/review",
            json={
                "action": "approve",
                "provider_name": "ABARROTES B",
                "total_amount": "412.50",
                "tax_amount": "0.00",
                "subtotal": "412.50",
                "expense_date": "2026-10-12",
            },
        )

        assert resp.status_code == 200, resp.text
        await db_session.refresh(ticket)
        assert ticket.total_amount == Decimal("412.50")
        assert ticket.extraction_status == ExtractionStatus.APROBADO.value