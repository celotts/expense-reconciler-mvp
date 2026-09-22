"""
AI Document Extraction Service - Extract structured data from invoices/receipts using LLM
"""
import json
import base64
import io
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date

from pdfplumber import open as pdf_open
from PIL import Image

from app.services.ai_client import ai_client, AIResponse
from app.core.config import settings


@dataclass
class ExtractedInvoice:
    """Structured invoice data extracted by AI"""
    provider_name: str = "UNKNOWN"
    provider_tax_id: Optional[str] = None
    provider_address: Optional[str] = None
    receiver_name: Optional[str] = None
    receiver_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_series: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: str = "MXN"
    exchange_rate: Optional[float] = None
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    tax_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    total: Decimal = Decimal("0")
    payment_method: Optional[str] = None
    payment_terms: Optional[str] = None
    items: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 0.0
    extraction_method: str = "llm"


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

Devuelve EXACTAMENTE este formato JSON. Los campos van SIEMPRE en este orden:
{"provider_name": "Nombre del emisor", "provider_tax_id": "RFC del emisor", "invoice_date": "YYYY-MM-DD", "currency": "MXN", "subtotal": 123.45, "tax_amount": 19.75, "total": 143.20, "payment_method": "EFECTIVO", "items": [{"description": "Producto", "quantity": 1, "unit_price": 123.45, "total": 123.45}], "raw_text": "Texto completo extraido", "confidence": 0.95}

Para CFDI agrega si están visibles: provider_address, receiver_name, receiver_tax_id, invoice_number, invoice_series, due_date, exchange_rate, tax_breakdown, payment_terms.
Para TICKETS SIMPLES (no CFDI): omite campos CFDI y usa payment_method "EFECTIVO", "TARJETA" o "TRANSFERENCIA".

