"""
AI Document Extraction Service - Extract structured data from invoices/receipts using LLM
"""
import json
import base64
import io
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import date

from app.services.ai_client import AIProvider, ai_client, AIResponse
from app.core.config import settings

# HEIC/HEIF: el producto se define por "la foto del telefono" y un iPhone
# produce HEIC, asi que sin esto el primer usuario no puede subir su
# comprobante.
#
# El registro del decodificador vive en `app/core/heif.py` y este modulo solo lo
# consulta. Antes el `register_heif_opener()` estaba AQUI, y eso hacia que el
# soporte de HEIC dependiera de que otro modulo se importara primero: quien
# llegara al OCR sin pasar por `ai_extractor` —un script, un test, un refactor—
# no podia abrir una foto de iPhone. Ver el porque en `app/core/heif.py`.
#
# `pillow-heif` trae la libreria nativa en su propia wheel (no hace falta
# `apt-get`), y `register_heif_opener` engancha el decodificador de PIL para que
# `Image.open` acepte el formato como si fuera cualquier otro.
#
# Es opcional a proposito: si la dependencia no esta, `DISPONIBLE` queda en
# `False` y se sigue funcionando sin HEIC, que es como estaba antes.
from app.core.heif import DISPONIBLE as _HEIC_DISPONIBLE


