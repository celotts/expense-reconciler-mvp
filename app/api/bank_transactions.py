from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import Escribir, UsuarioActual, resolver_empresa
from app.core.subida import leer_csv_bancario
from app.models.bank_transaction import BankTransactionModel
from app.schemas.bank_transaction import (
    BankTransactionCreate,
    BankTransactionResponse,
    BankTransactionUpdate,
)
from app.services.parser_service import BankTransactionRow, parse_bank_csv

router = APIRouter(tags=["Bank Transactions"])


@router.post("/", response_model=BankTransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_bank_transaction(
    transaction_in: BankTransactionCreate,
    usuario: UsuarioActual,
    _: Escribir,
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Create a new bank transaction manually.

    P0-1: la empresa del cuerpo se resuelve contra la del usuario. Un
    movimiento no se puede crear en otra empresa ni "sin empresa".
    """
    empresa = await resolver_empresa(usuario, transaction_in.company_id, db)
    
    transaction = BankTransactionModel(
        **{**transaction_in.model_dump(), "company_id": empresa}
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


# Los parametros de forma vienen vacios por omision, y vacio significa "detectalo".
#
# ## POR QUE NO SE DEJA UN DEFAULT FIJO
#
# MEDIDO el 2026-10-12: con `date_column: str = Form("fecha")` el backend recibia
# SIEMPRE `"fecha"`, aunque el cliente no mandara nada, y entonces no podia saber si
# el llamador habia pedido "fecha" explicitamente o si solo era el default. Con
# esa confusion el detector de sinonimos no podia actuar —porque la columna ya
# venia "fijada"— y el de separador tampoco.
#
# La distincion importa porque son dos preguntas:
#   - el cliente NO manda nada -> se detecta el archivo
#   - el cliente manda algo    -> se respeta, porque quien lo mando lo sabe
#
# Con `""` como default las dos quedan distinguibles sin cambiar el contrato de
# los clientes que SI mandan valores.
@router.post("/import-csv", response_model=list[BankTransactionRow])
async def import_bank_csv_preview(
    file: UploadFile = File(...),
    date_column: str = Form(""),
    amount_column: str = Form(""),
    description_column: str = Form(""),
    reference_column: str = Form(""),
    date_format: str = Form(""),
    decimal_separator: str = Form(","),
    thousands_separator: str = Form("."),
    encoding: str = Form("utf-8"),
) -> list[BankTransactionRow]:
    """Preview CSV import without saving to database.

    Sin parametros de forma, detecta separador, encabezados y formato de fecha.
    Ver `parse_bank_csv` y el comentario de los defaults vacios, arriba.
    """
    content = await leer_csv_bancario(file, file.filename or "")
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
    except ValueError as e:
        # Solo los ValueError propios del parser llegan aqui. Cualquier otra
        # excepcion (bug) sube como 500 y se registra en el log, no se refleja.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post(
    "/import-csv-and-create",
    response_model=list[BankTransactionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_bank_csv_and_create(
    usuario: UsuarioActual,
    _: Escribir,
    file: UploadFile = File(...),
    company_id: UUID = Form(...),
    # Defaults vacios, por la misma razon que en el preview: vacio = "detectalo".
    # Ver el comentario de `import_bank_csv_preview`.
    date_column: str = Form(""),
    amount_column: str = Form(""),
    description_column: str = Form(""),
    reference_column: str = Form(""),
    date_format: str = Form(""),
    decimal_separator: str = Form(","),
    thousands_separator: str = Form("."),
    encoding: str = Form("utf-8"),
    db: AsyncSession = Depends(get_db),
) -> list[BankTransactionModel]:
    """Parse CSV and create bank transactions in database."""
    # P0-1: el CSV entero cae en la empresa resuelta, no en la del formulario
    # sin mas. Un formulario con otra empresa es 404.
    empresa = await resolver_empresa(usuario, company_id, db)
    
    content = await leer_csv_bancario(file, file.filename or "")
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
    except ValueError as e:
        # Solo los ValueError propios del parser llegan aqui. Cualquier otra
        # excepcion (bug) sube como 500 y se registra en el log, no se refleja.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    
    transactions = []
    for row in parsed:
        transaction = BankTransactionModel(
            company_id=empresa,
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


@router.get("/", response_model=list[BankTransactionResponse])
async def list_bank_transactions(
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[BankTransactionModel]:
    """List bank transactions with optional company filter.

    P0-1: el listado es SIEMPRE de la empresa resuelta. Sin `company_id`
    vale la del usuario; con otra, 404.
    """
    empresa = await resolver_empresa(usuario, company_id, db)
    query = select(BankTransactionModel).where(
        BankTransactionModel.company_id == empresa
    )
    query = query.offset(skip).limit(limit).order_by(BankTransactionModel.transaction_date.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{transaction_id}", response_model=BankTransactionResponse)
async def get_bank_transaction(
    transaction_id: UUID,
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Get a bank transaction by ID."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
        )
    # P0-1: un movimiento de otra empresa no existe para este usuario
    # (404, no 403).
    empresa = await resolver_empresa(usuario, company_id, db)
    if transaction.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
        )
    return transaction


@router.patch("/{transaction_id}", response_model=BankTransactionResponse)
async def update_bank_transaction(
    transaction_id: UUID,
    transaction_in: BankTransactionUpdate,
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> BankTransactionModel:
    """Update a bank transaction."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
        )
    # P0-1: corregir un movimiento de otra empresa es 404, no 403.
    empresa = await resolver_empresa(usuario, company_id, db)
    if transaction.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
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
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a bank transaction."""
    result = await db.execute(select(BankTransactionModel).where(BankTransactionModel.id == transaction_id))
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
        )
    # P0-1: borrar un movimiento de otra empresa es 404, no 403.
    empresa = await resolver_empresa(usuario, company_id, db)
    if transaction.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe"
        )
    
    await db.delete(transaction)
    await db.commit()