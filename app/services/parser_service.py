import io
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
from pydantic import BaseModel


class BankTransactionRow(BaseModel):
    transaction_date: date
    amount: Decimal
    description: str
    reference: str | None = None


class TicketExtractionResult(BaseModel):
    provider_name: str
    provider_tax_id: str | None = None
    total_amount: Decimal
    tax_amount: Decimal = Decimal("0.00")
    expense_date: date
    category: str | None = None
    raw_text: str


def parse_bank_csv(
    file_content: bytes,
    date_column: str = "fecha",
    amount_column: str = "importe",
    description_column: str = "concepto",
    reference_column: str = "referencia",
    date_format: str = "%d/%m/%Y",
    decimal_separator: str = ",",
    thousands_separator: str = ".",
    encoding: str = "utf-8",
    separator: str = ",",
) -> list[BankTransactionRow]:
    """
    Parse a CSV file containing bank transactions.
    
    Args:
        file_content: Raw bytes of the CSV file
        date_column: Column name for transaction date
        amount_column: Column name for amount
        description_column: Column name for description
        reference_column: Column name for reference (optional)
        date_format: Date format string
        decimal_separator: Character used as decimal separator
        thousands_separator: Character used as thousands separator
        encoding: File encoding
        separator: Column separator character (default: comma)
    
    Returns:
        List of BankTransactionRow objects
    """
    df = pd.read_csv(
        io.BytesIO(file_content),
        encoding=encoding,
        thousands=thousands_separator,
        decimal=decimal_separator,
        sep=separator
    )
    
    df.columns = df.columns.str.strip().str.lower()
    
    required_columns = {date_column, amount_column, description_column}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    
    transactions = []
    for _, row in df.iterrows():
        try:
            transaction_date = pd.to_datetime(row[date_column], format=date_format).date()
            # pandas already handles decimal/thousands separators when reading
            # row[amount_column] is already a float, so convert directly
            amount = Decimal(str(row[amount_column]))
            description = str(row[description_column]).strip()
            reference = str(row[reference_column]).strip() if reference_column in df.columns and pd.notna(row[reference_column]) else None
            
            transactions.append(BankTransactionRow(
                transaction_date=transaction_date,
                amount=amount,
                description=description,
                reference=reference
            ))
        except Exception as e:
            raise ValueError(f"Error parsing row: {row.to_dict()}. Error: {e!s}")
    
    return transactions


def extract_ticket_data(
    file_content: bytes,
    file_type: str = "pdf",
    extraction_config: dict[str, Any] | None = None,
) -> TicketExtractionResult:
    """
    Extract structured data from a ticket/receipt file (PDF, image, or text).
    
    This is a placeholder implementation. In production, integrate with:
    - OCR services (AWS Textract, Google Vision, Azure Form Recognizer)
    - PDF parsing libraries (pdfplumber, PyMuPDF)
    - ML models for receipt parsing
    
    Args:
        file_content: Raw bytes of the file
        file_type: Type of file ('pdf', 'image', 'text')
        extraction_config: Optional configuration for extraction
    
    Returns:
        TicketExtractionResult with extracted data
    """
    if file_type == "pdf":
        return _extract_from_pdf(file_content)
    elif file_type == "image":
        return _extract_from_image(file_content)
    elif file_type == "text":
        return _parse_receipt_text(file_content.decode("utf-8"))
    else:
        raise ValueError(f"Unsupported file type: {file_type}")


def _extract_from_pdf(file_content: bytes) -> TicketExtractionResult:
    """Placeholder for PDF extraction logic."""
    import pdfplumber
    
    text = ""
    with pdfplumber.open(io.BytesIO(file_content)) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
    
    return _parse_receipt_text(text)


def _extract_from_image(file_content: bytes) -> TicketExtractionResult:
    """Placeholder for image OCR extraction logic."""
    return TicketExtractionResult(
        provider_name="Unknown Provider",
        provider_tax_id=None,
        total_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date.today(),
        category=None,
        raw_text="[Image OCR not implemented]"
    )