def _a_jpeg(image_bytes: bytes) -> bytes | None:
    """Decodifica, reescala y pasa a JPEG. `None` si no se puede.

    El reescalado a 1600 px no es cosmetico: los modelos de vision locales
    (`moondream`) tardan mucho menos con la imagen pequena, y el total de un
    comprobante se lee igual a 1600 que a 4000.

    Devolver `None` en vez de un except silencioso es el punto de esta funcion.
    El llamador necesita distinguir "no pude abrirlo" de "no habia nada que
    abrir", y solo el segundo es una imagen valida de 0 bytes.

    Y el motivo del fallo se registra, porque "no se pudo leer la imagen" y "esta
    maquina no puede leer HEIC" piden acciones opuestas: la primera es un
    archivo malo, la segunda es un `pip install`. Sin esto, el segundo caso se
    reportaba como "muchas fotos fallan", que es como se escribe un sintoma en
    vez de una causa.

    `_HEIC_DISPONIBLE` es la unica razon por la que este modulo importa
    `app.core.heif`: importarlo ES el registro del decodificador, porque
    `register_heif_opener()` corre al importarse ese modulo. Sin esta linea,
    HEIC deja de funcionar en silencio y nadie lo ve hasta que alguien sube una
    foto de iPhone. Por eso no es un import que "no se usa": se consulta.
    """
    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        max_w = 1600
        if img.width > max_w:
            new_h = int(img.height * max_w / img.width)
            img = img.resize((max_w, new_h), PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as exc:
        if not _HEIC_DISPONIBLE:
            logger.warning(
                "no se pudo decodificar la imagen y pillow-heif no esta "
                "instalado; si es un HEIC de iPhone, ese es el motivo (%s)",
                exc,
            )
        else:
            logger.warning("no se pudo decodificar la imagen: %s", exc)
        return None


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
    # El IEPS, en pesos, y SEPARADO del IVA. No antes de esto: `tax_amount` era
    # "total de impuestos" en el prompt pero se guardaba y se exportaba bajo el
    # encabezado "IVA" (`export_service.CONTPAQI_COLUMNS`). Un comprobante con
    # IVA 8.14 e IEPS 8.59 llegaba al contador como IVA 16.73, sin marca de error.
    # Ver db/migrations/0012_el_impuesto_es_de_la_partida.sql.
    #
    # `None` y no `Decimal("0")`: `0.00` es "el papel dice que el IEPS es cero" y
    # `None` es "el papel no trae IEPS o no lo lei". El gate necesita la
    # diferencia, porque `subtotal + IVA == total` solo cuadra con el segundo.
    ieps_amount: Optional[Decimal] = None
    tax_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    total: Decimal = Decimal("0")
    payment_method: Optional[str] = None
    payment_terms: Optional[str] = None
    items: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 0.0
    extraction_method: str = "llm"


# System prompt for invoice extraction (text-based)
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

# Vision-specific prompt - more explicit about JSON-only output
VISION_EXTRACTION_PROMPT = """
Eres un experto en extracción de datos de facturas fiscales mexicanas (CFDI 4.0) y tickets de compra a partir de imágenes.
Analiza la imagen y extrae TODA la información estructurada posible.

REGLAS CRÍTICAS - DEBES SEGUIRLAS EXACTAMENTE:
1. SOLO devuelve JSON válido. NO agregues texto explicativo, NO uses markdown, NO agregues comentarios.
2. Montos: Siempre en formato decimal con 2 decimales (ej: 1500.00, no "1,500.00")
4. RFC: Formato exacto mexicano (12-13 chars: 3-4 letras + 6 dígitos + 3 homoclave)
5. Montos negativos NO existen en facturas (solo en notas de crédito)
6. IVA en México es 16% general, 8% frontera, 0% exento
7. Si no encuentras un dato, usa null (NO inventes, NO uses valores de ejemplo)

CAMPOS REQUERIDOS EN EL JSON:
- provider_name: nombre del proveedor/emisor (string o null)
- provider_tax_id: RFC del emisor (string o null)
- provider_address: dirección del emisor (string o null)
- receiver_name: nombre del receptor (string o null)
- receiver_tax_id: RFC del receptor (string o null)
- invoice_number: número de factura (string o null)
- invoice_series: serie de factura (string o null)
- invoice_date: fecha de factura YYYY-MM-DD (string o null)
- due_date: fecha de vencimiento YYYY-MM-DD (string o null)
- currency: moneda (default "MXN")
- exchange_rate: tipo de cambio (number o null)
- subtotal: subtotal sin impuestos (number)
- tax_amount: SOLO el IVA, nunca la suma de IVA+IEPS (number)
- ieps_amount: SOLO el IEPS, o null si el comprobante no lo trae (number o null)
- tax_breakdown: array de objetos con rate y amount
- total: total con impuestos (number)

REGLAS DE LOS IMPUESTOS, Y POR QUE NO SON SUGERENCIAS
-----------------------------------------------------
`tax_amount` e `ieps_amount` van separados porque se guardan y se exportan en
columnas distintas: `tax_amount` sale bajo el encabezado "IVA" del archivo que se
le entrega al contador. Si metes los dos impuestos en `tax_amount`, el contador
recibe un IVA que no existe y no hay forma de que lo note.

Medido sobre un ticket real de supermercado:

    SUBTOTAL       217.27
    IVA 16.0%        8.14     -> tax_amount
    IEPS 8.0%        8.59     -> ieps_amount
    TOTAL          234.00    y 217.27 + 8.14 + 8.59 = 234.00 exacto

Ojo con esto, que es el error facil: el IVA NO es el 16% del subtotal. Aqui el
IVA es 8.14 porque solo una parte de las partidas esta tasa 16% y el resto a 0%
(el papel lo marca con una letra al final de cada linea: T, C, A). Copia los
IMPORTES que imprime el papel, nunca los recalcules con una tasa: la tasa que el
papel imprime es la de la partida, no la del comprobante.

Y si el comprobante no trae IEPS, `ieps_amount` es `null`. No es `0`: `0` dice
"lei el IEPS y es cero", y `null` dice "no hay IEPS aqui".
- payment_method: "EFECTIVO" | "TARJETA" | "TRANSFERENCIA" | null
- payment_terms: condiciones de pago (string o null)
- items: array de objetos con description, quantity, unit_price, total, tax_rate, tax_amount
- raw_text: todo el texto visible en la imagen
- confidence: 0.0 a 1.0
- extraction_method: "vision"

IMPORTANTE: 
- Devuelve SOLO el JSON, nada más.
- NO uses ```json``` ni markdown.
- NO agregues texto antes o después del JSON.
- Si no ves un campo en la imagen, pon null.
- NO copies valores de ejemplo - extrae los datos REALES de la imagen.
"""


class AIExtractor:
    """Extract structured invoice data using LLM"""

    def __init__(self):
        self.enabled = settings.AI_ENABLED
        # Detect provider from ai_client
        # `AIProvider` es un `str` Enum, asi que comparar contra "ollama"/"openai"
        # funciona con las dos formas. La anotacion declara las DOS porque el
        # `getattr` devuelve un `str` si el atributo no existe, y sin ella el
        # `hasattr(...).value` de abajo parecia un error del type checker
        # cuando es justamente la comprobacion que lo hace seguro.
        self._provider: AIProvider | str = getattr(ai_client, "_provider", "openai")
        # `isinstance` y no `hasattr(self._provider, "value")`: los dos dicen lo
        # mismo en ejecucion, pero `hasattr` no le da al type checker la prueba
        # de que `.value` existe —marca "Attribute value is unknown" sobre una
        # linea que funciona—, y el `isinstance` ademas documenta que lo que se
        # busca es "es un enum o es la cadena de texto".
        if isinstance(self._provider, AIProvider):
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
            return settings.OLLAMA_VISION_MODEL
        return "gpt-4o"

    async def extract_from_image(self, image_bytes: bytes) -> ExtractedInvoice:
        """Extract from image (photo of receipt/invoice)

        Solo recibe los bytes, y a proposito. Antes llevaba un
        `mime_type="image/png"` que no se usaba NADA en el cuerpo: el formato sale
        de los bytes, en `app/core/archivo_real.py`, y llega aqui ya
        normalizado. Este metodo solo tiene que abrirlo, reescalar y convertir
        a JPEG.

        quitarlo no fue cosmetico. Con el parametro ahi, la firma era
        `extract_from_image(bytes, mime_type)`, y el tipo de la cascada no puede
        expresar "el segundo es opcional" —`Callable` no lo tiene—: habia que
        fingir que era obligatorio, o envolverlo en un `Protocol` entero para
        volver a decir lo mismo. Un parametro muerto costs un `Protocol` de 20
        lineas y una confusion que llega hasta el editor.

        Un archivo que no se puede decodificar NO se manda al modelo. Antes
        caia en un `except: pass` y los bytes crudos iban a `base64` declarados
        `image/jpeg`: para un HEIC es un byte stream que vision no descodifica,
        y lo que salia era basura con forma de lectura. Ahora devuelve
        `IMAGEN_ILEGIBLE`, que `capture.FALLOS_DEL_MODELO` traduce a un motivo
        de cola que dice la verdad.
        """
        if not self.enabled:
            return self._fallback_extraction(image_bytes)

        jpeg = _a_jpeg(image_bytes)
        if jpeg is None:
            return ExtractedInvoice(
                provider_name="IMAGEN_ILEGIBLE",
                confidence=0.0,
                raw_text="",
            )

        b64 = base64.b64encode(jpeg).decode()
        return await self._extract_from_vision(b64, "image/jpeg")

    async def extract_from_text(self, text: str) -> ExtractedInvoice:
        """Extract from raw text (OCR output, etc.)"""
        if not self.enabled:
            return self._fallback_extraction(text.encode())
        return await self._extract_from_text(text, "raw_text")

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

    async def _extract_from_vision(self, b64_image: str, mime_type: str) -> ExtractedInvoice:
        """Extract from single base64 image, with retries for invalid model output."""
        last_result: Optional[ExtractedInvoice] = None
        for _attempt in range(2):
            response = await ai_client.chat_completion(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_EXTRACTION_PROMPT},
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
            for field in ["subtotal", "tax_amount", "ieps_amount", "total"]:
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
            #
            # `None` cuando el modelo trae algo que no es un numero, y **no**
            # `Decimal("0")`. La diferencia es la misma que ya separa
            # `ieps_amount` de un 0 (ver el campo de arriba): `0.00` es "el papel
            # dice que esto vale cero" y `None` es "no lo lei o no lo lei bien".
            # Rellenar con cero hace que una lectura imposible sea
            # indistinguible de una lectura real de cero, que es exactamente el
            # dato que el gate no puede verificar con nada.
            #
            # Lo que el gate vea despues lo decide `capture.invoice_to_result`,
            # que traduce `None` a `Decimal("0.00")` **a proposito**: alli el cero
            # es el centinela de "no se pudo leer", y `_puntaje_de_extraccion` y
            # `_vale_la_pena` comparan contra el. Aqui, dentro del invoice, no:
            # aqui el cero seria una afirmacion sobre el papel.
            #
            # El `except` es de las tres excepciones que de verdad puede lanzar
            # `Decimal()` sobre texto. `Exception` entera tambien se tragaba un
            # `RecursionError` y cualquier otra cosa, y hacia parecer que el
            # fallback era una decision y no un accidente.
            def _to_decimal(v: Any) -> Decimal | None:
                try:
                    if v is None:
                        return None
                    if isinstance(v, Decimal):
                        return v
                    if isinstance(v, float):
                        return Decimal(str(v))
                    s = str(v).replace(",", "").replace("$", "").strip()
                    return Decimal(s)
                except (InvalidOperation, ValueError, TypeError):
                    logger.warning("el modelo devolvio un %r donde iba un importe", v)
                    return None

            for field in ["subtotal", "tax_amount", "total", "exchange_rate"]:
                if field in data and data[field] is not None:
                    data[field] = _to_decimal(data[field])
                elif field not in data:
                    data[field] = Decimal("0")

            # El IEPS NO entra en el bucle de arriba a proposito. Ahi, un campo
            # ausente se rellena con `Decimal("0")`, porque para el subtotal, el
            # IVA y el total un 0 es un numero y el modelo siempre los trae. Para
            # el IEPS no: `0.00` significa "el comprobante no tiene IEPS" y lo
            # haria parecer un dato leido. Se convierte si vino, y si no se
            # queda en None.
            if data.get("ieps_amount") is not None:
                data["ieps_amount"] = _to_decimal(data["ieps_amount"])

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

        except Exception:
            # El motivo ya se registro en `_a_jpeg`, con el detalle de si falta
            # el soporte de HEIC. Aqui solo se declara que la lectura fallo.
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
logger = logging.getLogger(__name__)

ai_extractor = AIExtractor()