Devuelve SOLO JSON válido. Sin markdown, sin explicaciones, sin campos adicionales.
"""


class AIExtractor:
    """Extract structured invoice data using LLM"""

    def __init__(self):
        self.enabled = settings.AI_ENABLED
        # Detect provider from ai_client
        self._provider = getattr(ai_client, "_provider", "openai")
        if hasattr(self._provider, "value"):
            self._provider = self._provider.value

    @property
    def _chat_model(self) -> str:
        """Model used for text-only extraction."""
        if self._provider == "ollama":
            return settings.OLLAMA_MODEL
        return settings.OPENAI_MODEL

    @property
    def _vision_model(self) -> str:
        """Model used for vision extraction."""
        if self._provider == "ollama":
            return settings.OLLAMA_MODEL
        return "gpt-4o"

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

        # Downscale large images before sending: speeds up local vision models a lot
        try:
            from PIL import Image as PILImage
            pil_img = PILImage.open(io.BytesIO(image_bytes))
            pil_img = pil_img.convert("RGB")
            max_w = 1600
            if pil_img.width > max_w:
                new_h = int(pil_img.height * max_w / pil_img.width)
                pil_img = pil_img.resize((max_w, new_h), PILImage.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            image_bytes = buf.getvalue()
        except Exception:
            pass

        b64 = base64.b64encode(image_bytes).decode()
        return await self._extract_from_vision(b64, "image/jpeg")

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
        """Extract using text-only LLM call, with retries for invalid model output."""
        last_result: Optional[ExtractedInvoice] = None
        for _attempt in range(3):
            response = await ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": INVOICE_EXTRACTION_PROMPT},
                    {"role": "user", "content": f"Texto del documento ({source}):\n\n{text[:8000]}"},
                ],
                model=self._chat_model,
                temperature=settings.AI_TEMPERATURE,
                max_tokens=settings.AI_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            result = self._parse_ai_response(response, source)
            if result.extraction_method != f"{source}_error":
                return result
            last_result = result
        return last_result or self._fallback_extraction(text.encode())

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
            model=self._vision_model,
            temperature=settings.AI_TEMPERATURE,
            max_tokens=settings.AI_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        return self._parse_ai_response(response, "vision")

    async def _extract_from_vision(self, b64_image: str, mime_type: str) -> ExtractedInvoice:
        """Extract from single base64 image, with retries for invalid model output."""
        last_result: Optional[ExtractedInvoice] = None
        for _attempt in range(2):
            response = await ai_client.chat_completion(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": INVOICE_EXTRACTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}", "detail": "high"}},
                    ]
                }],
                model=self._vision_model,
                temperature=settings.AI_TEMPERATURE,
                max_tokens=settings.AI_MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            result = self._parse_ai_response(response, "vision")
            if result.extraction_method != "vision_error":
                return result
            last_result = result
        return last_result or self._fallback_extraction(b64_image.encode())

    def _parse_ai_response(self, response: AIResponse, method: str) -> ExtractedInvoice:
        """Parse and validate AI response, tolerating imperfect model output."""
        raw = response.content.strip()
        data: Dict[str, Any] = {}
        try:
            # Strip markdown code fences if present (```json ... ```)
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            # Find the first {...} and parse it (tolerant of trailing text)
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(raw[start : end + 1])
        except Exception:
            data = {}

        # If JSON parsing failed, salvage individual fields with regex
        if not data or "provider_name" not in data:
            import re
            salvaged: Dict[str, Any] = {}
            m = re.search(r'"provider_name"\s*:\s*"([^"]+)"', raw)
            if m:
                salvaged["provider_name"] = m.group(1)
            m = re.search(r'"provider_tax_id"\s*:\s*(null|"[^"]*")', raw)
            if m and m.group(1) != "null":
                salvaged["provider_tax_id"] = m.group(1).strip('"')
            m = re.search(r'"invoice_date"\s*:\s*(null|"[^"]*")', raw)
            if m and m.group(1) != "null":
                salvaged["invoice_date"] = m.group(1).strip('"')
            m = re.search(r'"invoice_number"\s*:\s*(null|"[^"]*")', raw)
            if m and m.group(1) != "null":
                salvaged["invoice_number"] = m.group(1).strip('"')
            m = re.search(r'"currency"\s*:\s*"([^"]+)"', raw)
            if m:
                salvaged["currency"] = m.group(1)
            for field in ["subtotal", "tax_amount", "total"]:
                m = re.search(rf'"{field}"\s*:\s*"?([\d,]+\.?\d*)"?', raw)
                if m:
                    salvaged[field] = m.group(1)
            m = re.search(r'"raw_text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            if m:
                salvaged["raw_text"] = m.group(1).encode().decode("unicode_escape")
            if salvaged:
                data = salvaged

        try:
            # Coerce "null"/"NULL"/""/None yields real None for nullable fields
            def _clean_null(v: Any) -> Any:
                if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", ""):
                    return None
                return v

            nullable = [
                "provider_tax_id", "provider_address", "receiver_name", "receiver_tax_id",
                "invoice_number", "invoice_series", "invoice_date", "due_date",
                "exchange_rate", "payment_method", "payment_terms",
            ]
            for field in nullable:
                if field in data:
                    data[field] = _clean_null(data[field])

            # Convert Decimal fields (guard against malformed values)
            def _to_decimal(v: Any) -> Decimal:
                try:
                    if v is None:
                        return Decimal("0")
                    if isinstance(v, Decimal):
                        return v
                    if isinstance(v, float):
                        return Decimal(str(v))
                    s = str(v).replace(",", "").replace("$", "").strip()
                    return Decimal(s)
                except Exception:
                    return Decimal("0")

            for field in ["subtotal", "tax_amount", "total", "exchange_rate"]:
                if field in data and data[field] is not None:
                    data[field] = _to_decimal(data[field])
                elif field not in data:
                    data[field] = Decimal("0")

            # Convert items
            if not data.get("items"):
                data["items"] = []
            for item in data["items"]:
                if not isinstance(item, dict):
                    continue
                for f in ["quantity", "unit_price", "total", "tax_rate", "tax_amount"]:
                    if item.get(f) is not None:
                        item[f] = _to_decimal(item[f])

            # Convert tax_breakdown
            if not data.get("tax_breakdown"):
                data["tax_breakdown"] = []
            for tb in data["tax_breakdown"]:
                if not isinstance(tb, dict):
                    continue
                for f in ["rate", "amount"]:
                    if tb.get(f) is not None:
                        tb[f] = _to_decimal(tb[f])

            # Parse dates
            for field in ["invoice_date", "due_date"]:
                v = data.get(field)
                if isinstance(v, str):
                    try:
                        data[field] = date.fromisoformat(v.strip())
                    except ValueError:
                        data[field] = None

            if not data.get("provider_name"):
                data["provider_name"] = "UNKNOWN"
            if not data.get("currency"):
                data["currency"] = "MXN"
            if "raw_text" not in data or data["raw_text"] is None:
                data["raw_text"] = ""
            if "confidence" not in data or data["confidence"] is None:
                data["confidence"] = 0.0

            data["extraction_method"] = method if data else f"{method}_error"
            # Keep only fields understood by the dataclass (model may add extras)
            valid_fields = ExtractedInvoice.__dataclass_fields__.keys()
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return ExtractedInvoice(**filtered)

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
                raw_text=raw[:500],
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