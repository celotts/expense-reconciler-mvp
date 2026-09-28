import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.core.enums import UNKNOWN_PROVIDER, ConfidenceSource

# Mexican RFC: 3-4 letters (persona fisica/moral), 6 digits (YYMMDD), 3 alnum
# (homoclave). The letter group is LAZY so a 3-letter RFC like WAL910101XXX
# matches as a whole instead of swallowing 4 letters and shifting the groups.
RFC_LABEL_RE = re.compile(
    r"(?:rfc|nit|tax\s*id)\s*[:]?\s*([A-Z&Ñ]{3,4}?\d{6}[A-Z0-9]{3})\b",
    re.IGNORECASE,
)

# Line labels that describe a field, not the emitting business. A line starting
# with one of these is metadata, never a provider name.
# NOTE: only labels that are unambiguously field names belong here. Words like
# "tienda", "caja" or "consumidor" are common parts of real Mexican business
# names ("TIENDA GENERICA", "LA CASA DE LAS CAJAS") and must not be excluded.
FIELD_LABEL_RE = re.compile(
    r"^(?:rfc|nit|tax\s*id|fecha|hora|folio|serie|no\.?|factura|subtotal|total"
    r"|iva|impuesto|importe|monto|art[ií]culos?|concepto|cantidad|descripci[oó]n"
    r"|tel[eé]fono|tel\.?|direcci[oó]n|domicilio|referencia|metodo\s*de\s*pago)\b",
    re.IGNORECASE,
)

# Explicit issuer labels used by CFDI-style layouts. The business name is the
# VALUE of the line, not the whole line.
ISSUER_LABEL_RE = re.compile(
    r"^(?:emisor|proveedor|raz[oó]n\s*social|nombre(?:\s*comercial)?)\s*[:]\s*(.+)$",
    re.IGNORECASE,
)


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
    # None cuando el documento no trae fecha. Antes se ponia la de hoy, que es
    # una mentira con consecuencias: un gasto de marzo guardado como de
    # septiembre desaparece del cierre de marzo y aparece en el de septiembre.
    # El gate ya sabe tratar una fecha ausente (`date_missing`); lo que faltaba
    # era dejarle llegar el None.
    expense_date: date | None = None
    category: str | None = None
    raw_text: str
    # El parser por reglas no tiene modelo de confianza: no estima. Se deja
    # None y el gate lo trata como confianza media-baja, que es lo honesto.
    confidence: float | None = None
    # Subtotal del documento, si el documento lo trae. Permite verificar
    # subtotal + IVA == total en el gate, que es el check mas barato que hay.
    subtotal: Decimal | None = None
    # De donde salio lo que hay en estos campos. Sin esto, todo se guardaba
    # como `llm`, incluso un parseo de regex, y la columna miente sobre de
    # donde salio el dato. Sin esa verdad no se puede medir la exactitud de la
    # IA: se estaria promediando la IA con un regex que casi nunca falla.
    confidence_source: ConfidenceSource = ConfidenceSource.LLM


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
    """PDF con capa de texto, leido por reglas. Sin IA."""
    return _parse_receipt_text(extract_pdf_text(file_content))


def extract_pdf_text(file_content: bytes) -> str:
    """Texto de un PDF, pagina por pagina.

    Salen dos cosas y hay que distinguirlas: un PDF impreso (tiene capa de
    texto) y un PDF escaneado (es una imagen, aqui no hay nada que leer y
    sale cadena vacia). La distincion no se puede suponer por la extension:
    los dos son `.pdf`. Por eso `render_pdf_pages` existe y por eso quien
    llama tiene que mirar el resultado antes de decidir que hacer.
    """
    import pdfplumber

    partes: list[str] = []
    with pdfplumber.open(io.BytesIO(file_content)) as pdf:
        for pagina in pdf.pages:
            try:
                texto = pagina.extract_text()
            except Exception:
                # Una pagina que no se puede extraer no tumban al documento
                # entero. Un comprobante de 3 paginas con una rota sigue
                # siendo leible por las otras dos.
                texto = None
            if texto:
                partes.append(texto)
    return "\n".join(partes)


