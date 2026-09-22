from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.reconciliation import (
    ReconciliationMatchDetail,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
)


class ReconciliationResult:
    def __init__(
        self,
        ticket: TicketModel,
        bank_transaction: BankTransactionModel,
        match_status: str,
        amount_diff: Decimal,
        date_diff_days: int
    ):
        self.ticket = ticket
        self.bank_transaction = bank_transaction
        self.match_status = match_status
        self.amount_diff = amount_diff
        self.date_diff_days = date_diff_days


async def run_reconciliation(
    db: AsyncSession,
    request: ReconciliationRunRequest
) -> ReconciliationRunResponse:
    """
    Run the reconciliation engine to match tickets against bank transactions.
    
    Matching logic:
    - PERFECT: Exact amount match and date within tolerance
    - MANUAL: Amount match within tolerance but date outside tolerance, or vice versa
    - DISCREPANCY: Amount difference exceeds tolerance
    
    Args:
        db: Database session
        request: Reconciliation parameters
    
    Returns:
        ReconciliationRunResponse with match details and summary
    """
    tickets = await _get_unreconciled_tickets(db, request)
    bank_transactions = await _get_unreconciled_bank_transactions(db, request)

    matches: list[ReconciliationResult] = []
    matched_ticket_ids = set()
    matched_bank_ids = set()
    
    for ticket in tickets:
        best_match = _find_best_match(
            ticket,
            [bt for bt in bank_transactions if bt.id not in matched_bank_ids],
            request.amount_tolerance,
            request.date_tolerance_days
        )
        
        if best_match:
            bank_tx, match_status, amount_diff, date_diff = best_match
            matches.append(ReconciliationResult(ticket, bank_tx, match_status, amount_diff, date_diff))
            matched_ticket_ids.add(ticket.id)
            matched_bank_ids.add(bank_tx.id)
    
    await _save_reconciliations(db, matches)
    
    return _build_response(tickets, bank_transactions, matches, matched_ticket_ids, matched_bank_ids)


async def _get_unreconciled_tickets(
    db: AsyncSession, request: ReconciliationRunRequest
) -> list[TicketModel]:
    """Get tickets that haven't been reconciled yet."""
    query = select(TicketModel).where(TicketModel.company_id == request.company_id)
    
    if request.date_from:
        query = query.where(TicketModel.expense_date >= request.date_from)
    if request.date_to:
        query = query.where(TicketModel.expense_date <= request.date_to)
    
    subquery = select(ReconciliationModel.ticket_id).where(ReconciliationModel.ticket_id.is_not(None))
    query = query.where(TicketModel.id.not_in(subquery))
    
    result = await db.execute(query)
    return list(result.scalars().all())


async def _get_unreconciled_bank_transactions(
    db: AsyncSession, request: ReconciliationRunRequest
) -> list[BankTransactionModel]:
    """Get bank transactions that haven't been reconciled yet."""
    query = select(BankTransactionModel).where(
        and_(
            BankTransactionModel.company_id == request.company_id,
            BankTransactionModel.is_reconciled == False
        )
    )
    
    if request.date_from:
        query = query.where(BankTransactionModel.transaction_date >= request.date_from)
    if request.date_to:
        query = query.where(BankTransactionModel.transaction_date <= request.date_to)
    
    result = await db.execute(query)
    return list(result.scalars().all())


def _find_best_match(
    ticket: TicketModel,
    bank_transactions: list[BankTransactionModel],
    amount_tolerance: Decimal,
    date_tolerance_days: int,
) -> tuple[BankTransactionModel, str, Decimal, int] | None:
    """
    Find the best matching bank transaction for a ticket.
    
    Note: Ticket amounts are positive (expense totals), bank transaction amounts
    for expenses are negative. We compare absolute values.
    
    Returns:
        Tuple of (bank_transaction, match_status, amount_diff, date_diff_days) or None
    """
    best_match = None
    best_score = float('inf')
    
    for bank_tx in bank_transactions:
        # Compare absolute values: ticket total (positive) vs bank amount (negative for expenses)
        amount_diff = abs(abs(ticket.total_amount) - abs(bank_tx.amount))
        date_diff = abs((ticket.expense_date - bank_tx.transaction_date).days)
        
        if amount_diff > amount_tolerance:
            continue
        
        score = float(amount_diff) * 100 + date_diff
        
        if score < best_score:
            best_score = score
            best_match = (bank_tx, amount_diff, date_diff)
    
    if not best_match:
        return None
    
    bank_tx, amount_diff, date_diff = best_match

    if (
        amount_diff == Decimal(0)
        and date_diff <= date_tolerance_days
        or amount_diff <= amount_tolerance
        and date_diff <= date_tolerance_days
    ):
        match_status = "PERFECT"
    elif amount_diff <= amount_tolerance:
        match_status = "MANUAL"
    else:
        match_status = "DISCREPANCY"
    
    return (bank_tx, match_status, amount_diff, date_diff)


async def _save_reconciliations(
    db: AsyncSession, matches: list[ReconciliationResult]
) -> None:
    """Save reconciliation results to database."""
    for match in matches:
        reconciliation = ReconciliationModel(
            ticket_id=match.ticket.id,
            bank_transaction_id=match.bank_transaction.id,
            match_status=match.match_status
        )
        db.add(reconciliation)
        
        match.bank_transaction.is_reconciled = True
    
    await db.commit()


def _build_response(
    tickets: list[TicketModel],
    bank_transactions: list[BankTransactionModel],
    matches: list[ReconciliationResult],
    matched_ticket_ids: set,
    matched_bank_ids: set,
) -> ReconciliationRunResponse:
    """Build the response object."""
    perfect = sum(1 for m in matches if m.match_status == "PERFECT")
    manual = sum(1 for m in matches if m.match_status == "MANUAL")
    discrepancy = sum(1 for m in matches if m.match_status == "DISCREPANCY")
    
    match_details = [
        ReconciliationMatchDetail(
            ticket_id=m.ticket.id,
            ticket_provider=m.ticket.provider_name,
            ticket_amount=m.ticket.total_amount,
            ticket_date=m.ticket.expense_date,
            bank_transaction_id=m.bank_transaction.id,
            bank_description=m.bank_transaction.description,
            bank_amount=m.bank_transaction.amount,
            bank_date=m.bank_transaction.transaction_date,
            match_status=m.match_status,
            amount_diff=m.amount_diff,
            date_diff_days=m.date_diff_days
        )
        for m in matches
    ]
    
    return ReconciliationRunResponse(
        total_tickets=len(tickets),
        total_bank_transactions=len(bank_transactions),
        perfect_matches=perfect,
        manual_review=manual,
        discrepancies=discrepancy,
        unmatched_tickets=len(tickets) - len(matched_ticket_ids),
        unmatched_bank_transactions=len(bank_transactions) - len(matched_bank_ids),
        matches=match_details
    )