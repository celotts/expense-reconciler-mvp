import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MATCHED_STATUSES
from app.core.texto import forzar_texto
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
        forzar_texto(worksheet)
        _ajustar_anchos(worksheet, df)
    
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
        forzar_texto(worksheet)
        _ajustar_anchos(worksheet, df)
    
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
        forzar_texto(writer.sheets["Export"])

    return output.getvalue()


def _ajustar_anchos(worksheet: Any, df: pd.DataFrame) -> None:
    """Ancho de cada columna, al contenido mas largo, con un tope.

    Estava en linea duplicada tres veces, con dos defectos que solo aparecen al
    ejecutar:

    - `chr(65 + idx)` da la letra de columna SOLO hasta la 26. En la 27 sale `[`,
      que no es un identificador de columna, y el archivo se guardaba con un
      `<col>` sin nombre. Hoy el archivo mas ancho tiene 16 columnas, asi que no
      muerde; el dia que se anada una, el fallo sale como un archivo que Excel
      rehace sin los anchos y sin avisar. `get_column_letter` no tiene ese
      tope.

    - El ancho se guardaba como `int`. El esquema OOXML lo declara como
      `double`, y un lector que separe el tipo -- openpyxl, y cualquier
      verificador que reabra el archivo -- falla con `expected float`. Excel es
      tolerante y lo abria igual, asi que el defecto pasaba desapercibido: el
      archivo se veia bien y no se podia volver a leer con herramientas. Salio
      al escribir un test que reabre lo que genera el exportador.
    """
    for idx, col in enumerate(df.columns):
        try:
            max_len = int(df[col].astype(str).map(len).max())
        except (TypeError, ValueError):
            # Una columna completamente vacia no tiene longitudes que comparar.
            # Sin esto, `max()` de una serie vacia lanza y se cae el export
            # entero por una columna que el usuario no lleno.
            max_len = 0
        ancho = min(max(max_len, len(str(col))) + 2, 50)
        worksheet.column_dimensions[get_column_letter(idx + 1)].width = float(ancho)


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
        BankTransactionModel.amount,
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
        # Sale de MATCHED_STATUSES y no de una lista escrita aqui. Una
        # discrepancia no se exporta como si estuviera cuadrada: mandarla a
        # CONTPAQI con el monto del banco es inventar el cuadre del mes.
        query = query.where(
            ReconciliationModel.match_status.in_([s.value for s in MATCHED_STATUSES])
        )
    
    result = await db.execute(query)
    rows = result.all()
    
    data = []
    for row in rows:
        # La diferencia se calcula con el importe del banco, que hace falta
        # pedirlo explicitamente en el SELECT de arriba.
        #
        # Antes estaba escrito como
        #     abs(row.total_amount - BankTransactionModel.__table__.c.amount
        #         if False else Decimal(0))
        # es decir, una rama muerta que se descartaba siempre: la columna
        # "Diferencia Monto" valia 0.00 para todas las filas, aunque el gasto y
        # el banco difirieran en cualquier cantidad. En un archivo de
        # conciliacion, una columna de diferencia que no diferencia dice
        # "todo cuadra" cuando puede que nada cuadre, y el contador se la
        # lleva puesta sin saber que el numero no se computo nunca.
        #
        # Se mantiene `Decimal` de punta a punta y se formatea al final. Un
        # `float` de 1100.10 - 1100.00 da 0.09999999999990905, y eso ahi si se
        # nota.
        amount_diff = abs(row.total_amount - row.amount)
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
        "Referencia": ("Referencia Banco", True),
        # Las ocho columnas siguientes NO tienen de donde sacarse.
        #
        # Antes cada una traia un literal fijo que se escribia en TODAS las
        # filas: TipoComprobante="I", Moneda="MXN", TipoCambio="1.0000",
        # MetodoPago="PUE", UsoCFDI="G03", y Cuenta/Serie/Folio vacias. El
        # problema no es que fueran un default: es que un default escrito en una
        # celda deja de ser un default y pasa a ser una afirmacion. Un
        # comprobante en USD salia con Moneda=MXN y TipoCambio=1.0000, sin
        # marca de error, y el contador lo subia a CONTPAQI creyendolo.
        #
        # Para un contador, una celda vacia dice "no lo se" y una celda con
        # 1.0000 dice "se que es 1.0000". La primera se corrige a mano; la
        # segunda no se ve. Es la regla 11 del contrato —lo que no se pudo
        # calcular se declara, no se rellena— aplicada a la exportacion.
        #
        # "Cuenta" tampoco: depende del catalogo contable y del regimen, y
        # poner una cuenta al azar es peor que no ponerla.
        #
        # Cuando el contador si las quiera, el camino es `AccountingMapping`,
        # que es el mecanismo de siempre y sigue funcionando igual: ahi poner
        # TipoCambio es una decision suya, documentada y revisable.
        "Cuenta": ("", False),
        "TipoComprobante": ("", False),
        "Serie": ("", False),
        "Folio": ("", False),
        "Moneda": ("", False),
        "TipoCambio": ("", False),
        "MetodoPago": ("", False),
        "UsoCFDI": ("", False),
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