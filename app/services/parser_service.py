import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from pydantic import BaseModel, field_validator

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


# Un importe solo puede llevar digitos, un signo y separadores. Se acepta tambien
# el simbolo de la moneda porque algunos exportadores lo meten en la celda
# ("$1,234.56"), y quitarlo es mejor que rechazar la fila: el importe se puede
# leer sin problema, lo unico que sobra es la marca.
_MONEDA = "€$£¥"
_NO_NUMERO = re.compile(r"[^0-9.,+-]")


def _grupos_de_miles(entero: str, sep: str) -> str | None:
    """El entero sin sus separadores de miles, o `None` si no encaja.

    Encaja cuando, al partir por `sep`, el primer grupo mide de una a tres
    digitos y TODOS los demas miden exactamente tres. Esa es la unica prueba
    fiable: "1,234" son mil doscientos, y "1,23" no lo son. El tamano total del
    numero no ayuda, porque "89.000" y "89.00" ocupan lo mismo en pantalla y
    significan cosas distintas.
    """
    if sep not in entero:
        return entero
    grupos = entero.split(sep)
    if not grupos[0] or not 1 <= len(grupos[0]) <= 3:
        return None
    if not all(len(g) == 3 for g in grupos[1:]):
        return None
    return "".join(grupos)


def _como_decimal(texto: str, sep: str) -> str | None:
    """Lee `texto` tomando `sep` como decimal. `None` si no cuadra."""
    entero, _, frac = texto.rpartition(sep)
    if not entero or not frac or not frac.isdigit():
        return None
    entero_limpio = _grupos_de_miles(entero, "." if sep == "," else ",")
    if entero_limpio is None or not entero_limpio.isdigit():
        return None
    return f"{entero_limpio}.{frac}"


def _a_decimal(valor: object, decimal_separator: str, thousands_separator: str) -> Decimal:
    """Convierte el importe de un CSV a `Decimal`.

    Se hace a mano y no delegando en pandas porque los separadores que usan
    muchos bancos son los INVERSOS de los que pandas asume por omision, y
    delegar salia mal de dos maneras: rechazar filas que si se podian leer y,
    peor, guardar un importe cien veces mayor sin avisar. Ver la nota en
    `parse_bank_csv`.

    **El orden de los intentos es lo que hace que esto funcione.** "1,234.56" es
    ambiguo: con convencion mexicana (coma decimal) no es un numero valido, y con
    convencion anglosajona son mil doscientos con cincuenta y seis. Se prueba en
    este orden, que da la misma respuesta en los dos casos:

      1. El separador DECLARADO como decimal, con uno o dos digitos detras.
      2. El otro, como decimal, con uno o dos digitos detras.
      3. El separador DECLARADO como miles, si los grupos miden tres.
      4. El otro, como miles.
      5. El declarado como decimal con mas de dos digitos: "0.0001" es un
         importe legitimo y rechazarlo seria peor que aceptarlo.

    El declarado va primero para que un archivo que SI sigue la convencion que
    pidio el usuario no se lea con la contraria. Y el paso 3 va antes que el 5
    porque "1,234" con coma declarada son mil doscientos treinta y cuatro, no uno
    con doscientos treinta y cuatro milimas: tres digitos exactos es grupo de
    miles.

    `Decimal` y no `float` por la razon de siempre: 0.1 + 0.2 tiene que dar 0.3
    en una conciliacion. Con punto flotante, el total de mil tickets acaba en un
    peso de diferencia que nadie sabe de donde salio.
    """
    if valor is None:
        raise ValueError("importe vacio")

    crudo = str(valor).strip()
    if not crudo:
        raise ValueError("importe vacio")

    # Se quita lo que no aporta al numero: "$", "MXN", espacios internos.
    limpio = _NO_NUMERO.sub("", crudo.replace(_MONEDA, ""))

    if not limpio or limpio in {"+", "-", ".", ","}:
        raise ValueError(f"importe no numerico: {crudo!r}")

    negativo = limpio.startswith("-")
    if limpio[0] in "+-":
        limpio = limpio[1:]

    otro = "," if decimal_separator == "." else "."
    # (separador, True) = leerlo como miles; (separador, False) = como decimal.
    intentos = [
        (decimal_separator, False),
        (otro, False),
        (decimal_separator, True),
        (otro, True),
    ]

    largo: str | None = None
    resultado: str | None = None

    for sep, es_miles in intentos:
        if es_miles:
            if sep not in limpio:
                continue
            entero = _grupos_de_miles(limpio, sep)
            if entero is not None and entero.isdigit():
                return Decimal(entero) * (-1 if negativo else 1)
            continue

        leido = _como_decimal(limpio, sep)
        if leido is None:
            continue
        if len(leido.split(".")[1]) <= 2:
            resultado = leido
            break
        # Un decimal largo solo entra si al final no sirvio ninguna otra.
        largo = leido

    if resultado is None and limpio.isdigit():
        resultado = limpio
    if resultado is None:
        resultado = largo

    if resultado is None:
        raise ValueError(f"importe no numerico: {crudo!r}")

    try:
        numero = Decimal(resultado)
    except InvalidOperation as exc:
        raise ValueError(f"importe no numerico: {crudo!r}") from exc

    return -numero if negativo else numero


