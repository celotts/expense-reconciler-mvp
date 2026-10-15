"""El muestreo contra la base real y contra el reporte.

Los tests de `test_spot_check.py` comprueban la aritmetica. Estos comprueban lo
que importa para el resto del sistema: que la marca de la muestra llegue a la
fila correcta, que la base la respalde, y que el reporte diga lo que la
muestra sostiene y nada mas.

Tres cosas se comprueban aqui que no se ven en los tests de la unidad:

- Que la constraint de la base existe y muerde. Una constraint que solo esta
  en el archivo de migracion no la ejecuta la suite, porque los tests corren
  contra SQLite. Si el estado valido viviera unicamente en el SQL, se podrian
  escribir mil filas invalidas en los tests y el primer registro de revision
  reventaria en produccion.
- Que el orden de las rutas funciona. `/accuracy` y `/spot-check` tienen dos
  segmentos, igual que `/{ticket_id}`. FastAPI resuelve en orden de
  declaracion, no por especificidad, asi que declaradas despues la ruta con
  UUID se las come y contestan 422. Ya paso; este test es la red.
- Que el reporte no promedia entre metodos de lectura. Es la regla que mas
  cara sale si no se respeta, porque el numero sale bien y la conclusion sale
  mal.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.enums import (
    UNKNOWN_PROVIDER,
    ConfidenceSource,
    ExtractionStatus,
    SourceType,
    SpotCheckStatus,
)
from app.models.ticket import TicketModel
from app.services.accuracy_service import SLO_EXACTITUD, Veredicto, en_muestra
from app.services.parser_service import TicketExtractionResult

P = "/api/v1/tickets"

# `SourceType` no tiene un valor para texto plano: el endpoint cae a `image`
# para cualquier tipo que no reconozca. Se replica esa regla en vez de inventar
# un valor, para que el test siga midiendo lo que la API hace.
TIPO_POR_DEFECTO = (
    SourceType.IMAGE
)


def _en_la_muestra(contenido: bytes) -> bool:
    return en_muestra(hashlib.sha256(contenido).hexdigest())


def _contenido_en_la_muestra(etiqueta: str = "muestra") -> bytes:
    """Bytes cuyo hash SI cae dentro del 5% de la muestra.

    Se busca de verdad en vez de parchear `en_muestra`, porque lo que se
    comprueba es el cableado completo: que la marca llega a la fila desde la
    funcion real. Parchear la funcion probaria el parche, no el sistema.
    """
    for i in range(500):
        candidato = f"{etiqueta}-{i}".encode()
        if _en_la_muestra(candidato):
            return candidato
    raise AssertionError("no se encontro contenido en la muestra en 500 intentos")


def _contenido_fuera_de_la_muestra(etiqueta: str = "fuera") -> bytes:
    for i in range(500):
        candidato = f"{etiqueta}-{i}".encode()
        if not _en_la_muestra(candidato):
            return candidato
    raise AssertionError("no se encontro contenido fuera de la muestra en 500 intentos")


async def _guardar(
    db_session,
    company_id,
    contenido: bytes,
    *,
    origen: str = ConfidenceSource.RULES.value,
    en_cola: bool = False,
):
    """Guarda un ticket por la misma ruta que usa la API.

    `en_cola=True` fuerza un ticket que el gate no aprueba: proveedor
    desconocido y total cero, que es exactamente el caso que el gate manda a
    la cola.
    """
    from app.api.tickets import _persist_extracted

    extracted = TicketExtractionResult(
        provider_name=UNKNOWN_PROVIDER if en_cola else "Tiendas Ramirez SA de CV",
        provider_tax_id=None if en_cola else "TRAM910101XXX",
        total_amount=Decimal("0.00") if en_cola else Decimal("1100.00"),
        tax_amount=Decimal("0.00") if en_cola else Decimal("136.00"),
        subtotal=Decimal("0.00") if en_cola else Decimal("964.00"),
        expense_date=date(2025, 3, 15),
        category="alimentos",
        confidence=0.2 if en_cola else 0.95,
        confidence_source=origen,
        raw_text=contenido.decode("utf-8", "ignore"),
    )
    return await _persist_extracted(
        db_session, company_id, extracted, contenido, TIPO_POR_DEFECTO,
        source_file="comprobante.txt",
    )


class TestLaMarcaLlegaALaFila:
    """Solo lo automatico y aprobado entra a la muestra.

    La condicion es deliberadamente estrecha. Un ticket que esta en la cola ya
    fue medido por el gate, y meterlo en el promedio de exactitud contaria
    casos que nunca fueron automaticos: el numero saldria mejor de lo que el
    sistema es. Un ticket de captura manual no lo leyo ni un regex ni un
    modelo. Los dos inflan, y por eso quedan fuera.
    """

    @pytest.mark.asyncio
    async def test_un_aprobado_solo_va_a_la_muestra(self, db_session, test_company):
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra()
        )
        assert ticket.extraction_status == ExtractionStatus.AUTO_APROBADO.value
        assert ticket.spot_check_status == SpotCheckStatus.PENDIENTE.value

    @pytest.mark.asyncio
    async def test_lo_que_no_cae_en_el_5pct_queda_fuera(
        self, db_session, test_company
    ):
        """El 95% no se marca. Si se marcara todo, la muestra seria la cola de
        revision con otro nombre, y nadie podria revisar."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_fuera_de_la_muestra()
        )
        assert ticket.spot_check_status is None

    @pytest.mark.asyncio
    async def test_un_ticket_en_la_cola_no_se_muestrea(
        self, db_session, test_company
    ):
        """Lo que esta PENDIENTE ya lo decidio el gate, no el extractor.

        Su exactitud no la produce la lectura, asi que promediarlo haria que
        el sistema pareciera mas preciso de lo que es.
        """
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("cola"),
            en_cola=True,
        )
        assert ticket.extraction_status != ExtractionStatus.AUTO_APROBADO.value
        assert ticket.spot_check_status is None

    @pytest.mark.asyncio
    async def test_la_captura_manual_no_se_muestrea(self, db_session, test_company):
        """Lo manual no tiene hash y no es automatismo. Los dos motivos llevan
        al mismo resultado, asi que no hace falta ni el hash ni la condicion
        para excluirlo."""
        from app.services.confidence_gate import gate_manual_ticket

        gate = gate_manual_ticket(
            provider_name="Farmacia del Norte",
            total_amount=Decimal("350.00"),
            tax_amount=Decimal("52.07"),
            expense_date=date(2025, 4, 2),
            provider_tax_id="FDNO010101XXX",
        )
        fila = TicketModel(
            company_id=test_company.id,
            provider_name="Farmacia del Norte",
            provider_tax_id="FDNO010101XXX",
            total_amount=Decimal("350.00"),
            tax_amount=Decimal("52.07"),
            subtotal=Decimal("297.93"),
            expense_date=date(2025, 4, 2),
            category="salud",
            confidence=None,
            extraction_status=gate.status.value,
            source_type="manual",
        )
        db_session.add(fila)
        await db_session.commit()
        await db_session.refresh(fila)

        assert fila.confidence is None
        assert fila.source_hash is None
        assert fila.spot_check_status is None


