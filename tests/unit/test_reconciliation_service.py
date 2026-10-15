import pytest
from decimal import Decimal
from datetime import date
from uuid import UUID, uuid4
from pydantic import ValidationError
from sqlalchemy import select

from app.core.enums import MatchStatus
from app.services.reconciliation_service import (
    run_reconciliation,
    _find_best_match,
    _find_discrepancia,
    _menciona_proveedor,
)
from app.models.ticket import TicketModel
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.schemas.reconciliation import ReconciliationRunRequest


class TestReconciliationMatching:
    def test_find_best_match_perfect_amount_and_date(self):
        company_id = uuid4()
        ticket = TicketModel(
            id=uuid4(),
            company_id=company_id,
            provider_name="TEST",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15)
        )
        
        bank_tx = BankTransactionModel(
            id=uuid4(),
            company_id=company_id,
            amount=Decimal("-100.00"),
            transaction_date=date(2025, 1, 15),
            description="TEST"
        )
        
        result = _find_best_match(
            ticket, [bank_tx],
            amount_tolerance=Decimal("0.01"),
            date_tolerance_days=3
        )
        
        assert result is not None
        matched_tx, status, amt_diff, date_diff, _ = result
        assert status == "PERFECT"
        assert amt_diff == Decimal("0")
        assert date_diff == 0

    def test_find_best_match_amount_ok_date_outside_tolerance(self):
        company_id = uuid4()
        ticket = TicketModel(
            id=uuid4(),
            company_id=company_id,
            provider_name="TEST",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15)
        )
        
        bank_tx = BankTransactionModel(
            id=uuid4(),
            company_id=company_id,
            amount=Decimal("-100.00"),
            transaction_date=date(2025, 1, 25),  # 10 días después
            description="TEST"
        )
        
        result = _find_best_match(
            ticket, [bank_tx],
            amount_tolerance=Decimal("0.01"),
            date_tolerance_days=3
        )
        
        assert result is not None
        _, status, _, _, _ = result
        assert status == "MANUAL"

    def test_find_best_match_amount_difference_exceeds_tolerance(self):
        company_id = uuid4()
        ticket = TicketModel(
            id=uuid4(),
            company_id=company_id,
            provider_name="TEST",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15)
        )
        
        bank_tx = BankTransactionModel(
            id=uuid4(),
            company_id=company_id,
            amount=Decimal("-150.00"),  # Diferencia de 50
            transaction_date=date(2025, 1, 15),
            description="TEST"
        )
        
        result = _find_best_match(
            ticket, [bank_tx],
            amount_tolerance=Decimal("1.00"),
            date_tolerance_days=3
        )
        
        assert result is None  # No match porque diferencia > tolerancia

    def test_find_best_match_picks_closest_amount(self):
        company_id = uuid4()
        ticket = TicketModel(
            id=uuid4(),
            company_id=company_id,
            provider_name="TEST",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15)
        )
        
        bank_tx1 = BankTransactionModel(
            id=uuid4(),
            company_id=company_id,
            amount=Decimal("-100.50"),  # Diff 0.50
            transaction_date=date(2025, 1, 15),
            description="TEST1"
        )
        bank_tx2 = BankTransactionModel(
            id=uuid4(),
            company_id=company_id,
            amount=Decimal("-100.01"),  # Diff 0.01 - mejor match
            transaction_date=date(2025, 1, 15),
            description="TEST2"
        )
        
        result = _find_best_match(
            ticket, [bank_tx1, bank_tx2],
            amount_tolerance=Decimal("1.00"),
            date_tolerance_days=3
        )
        
        assert result is not None
        matched_tx, _, _, _, _ = result
        assert matched_tx.id == bank_tx2.id