def render_pdf_pages(
    file_content: bytes,
    max_pages: int = 3,
    scale: float = 2.0,
) -> list[bytes]:
    """Convierte paginas de PDF en imagenes JPEG, para leerlas con vision.

    Es lo que hace falta con un PDF escaneado: no hay texto, hay que mirar.

    Se usa pypdfium2 y no PyMuPDF porque pypdfium2 ya viene como dependencia de
    pdfplumber. La version anterior importaba `fitz`, que no estaba
    instalado: la funcion devolvia una lista vacia y se tragaba la excepcion,
    asi que un PDF escaneado caia a `_fallback_extraction` sin decir por que.
    Un fallo silencioso en el punto exacto donde se decide si un documento
    necesita IA o no es el peor lugar posible para tragarse una excepcion.

    JPEG y no PNG: es una foto de papel, y lo que sale de aqui se manda a un
    modelo. PNG de una pagina escaneada pesa megabytes; JPEG, cientos de KB.
    """
    import pypdfium2 as pdfium

    documento = pdfium.PdfDocument(file_content)
    try:
        paginas = []
        for indice in range(min(len(documento), max_pages)):
            bitmap = documento[indice].render(scale=scale)
            imagen = bitmap.to_pil().convert("RGB")
            buffer = io.BytesIO()
            imagen.save(buffer, format="JPEG", quality=85)
            paginas.append(buffer.getvalue())
        return paginas
    finally:
        documento.close()


    return TicketExtractionResult(
        provider_name=UNKNOWN_PROVIDER,
        provider_tax_id=None,
        total_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        # Sin OCR todavia. La fecha se queda en None en vez de ponerse la de
        # hoy: un ticket sin leer no puede afirmar que se gasto hoy.
        expense_date=None,
        category=None,
        raw_text="[Image OCR not implemented]"
    )