class TestLaBaseRespaldaLoQueDiceElCodigo:
    """Las constraints existen en el modelo, no solo en la migracion.

    Si vivieran unicamente en el DDL de Postgres, la suite no las ejecutaria
    (los tests corren contra SQLite) y el primer registro de revision en
    produccion seria el que descubre el error.
    """

    @pytest.mark.asyncio
    async def test_un_estado_inventado_no_se_puede_guardar(
        self, db_session, test_company
    ):
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("malo")
        )
        ticket.spot_check_status = "REVISADO_POR_QUALQUIERA"
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_un_veredicto_sin_fecha_no_se_puede_guardar(
        self, db_session, test_company
    ):
        """Un veredicto sin fecha no se puede envejecer, y la antiguedad es lo
        que permite medir a que ritmo se acumula la evidencia."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("sin-fecha")
        )
        ticket.spot_check_status = SpotCheckStatus.CORRECTO.value
        ticket.spot_checked_at = None
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_no_se_puede_muestrear_lo_que_no_fue_automatico(
        self, db_session, test_company
    ):
        """Un ticket que una persona aprobo no mide el automatismo, y la base
        lo impide aunque alguien se salte el endpoint."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("aprobado"),
        )
        ticket.extraction_status = ExtractionStatus.APROBADO.value
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestRegistrarElVeredicto:
    """Registrar un veredicto no toca el ticket: solo dice si la lectura
    coincidia con el papel.

    Que no lo toque es lo importante. Si el muestreo corrigiera el total, la
    exactitud medida dependeria de quien reviso, y dejaria de medir el
    automatismo para medir a la persona.
    """

    @pytest.mark.asyncio
    async def test_un_correcto_deja_fecha_y_no_toca_los_datos(
        self, async_client, db_session, test_company
    ):
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("ok")
        )
        total_antes = ticket.total_amount

        r = await async_client.patch(
            f"{P}/{ticket.id}/spot-check", json={"correct": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["spot_check_status"] == SpotCheckStatus.CORRECTO.value

        fila = (await db_session.execute(
            select(TicketModel).where(TicketModel.id == ticket.id)
        )).scalar_one()
        assert fila.spot_checked_at is not None
        assert fila.total_amount == total_antes
        assert fila.reviewed_at is None, "el muestreo no es una revision humana"
        assert fila.reviewed_by is None

    @pytest.mark.asyncio
    async def test_un_incorrecto_guarda_que_campos_fallaron(
        self, async_client, db_session, test_company
    ):
        """Sin los campos, el reporte dice que hay error y no dice que
        arreglar. El campo fallido convierte una estadistica en trabajo."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("mal-campo")
        )
        r = await async_client.patch(
            f"{P}/{ticket.id}/spot-check",
            json={
                "correct": False,
                "campos_incorrectos": ["expense_date", "total_amount"],
                "notes": "la fecha del papel es 14/03, no 15/03",
            },
        )
        assert r.status_code == 200, r.text
        assert set(r.json()["spot_check_wrong_fields"]) == {
            "expense_date", "total_amount",
        }
        assert r.json()["spot_check_notes"].startswith("la fecha")

    @pytest.mark.asyncio
    async def test_marcar_malo_sin_decir_que_campo_se_rechaza(
        self, async_client, db_session, test_company
    ):
        """Se rechaza con 422 y no con 400, porque no es que el dato este mal:
        es que esta incompleto. Y el mensaje dice que hacer."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("vacio")
        )
        r = await async_client.patch(
            f"{P}/{ticket.id}/spot-check", json={"correct": False},
        )
        assert r.status_code == 422
        assert "que campo fallo" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_se_puede_verificar_lo_que_esta_en_la_cola(
        self, async_client, db_session, test_company
    ):
        """Dar por correcto un ticket que el gate dejo en la cola no mide el
        automatismo: mide una decision que ya tomo el gate."""
        ticket = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("cola-2"),
            en_cola=True,
        )
        r = await async_client.patch(
            f"{P}/{ticket.id}/spot-check", json={"correct": True},
        )
        assert r.status_code == 409
        assert "aprobo solo" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_un_ticket_inexistente_da_404(self, async_client):
        r = await async_client.patch(
            f"{P}/{uuid4()}/spot-check", json={"correct": True},
        )
        assert r.status_code == 404