class TestRunReconciliation:
    @pytest.mark.asyncio
    async def test_run_reconciliation_creates_matches(self, db_session, test_company):
        # Crear tickets
        ticket1 = TicketModel(
            company_id=test_company.id,
            provider_name="WALMART",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("16.00"),
            expense_date=date(2025, 1, 15),
            category="SUPERMERCADO"
        )
        ticket2 = TicketModel(
            company_id=test_company.id,
            provider_name="AMAZON",
            total_amount=Decimal("250.00"),
            tax_amount=Decimal("40.00"),
            expense_date=date(2025, 1, 16),
            category="ONLINE"
        )
        db_session.add_all([ticket1, ticket2])
        
        # Crear movimientos bancarios
        bank1 = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2025, 1, 15),
            amount=Decimal("-100.00"),
            description="PAGO WALMART SUPERCENTER",
            reference="REF001"
        )
        bank2 = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2025, 1, 16),
            amount=Decimal("-250.00"),
            description="COMPRA AMAZON MX",
            reference="REF002"
        )
        db_session.add_all([bank1, bank2])
        await db_session.commit()
        
        # Ejecutar conciliación
        request = ReconciliationRunRequest(
            company_id=test_company.id,
            amount_tolerance=Decimal("0.01"),
            date_tolerance_days=3
        )
        
        response = await run_reconciliation(db_session, request)
        
        # Verificar resultados
        assert response.total_tickets == 2
        assert response.total_bank_transactions == 2
        assert response.perfect_matches == 2
        assert response.manual_review == 0
        assert response.discrepancies == 0
        assert response.unmatched_tickets == 0
        assert response.unmatched_bank_transactions == 0
        assert len(response.matches) == 2
        
        # Verificar que se guardaron en BD
        result = await db_session.execute(select(ReconciliationModel))
        reconciliations = result.scalars().all()
        assert len(reconciliations) == 2
        
        # Verificar que bank transactions marcados como reconciliados
        result = await db_session.execute(select(BankTransactionModel))
        banks = result.scalars().all()
        assert all(b.is_reconciled for b in banks)

    @pytest.mark.asyncio
    async def test_run_reconciliation_with_manual_match(self, db_session, test_company):
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("500.00"),
            expense_date=date(2025, 1, 10)
        )
        db_session.add(ticket)
        
        bank = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2025, 1, 20),  # 10 días diferencia
            amount=Decimal("-500.00"),
            description="PAGO PROVEEDOR"
        )
        db_session.add(bank)
        await db_session.commit()
        
        request = ReconciliationRunRequest(
            company_id=test_company.id,
            amount_tolerance=Decimal("0.01"),
            date_tolerance_days=3  # Tolerancia menor a la diferencia
        )
        
        response = await run_reconciliation(db_session, request)
        
        assert response.manual_review == 1
        assert response.perfect_matches == 0
        assert response.matches[0].match_status == "MANUAL"
        assert response.matches[0].date_diff_days == 10

    @pytest.mark.asyncio
    async def test_run_reconciliation_unmatched_items(self, db_session, test_company):
        # Solo tickets, sin movimientos bancarios
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="SIN MATCH",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15)
        )
        db_session.add(ticket)
        await db_session.commit()
        
        request = ReconciliationRunRequest(company_id=test_company.id)
        response = await run_reconciliation(db_session, request)
        
        assert response.total_tickets == 1
        assert response.total_bank_transactions == 0
        assert response.unmatched_tickets == 1
        assert response.unmatched_bank_transactions == 0
        assert len(response.matches) == 0

def _ticket(company_id, monto="100.00", fecha=date(2025, 1, 15), proveedor="WALMART"):
    return TicketModel(
        id=uuid4(),
        company_id=company_id,
        provider_name=proveedor,
        total_amount=Decimal(monto),
        expense_date=fecha,
    )


def _mov(company_id, monto, fecha, descripcion, id_fijo=None):
    return BankTransactionModel(
        id=id_fijo or uuid4(),
        company_id=company_id,
        amount=Decimal(monto),
        transaction_date=fecha,
        description=descripcion,
    )


