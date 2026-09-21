import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4
from sqlalchemy import select

from app.services.reconciliation_service import run_reconciliation, _find_best_match
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
        matched_tx, status, amt_diff, date_diff = result
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
        _, status, _, _ = result
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
        matched_tx, _, _, _ = result
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