class TestElReporteDiceLoQueLaMuestraSostiene:
    """El entregable: el numero, con lo que le falta para sostenerse."""

    @pytest.mark.asyncio
    async def test_sin_datos_no_afirma_nada(self, async_client, test_company):
        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["veredicto_global"] == Veredicto.SIN_EVIDENCIA
        assert "no se puede afirmar" in cuerpo["explicacion"]
        for medida in cuerpo["por_origen"]:
            assert medida["exactitud"] is None
            assert medida["revisados"] == 0

    @pytest.mark.asyncio
    async def test_los_tres_origenes_aparecen_aunque_no_tengan_datos(
        self, async_client, test_company
    ):
        """Un origen sin evidencia tiene que verse igual de vacio. Si solo
        aparecieran los que tienen datos, la conclusion "se cumple" se
        apoyaria en una base que nadie miro, y el origen sin revisar pasaria
        desapercibido justo cuando es el que hay que mirar."""
        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        origenes = {m["origen"] for m in r.json()["por_origen"]}
        assert {"llm", "pdf_text", "rules"} <= origenes

    @pytest.mark.asyncio
    async def test_no_promedia_entre_metodos_de_lectura(
        self, async_client, db_session, test_company
    ):
        """La regla mas cara si se rompe, porque el numero sale bien.

        Se crea un ticket leido con reglas, verificado como correcto, y otro
        leido con el modelo, verificado como incorrecto. Promediar daria 50%,
        que no describe ninguna via de lectura del sistema.
        """
        bueno = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("reglas-ok"),
            origen=ConfidenceSource.RULES.value,
        )
        malo = await _guardar(
            db_session, test_company.id, _contenido_en_la_muestra("llm-mal"),
            origen=ConfidenceSource.LLM.value,
        )
        for ticket, correcto in ((bueno, True), (malo, False)):
            payload: dict = {"correct": correcto}
            if not correcto:
                payload["campos_incorrectos"] = ["total_amount"]
            r = await async_client.patch(
                f"{P}/{ticket.id}/spot-check", json=payload
            )
            assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        por_origen = {m["origen"]: m for m in r.json()["por_origen"]}

        assert por_origen["rules"]["aciertos"] == 1
        assert por_origen["rules"]["exactitud"] == 1.0
        assert por_origen["llm"]["aciertos"] == 0
        assert por_origen["llm"]["incorrectos"] == 1
        assert por_origen["llm"]["exactitud"] == 0.0
        # Y el global no es el promedio de los dos: es el peor.
        assert r.json()["veredicto_global"] != Veredicto.CUMPLE

    @pytest.mark.asyncio
    async def test_el_veredicto_global_es_el_peor_no_el_promedio(
        self, async_client, db_session, test_company
    ):
        """Un sistema con una via perfecta y otra rota no cumple el objetivo.

        Aqui la via buena no llega a CUMPLE porque 30 revisiones no alcanzan a
        Certificar el 96%, asi que el caso que se comprueba es que un
        INCONCLUYENTE no tapa un NO_CUMPLE. La regla completa, con un CUMPLE de
        verdad enfrente, esta en TestElPeorVeredictoGana, en el test unitario
        del agregador: hacerla aqui exigiria sembrar 374 filas para que un
        test de integracion llegue a una conclusion que la funcion decide sin
        tocar la base.
        """
        for i in range(30):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"reglas-lote-{i}"),
                origen=ConfidenceSource.RULES.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check", json={"correct": True}
            )
            assert r.status_code == 200, r.text

        for i in range(20):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"llm-lote-{i}"),
                origen=ConfidenceSource.LLM.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check",
                json={"correct": False, "campos_incorrectos": ["expense_date"]},
            )
            assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        cuerpo = r.json()
        por_origen = {m["origen"]: m for m in cuerpo["por_origen"]}
        assert por_origen["rules"]["veredicto"] == Veredicto.INCONCLUYENTE
        assert por_origen["llm"]["veredicto"] == Veredicto.NO_CUMPLE
        assert cuerpo["veredicto_global"] == Veredicto.NO_CUMPLE
        assert "llm" in cuerpo["explicacion"]
        # El promedio de los dos seria ~60%, o sea NO_CUMPLE tambien. El
        # promedio SI cambia la conclusion en el otro sentido, y por eso esta
        # comprobacion sola no alcanza: hace falta la del agregador.
        assert por_origen["rules"]["aciertos"] / por_origen["rules"]["revisados"] > 0.9

    @pytest.mark.asyncio
    async def test_con_poca_muestra_dice_que_no_alcanza_y_por_que(
        self, async_client, db_session, test_company
    ):
        """Este es el caso que motiva todo el modulo: 24 de 25 dan
        exactamente 96.0%, y aun asi no se puede afirmar nada."""
        for i in range(24):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"muestra-{i}"),
                origen=ConfidenceSource.LLM.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check", json={"correct": True}
            )
            assert r.status_code == 200, r.text

        t = await _guardar(
            db_session, test_company.id,
            _contenido_en_la_muestra("muestra-24"),
            origen=ConfidenceSource.LLM.value,
        )
        r = await async_client.patch(
            f"{P}/{t.id}/spot-check",
            json={"correct": False, "campos_incorrectos": ["total_amount"]},
        )
        assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        cuerpo = r.json()
        llm = next(m for m in cuerpo["por_origen"] if m["origen"] == "llm")

        assert llm["revisados"] == 25
        assert llm["exactitud"] == 0.96, "el punto medio SI es 96%"
        assert llm["veredicto"] == Veredicto.INCONCLUYENTE, "pero no alcanza"
        assert llm["intervalo_inferior"] < SLO_EXACTITUD
        assert llm["intervalo_superior"] > SLO_EXACTITUD
        assert llm["motivo_faltante"] == "acierto_en_la_linea"
        assert "extractor" in cuerpo["explicacion"], (
            "el texto tiene que decir que medir mas no lo arregla"
        )

    @pytest.mark.asyncio
    async def test_reporta_cuantos_faltan_cuando_si_falta_muestra(
        self, async_client, db_session, test_company
    ):
        """Con acierto por encima del objetivo y muestra chica, el reporte
        tiene que dar un numero accionable."""
        for i in range(20):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"alta-{i}"),
                origen=ConfidenceSource.PDF_TEXT.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check", json={"correct": True}
            )
            assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        cuerpo = r.json()
        pdf_text = next(m for m in cuerpo["por_origen"] if m["origen"] == "pdf_text")

        assert pdf_text["exactitud"] == 1.0
        assert pdf_text["motivo_faltante"] == "suficiente"
        # 20 aciertos de 20 no alcanzan a afirmar el 96%. El numero sale del
        # calculo, no de un valor escrito a mano: si el metodo cambia, este
        # test lo dice.
        from app.services.accuracy_service import faltantes_para_afirmar

        assert pdf_text["total_revisiones_necesarias"] == faltantes_para_afirmar(
            20, 20
        ).total_necesario
        assert pdf_text["total_revisiones_necesarias"] > 20
        assert f"faltan {pdf_text['total_revisiones_necesarias']}" in cuerpo["explicacion"]

    @pytest.mark.asyncio
    async def test_dice_que_campo_falla_mas(self, async_client, db_session, test_company):
        """Lo que convierte el muestreo en trabajo en vez de en un numero."""
        fallos = ["total_amount", "total_amount", "expense_date",
                  "expense_date", "expense_date", "expense_date",
                  "expense_date", "expense_date", "expense_date", "provider_name"]
        for i, campo in enumerate(fallos):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"campos-{i}"),
                origen=ConfidenceSource.LLM.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check",
                json={"correct": False, "campos_incorrectos": [campo]},
            )
            assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/accuracy", params={"company_id": str(test_company.id)}
        )
        llm = next(m for m in r.json()["por_origen"] if m["origen"] == "llm")
        assert llm["campo_mas_fallido"] == "expense_date"
        assert llm["conteo_por_campo"]["expense_date"] == 7
        assert llm["conteo_por_campo"]["total_amount"] == 2
        assert llm["conteo_por_campo"]["provider_name"] == 1