class TestElDesempateEsExplicito:
    """El criterio de eleccion se puede leer, y dos corridas con la misma
    entrada dan el mismo resultado."""

    def test_a_igual_monto_gana_la_fecha_mas_cercana(self):
        """El caso que motivó el arreglo: el mismo comercio y el mismo importe
        del mes pasado ganaba igual que el de hoy."""
        empresa = uuid4()
        ticket = _ticket(empresa)

        del_mes_pasado = _mov(empresa, "-100.00", date(2024, 12, 15), "PAGO WALMART")
        de_hoy = _mov(empresa, "-100.00", date(2025, 1, 15), "PAGO WALMART")

        result = _find_best_match(
            ticket, [del_mes_pasado, de_hoy], Decimal("0.01"), 3
        )

        assert result is not None
        assert result.bank_transaction.id == de_hoy.id
        assert result.date_diff_days == 0

    def test_el_monto_manda_sobre_la_fecha(self):
        """El importe manda, y por encima de cuanto pese la fecha.

        El codigo anterior multiplicaba la diferencia de monto por 100 y la
        sumaba a la de dias: un `100` que no significaba cien dias, sino "un
        peso es infinitamente mas importante que la fecha, mas o menos". Con
        la ventana de dias y la tolerancia que Cada quien configura, ese factor
        fijo dice cualquier cosa. Aqui no hay factor: el monto va primero en la
        tupla de comparacion, y la fecha solo desempata.
        """
        empresa = uuid4()
        ticket = _ticket(empresa)

        casi_exacto = _mov(empresa, "-100.50", date(2025, 1, 15), "PAGO WALMART")
        exacto_perdido = _mov(empresa, "-100.00", date(2024, 12, 6), "PAGO WALMART")

        result = _find_best_match(
            ticket, [exacto_perdido, casi_exacto], Decimal("1.00"), 3
        )

        assert result is not None
        assert result.bank_transaction.id == exacto_perdido.id
        # Y el estado dice MANUAL, no PERFECT: alguien tiene que mirarlo.
        assert result.match_status is MatchStatus.MANUAL

    def test_a_igual_monto_y_fecha_manda_la_descripcion(self):
        """Ultimo desempate: el nombre del proveedor en la descripcion del
        banco. Con monto y fecha identicos no hay nada mas que mirar."""
        empresa = uuid4()
        ticket = _ticket(empresa, proveedor="WALMART")

        # Los ids son FIJOS y en orden contrario al esperado. El ultimo
        # desempate de la clave es `str(id)`, asi que si el desempate por
        # descripcion desaparece, el id decide y con estos valores gana el
        # equivocado, siempre. Con ids aleatorios el test pasaria la mitad de
        # las veces y nadie se enteraria: verde intermitente es peor que rojo.
        sin_walmart = _mov(
            empresa, "-100.00", date(2025, 1, 15), "COMPRA TARJETA DEBITO",
            id_fijo=UUID("00000000-0000-0000-0000-000000000001"),
        )
        con_walmart = _mov(
            empresa, "-100.00", date(2025, 1, 15), "PAGO WALMART SUPERCENTER",
            id_fijo=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        )

        result = _find_best_match(ticket, [sin_walmart, con_walmart], Decimal("0.01"), 3)

        assert result is not None
        assert result.bank_transaction.id == con_walmart.id

    def test_el_orden_de_la_lista_no_decide(self):
        """La lista llega de la base, sin orden garantizado. Si el resultado
        dependiera del orden, dos corridas idénticas podrían dar tickets
        distintos y nadie lo notaria hasta un cierre que no cuadra."""
        empresa = uuid4()
        ticket = _ticket(empresa)

        a = _mov(empresa, "-100.00", date(2025, 1, 15), "PAGO WALMART")
        b = _mov(empresa, "-100.00", date(2025, 1, 15), "PAGO OTRA COSA")
        c = _mov(empresa, "-100.00", date(2025, 1, 15), "PAGO TERCEROS")

        primero = _find_best_match(ticket, [a, b, c], Decimal("0.01"), 3)
        invertido = _find_best_match(ticket, [c, b, a], Decimal("0.01"), 3)
        revuelto = _find_best_match(ticket, [b, c, a], Decimal("0.01"), 3)

        assert primero is not None and invertido is not None and revuelto is not None
        assert primero.bank_transaction.id == invertido.bank_transaction.id
        assert primero.bank_transaction.id == revuelto.bank_transaction.id

    def test_la_frontera_exacta_de_la_tolerancia_si_concilia(self):
        """"Dentro de la tolerancia" incluye la frontera. Si la comparacion
        fuera `>=`, una diferencia de exactamente $1.00 con tolerancia $1.00
        dejaria de conciliar, y el operador no tendria forma de saber por que:
        el numero que ve en pantalla es el mismo que la base rechazo."""
        empresa = uuid4()
        ticket = _ticket(empresa, monto="100.00")
        mov = _mov(empresa, "-101.00", date(2025, 1, 15), "PAGO WALMART")

        justo = _find_best_match(ticket, [mov], Decimal("1.00"), 3)
        fuera = _find_best_match(ticket, [mov], Decimal("0.99"), 3)

        assert justo is not None, "la diferencia igual a la tolerancia si entra"
        assert justo.amount_diff == Decimal("1.00")
        assert fuera is None, "un centavo por encima de la tolerancia, no"

    def test_la_fecha_no_descarta_un_comprobante_pagado_tarde(self):
        """Un pago 10 dias despues sigue siendo el pago. Antes se reportaba
        MANUAL, y eso se mantiene; lo que no puede pasar es que se pierda."""
        empresa = uuid4()
        ticket = _ticket(empresa)
        mov = _mov(empresa, "-100.00", date(2025, 1, 25), "PAGO WALMART")

        result = _find_best_match(ticket, [mov], Decimal("0.01"), 3)

        assert result is not None
        assert result.match_status is MatchStatus.MANUAL
        assert result.date_diff_days == 10

    def test_el_criterio_se_puede_leer(self):
        """La razon se expone en texto. Un numero de score no se puede
        discutir con el contador; esto si."""
        empresa = uuid4()
        ticket = _ticket(empresa)
        mov = _mov(empresa, "-100.00", date(2025, 1, 16), "PAGO WALMART SUPERCENTER")

        result = _find_best_match(ticket, [mov], Decimal("0.01"), 3)

        assert result is not None
        texto = result.criterio.texto()
        assert "monto exacto" in texto
        assert "1 dia de diferencia de fecha" in texto
        assert "proveedor" in texto


