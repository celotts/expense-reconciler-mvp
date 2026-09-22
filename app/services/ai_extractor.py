"""
AI Document Extraction Service - Extract structured data from invoices/receipts using LLM
"""
import json
import base64
import io
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from decimal import Decimal
from datetime import date

from pdfplumber import open as pdf_open
from PIL import Image

from app.services.ai_client import ai_client, AIResponse
from app.core.config import settings


@dataclass
class ExtractedInvoice:
    """Structured invoice data extracted by AI"""
    provider_name: str
    provider_tax_id: Optional[str]
    provider_address: Optional[str]
    receiver_name: Optional[str]
    receiver_tax_id: Optional[str]
    invoice_number: Optional[str]
    invoice_series: Optional[str]
    invoice_date: Optional[date]
    due_date: Optional[date]
    currency: str
    exchange_rate: Optional[float]
    subtotal: Decimal
    tax_amount: Decimal
    tax_breakdown: List[Dict[str, Any]]  # [{"tax_type": "IVA", "rate": 0.16, "amount": 160.00}]
    total: Decimal
    payment_method: Optional[str]
    payment_terms: Optional[str]
    items: List[Dict[str, Any]]  # [{"description": "...", "quantity": 1, "unit_price": 100, "total": 100}]
    raw_text: str
    confidence: float
    extraction_method: str


# System prompt for invoice extraction
INVOICE_EXTRACTION_PROMPT = """
Eres un experto en extracción de datos de facturas fiscales mexicanas (CFDI 4.0) y tickets de compra.
Extrae TODA la información estructurada posible del documento proporcionado.

REGLAS CRÍTICAS:
1. Montos: Siempre en formato decimal con 2 decimales (ej: 1500.00, no "1,500.00")
2. Fechas: Formato ISO YYYY-MM-DD
3. RFC: Formato exacto mexicano (12-13 chars: 3-4 letras + 6 dígitos + 3 homoclave)
4. Montos negativos NO existen en facturas (solo en notas de crédito)
5. IVA en México es 16% general, 8% frontera, 0% exento
6. Si no encuentras un dato, usa null (no inventes)

EXTRAE ESTOS CAMPOS:
{
  "provider_name": "Nombre del emisor (quien emite la factura)",
  "provider_tax_id": "RFC del emisor",
  "provider_address": "Domicilio fiscal del emisor",
  "receiver_name": "Nombre del receptor (quien recibe la factura)",
  "receiver_tax_id": "RFC del receptor",
  "invoice_number": "Folio/Número de factura",
  "invoice_series": "Serie de la factura",
  "invoice_date": "Fecha de emisión (YYYY-MM-DD)",
  "due_date": "Fecha de vencimiento si aplica",
  "currency": "Moneda (MXN, USD, etc.)",
  "exchange_rate": "Tipo de cambio si no es MXN",
  "subtotal": "Subtotal antes de impuestos",
  "tax_amount": "Total de impuestos (IVA + otros)",
  "tax_breakdown": [
    {"tax_type": "IVA", "rate": 0.16, "amount": 160.00},
    {"tax_type": "IEPS", "rate": 0.08, "amount": 80.00}
  ],
  "total": "Total de la factura",
  "payment_method": "PUE, PPD, etc.",
  "payment_terms": "Condiciones de pago",
  "items": [
    {
      "description": "Descripción del producto/servicio",
      "quantity": 1,
      "unit": "PZA/KG/M2/etc",
      "unit_price": 100.00,
      "total": 100.00,
      "tax_rate": 0.16,
      "tax_amount": 16.00
    }
  ],
  "raw_text": "Texto completo extraído para referencia",
  "confidence": 0.95,
  "extraction_method": "gpt-4o-vision"
}

Para TICKETS SIMPLES (no CFDI):
- Omite campos CFDI (serie, folio fiscal, etc.)
- Enfócate en: proveedor, RFC si visible, fecha, items, subtotal, IVA, total, forma de pago
- payment_method: "EFECTIVO", "TARJETA", "TRANSFERENCIA", etc.

Devuelve SOLO JSON válido. Sin markdown, sin explicaciones.
"""