class TestElReporteCuentaLoQueFalta:
    """Los pendientes tambien son informacion.

    Si hay 40 PENDIENTE y 25 revisados, el 96% de hoy no es el de manana. Un
    reporte que solo mira los veredictos presenta como estable una medicion que
    todavia esta cambiando.
    """

    @pytest.mark.asyncio
    async def test_cuenta_los_pendientes_de_revision(
        self, async_client, db_session, test_company
    ):
        for i in range(5):
            await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"pend-{i}"),
                origen=ConfidenceSource.LLM.value,
            )
        for i in range(12):
            t = await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"revisado-{i}"),
                origen=ConfidenceSource.LLM.value,
            )
            r = await async_client.patch(
                f"{P}/{t.id}/spot-check", json={"correct": True}
            )
            assert r.status_code == 200, r.text

        r = await async_client.get(
            f"{P}/spot-check", params={"company_id": str(test_company.id)}
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["total_pendientes"] == 5
        assert cuerpo["total_revisados"] == 12
        assert cuerpo["aciertos"] == 12
        assert cuerpo["incorrectos"] == 0

    @pytest.mark.asyncio
    async def test_el_total_no_lo_corta_el_limite(
        self, async_client, db_session, test_company
    ):
        """Con 40 pendientes y limit=5, la pantalla tiene que decir 40.

        Si el conteo viniera de la lista ya truncada, una cola grande se veria
        como si casi estuviera vacia, que es justo cuando mas trabajo hay.
        """
        for i in range(40):
            await _guardar(
                db_session, test_company.id,
                _contenido_en_la_muestra(f"cola-larga-{i}"),
                origen=ConfidenceSource.RULES.value,
            )

        r = await async_client.get(
            f"{P}/spot-check",
            params={"company_id": str(test_company.id), "limit": 5},
        )
        cuerpo = r.json()
        assert cuerpo["total_pendientes"] == 40
        assert len(cuerpo["tickets"]) == 5, "la lista si se limita"

    @pytest.mark.asyncio
    async def test_la_antiguedad_dice_hace_cuanto_esta_esperando(
        self, async_client, db_session, test_company
    ):
        """Una muestra que envejece sin revisarse es evidencia que se esta
        perdiendo: el volumen de entonces ya no describe el sistema de hoy."""
        await _guardar(
            db_session, test_company.id,
            _contenido_en_la_muestra("viejo"),
            origen=ConfidenceSource.RULES.value,
        )
        r = await async_client.get(
            f"{P}/spot-check", params={"company_id": str(test_company.id)}
        )
        antiguedad = r.json()["antiguedad_promedio_dias"]
        assert antiguedad is not None
        assert antiguedad >= 0.0


class TestLaColaEsTrabajoNoHistorial:
    """Por defecto solo lo PENDIENTE, y los conteos siempre globales.

    El contrato es el mismo que el de la cola de revision, y por la misma
    razon: una cola de trabajo es trabajo. Con 500 tickets muestreados y 400 ya
    verificados, mezclar los dos deja la primera pagina con 20 cosas que hacer
    entre 100 filas, y el revisor tiene que filtrar a ojo.

    Los conteos no siguen al filtro porque el encabezado tiene que mostrar el
    tamano real de la cola. Si bajaran con el filtro, el badge saltaria de 200
    a 3 y dejaria de informar.
    """

    @staticmethod
    async def _sembrar(async_client, db_session, company_id, etiqueta, cuantos, revisar):
        """Siembra `cuantos` tickets de la muestra, opcionalmente revisados.

        El cliente va como parametro y no en `self` a proposito: guardar el
        cliente de una prueba en el objeto de la clase y que otra lo lea es la
        forma de que una prueba dependa del orden en que corrieron.
        """
        for i in range(cuantos):
            t = await _guardar(
                db_session, company_id,
                _contenido_en_la_muestra(f"{etiqueta}-{i}"),
                origen=ConfidenceSource.RULES.value,
            )
            if revisar:
                r = await async_client.patch(
                    f"{P}/{t.id}/spot-check", json={"correct": True}
                )
                assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_por_defecto_solo_aparece_lo_pendiente(
        self, async_client, db_session, test_company
    ):
        await self._sembrar(
            async_client, db_session, test_company.id, "pend", 6, revisar=False
        )
        await self._sembrar(
            async_client, db_session, test_company.id, "hecho", 4, revisar=True
        )

        r = await async_client.get(
            f"{P}/spot-check", params={"company_id": str(test_company.id)}
        )
        cuerpo = r.json()
        assert len(cuerpo["tickets"]) == 6, "solo lo pendiente"
        assert all(t["spot_check_status"] == "PENDIENTE" for t in cuerpo["tickets"])
        # Y los conteos si ven los 10, no los 6 de la lista.
        assert cuerpo["total_pendientes"] == 6
        assert cuerpo["total_revisados"] == 4

    @pytest.mark.asyncio
    async def test_el_historico_se_pide_explicitamente(
        self, async_client, db_session, test_company
    ):
        """Los veredictos ya no se pierden: se piden con `?status=`."""
        await self._sembrar(
            async_client, db_session, test_company.id, "pend2", 3, revisar=False
        )
        await self._sembrar(
            async_client, db_session, test_company.id, "hecho2", 5, revisar=True
        )

        r = await async_client.get(
            f"{P}/spot-check",
            params={"company_id": str(test_company.id), "status": "CORRECTO"},
        )
        cuerpo = r.json()
        assert len(cuerpo["tickets"]) == 5
        assert all(t["spot_check_status"] == "CORRECTO" for t in cuerpo["tickets"])
        # Y los conteos no se mueven: son globales por contrato.
        assert cuerpo["total_pendientes"] == 3
        assert cuerpo["total_revisados"] == 5

    @pytest.mark.asyncio
    async def test_lo_que_no_esta_en_la_muestra_no_aparece_aunque_se_pida(
        self, async_client, db_session, test_company
    ):
        """Pedir `CORRECTO` no puede hacer aparecer lo que nunca fue elegido.

        Es el filtro del punto 1 del docstring, comprobado por el otro lado: no
        solo que la lista por defecto lo excluya, sino que ningun filtro lo
        pueda colar.
        """
        await _guardar(
            db_session, test_company.id, _contenido_fuera_de_la_muestra("nunca")
        )
        for estado in ("CORRECTO", "INCORRECTO", "PENDIENTE"):
            r = await async_client.get(
                f"{P}/spot-check",
                params={"company_id": str(test_company.id), "status": estado},
            )
            assert r.status_code == 200, r.text
            assert r.json()["tickets"] == [], f"con status={estado}"


class TestLasRutasNoSeComenEntreSi:
    """`/accuracy` y `/spot-check` tienen dos segmentos, igual que `/{ticket_id}`.

    FastAPI resuelve en orden de declaracion, no por especificidad. Declaradas
    despues de `/{ticket_id}`, la ruta con UUID las intercepta y contestan 422
    con un error de parseo de UUID que no dice nada del reporte.

    Ya paso. Este test va primero en la clase porque si alguien mueve el
    bloque al final del archivo, esto falla antes y mas claro que cualquier
    otro.
    """

    def test_las_rutas_con_nombre_estan_antes_del_uuid(self):
        """El orden se lee del router de tickets, que es donde se declara.

        No de `app.routes`: esa lista viene aplanada y con los prefijos ya
        puestos, asi que las rutas de los distintos modulos se mezclan y el
        orden que se ve ahi no es el que usa el resolutor.
        """
        from app.api.tickets import router as tickets_router

        rutas = [
            r.path for r in tickets_router.routes
            if "GET" in (getattr(r, "methods", None) or set())
        ]
        pos_uuid = rutas.index("/{ticket_id}")
        for nombrada in ("/accuracy", "/spot-check", "/review-queue"):
            assert nombrada in rutas, f"falta la ruta {nombrada}"
            assert rutas.index(nombrada) < pos_uuid, (
                f"{nombrada} esta despues de /{{ticket_id}}: "
                "la ruta del UUID la intercepta y contesta 422"
            )

    @pytest.mark.asyncio
    async def test_el_reporte_responde_de_verdad(self, async_client):
        r = await async_client.get(f"{P}/accuracy")
        assert r.status_code == 200
        assert "uuid" not in r.text.lower()

    @pytest.mark.asyncio
    async def test_la_cola_responde_de_verdad(self, async_client):
        r = await async_client.get(f"{P}/spot-check")
        assert r.status_code == 200
        assert "uuid" not in r.text.lower()