class TestLaDiscrepanciaExiste:
    """La categoria estaba declarada en el schema y pintada en la UI, y era
    inalcanzable: ningun candidato con el monto fuera de tolerancia llegaba a
    clasificarse."""

    def test_lejos_en_monto_y_en_fecha_no_hay_nada_que_reportar(self):
        """El limite del cambio: si no se parece en nada, sigue siendo un
        ticket sin movimiento. no se reporta ruido."""
        empresa = uuid4()
        ticket = _ticket(
            empresa, monto="100.00", fecha=date(2025, 1, 15), proveedor="WALMART"
        )
        mov = _mov(
            empresa, "-842.00", date(2024, 3, 2), "RENTA OFICINA ENERO"
        )

        resultado = _find_discrepancia(ticket, [mov], 3)

        assert resultado is None

    def test_mismo_dia_otro_monto_si_se_reporta(self):
        empresa = uuid4()
        ticket = _ticket(empresa, proveedor="TIENDA")
        mov = _mov(empresa, "-150.00", date(2025, 1, 15), "PAGO TIENDA")

        resultado = _find_discrepancia(ticket, [mov], 3)

        assert resultado is not None
        assert resultado.amount_diff == Decimal("50.00")
        assert resultado.date_diff_days == 0
        assert "50.00" in resultado.criterio.texto()

    def test_la_discrepancia_elige_el_mas_cercano_en_fecha(self):
        empresa = uuid4()
        ticket = _ticket(empresa, proveedor="TIENDA")

        lejos = _mov(empresa, "-180.00", date(2025, 1, 12), "PAGO TIENDA")
        cerca = _mov(empresa, "-150.00", date(2025, 1, 15), "PAGO TIENDA")

        resultado = _find_discrepancia(ticket, [lejos, cerca], 3)

        assert resultado is not None
        assert resultado.bank_transaction.id == cerca.id