def _parse_receipt_text(text: str) -> TicketExtractionResult:
    """
    Parse receipt text to extract structured data.
    Handles Mexican number format: 1,234.56 (comma=thousands, dot=decimal)
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    provider_name = UNKNOWN_PROVIDER
    total_amount = Decimal("0.00")
    tax_amount = Decimal("0.00")
    # Se queda en None si el documento no trae fecha. Poner la de hoy seria
    # fabricar un dato: el ticket parece fechado hoy y el cierre mensual lo
    # cuenta en el mes equivocado. El gate lo manda a la cola con el motivo
    # `date_missing`, que es lo que tiene que pasar.
    expense_date: date | None = None
    provider_tax_id = None
    category = None
    # Subtotal del documento, si aparece. El gate lo usa para verificar
    # `subtotal + IVA == total`, que no necesita comparar contra nada externo.
    subtotal: Decimal | None = None
    
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

    def is_provider_candidate(line: str) -> bool:
        """True if a line plausibly names the business that issued the receipt."""
        # Metadata line ("FECHA: 20/02/2025", "RFC: WAL...", "IVA (16%): ...")
        if FIELD_LABEL_RE.match(line):
            return False
        # Boilerplate document headers, anywhere in the line
        skip_patterns = ["factura electrónica", "factura electronica", "cfdi", "comprobante", "recibo"]
        if any(p in line.lower() for p in skip_patterns):
            return False
        # Too short to be a business name. Cuatro, no cinco: "OXXO" es un
        # minorista real y de los mas frecuentes del pais, y con corte en cinco
        # su ticket iba a la cola con `provider_missing` aunque el nombre
        # estuviera en la primera linea. Todo lo que se acepta como nombre tiene
        # que pasar antes por los filtros de label, numero y dos puntos, que
        # son los que de verdad descartan el ruido.
        if len(line) < 4:
            return False
        # A bare number is an amount, not a name
        if re.match(r"^[\s$€]*[\d.,]+$", line):
            return False
        # A colon in the middle means it is a "LABEL: value" row
        if ":" in line:
            return False
        # An item line ends with its price. "Cafe en grano 1kg 250.00" is a
        # product, not a supplier, and neither is "Av. Insurgentes Sur 1234".
        # De paso descarta el domicilio del emisor, que va justo debajo del
        # nombre y antes era candidato.
        ultimo = line.split()[-1] if line.split() else ""
        if re.fullmatch(r"[$€]?[\d.,]+", ultimo):
            return False
        # Dos maneras de parecer un negocio. Antes solo se aceptaba la primera,
        # y esa es la razon de que el proveedor se perdiera en la mayoria de
        # los comprobantes reales.
        #
        # (A) Mayusculas sostenidas: "GRUPO ACME SA DE CV", "OXXO", "TIENDA".
        # (B) Mayuscula inicial en varias palabras: "Tiendas Ramirez SA de CV",
        #     "Cafe La Esquina", "Ferreteria del Sur".
        #
        # (B) es como los emite cualquier sistema de facturacion que no arma el
        # ticket en un formato de 80 columnas en mayusculas. Con solo (A),
        # "Tiendas Ramirez SA de CV" no califica: sus unicas mayusculas
        # consecutivas son "SA" y "CV". El ticket caia a la cola con
        # `provider_missing` con el nombre del comercio a la vista, en la
        # primera linea, que es justo donde se busca.
        if re.search(r"[A-Z]{3,}", line):
            return True
        palabras = [
            p
            for p in re.findall(
                r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]+", line
            )
            if len(p) >= 3 and p[0].isupper()
        ]
        return len(palabras) >= 2

    # First pass: identify emitter (provider). It is usually the first non-empty
    # line, so the scan starts at i == 0 and stops at the first plausible name.
    for line in lines[:15]:
        rfc_match = RFC_LABEL_RE.search(line)
        if rfc_match and provider_tax_id is None:
            provider_tax_id = rfc_match.group(1)

        if provider_name != UNKNOWN_PROVIDER:
            continue

        # CFDI layouts label the emitter explicitly: "EMISOR: FARMACIAS DEL SUR"
        issuer_match = ISSUER_LABEL_RE.match(line)
        if issuer_match and is_provider_candidate(issuer_match.group(1)):
            provider_name = issuer_match.group(1).strip()
            continue

        if is_provider_candidate(line):
            provider_name = line

    # Second pass: extract amounts and dates
    for line in lines:
        # Subtotal. Antes no se extraia y el campo quedaba en None siempre.
        #
        # Sin esto, `subtotal_plus_tax_mismatch` - el check determinista mas
        # fuerte que hay, porque no depende de comparar el total contra nada
        # externo sino de la aritmetica interna del propio documento - no
        # corria nunca en la ruta de reglas. Solo lo podia activar el modelo.
        # Es decir: el camino mas barato y mas exacto era el unico sin la
        # verificacion, y por eso sus tickets no tenian como se comprobar que
        # estaban bien.
        #
        # El patron exige whitespace o inicio de linea antes de "subtotal", asi
        # que "SUBTOTAL" no se confunde con el "TOTAL" de la linea siguiente.
        subtotal_match = re.search(
            r"(?:^|\s)sub[\s-]?total[\s:]*[$€]?\s*([\d.,]+)",
            line, re.IGNORECASE,
        )
        if subtotal_match:
            try:
                subtotal = parse_mexican_number(subtotal_match.group(1))
            except (InvalidOperation, ValueError):
                pass

        # Total - look for "TOTAL:" specifically (not SUBTOTAL)
        total_match = re.search(r"(?:^|\s)total[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
        if total_match and "subtotal" not in line.lower():
            try:
                total_amount = parse_mexican_number(total_match.group(1))
            except (InvalidOperation, ValueError):
                pass

        # IVA - word-anchored so product names containing "iva"/"tax" don't match
        tax_match = re.search(r"\b(?:iva|tax|impuesto)\b(?:\s*\(\d+(?:[.,]\d+)?\s*%\))?[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
        if tax_match:
            try:
                tax_amount = parse_mexican_number(tax_match.group(1))
            except (InvalidOperation, ValueError):
                pass

        # Date - prefer "Fecha Expedicion" or "Fecha:" patterns
        date_match = re.search(r"(?:fecha\s*(?:expedicion|emision)?)[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", line, re.IGNORECASE)
        if date_match:
            try:
                expense_date = pd.to_datetime(date_match.group(1), dayfirst=True).date()
            except (ValueError, TypeError):
                pass
        else:
            # ISO format: alone, with a time component, or followed by anything.
            # `(?!\d)` instead of `\b` because "15T10:20" has no word boundary
            # between the date and the time ("5" and "T" are both word chars).
            iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})(?!\d)", line)
            if iso_match:
                try:
                    expense_date = pd.to_datetime(iso_match.group(1)).date()
                except (ValueError, TypeError):
                    pass

        # RFC - emitter (first one found wins)
        rfc_match = RFC_LABEL_RE.search(line)
        if rfc_match and provider_tax_id is None:
            provider_tax_id = rfc_match.group(1)
    
    # Fallback: if no total found, try generic total match
    if total_amount == Decimal("0.00"):
        for line in reversed(lines):
            total_match = re.search(r"(?:^|\s)(?:total|importe)[\s:]*[$€]?\s*([\d.,]+)", line, re.IGNORECASE)
            if total_match:
                try:
                    total_amount = parse_mexican_number(total_match.group(1))
                    break
                except (InvalidOperation, ValueError):
                    pass
    
    return TicketExtractionResult(
        provider_name=provider_name,
        provider_tax_id=provider_tax_id,
        total_amount=total_amount,
        tax_amount=tax_amount,
        expense_date=expense_date,
        category=category,
        raw_text=text,
        # Sin esto, el gate nunca recibe el subtotal de la ruta de reglas y el
        # check `subtotal_plus_tax_mismatch` no puede activarse nunca.
        subtotal=subtotal,
    )