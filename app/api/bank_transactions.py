from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.schemas.bank_transaction import BankTransactionCreate, BankTransactionUpdate, BankTransactionResponse
from app.services.parser_service import parse_bank_csv, BankTransactionRow

router = APIRouter(tags=["Bank Transactions"])


@router.post("/", response_model=BankTransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_bank_transaction(
    transaction_in: BankTransactionCreate,
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Create a new bank transaction manually."""
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == transaction_in.company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    transaction = BankTransactionModel(**transaction_in.model_dump())
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.post("/import-csv", response_model=List[BankTransactionRow])
async def import_bank_csv_preview(
    file: UploadFile = File(...),
    date_column: str = Form("fecha"),
    amount_column: str = Form("importe"),
    description_column: str = Form("concepto"),
    reference_column: str = Form("referencia"),
    date_format: str = Form("%d/%m/%Y"),
    decimal_separator: str = Form(","),
    thousands_separator: str = Form("."),
    encoding: str = Form("utf-8")
) -> List[BankTransactionRow]:
    """Preview CSV import without saving to database."""
    content = await file.read()
    try:
        transactions = parse_bank_csv(
            content,
            date_column=date_column,
            amount_column=amount_column,
            description_column=description_column,
            reference_column=reference_column,
            date_format=date_format,
            decimal_separator=decimal_separator,
            thousands_separator=thousands_separator,
            encoding=encoding
        )
        return transactions
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse CSV: {str(e)}"
        )


@router.post("/import-csv-and-create", response_model=List[BankTransactionResponse], status_code=status.HTTP_201_CREATED)
async def import_bank_csv_and_create(
    file: UploadFile = File(...),
    company_id: UUID = Form(...),
    date_column: str = Form("fecha"),
    amount_column: str = Form("importe"),
    description_column: str = Form("concepto"),
    reference_column: str = Form("referencia"),
    date_format: str = Form("%d/%m/%Y"),
    decimal_separator: str = Form(","),
    thousands_separator: str = Form("."),
    encoding: str = Form("utf-8"),
    db: AsyncSession = Depends(get_db)
) -> List[BankTransactionModel]:
    """Parse CSV and create bank transactions in database."""
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    content = await file.read()
    try:
        parsed = parse_bank_csv(
            content,
            date_column=date_column,
            amount_column=amount_column,
            description_column=description_column,
            reference_column=reference_column,
            date_format=date_format,
            decimal_separator=decimal_separator,
            thousands_separator=thousands_separator,
            encoding=encoding
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse CSV: {str(e)}"
        )
    
    transactions = []
    for row in parsed:
        transaction = BankTransactionModel(
            company_id=company_id,
            transaction_date=row.transaction_date,
            amount=row.amount,
            description=row.description,
            reference=row.reference
        )
        db.add(transaction)
        transactions.append(transaction)
    
    await db.commit()
    for tx in transactions:
        await db.refresh(tx)
    
    return transactions


@router.get("/", response_model=List[BankTransactionResponse])
async def list_bank_transactions(
    company_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
) -> List[BankTransactionModel]:
    """List bank transactions with optional company filter."""
    query = select(BankTransactionModel)
    if company_id:
        query = query.where(BankTransactionModel.company_id == company_id)
    query = query.offset(skip).limit(limit).order_by(BankTransactionModel.transaction_date.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{transaction_id}", response_model=BankTransactionResponse)
async def get_bank_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Get a bank transaction by ID."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank transaction not found"
        )
    return transaction


@router.patch("/{transaction_id}", response_model=BankTransactionResponse)
async def update_bank_transaction(
    transaction_id: UUID,
    transaction_in: BankTransactionUpdate,
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Update a bank transaction."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank transaction not found"
        )
    
    update_data = transaction_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(transaction, field, value)
    
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bank_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a bank transaction."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank transaction not found"
        )
    
    await db.delete(transaction)
    await db.commit()