# Cuanto `raw_text` se guarda antes de recortar.
#
# No es un capricho de tamano. `raw_text` va a la columna `tickets.raw_text` tal
# cual, sin tope en la base, y un PDF de Conditions Generales de 200 paginas
# deja varios MB en una fila. Medido: un archivo de 3 MB de texto entraba
# completo. Eso no se rompe en la lectura, se rompe en el dia que se listan 100
# tickets para la cola de revision y la consulta se arrastra cada uno con su
# documento entero.
#
# Se recorta cabeza Y cola, no solo la cabeza, por una razon concreta: en un
# comprobante los datos que se revisan estan en los dos extremos. El proveedor
# esta arriba, y el total, el IVA y la fecha de emision casi siempre estan
# ABAJO, en el bloque de sumas. Un recorte por cabeza guardaria el nombre del
# comercio y tiraria las cifras, que es justo lo que el revisor necesita
# contrastar contra el papel.
RAW_TEXT_MAX_CHARS = 20_000
_RAW_TEXT_POR_LATIL = 12_000


def recortar_raw_text(texto: str) -> str:
    """Deja el texto en un tamano guardable, sin perder los dos extremos.

    El recorte se marca en el propio texto, y no se marca nada cuando no hubo
    recorte. Dos razones:

    - Un recorte silencioso hace que el campo parezca completo. El revisor
      busca "TOTAL 1,100.00" en el `raw_text`, no lo encuentra, y anota que el
      total no aparece, cuando lo que paso es que se quedo fuera de la ventana.
    - Marcarlo solo cuando recorta es lo que hace que este texto sea buscable:
      `raw_text LIKE '%truncado%'` cuenta los documentos de los que no se guardo
      todo, sin agregar otra columna.

    El texto ya recortado no lleva marca. Volver a recortar un texto que ya
    esta dentro del tope no debe ensuciarlo con un aviso de algo que no paso.
    """
    if len(texto) <= RAW_TEXT_MAX_CHARS:
        return texto

    cola = RAW_TEXT_MAX_CHARS - _RAW_TEXT_POR_LATIL
    se_omitieron = len(texto) - RAW_TEXT_MAX_CHARS
    return (
        texto[:_RAW_TEXT_POR_LATIL]
        + f"\n[... {se_omitieron} caracteres omitidos: "
          f"raw_text se guarda con tope de {RAW_TEXT_MAX_CHARS} ...]\n"
        + texto[-cola:]
    )


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

    @field_validator("raw_text")
    @classmethod
    def _topa_raw_text(cls, v: str) -> str:
        """El tope vive aqui y no en quien arma el resultado.

        Es el mismo argumento que el de `app/core/subida.py`: un control puesto
        en cada lugar que arma el texto se puede olvidar, y el que se olvide no
        falla, guarda el documento entero y nadie se entera hasta que listar 100
        tickets para la cola se arrastra. En el validador no hay forma de
        saltarselo: todo `TicketExtractionResult` que exista paso por aqui, lo
        arme el parser de reglas o el modelo.
        """
        return recortar_raw_text(v)


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
    # Allowlist de codificaciones. Python acepta docenas de codecs; algunos
    # (utf-7, zlib_codec, bz2_codec, rot_13, etc.) pueden abusarse:
    # - utf-7 convierte +ADw-script+AD4- en <script> -> XSS si se renderiza sin escape.
    # - zlib_codec/bz2_codec descomprimen en memoria -> bomba de descompresion.
    # - raw_unicode_escape, unicode_escape interpretan escapes -> confusion.
    # El CSV bancario viene de exportadores contables: utf-8, latin-1, cp1252
    # cubren el 99% de los casos reales. El resto se rechaza con mensaje claro.
    ENCODINGS_PERMITIDOS = {"utf-8", "latin-1", "cp1252", "iso-8859-1"}
    if encoding.lower() not in ENCODINGS_PERMITIDOS:
        raise ValueError(
            f"Codificacion no permitida: {encoding}. Use utf-8, latin-1 o cp1252."
        )

    # `dtype=str` y SIN `thousands`/`decimal`: la conversion del importe se hace
    # abajo, a mano, con los separadores que pidio el usuario.
    #
    # Delegarlo en pandas no funciona, y falla de dos maneras distintas segun
    # cual de los dos argumentos este invertido:
    #
    #   - Con `decimal=","` y un importe CON separador de miles ("1,234.56"),
    #     pandas infiere la columna como texto, no la convierte, y aqui arrive
    #     como la cadena "1,234.56". `Decimal("1,234.56")` es invalido y la
    #     fila se rechaza con "fila invalida", sin decir cual.
    #   - Con `decimal=","` y un importe SIN miles ("890.00"), pandas aplica el
    #     `.` como separador de miles y guarda 89000.00: cien veces el importe
    #     real, sin error ni aviso.
    #
    # El segundo es el que importa. Un banco que reporta 890.00 y una conciliacion
    # que guarda 89,000.00 no producen un 400: producen una conciliacion
    # "correcta" con todos los tickets en discrepancia y ningun indicio de por
    # que. Leer como texto y convertir aqui hace que el separador que pidio el
    # usuario sea el unico que cuenta, y que la aritmetica sea la de `Decimal`.
    df = pd.read_csv(
        io.BytesIO(file_content),
        encoding=encoding,
        sep=separator,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
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
            amount = _a_decimal(row[amount_column], decimal_separator, thousands_separator)
            description = str(row[description_column]).strip()
            reference = str(row[reference_column]).strip() if reference_column in df.columns and pd.notna(row[reference_column]) else None
            
            transactions.append(BankTransactionRow(
                transaction_date=transaction_date,
                amount=amount,
                description=description,
                reference=reference
            ))
        except Exception:
            # No se refleja el contenido de la fila ni el error interno: el
            # cliente recibe un mensaje generico y los detalles quedan en el
            # log del servidor. Si se refleja la fila, un CSV malicioso con
            # datos largos en cada columna puede hacer que el mensaje de error
            # sea enorme y filtrar informacion de otras filas.
            raise ValueError("Error en el formato del CSV: fila invalida")
    
    return transactions


def extract_ticket_data(
    file_content: bytes,
    file_type: str = "pdf",
    extraction_config: dict[str, Any] | None = None,
) -> TicketExtractionResult:
    """Extrae por reglas, sin IA. Solo PDF con capa de texto y texto plano.

    Esta es la parte determinista de la captura, sin modelo. La ruta completa es
    `app.services.capture.capture_ticket`, que es la que usa la API: decide si
    un documento necesita modelo y, si lo necesita, lo consulta. Aqui no hay
    esa decision, y por eso una imagen no se puede resolver.

    Antes esta funcion devolvia para una imagen un ticket de relleno con
    proveedor "Unknown Provider" y total 0, indistinguible de un comprobante
    real de cero pesos. Un ticket inventado que se guarda es un gasto falso en
    la base, y un gasto falso que nadie sabe que es falso sale en el cierre
    mensual. Que falle de forma visible es mejor que devolver algo plausible.

    `extraction_config` se acepta y se ignora. No hay nada que configurar: la
    politica de captura esta en `capture.py` y es una sola.
    """
    if file_type == "pdf":
        return _extract_from_pdf(file_content)
    if file_type == "text":
        return _parse_receipt_text(file_content.decode("utf-8"))
    if file_type == "image":
        raise ValueError(
            "una imagen no se extrae por reglas: use "
            "app.services.capture.capture_ticket, que consulta al modelo"
        )
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


# Antes de este punto de la historia habia aqui un `return` con un
# `TicketExtractionResult` de relleno y `raw_text="[Image OCR not implemented]"`,
# inalcanzable por quedar despues del `finally`. Se borro por dos razones, y las
# dos son sobre leer el codigo:
#
# - Decia que la imagen no se podia leer, cuando la verdad es que las paginas
#   renderizadas salen aqui y quien las mira es la cascada de `capture.py`. Un
#   comentario que afirma lo contrario del comportamiento es peor que nada.
# - Era la clase de codigo que hace que un bug pase desapercibido. La excepcion
#   de `render_pdf_pages` se traga en `capture._vision_pdf` y se convierte en un
#   motivo de cola; ese `return` era la version written de lo mismo, pero sin
#   llegar a loguearse nunca.
#
# Si alguna vez vuelve a hacer falta un resultado vacio, el lugar es
# `app.services.capture._ilegible`, que si construye uno, y lo marca con el
# motivo real.


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