class AIExtractor:
    """Extract structured invoice data using LLM"""

    def __init__(self):
        self.enabled = settings.AI_ENABLED

    async def extract_from_pdf(self, pdf_bytes: bytes) -> ExtractedInvoice:
        """Extract from PDF - tries text first, then images"""
        if not self.enabled:
            return self._fallback_extraction(pdf_bytes)

        # Try text extraction first (faster, cheaper)
        text = self._extract_pdf_text(pdf_bytes)
        if len(text) > 100:
            try:
                return await self._extract_from_text(text, "pdf_text")
            except Exception:
                pass

        # Fallback to vision (images)
        images = self._pdf_to_images(pdf_bytes)
        if images:
            return await self._extract_from_images(images)

        return self._fallback_extraction(pdf_bytes)

    async def extract_from_image(self, image_bytes: bytes, mime_type: str = "image/png") -> ExtractedInvoice:
        """Extract from image (photo of receipt/invoice)"""
        if not self.enabled:
            return self._fallback_extraction(image_bytes)

        b64 = base64.b64encode(image_bytes).decode()
        return await self._extract_from_vision(b64, mime_type)

    async def extract_from_text(self, text: str) -> ExtractedInvoice:
        """Extract from raw text (OCR output, etc.)"""
        if not self.enabled:
            return self._fallback_extraction(text.encode())
        return await self._extract_from_text(text, "raw_text")

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF using pdfplumber"""
        text_parts = []
        try:
            with pdf_open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
        except Exception:
            pass
        return "\n\n".join(text_parts)

    def _pdf_to_images(self, pdf_bytes: bytes, max_pages: int = 3) -> List[str]:
        """Convert PDF pages to base64 images"""
        images = []
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                images.append(base64.b64encode(buf.getvalue()).decode())
            doc.close()
        except Exception:
            pass
        return images

    async def _extract_from_text(self, text: str, source: str) -> ExtractedInvoice:
        """Extract using text-only LLM call"""
        response = await ai_client.chat_completion(
            messages=[
                {"role": "system", "content": INVOICE_EXTRACTION_PROMPT},
                {"role": "user", "content": f"Texto del documento ({source}):\n\n{text[:8000]}"},
            ],
            model=settings.OPENAI_MODEL,
            temperature=settings.AI_TEMPERATURE,
            max_tokens=settings.AI_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        return self._parse_ai_response(response, source)

    async def _extract_from_images(self, images: List[str]) -> ExtractedInvoice:
        """Extract using vision model from images"""
        content = [
            {"type": "text", "text": INVOICE_EXTRACTION_PROMPT},
        ]
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"}
            })

        response = await ai_client.chat_completion(
            messages=[{"role": "user", "content": content}],
            model="gpt-4o",  # Vision model
            temperature=settings.AI_TEMPERATURE,
            max_tokens=settings.AI_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        return self._parse_ai_response(response, "vision")

    async def _extract_from_vision(self, b64_image: str, mime_type: str) -> ExtractedInvoice:
        """Extract from single base64 image"""
        response = await ai_client.chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": INVOICE_EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}", "detail": "high"}},
                ]
            }],
            model="gpt-4o",
            temperature=settings.AI_TEMPERATURE,
            max_tokens=settings.AI_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        return self._parse_ai_response(response, "vision")

    def _parse_ai_response(self, response: AIResponse, method: str) -> ExtractedInvoice:
        """Parse and validate AI response"""
        try:
            data = json.loads(response.content)

            # Convert Decimal fields
            decimal_fields = ["subtotal", "tax_amount", "total", "exchange_rate"]
            for field in decimal_fields:
                if field in data and data[field] is not None:
                    data[field] = Decimal(str(data[field]))

            # Convert items
            if "items" in data and data["items"]:
                for item in data["items"]:
                    for f in ["quantity", "unit_price", "total", "tax_rate", "tax_amount"]:
                        if f in item and item[f] is not None:
                            item[f] = Decimal(str(item[f]))

            # Convert tax_breakdown
            if "tax_breakdown" in data and data["tax_breakdown"]:
                for tb in data["tax_breakdown"]:
                    for f in ["rate", "amount"]:
                        if f in tb and tb[f] is not None:
                            tb[f] = Decimal(str(tb[f]))

            # Parse dates
            for field in ["invoice_date", "due_date"]:
                if field in data and data[field]:
                    if isinstance(data[field], str):
                        try:
                            data[field] = date.fromisoformat(data[field])
                        except ValueError:
                            data[field] = None

            data["extraction_method"] = method
            return ExtractedInvoice(**data)

        except Exception as e:
            # Fallback if parsing fails
            return ExtractedInvoice(
                provider_name="ERROR_PARSING",
                provider_tax_id=None,
                provider_address=None,
                receiver_name=None,
                receiver_tax_id=None,
                invoice_number=None,
                invoice_series=None,
                invoice_date=None,
                due_date=None,
                currency="MXN",
                exchange_rate=None,
                subtotal=Decimal("0"),
                tax_amount=Decimal("0"),
                tax_breakdown=[],
                total=Decimal("0"),
                payment_method=None,
                payment_terms=None,
                items=[],
                raw_text=response.content[:500],
                confidence=0.0,
                extraction_method=f"{method}_error",
            )

    def _fallback_extraction(self, content: bytes) -> ExtractedInvoice:
        """Fallback when AI disabled or fails"""
        return ExtractedInvoice(
            provider_name="AI_DISABLED",
            provider_tax_id=None,
            provider_address=None,
            receiver_name=None,
            receiver_tax_id=None,
            invoice_number=None,
            invoice_series=None,
            invoice_date=None,
            due_date=None,
            currency="MXN",
            exchange_rate=None,
            subtotal=Decimal("0"),
            tax_amount=Decimal("0"),
            tax_breakdown=[],
            total=Decimal("0"),
            payment_method=None,
            payment_terms=None,
            items=[],
            raw_text=content[:500].decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)[:500],
            confidence=0.0,
            extraction_method="fallback",
        )


# Global instance
ai_extractor = AIExtractor()