class TestLaNormalizacionDeTextos:
    def test_compara_con_acentos_y_signos(self):
        empresa = uuid4()
        ticket = TicketModel(
            id=uuid4(),
            company_id=empresa,
            provider_name="Café Martínez S.A. de C.V.",
            total_amount=Decimal("100.00"),
            expense_date=date(2025, 1, 15),
        )
        mov = _mov(empresa, "-100.00", date(2025, 1, 15), "pago cafe martinez s a de c v")

        assert _menciona_proveedor(ticket.provider_name, mov.description) is True

    def test_una_mencion_parcial_no_cuenta_como_mencion(self):
        """Que aparezca UNA palabra del proveedor no es que aparezca el
        proveedor. Con `any` en vez de `all`, "PAGO WALMART" contaria como si
        fuera "WALMART SUPERCENTER" y el desempate final seria ruido."""
        empresa = uuid4()
        assert _menciona_proveedor("WALMART SUPERCENTER", "PAGO WALMART") is False
        assert _menciona_proveedor("WALMART SUPERCENTER", "PAGO WALMART SUPERCENTER") is True

    def test_un_token_muy_corto_no_cierra_el_despiste(self):
        assert _menciona_proveedor("DE", "PAGO DE SERVICIOS") is False

    def test_sin_nombre_util_no_hay_senal(self):
        assert _menciona_proveedor("Unknown Provider", "PAGO CUALQUIER COSA") is False


class TestElRecorridoEsReproducible:
    """El emparejamiento es voraz: el primer ticket que encuentra un movimiento
    lo toma. Por eso el orden en que se recorren los tickets ES parte de la
    decision, y tiene que estar escrito en la consulta y no en el plan de
    Postgres."""

    @pytest.mark.asyncio
    async def test_los_tickets_salen_en_orden_cronologico_aunque_se_inserten_al_reves(
        self, db_session, test_company
    ):
        from app.services.reconciliation_service import _get_unreconciled_tickets

        # Se insertan del mas nuevo al mas viejo, que es como caen cuando se
        # sube un lote de un mes entero.
        for dia in (15, 10, 20, 5):
            db_session.add(
                TicketModel(
                    company_id=test_company.id,
                    provider_name="PROVEEDOR",
                    total_amount=Decimal("10.00"),
                    expense_date=date(2025, 1, dia),
                )
            )
        await db_session.commit()

        tickets = await _get_unreconciled_tickets(
            db_session, ReconciliationRunRequest(company_id=test_company.id)
        )

        fechas = [t.expense_date for t in tickets]
        assert fechas == sorted(fechas), "el recorrido tiene que ir de mas antiguo a mas reciente"

    @pytest.mark.asyncio
    async def test_dos_tickets_del_mismo_importe_no_se_quedan_sin_movimiento(
        self, db_session, test_company
    ):
        """Dos tickets de $100 y dos movimientos de $100 el mismo dia. Cada uno
        se lleva el suyo, y el que decide es el nombre del proveedor en la
        descripcion, no el orden de las filas.

        Sin el desempate por descripcion, el primer ticket en recorrerse se
        se llevaba los dos: le tomaba el movimiento del otro y el segundo
        se quedaba sin nada, aunque los dos cuadravaban perfecto.
        """
        for proveedor in ("TIENDA UNO", "TIENDA DOS"):
            db_session.add(
                TicketModel(
                    company_id=test_company.id,
                    provider_name=proveedor,
                    total_amount=Decimal("100.00"),
                    expense_date=date(2025, 1, 15),
                )
            )
        for descripcion in ("PAGO TIENDA DOS", "PAGO TIENDA UNO"):
            db_session.add(
                BankTransactionModel(
                    company_id=test_company.id,
                    transaction_date=date(2025, 1, 15),
                    amount=Decimal("-100.00"),
                    description=descripcion,
                )
            )
        await db_session.commit()

        response = await run_reconciliation(
            db_session, ReconciliationRunRequest(company_id=test_company.id)
        )

        assert response.perfect_matches == 2, "los dos tienen que cuadrar"
        assert response.unmatched_tickets == 0
        assert response.unmatched_bank_transactions == 0

        # Y cada ticket quedo con el movimiento que menciona su nombre.
        pares = {
            (m.ticket_provider, m.bank_description) for m in response.matches
        }
        assert pares == {
            ("TIENDA UNO", "PAGO TIENDA UNO"),
            ("TIENDA DOS", "PAGO TIENDA DOS"),
        }


