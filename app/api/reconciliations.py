from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.accounting_mapping import (
    AccountingMappingCreate,
    AccountingMappingResponse,
)
from app.schemas.reconciliation import (
    ReconciliationCreate,
    ReconciliationResponse,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
)
from app.services.export_service import (
    export_generic,
    export_to_contpaqi,
    export_to_excel,
)
from app.services.reconciliation_service import run_reconciliation

router = APIRouter(tags=["Reconciliations"])


@router.post("/run", response_model=ReconciliationRunResponse)
async def run_reconciliation_engine(
    request: ReconciliationRunRequest,
    db: AsyncSession = Depends(get_db)
) -> ReconciliationRunResponse:
    """Run the automatic reconciliation engine."""
    return await run_reconciliation(db, request)


@router.get("/", response_model=list[ReconciliationResponse])
async def list_reconciliations(
    company_id: UUID | None = None,
    match_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[ReconciliationModel]:
    """List reconciliations with optional filters."""
    query = select(ReconciliationModel).options(
        selectinload(ReconciliationModel.ticket),
        selectinload(ReconciliationModel.bank_transaction)
    )
    
    if company_id:
        query = query.join(TicketModel).where(TicketModel.company_id == company_id)
    if match_status:
        query = query.where(ReconciliationModel.match_status == match_status)
    
    query = query.offset(skip).limit(limit).order_by(ReconciliationModel.matched_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{reconciliation_id}", response_model=ReconciliationResponse)
async def get_reconciliation(
    reconciliation_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> ReconciliationModel:
    """Get a reconciliation by ID."""
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction)
        )
        .where(ReconciliationModel.id == reconciliation_id)
    )
    reconciliation = result.scalar_one_or_none()
    if not reconciliation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reconciliation not found"
        )
    return reconciliation


@router.post("/", response_model=ReconciliationResponse, status_code=status.HTTP_201_CREATED)
async def create_reconciliation(
    reconciliation_in: ReconciliationCreate,
    db: AsyncSession = Depends(get_db)
) -> ReconciliationModel:
    """Create a manual reconciliation link."""
    if reconciliation_in.ticket_id:
        ticket_result = await db.execute(
            select(TicketModel).where(TicketModel.id == reconciliation_in.ticket_id)
        )
        if not ticket_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ticket not found"
            )
    
    if reconciliation_in.bank_transaction_id:
        bank_result = await db.execute(
            select(BankTransactionModel).where(BankTransactionModel.id == reconciliation_in.bank_transaction_id)
        )
        bank_tx = bank_result.scalar_one_or_none()
        if not bank_tx:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bank transaction not found"
            )
        if bank_tx.is_reconciled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bank transaction already reconciled"
            )
        bank_tx.is_reconciled = True
    
    reconciliation = ReconciliationModel(**reconciliation_in.model_dump())
    db.add(reconciliation)
    await db.commit()
    await db.refresh(reconciliation)
    
    # Reload with relationships for response
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction)
        )
        .where(ReconciliationModel.id == reconciliation.id)
    )
    return result.scalar_one()


@router.delete("/{reconciliation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reconciliation(
    reconciliation_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a reconciliation and mark bank transaction as unreconciled."""
    result = await db.execute(
        select(ReconciliationModel).where(ReconciliationModel.id == reconciliation_id)
    )
    reconciliation = result.scalar_one_or_none()
    if not reconciliation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reconciliation not found"
        )
    
    if reconciliation.bank_transaction_id:
        bank_result = await db.execute(
            select(BankTransactionModel).where(BankTransactionModel.id == reconciliation.bank_transaction_id)
        )
        bank_tx = bank_result.scalar_one_or_none()
        if bank_tx:
            bank_tx.is_reconciled = False
    
    await db.delete(reconciliation)
    await db.commit()


@router.get("/export/excel", response_class=Response)
async def export_reconciliations_excel(
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations to standard Excel format."""
    content = await export_to_excel(db, company_id, date_from, date_to, only_reconciled)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=conciliacion.xlsx"}
    )


@router.get("/export/contpaqi", response_class=Response)
async def export_reconciliations_contpaqi(
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    mapping_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations to CONTPAQI format."""
    content = await export_to_contpaqi(db, company_id, date_from, date_to, only_reconciled, mapping_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=contpaqi_polizas.xlsx"}
    )


@router.get("/export/generic", response_class=Response)
async def export_reconciliations_generic(
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    columns: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations with custom columns."""
    column_list = None
    if columns:
        column_list = [c.strip() for c in columns.split(",")]
    content = await export_generic(db, company_id, date_from, date_to, only_reconciled, columns=column_list)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export.xlsx"}
    )


@router.post("/mappings", response_model=AccountingMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_accounting_mapping(
    mapping_in: AccountingMappingCreate,
    db: AsyncSession = Depends(get_db)
) -> AccountingMappingModel:
    """Create an accounting mapping template."""
    mapping = AccountingMappingModel(**mapping_in.model_dump())
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.get("/mappings", response_model=list[AccountingMappingResponse])
async def list_accounting_mappings(
    company_id: UUID | None = None, db: AsyncSession = Depends(get_db)
) -> list[AccountingMappingModel]:
    """List accounting mappings."""
    query = select(AccountingMappingModel)
    if company_id:
        query = query.where(AccountingMappingModel.company_id == company_id)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/mappings/{mapping_id}", response_model=AccountingMappingResponse)
async def get_accounting_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> AccountingMappingModel:
    """Get an accounting mapping by ID."""
    result = await db.execute(select(AccountingMappingModel).where(AccountingMappingModel.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Accounting mapping not found"
        )
    return mapping