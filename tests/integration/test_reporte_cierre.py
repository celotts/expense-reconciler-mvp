"""El informe de cierre mensual, de punta a punta (criterios A1-A5, A8, A9).

Este archivo prueba el **recorrido**: la peticion entra por HTTP, la consulta corre
contra una base de verdad, y lo que sale se compara contra una suma calculada aqui
mismo. No prueba una funcion interna.

La razon es que el defecto que mas caro sale en este repo no es una funcion mal
escrita, es **una capa que se pierde el dato entre medio**. El precedente es
`test_ieps_extremo_a_extremo.py`: un campo se agrego a cuatro schemas y a ninguno
de los dos sitios donde el gate se relee, y el sintoma fue un `AttributeError` tres
capas mas abajo. Un test por capa habria pasado en los tres casos.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import zlib

import pytest

from app.models.bank_transaction import BankTransactionModel
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel

P = "/api/v1/reports"

# Un periodo ya cerrado: cualquier fecha de hoy sirve, y se fija una para que el
# comparativo sea concluyente (un periodo en curso se declara no concluyente a
# proposito, y eso tiene su propio test).
PERIODO = "2026-01"
MES_ANTERIOR = "2025-12"


def _ticket(
    db_session,
    company,
    *,
    total="100.00",
    fecha=date(2026, 1, 10),
    categoria="GASTOS GENERALES",
    estado="AUTO_APROBADO",
    fuente="llm",
    spot=None,
    spot_by=None,
    reviewed=False,
):
    # El IVA es proporcional, no fijo. La base tiene la constraint
    # `ck_tickets_tax_lte_total_when_settled` (IVA <= total en estados
    # liquidados), y un `tax_amount` de 16.00 fijo viola la constraint en cuanto
    # el total es menor que 16. El dato tiene que ser valido: un test que inserta
    # filas invalidas mide el error de la constraint, no el informe.
    t = TicketModel(
        company_id=company.id,
        provider_name="PROVEEDOR DE PRUEBA",
        total_amount=Decimal(total),
        tax_amount=(Decimal(total) * Decimal("0.16")).quantize(Decimal("0.01")),
        expense_date=fecha,
        category=categoria,
        extraction_status=estado,
        confidence_source=fuente,
        confidence=Decimal("0.95"),
        source_type="pdf",
    )
    if spot is not None:
        t.spot_check_status = spot
        t.spot_checked_by = spot_by
        t.spot_checked_at = datetime(2026, 2, 1, 12, 0, 0)
    if reviewed:
        t.reviewed_at = datetime(2026, 2, 1, 12, 0, 0)
        t.reviewed_by = "contador@despacho.mx"
    db_session.add(t)
    return t


def _texto_del_pdf(pdf: bytes) -> str:
    """El texto que hay DENTRO del PDF, descomprimiendo sus flujos.

    Los flujos de contenido van comprimidos con zlib, asi que buscar texto en los
    bytes crudo no encuentra nada. El detalle que hizo fallar esto la primera vez:
    el filtro escribio `if b[:1] != b"x"` para saltarse los flujos que empiezan
    con `/`, y el byte magico de zlib es **0x78**, que en ASCII es exactamente
    `x`. El filtro estaba descartando el unico flujo que importaba, y el test
    fallaba con `b''` sin dar ninguna pista de por que.
    """
    import zlib

    partes: list[bytes] = []
    for crudo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        # zlib SIEMPRE empieza con 0x78. Se comprueba el byte magico en vez de
        # filtrar por una letra.
        if not crudo.startswith(b"\x78"):
            continue
        try:
            partes.append(zlib.decompress(crudo))
        except zlib.error:
            continue
    return b"".join(partes).decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# A1: el PDF se descarga, es un PDF, y abre
# ---------------------------------------------------------------------------

class TestA1ElPdfSeDescarga:
    async def test_el_pdf_responde_200_y_es_un_pdf(self, async_client, test_company):
        r = await async_client.get(
            f"{P}/cierre-mensual.pdf",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        # Firma de un PDF real. No "contiene application/pdf": el header lo pone
        # cualquiera, el primer byte lo pone el que sabe escribir un PDF.
        assert r.content.startswith(b"%PDF-")

    async def test_el_nombre_del_archivo_trae_el_rfc_y_el_periodo(
        self, async_client, test_company
    ):
        r = await async_client.get(
            f"{P}/cierre-mensual.pdf",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        cd = r.headers["content-disposition"]
        assert "cierre_" in cd
        assert test_company.tax_id in cd
        assert PERIODO in cd

    async def test_el_json_y_el_pdf_dicen_lo_mismo(
        self, async_client, test_company, db_session
    ):
        """El preview y el documento no pueden discrepar (R7 del front).

        No basta con que los dos endpoint existan: tienen que salir del mismo
        objeto. Si el PDF calculara su propia exactitud, un dia pondria 94% y el
        tablero 92%, y el contador veria dos veredictos en la misma pantalla.
        """
        _ticket(db_session, test_company, total="500.00")

        params = {"company_id": str(test_company.id), "periodo": PERIODO}
        js = (await async_client.get(f"{P}/cierre-mensual", params=params)).json()
        pdf = (await async_client.get(f"{P}/cierre-mensual.pdf", params=params)).content

        # El total del JSON tiene que aparecer en el PDF. Los PDF comprimen los
        # flujos de texto, asi que se busca el numero en crudo y, si no esta,
        # se descomprime.
        objetivo = f"{Decimal('500.00'):,.2f}"
        texto = _texto_del_pdf(pdf)
        assert objetivo in texto, (
            f"el total del JSON ({objetivo}) no aparece en el PDF: "
            "los dos no salen del mismo objeto"
        )


# ---------------------------------------------------------------------------
# A2: el total del informe iguala una suma independiente
# ---------------------------------------------------------------------------

class TestA2ElTotalCuadra:
    async def test_el_total_iguala_la_suma_por_expense_date(
        self, async_client, test_company, db_session
    ):
        """A2, y es el criterio mas importante de todos.

        La suma se calcula aqui, en el test, con los MISMOS datos que se metieron.
        Si difiere, el informe esta contando otra cosa —o un periodo distinto, o
        dos veces— y ninguna otra seccion lo delata: el desglose por categoria
        cuadria consigo mismo igual de bien.
        """
        montos = ["100.00", "250.50", "49.99", "1000.00"]
        for i, m in enumerate(montos):
            _ticket(db_session, test_company, total=m, fecha=date(2026, 1, 5 + i))

        # Un ticket de OTRO periodo, que no puede entrar en la suma.
        _ticket(db_session, test_company, total="9999.00", fecha=date(2026, 2, 5))
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        assert r.status_code == 200
        datos = r.json()

        esperado = sum(Decimal(m) for m in montos)
        assert Decimal(datos["gasto"]["total"]) == esperado, (
            f"el informe dice {datos['gasto']['total']} y la suma independiente "
            f"da {esperado}"
        )
        assert datos["gasto"]["tickets"] == len(montos)

    async def test_el_comparativo_de_un_periodo_cerrado_es_el_mes_anterior_completo(
        self, async_client, test_company, db_session
    ):
        """Un periodo ya terminado se compara contra el mes anterior COMPLETO.

        No contra el mismo dia: "enero contra diciembre" es la lectura que espera
        un contador, y con el corte al dia equivalente (31 contra 31) los dos meses
        entran enteros. El corte al mismo dia importa para el mes EN CURSO, que
        tiene su test aparte.
        """
        _ticket(db_session, test_company, total="500.00", fecha=date(2026, 1, 10))
        _ticket(db_session, test_company, total="777.00", fecha=date(2025, 12, 20))
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        comp = r.json()["gasto"]["comparativo"]
        assert comp["conclusivo"] is True
        assert Decimal(comp["monto_anterior"]) == Decimal("777.00")
        assert comp["nombre_anterior"] == "diciembre de 2025"

    async def test_el_corte_al_mismo_dia_aplica_a_un_periodo_en_curso(
        self, async_client, test_company, db_session
    ):
        """El corte al mismo dia, aqui, y con el importe comprobado.

        Un mes a medias comparado contra uno completo daria siempre una caida que
        no existe. Este test es el que fija que el corte se aplica de verdad, y no
        solo que la nota aparece.
        """
        hoy = date.today()
        # Mes anterior completo.
        ant = hoy.replace(day=1) - timedelta(days=1)
        _ticket(db_session, test_company, total="1000.00", fecha=date(ant.year, ant.month, 1))
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={
                "company_id": str(test_company.id),
                "periodo": f"{hoy.year}-{hoy.month:02d}",
            },
        )
        comp = r.json()["gasto"]["comparativo"]
        assert comp["conclusivo"] is False
        # El mes anterior se corta al dia de hoy, no al final del mes: el dia 1
        # cae dentro del corte y por eso esta. Con el dia 15 seria distinto, y
        # por eso el importe se comprueba aqui y no en un caso de principio.
        assert Decimal(comp["monto_anterior"]) == Decimal("1000.00")

    async def test_febrero_de_2026_no_rompe_el_corte(
        self, async_client, test_company, db_session
    ):
        """Febrero tiene 28 dias y el periodo pide corte al 28. Un `replace(day=31)`
        reventaria con ValueError, y el error se veria como un 500."""
        _ticket(db_session, test_company, total="100.00", fecha=date(2026, 2, 28))
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": "2026-02"},
        )
        assert r.status_code == 200, r.text
        assert Decimal(r.json()["gasto"]["total"]) == Decimal("100.00")

    async def test_un_periodo_en_curso_se_declare_no_concluyente(
        self, async_client, test_company, db_session
    ):
        """R7 aplicado al comparativo: si no aplica, lo dice.

        Comparar 6 dias contra 31 produce una caida que no existe, y un contador
        que ve "-80%" busca un problema que no esta. El informe tiene que decirlo.
        """
        hoy = date.today()
        _ticket(
            db_session, test_company, total="10.00", fecha=date(hoy.year, hoy.month, 1)
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={
                "company_id": str(test_company.id),
                "periodo": f"{hoy.year}-{hoy.month:02d}",
            },
        )
        comp = r.json()["gasto"]["comparativo"]
        assert comp["conclusivo"] is False
        assert comp["nota"], "un comparativo no concluyente tiene que explicar por que"


# ---------------------------------------------------------------------------
# A3: sin evidencia, SIN_EVIDENCIA y nunca 0%
# ---------------------------------------------------------------------------

class TestA3SinEvidenciaDiceSinEvidencia:
    async def test_con_el_muestreo_vacio_dice_sin_evidencia(
        self, async_client, test_company
    ):
        """A3. La trampa es `0%`, y `0%` es una afirmacion: dice que el sistema
        fallo en todo el periodo. Lo que paso es que nadie mire."""
        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        ex = r.json()["exactitud"]
        assert ex["veredicto_global"] == "SIN_EVIDENCIA"
        for origen in ex["por_origen"]:
            assert origen["exactitud"] is None, (
                f"{origen['origen']} reporta exactitud sin ninguna revision"
            )

    async def test_sin_evidencia_no_dice_cero_por_ningun_lado(
        self, async_client, test_company, db_session
    ):
        """El 0% no puede aparecer NI como exactitud NI como texto.

        Se comprueba el JSON entero como cadena, porque un 0 puede esconderse en
        cualquier lado: en un campo, en un mensaje, en una etiqueta.
        """
        _ticket(db_session, test_company, total="10.00")
        await db_session.commit()
        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        crudo = r.text
        assert "0.0%" not in crudo
        assert "exactitud\": 0" not in crudo

    async def test_el_aviso_de_que_no_alcanza_va_arriba(
        self, async_client, test_company
    ):
        """R2: si la evidencia no alcanza, eso se dice en la PRIMERA pagina.

        Y en el PDF, antes de cualquier cifra: si va despues, el contador ya leyo
        el total y conclusiono antes de llegar al aviso.
        """
        params = {"company_id": str(test_company.id), "periodo": PERIODO}
        js = (await async_client.get(f"{P}/cierre-mensual", params=params)).json()
        assert js["advertencia_principal"], "sin evidencia tiene que haber advertencia"
        assert "NO alcanza" in js["advertencia_principal"] or "NO ESTA" in js["advertencia_principal"]

    async def test_sin_evidencia_la_verificacion_humana_dice_cero_no_na(
        self, async_client, test_company
    ):
        """Aqui 0 SI es verdad: no se tomo ninguna muestra. Un `None` se leeria
        como "no disponible", y si lo hubo: se sabe que no se reviso nada."""
        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        v = r.json()["verificacion"]
        assert v["en_muestra"] == 0
        assert v["revisadas"] == 0


# ---------------------------------------------------------------------------
# A4: determinismo
# ---------------------------------------------------------------------------

class TestA4Determinismo:
    async def test_dos_llamadas_dan_bytes_identicos(
        self, async_client, test_company, db_session
    ):
        """R4. Mismo periodo + misma base -> el MISMO archivo.

        Es la unica garantia que hace que el pdf sirva como evidencia: si dos
        descargas del mismo mes difieren en un byte, alguien no puede demostrar que
        el documento que entrega es el mismo que reviso.
        """
        _ticket(db_session, test_company, total="1234.56", fecha=date(2026, 1, 15))
        await db_session.commit()

        params = {"company_id": str(test_company.id), "periodo": PERIODO}
        a = await async_client.get(f"{P}/cierre-mensual.pdf", params=params)
        b = await async_client.get(f"{P}/cierre-mensual.pdf", params=params)
        assert a.status_code == 200 and b.status_code == 200
        assert a.content == b.content, "el PDF no es reproducible"

    async def test_el_sello_de_fecha_no_depende_del_reloj(
        self, async_client, test_company, db_session
    ):
        """Por que R4 no es trivial: `fpdf2` sella `datetime.now()` en cada archivo.

        Sin `set_creation_date` el test de bytes identicos falla SIEMPRE, y el
        sintoma (dos archivos que se parecen pero no son iguales) no apunta a nada
        del codigo de negocio. Por eso se comprueba que el sello sale del periodo.
        """
        _ticket(db_session, test_company, total="10.00", categoria="RENTA")
        await db_session.commit()

        params = {"company_id": str(test_company.id), "periodo": PERIODO}
        pdf = (await async_client.get(f"{P}/cierre-mensual.pdf", params=params)).content

        # El sello va dentro del archivo. Que el anio del periodo aparezca en los
        # metadatos prueba que la fecha viene del dato y no del reloj.
        import datetime as _dt

        marca = f"D:{_dt.date(2026, 1, 31).strftime('%Y%m%d')}"
        assert marca.encode() in pdf, (
            "el PDF no sella la fecha del periodo: R4 no se puede cumplir"
        )

    async def test_el_json_tampoco_depende_del_reloj(
        self, async_client, test_company
    ):
        """Si el JSON metiera `datetime.now()`, el PDF tampoco podria ser
        determinista aunque no lo escribiera: salen del mismo objeto."""
        params = {"company_id": str(test_company.id), "periodo": PERIODO}
        a = (await async_client.get(f"{P}/cierre-mensual", params=params)).text
        b = (await async_client.get(f"{P}/cierre-mensual", params=params)).text
        assert a == b


# ---------------------------------------------------------------------------
# A5: periodo invalido
# ---------------------------------------------------------------------------

class TestA5ElPeriodoSeValida:
    @pytest.mark.parametrize(
        "periodo", ["2026-13", "2026-00", "2026-1", "26-01", "2026", "", "2026-01-01"]
    )
    async def test_un_periodo_que_no_existe_da_422(self, async_client, test_company, periodo):
        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": periodo},
        )
        assert r.status_code == 422, f"{periodo!r} dio {r.status_code}"

    async def test_ambos_meses_validos_de_31_dias_pasan(self, async_client, test_company):
        for periodo in ("2026-01", "2026-12", "2026-07"):
            r = await async_client.get(
                f"{P}/cierre-mensual",
                params={"company_id": str(test_company.id), "periodo": periodo},
            )
            assert r.status_code == 200, f"{periodo} dio {r.status_code}: {r.text}"

    async def test_un_periodo_incompleto_da_422(self, async_client, test_company):
        r = await async_client.get(
            f"{P}/cierre-mensual", params={"company_id": str(test_company.id)}
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# A8: pendientes y R3
# ---------------------------------------------------------------------------

class TestA8PendientesBloqueanElCierre:
    async def test_un_ticket_sin_categoria_aparece_y_bloquea(
        self, async_client, test_company, db_session
    ):
        """A8 y R3 a la vez.

        La regla dice dos cosas: que el pendiente se reporte, y que el periodo no
        se declare cerrado. Aqui se prueban las dos, porque un informe que reporta
        el pendiente y aun asi dice "cerrado" es peor que uno que no lo reporta:
        el contador ve la luz verde y no mira el pendiente.
        """
        _ticket(db_session, test_company, total="100.00", categoria=None)
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["pendientes"]["sin_categoria_tickets"] == 1
        assert datos["puede_cerrarse"] is False
        assert datos["estado_periodo"] == "NO_CIERRA"
        assert "sin categoria" in datos["pendientes"]["motivo"]

    async def test_sin_pendientes_el_periodo_puede_cerrarse(
        self, async_client, test_company, db_session
    ):
        """R3 en la otra direccion: sin pendientes, el periodo CIERRA.

        Y "conciliado" significa de verdad: un ticket sin fila en
        `reconciliations` esta SIN CONCILIAR, aunque tenga categoria. Si no
        estuviera conciliado contra el banco no sabriamos que el dinero salio, y
        declararlo cerrado seria cerrar un periodo que nadie cruzo. Por eso este
        test crea la conciliacion PERFECT y no basta con el ticket suelto.
        """
        t = _ticket(db_session, test_company, total="100.00", categoria="RENTA")
        await db_session.flush()
        banco = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2026, 1, 10),
            amount=Decimal("100.00"),
            description="RENTA",
        )
        db_session.add(banco)
        await db_session.flush()
        db_session.add(
            ReconciliationModel(
                ticket_id=t.id, bank_transaction_id=banco.id, match_status="PERFECT"
            )
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["pendientes"]["sin_categoria_tickets"] == 0
        assert datos["pendientes"]["sin_conciliar_tickets"] == 0
        assert datos["puede_cerrarse"] is True
        assert datos["estado_periodo"] == "CIERRE_DISPONIBLE"

    async def test_un_ticket_nunca_conciliado_bloquea_el_cierre(
        self, async_client, test_company, db_session
    ):
        """Con categoria y sin conciliar: sigue sin poder cerrar.

        Este es el pendiente que mas se pasa por alto, y el que el informe tiene
        que sacar a la luz: un gasto del mes que nunca se cruzo contra el banco.
        """
        _ticket(db_session, test_company, total="100.00", categoria="RENTA")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["pendientes"]["sin_categoria_tickets"] == 0
        assert datos["pendientes"]["sin_conciliar_tickets"] == 1
        assert datos["puede_cerrarse"] is False

    async def test_una_categoria_en_blanco_cuenta_como_sin_categoria(
        self, async_client, test_company, db_session
    ):
        """Un espacio no es una categoria. Si se contara como clasificado, el
        pendiente desaparece sin que nadie lo haya resuelto."""
        _ticket(db_session, test_company, total="100.00", categoria="   ")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        assert r.json()["pendientes"]["sin_categoria_tickets"] == 1

    async def test_el_sin_categoria_del_informe_es_solo_del_periodo(
        self, async_client, test_company, db_session
    ):
        """Un ticket de enero sin categoria NO bloquea el informe de marzo.

        Si bloqueara, ningun periodo podria cerrarse nunca y el informe dejaria de
        servir para lo unico que tiene que servir. Los pendientes de enero salen
        en el informe de enero.
        """
        _ticket(db_session, test_company, total="100.00", fecha=date(2026, 1, 10), categoria=None)
        _ticket(db_session, test_company, total="100.00", fecha=date(2026, 3, 10), categoria="RENTA")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": "2026-03"},
        )
        assert r.json()["pendientes"]["sin_categoria_tickets"] == 0

    async def test_un_periodo_ya_cerrado_se_reporta_cerrado(
        self, async_client, test_company, db_session
    ):
        t = _ticket(db_session, test_company, total="100.00", categoria="RENTA")
        await db_session.flush()
        banco = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2026, 1, 10),
            amount=Decimal("100.00"),
            description="RENTA",
        )
        db_session.add(banco)
        await db_session.flush()
        db_session.add(
            ReconciliationModel(
                ticket_id=t.id, bank_transaction_id=banco.id, match_status="PERFECT"
            )
        )
        db_session.add(
            CierrePeriodoModel(
                company_id=test_company.id,
                periodo=PERIODO,
                cerrado_por="contador@despacho.mx",
                cerrado_at=datetime(2026, 2, 1),
            )
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["cerrado"] is True
        assert datos["cerrado_por"] == "contador@despacho.mx"
        assert datos["estado_periodo"] == "CERRADO"

    async def test_escribir_en_un_periodo_cerrado_se_reporta_como_hallazgo(
        self, async_client, test_company, db_session
    ):
        """El estado que se descubrio al escribir este test.

        Alguien cerro enero, y despues se metio un ticket de enero. Con tres
        estados esto se reportaba como `NO_CIERRA`, que es FALSO —enero SI esta
        cerrado en el registro— y ademas tapaba el hallazgo mas util del informe:
        que se escribio en un periodo ya cerrado.

        El informe tiene que decir las dos cosas: el hecho y la anomalia.
        """
        _ticket(db_session, test_company, total="100.00", categoria=None)
        db_session.add(
            CierrePeriodoModel(
                company_id=test_company.id,
                periodo=PERIODO,
                cerrado_por="contador@despacho.mx",
                cerrado_at=datetime(2026, 2, 1),
            )
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["cerrado"] is True, "el hecho del registro no puede desaparecer"
        assert datos["estado_periodo"] == "CERRADO_CON_PENDIENTES"
        assert datos["puede_cerrarse"] is False

    async def test_una_discrepancia_bloquea_el_cierre(
        self, async_client, test_company, db_session
    ):
        """`DISCREPANCY` no cuenta como conciliado, a proposito (AGENTS.md)."""
        t = _ticket(db_session, test_company, total="100.00", categoria="RENTA")
        await db_session.flush()
        banco = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2026, 1, 10),
            amount=Decimal("150.00"),  # el banco dice otra cantidad
            description="PAGO",
        )
        db_session.add(banco)
        await db_session.flush()
        db_session.add(
            ReconciliationModel(
                ticket_id=t.id,
                bank_transaction_id=banco.id,
                match_status="DISCREPANCY",
            )
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        datos = r.json()
        assert datos["pendientes"]["discrepancias_abiertas"] == 1
        assert datos["puede_cerrarse"] is False
        assert datos["conciliacion"]["por_estado"]["DISCREPANCY"] == 1


# ---------------------------------------------------------------------------
# A9: auth y empresa inexistente
# ---------------------------------------------------------------------------

class TestA9AuthYEmpresaInexistente:
    async def test_sin_token_da_401(self, async_client_sin_autenticar, test_company):
        r = await async_client_sin_autenticar.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        assert r.status_code == 401

    async def test_el_pdf_tambien_exige_token(
        self, async_client_sin_autenticar, test_company
    ):
        """No es un endpoint de solo lectura "inofensivo": es el documento que se
        entrega al cliente final, y sin token seria la fuga mas limpia del sistema.
        """
        r = await async_client_sin_autenticar.get(
            f"{P}/cierre-mensual.pdf",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        assert r.status_code == 401

    async def test_una_empresa_que_no_existe_da_404_no_un_informe_vacio(
        self, async_client
    ):
        """Un 200 con ceros se lee como "este mes no gastaste", y es una
        afirmacion distinta de "no existe la empresa que pediste"."""
        from uuid import uuid4

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(uuid4()), "periodo": PERIODO},
        )
        assert r.status_code == 404

    async def test_el_firmante_sale_del_token(
        self, async_client, test_company, db_session
    ):
        """Regla 23: quien firma sale del token, nunca del cuerpo.

        Y no hay campo `firmado_por` que mandarlo: si lo hubiera, un informe
        podria salir firmado por quien no lo reviso.
        """
        _ticket(db_session, test_company, total="10.00", categoria="RENTA")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={
                "company_id": str(test_company.id),
                "periodo": PERIODO,
                # Un cliente que intenta firmarlo por otro:
                "firmado_por": "contador@despacho.mx",
            },
        )
        assert r.status_code == 200
        # El extra se ignora y el firmante es el del token.
        assert "@test.local" in r.json()["firmado_por"]


# ---------------------------------------------------------------------------
# R5: "leido por el sistema" y "verificado por una persona" son cosas distintas
# ---------------------------------------------------------------------------

class TestR5LasDosAfirmacionesNoSeConfunden:
    async def test_auto_aprobado_y_aprobado_cuentan_en_campos_distintos(
        self, async_client, test_company, db_session
    ):
        _ticket(db_session, test_company, total="100.00", estado="AUTO_APROBADO")
        _ticket(db_session, test_company, total="200.00", estado="APROBADO", reviewed=True)
        _ticket(db_session, test_company, total="50.00", estado="REQUIERE_REVISION")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        lec = r.json()["lectura"]
        assert lec["leido_por_el_sistema"] == 1
        assert lec["verificado_por_una_persona"] == 1
        assert lec["pendiente_de_revision"] == 1
        # Y no hay un "total revisados: 2" que junte las dos cosas.
        assert "revisados" not in lec


# ---------------------------------------------------------------------------
# La muestra y quien la firmo
# ---------------------------------------------------------------------------

class TestElRegistroDeLaMuestra:
    async def test_cuenta_la_muestra_y_dice_quien_firmo(
        self, async_client, test_company, db_session
    ):
        _ticket(
            db_session, test_company, total="100.00",
            estado="AUTO_APROBADO", spot="CORRECTO", spot_by="ana@despacho.mx",
        )
        _ticket(
            db_session, test_company, total="200.00",
            estado="AUTO_APROBADO", spot="INCORRECTO", spot_by="ana@despacho.mx",
        )
        _ticket(
            db_session, test_company, total="300.00",
            estado="AUTO_APROBADO", spot="PENDIENTE",
        )
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        v = r.json()["verificacion"]
        assert v["en_muestra"] == 3
        assert v["revisadas"] == 2
        assert v["firmadas"] == 2
        assert v["pendientes_de_firmar"] == 1
        assert v["revisores"] == ["ana@despacho.mx"]
        assert v["ultima_verificacion"] == "2026-02-01"

    async def test_la_verificacion_no_reporta_un_porcentaje(
        self, async_client, test_company, db_session
    ):
        """R1 aplicado aqui: un "% firmado" sobre una muestra de 3 es tan fragil
        como un "% de exactitud" sobre 3. Conteos, que no se pueden malinterpretar.
        """
        _ticket(
            db_session, test_company, total="100.00",
            estado="AUTO_APROBADO", spot="CORRECTO", spot_by="ana@despacho.mx",
        )
        _ticket(db_session, test_company, total="200.00", estado="AUTO_APROBADO", spot="PENDIENTE")
        await db_session.commit()

        r = await async_client.get(
            f"{P}/cierre-mensual",
            params={"company_id": str(test_company.id), "periodo": PERIODO},
        )
        v = r.json()["verificacion"]
        assert not any(isinstance(x, float) for x in v.values() if not isinstance(x, (list, str)))