class TestElEnumYTElPatronNoSeDivergen:
    def test_el_patron_del_schema_armado_desde_el_enum(self):
        """El patron de `match_status` se construye con el enum. La unica
        forma de que dejen de coincidir es que alguien escriba el patron a
        mano otra vez, y este test es lo que avisa."""

        from app.schemas.reconciliation import PATRON_MATCH_STATUS

        for estado in MatchStatus:
            assert estado.value in PATRON_MATCH_STATUS, (
                f"{estado.value} esta en el enum pero el patron no lo acepta"
            )

    def test_el_patron_no_acepta_un_estado_que_no_existe(self):
        from app.schemas.reconciliation import ReconciliationBase

        with pytest.raises(ValidationError):
            ReconciliationBase(match_status="CASI_PERFECTO")

    def test_los_estados_conciliados_no_incluyen_la_discrepancia(self):
        """Lo que se exporta a CONTPAQI. Una discrepancia mandada al reporte
        seria un cuadre inventado."""
        from app.core.enums import MATCHED_STATUSES

        assert MatchStatus.PERFECT in MATCHED_STATUSES
        assert MatchStatus.MANUAL in MATCHED_STATUSES
        assert MatchStatus.DISCREPANCY not in MATCHED_STATUSES


class TestLoQueQuedaEscrito:
    """La respuesta HTTP se arma con los objetos en memoria. Lo que se escribe
    en la base es otra cosa, y es la que se lee al listar, al exportar y al
    hacer un cierre. Antes ningun test miraba esa columna: la mutacion que
    guarda `str(estado)` en vez de `estado.value` (que produce
    "MatchStatus.PERFECT") pasaba toda la suite."""

    @pytest.mark.asyncio
    async def test_el_estado_se_guarda_como_valor_y_no_como_nombre_de_clase(
        self, db_session, test_company
    ):
        db_session.add(
            TicketModel(
                company_id=test_company.id,
                provider_name="WALMART",
                total_amount=Decimal("100.00"),
                expense_date=date(2025, 1, 15),
            )
        )
        db_session.add(
            BankTransactionModel(
                company_id=test_company.id,
                transaction_date=date(2025, 1, 15),
                amount=Decimal("-100.00"),
                description="PAGO WALMART",
            )
        )
        await db_session.commit()

        await run_reconciliation(
            db_session, ReconciliationRunRequest(company_id=test_company.id)
        )

        result = await db_session.execute(select(ReconciliationModel))
        guardadas = result.scalars().all()

        assert len(guardadas) == 1
        assert guardadas[0].match_status == MatchStatus.PERFECT.value
        assert guardadas[0].match_status == "PERFECT"
        # El enum es un str, asi que la comparacion con el string pasa igual.
        # Lo que no debe aparecer es el nombre de la clase.
        assert "MatchStatus" not in guardadas[0].match_status