def _parse_receipt_text(text: str) -> TicketExtractionResult:
    """
    Parse receipt text to extract structured data.
    Handles Mexican number format: 1,234.56 (comma=thousands, dot=decimal)
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    provider_name = "Unknown Provider"
    total_amount = Decimal("0.00")
    tax_amount = Decimal("0.00")
    expense_date = date.today()
    provider_tax_id = None
    category = None
    
    import re
    
    def parse_mexican_number(num_str: str) -> Decimal:
        """Convert Mexican format '1,234.56' to Decimal."""
        cleaned = num_str.replace("$", "").replace("€", "").replace(" ", "")
        if "," in cleaned and "." in cleaned:
            comma_pos = cleaned.rfind(",")
            dot_pos = cleaned.rfind(".")
            if comma_pos < dot_pos:
                cleaned = cleaned.replace(",", "")
            else:
                cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned and cleaned.count(",") == 1 and len(cleaned.split(",")[-1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", "")
        return Decimal(cleaned)
    
    # First pass: identify emitter (provider) - usually in first 10 lines
    for i, line in enumerate(lines[:15]):
        # Look for RFC of emitter
        rfc_match = re.search(r"(?:rfc|nit|tax id)[\s:]*([A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3})", line, re.IGNORECASE)
        if rfc_match and provider_tax_id is None:
            provider_tax_id = rfc_match.group(1)
        
        # Look for provider name (skip headers like "FACTURA ELECTRÓNICA")
        if provider_name == "Unknown Provider" and i > 0:
            # Skip common headers
            skip_patterns = ["factura", "electrónica", "cfdi", "ticket", "comprobante", "recibo"]
            if not any(p in line.lower() for p in skip_patterns) and len(line) > 5:
                # Check if it looks like a company name (has spaces, reasonable length)
                if re.search(r"[A-Z]{3,}", line) and not re.match(r"^\d", line):
                    provider_name = line
    
    # Second pass: extract amounts and dates
    for line in lines:
        # Total - look for "TOTAL:" specifically (not subtotal)
        total_match = re.search(r"(?:^|\s)total[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
        if total_match and "subtotal" not in line.lower():
            try:
                total_amount = parse_mexican_number(total_match.group(1))
            except Exception:
                pass
        
        # IVA
        tax_match = re.search(r"(?:iva|tax|impuesto)(?:\s*\(\d+%\))?[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
        if tax_match:
            try:
                tax_amount = parse_mexican_number(tax_match.group(1))
            except Exception:
                pass
        
        # Date - prefer "Fecha Expedicion" or "Fecha:" patterns
        date_match = re.search(r"(?:fecha\s*(?:expedicion|emision)?)[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", line, re.IGNORECASE)
        if date_match:
            try:
                expense_date = pd.to_datetime(date_match.group(1), dayfirst=True).date()
            except Exception:
                pass
        elif re.search(r"\b\d{4}-\d{2}-\d{2}T", line):  # ISO format with time
            try:
                expense_date = pd.to_datetime(line.split("T")[0]).date()
            except Exception:
                pass
        
        # RFC - emitter (first one found in emitter section)
        rfc_match = re.search(r"(?:rfc|nit|tax id)[\s:]*([A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3})", line, re.IGNORECASE)
        if rfc_match and provider_tax_id is None:
            provider_tax_id = rfc_match.group(1)
    
    # Fallback: if no total found, try generic total match
    if total_amount == Decimal("0.00"):
        for line in reversed(lines):
            total_match = re.search(r"(?:total|importe)[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
            if total_match:
                try:
                    total_amount = parse_mexican_number(total_match.group(1))
                    break
                except Exception:
                    pass
    
    return TicketExtractionResult(
        provider_name=provider_name,
        provider_tax_id=provider_tax_id,
        total_amount=total_amount,
        tax_amount=tax_amount,
        expense_date=expense_date,
        category=category,
        raw_text=text
    )