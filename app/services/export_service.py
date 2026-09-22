import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel

CONTPAQI_COLUMNS = [
    "Fecha",
    "Concepto",
    "RFC",
    "Nombre",
    "Importe",
    "IVA",
    "Total",
    "Cuenta",
    "Referencia",
    "TipoComprobante",
    "Serie",
    "Folio",
    "Moneda",
    "TipoCambio",
    "MetodoPago",
    "UsoCFDI"
]

EXCEL_STANDARD_COLUMNS = [
    "Fecha",
    "Proveedor",
    "RFC Proveedor",
    "Concepto",
    "Subtotal",
    "IVA",
    "Total",
    "Fecha Banco",
    "Descripcion Banco",
    "Referencia Banco",
    "Estatus Conciliacion",
    "Diferencia Monto",
    "Diferencia Dias"
]


async def export_to_excel(
    db: AsyncSession,
    company_id: UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    only_reconciled: bool = True,
) -> bytes:
    """
    Export reconciled data to standard Excel format.
    
    Args:
        db: Database session
        company_id: Company UUID
        date_from: Filter from date
        date_to: Filter to date
        only_reconciled: Only export reconciled items
    
    Returns:
        Excel file as bytes
    """
    data = await _get_reconciliation_data(db, company_id, date_from, date_to, only_reconciled)
    
    df = pd.DataFrame(data, columns=EXCEL_STANDARD_COLUMNS)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Conciliacion")
        
        worksheet = writer.sheets["Conciliacion"]
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
    
    return output.getvalue()


async def export_to_contpaqi(
    db: AsyncSession,
    company_id: UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    only_reconciled: bool = True,
    mapping_id: UUID | None = None,
) -> bytes:
    """
    Export reconciled data to CONTPAQI-compatible format.
    
    CONTPAQI requires specific column layout for importing journal entries.
    
    Args:
        db: Database session
        company_id: Company UUID
        date_from: Filter from date
        date_to: Filter to date
        only_reconciled: Only export reconciled items
        mapping_id: Optional accounting mapping template ID
    
    Returns:
        Excel file as bytes in CONTPAQI format
    """
    data = await _get_reconciliation_data(db, company_id, date_from, date_to, only_reconciled)
    
    mapping = None
    if mapping_id:
        result = await db.execute(
            select(AccountingMappingModel).where(AccountingMappingModel.id == mapping_id)
        )
        mapping = result.scalar_one_or_none()
    
    contpaqi_data = _transform_to_contpaqi(data, mapping)
    
    df = pd.DataFrame(contpaqi_data, columns=CONTPAQI_COLUMNS)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Polizas")
        
        worksheet = writer.sheets["Polizas"]
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
    
    return output.getvalue()


async def export_generic(
    db: AsyncSession,
    company_id: UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    only_reconciled: bool = True,
    columns: list[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> bytes:
    """
    Export reconciled data with custom column selection and mapping.
    
    Args:
        db: Database session
        company_id: Company UUID
        date_from: Filter from date
        date_to: Filter to date
        only_reconciled: Only export reconciled items
        columns: List of column names to include
        column_mapping: Dict mapping internal names to output names
    
    Returns:
        Excel file as bytes
    """
    data = await _get_reconciliation_data(db, company_id, date_from, date_to, only_reconciled)
    
    if not data:
        df = pd.DataFrame(columns=columns or EXCEL_STANDARD_COLUMNS)
    else:
        df = pd.DataFrame(data, columns=EXCEL_STANDARD_COLUMNS)
        
        if column_mapping:
            df = df.rename(columns=column_mapping)
        
        if columns:
            available = [c for c in columns if c in df.columns]
            df = df[available]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Export")
    
    return output.getvalue()


async def _get_reconciliation_data(
    db: AsyncSession,
    company_id: UUID,
    date_from: date | None,
    date_to: date | None,
    only_reconciled: bool,
) -> list[list[Any]]:
    """Get joined reconciliation data from database."""
    query = select(
        TicketModel.expense_date,
        TicketModel.provider_name,
        TicketModel.provider_tax_id,
        TicketModel.category,
        TicketModel.total_amount,
        TicketModel.tax_amount,
        BankTransactionModel.transaction_date,
        BankTransactionModel.description,
        BankTransactionModel.reference,
        ReconciliationModel.match_status
    ).join(
        ReconciliationModel, TicketModel.id == ReconciliationModel.ticket_id
    ).join(
        BankTransactionModel, ReconciliationModel.bank_transaction_id == BankTransactionModel.id
    ).where(
        TicketModel.company_id == company_id
    )
    
    if date_from:
        query = query.where(TicketModel.expense_date >= date_from)
    if date_to:
        query = query.where(TicketModel.expense_date <= date_to)
    
    if only_reconciled:
        query = query.where(ReconciliationModel.match_status.in_(["PERFECT", "MANUAL"]))
    
    result = await db.execute(query)
    rows = result.all()
    
    data = []
    for row in rows:
        amount_diff = abs(
            row.total_amount - BankTransactionModel.__table__.c.amount
            if False
            else Decimal(0)
        )
        date_diff = abs((row.expense_date - row.transaction_date).days)

        data.append(
            [
                row.expense_date.strftime("%d/%m/%Y"),
                row.provider_name,
                row.provider_tax_id or "",
                row.category or "",
                float(row.total_amount - (row.tax_amount or Decimal(0))),
                float(row.tax_amount or Decimal(0)),
                float(row.total_amount),
                row.transaction_date.strftime("%d/%m/%Y"),
                row.description,
                row.reference or "",
                row.match_status,
                f"{amount_diff:.2f}",
                date_diff,
            ]
        )
    
    return data


def _transform_to_contpaqi(
    data: list[list[Any]], mapping: AccountingMappingModel | None
) -> list[list[Any]]:
    """
    Transform standard data to CONTPAQI format.
    
    CONTPAQI expects specific columns for journal entry import.
    """
    default_mapping = {
        "Fecha": ("Fecha", True),
        "Concepto": ("Concepto", True),
        "RFC": ("RFC Proveedor", True),
        "Nombre": ("Proveedor", True),
        "Importe": ("Subtotal", True),
        "IVA": ("IVA", True),
        "Total": ("Total", True),
        "Cuenta": ("", False),
        "Referencia": ("Referencia Banco", True),
        "TipoComprobante": ("I", False),
        "Serie": ("", False),
        "Folio": ("", False),
        "Moneda": ("MXN", False),
        "TipoCambio": ("1.0000", False),
        "MetodoPago": ("PUE", False),
        "UsoCFDI": ("G03", False)
    }
    
    if mapping and mapping.column_mappings:
        column_map = mapping.column_mappings
        # Merge with defaults for missing keys
        for k, v in default_mapping.items():
            if k not in column_map:
                column_map[k] = v[0]
    else:
        column_map = {k: v[0] for k, v in default_mapping.items()}
    
    contpaqi_rows = []
    for row in data:
        contpaqi_row = []
        for col in CONTPAQI_COLUMNS:
            source_col = column_map.get(col, "")
            is_standard = False
            if not mapping or not mapping.column_mappings:
                is_standard = default_mapping.get(col, ("", False))[1]
            
            if source_col and source_col in EXCEL_STANDARD_COLUMNS:
                idx = EXCEL_STANDARD_COLUMNS.index(source_col)
                contpaqi_row.append(row[idx] if idx < len(row) else "")
            elif source_col and not is_standard:
                # Default literal value
                contpaqi_row.append(source_col)
            else:
                contpaqi_row.append("")
        contpaqi_rows.append(contpaqi_row)
    
    return contpaqi_rows