"""La unica ruta por la que entra un comprobante al sistema.

Un archivo llega como foto, escaneo o PDF y sale de aqui como
`TicketExtractionResult` con la confianza y el origen ya decididos. Ni la API
ni el worker de carga masiva deciden nada: convergent los dos aqui, y por eso
no pueden divergir.

La ruta es una cascada y cada escalon se usa solo si el anterior no pudo:

    foto                    vision: no hay texto, hay que mirar
    pdf con texto           reglas, sin IA
    pdf con texto ilegible  el modelo lee el texto
    pdf sin texto           el modelo mira las paginas renderizadas

El orden importa y no es obvio. Un PDF impreso se lee exacto con pdfplumber:
mandarlo a un modelo cuesta tiempo y tokens, y encima puede equivocarse donde
el texto no se equivoca. Al reves, un PDF escaneado no tiene texto que leer, y
un regex sobre una cadena vacia no produce nada: produce un ticket falso.

Que la cascada termine en vision no significa que el resultado sea malo.
Significa que el documento no se pudo leer de forma exacta, y eso queda dicho:
el origen se guarda como `llm`, no como `pdf_text`, y quien audite lo ve.

Lo que esta cascada no hace, a proposito:

- No descarta un documento porque el modelo falle. Un papel que el sistema no
  pudo leer sigue siendo un papel que alguien tiene que revisar, y se guarda
  en la cola con su motivo. Perder el archivo es peor que tener un dato
  pendiente, porque nadie llega a saber que hubo que subirlo.
- No inventa datos. Si el documento no trae fecha, la fecha queda en None y el
  ticket entra a la cola con el motivo `date_missing`, en vez de quedar
  fechado el dia de hoy.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

from app.core.enums import UNKNOWN_PROVIDER, ConfidenceSource
from app.services.parser_service import (
    TicketExtractionResult,
    _parse_receipt_text,
    extract_pdf_text,
    render_pdf_pages,
)

logger = logging.getLogger(__name__)

ExtractFn = Callable[[bytes, str], Awaitable["ExtractedInvoice"]]
ExtractTextFn = Callable[[str], Awaitable["ExtractedInvoice"]]


class ExtractionUnavailable(RuntimeError):
    """El archivo no se pudo abrir.

    Distinto de "lei mal" a proposito: leer mal deja un ticket en la cola con
    motivo, que es recuperable. Esto es un archivo que ni siquiera se pudo
    abrir, y no hay nada que revisar todavia. Un PDF corrupto o un tipo de
    archivo que no existe tiene que decir eso, no aparecer como un comprobante
    sin proveedor.
    """


# Los centinelas que la capa de IA usa para decir "no pude leer el proveedor".
#
# Son tres y ninguno es el canonico. Sin normalizarlos aqui, un ticket con
# proveedor "AI_DISABLED" pasa el check de proveedor del gate (la cadena no es
# la centinela, es no vacia) y la cola muestra "AI_DISABLED" como si fuera el
# nombre de un comercio. Se normalizan en un solo punto: el mapeo.
SENTINELS_SIN_PROVEEDOR = frozenset({"UNKNOWN", "ERROR_PARSING", "AI_DISABLED"})

# Fallos que son del sistema y no del documento. La diferencia importa porque
# se reportan distinto: `AI_DISABLED` es una configuracion (el extractor esta
# apagado) y `ERROR_PARSING` es un fallo de la llamada (el modelo devolvio algo
# que no se pudo leer). Ninguno de los dos es "el papel esta borroso", y
# reportarlos como si lo fueran esconde la causa raiz detras de la palabra
# "ilegible".
FALLOS_DEL_MODELO = {
    "ERROR_PARSING": "el modelo devolvio una respuesta que no se pudo interpretar",
    "AI_DISABLED": "el extractor de IA esta apagado o no respondio",
}

# Debajo de esto no hay documento que leer, solo ruido de escaner. Por debajo
# de unos 40 caracteres un comprobante no tiene proveedor, ni total, ni fecha;
# lo que suele estar es la marca de agua o el nombre del archivo del escaner.
PDF_MIN_CHARS_PARA_INTENTAR = 40

# Cuantas paginas se mandan a vision. Un comprobante de hotel son tres paginas;
# un CFDI de ferreteria, veinte lineas. Mandar veinte paginas al modelo cuesta
# tiempo y tokens que no vuelven en forma de datos, y las ultimas paginas de un
# comprobante largo
# casi siempre son condiciones generales.
PDF_MAX_PAGINAS_A_VISION = 3

# Escala de render. A 2.0 una pagina letter queda en ~1700x2200: se lee bien un
# total en letra pequena sin pagar por megapixeles de mas.
PDF_ESCALA_RENDER = 2.0

# Confianza del parseo por reglas, segun lo que encontro de verdad.
#
# Son priors y no medidas: el acierto exacto de un regex sobre un tipo de
# comprobante no se conoce hasta que alguien revise una muestra y diga cuantos
# estaban bien. Ese muestreo es el que quedo de acuerdo para medir el 96%;
# estos numeros son el punto de partida, no el resultado.
#
# Que dependa de lo encontrado y no sea un numero fijo importa: un parseo que
# saco RFC, subtotal y fecha no es el mismo resultado que uno que adivino un
# total suelto, y cobrarlos igual aplana el detalle que sirve para medir.
#
# Las ocho combinaciones estan escritas, no se calculan. Una combinacion que
# llegara faltando produce un AssertionError en vez de un `None` silencioso, y
# `None` aqui significa confianza nula: el ticket cae a la cola sin que nadie
# sepa que la tabla esta incompleta.
#
# El orden es por evidencia acumulada: rfc*2 + subtotal*3 + fecha*1. El
# subtotal pesa mas que el RFC porque es el unico de los tres que deja
# verificar aritmeticamente (`subtotal + iva == total`); un RFC que no cuadra
# con las cuentas no prueba nada. La confianza nunca baja al agregar evidencia,
# y eso se comprueba en los tests.
CONFIANZA_POR_CAMPOS: tuple[tuple[tuple[bool, bool, bool], float], ...] = (
    # (rfc, subtotal, fecha) -> confianza
    ((False, False, False), 0.86),  # solo un total suelto
    ((False, False, True), 0.88),
    ((True, False, False), 0.90),
    ((False, True, False), 0.90),   # mismo puntaje: el subtotal solo no ancla
    ((True, False, True), 0.90),
    ((False, True, True), 0.92),
    ((True, True, False), 0.94),
    ((True, True, True), 0.97),
)


def confianza_por_campos(
    tiene_rfc: bool,
    tiene_subtotal: bool,
    tiene_fecha: bool,
) -> float:
    """Confianza del parseo por reglas, derivada de la evidencia encontrada.

    Cada campo que se cruza con el documento es una fuente menos de error
    posible. Un total sin RFC puede ser el total de otra cosa; con RFC, con
    fecha y con un subtotal que cuadra con el IVA, es el total.
    """
    for (rfc, subtotal, fecha), confianza in CONFIANZA_POR_CAMPOS:
        if (rfc, subtotal, fecha) == (tiene_rfc, tiene_subtotal, tiene_fecha):
            return confianza
    raise AssertionError(
        "falta una combinacion en CONFIANZA_POR_CAMPOS: "
        f"rfc={tiene_rfc} subtotal={tiene_subtotal} fecha={tiene_fecha}"
    )


def invoice_to_result(invoice: "ExtractedInvoice") -> TicketExtractionResult:
    """Traduce lo que devuelve el modelo a lo que el gate entiende.

    Vive aqui y no en la API porque es parte de la ruta de captura: el worker
    de carga masiva tambien lo necesita, y si cada capa tuviera el suyo,
    acabarian normalizando los centinelas de forma distinta.
    """
    from app.services.ai_extractor import ExtractedInvoice

    nombre = (invoice.provider_name or "").strip()
    if nombre in SENTINELS_SIN_PROVEEDOR or not nombre:
        nombre = UNKNOWN_PROVIDER

    return TicketExtractionResult(
        provider_name=nombre,
        provider_tax_id=invoice.provider_tax_id,
        total_amount=invoice.total if invoice.total is not None else Decimal("0.00"),
        tax_amount=invoice.tax_amount if invoice.tax_amount is not None else Decimal("0.00"),
        expense_date=invoice.invoice_date,
        category=None,
        raw_text=invoice.raw_text,
        subtotal=invoice.subtotal if invoice.subtotal is not None else None,
        confidence=float(invoice.confidence) if invoice.confidence else None,
        # El modelo puede leer un PDF escaneado o una foto: en los dos casos
        # lo que hizo fue mirar. `llm` es la verdad en ambos.
        confidence_source=ConfidenceSource.LLM,
    )


def motivo_de_fallo_del_modelo(invoice: "ExtractedInvoice") -> str | None:
    """Razon en español de un fallo del modelo, o None si no hay fallo.

    Se separa de la normalizacion de centinelas a proposito. "El proveedor no
    se pudo leer" y "el extractor esta apagado" producen el mismo ticket (sin
    proveedor, en la cola), pero piden cosas opuestas: al primero se le
    busca otro escalon de lectura, al segundo se le enciende el extractor. Si
    los dos se reportan igual, el segundo se manifesta como "muchos tickets
    raros" y nadie lo reconoce como una configuracion apagada.
    """
    return FALLOS_DEL_MODELO.get((invoice.provider_name or "").strip())


def _es_extraccion_util(resultado: TicketExtractionResult) -> bool:
    """¿Sirvio de algo la lectura?

    Proveedor y total son el minimo para que un ticket tenga sentido. Si falta
    uno de los dos, el documento no se entendio y todavia se le puede pedir a
    otro escalon que lo mire. Guardar medio comprobante en la cola cuando aun
    queda un escalon disponible es trabajo humano desperdiciado.
    """
    return resultado.provider_name != UNKNOWN_PROVIDER and resultado.total_amount > 0


def _marcar_por_reglas(
    resultado: TicketExtractionResult,
    source: ConfidenceSource,
) -> TicketExtractionResult:
    """Anota origen y confianza de una extraccion determinista.

    El parser por reglas no estima confianza: no tiene nada que decir. Lo que
    hace es reportar cuanto encontro, y de ahi sale el numero. Es una distincion
    que importa: no es la certeza del modelo, es el conteo de evidencia.
    """
    resultado.confidence_source = source
    resultado.confidence = confianza_por_campos(
        tiene_rfc=bool(resultado.provider_tax_id),
        tiene_subtotal=resultado.subtotal is not None,
        tiene_fecha=resultado.expense_date is not None,
    )
    return resultado


def _ilegible(motivo: str, raw_text: str = "") -> TicketExtractionResult:
    """Un documento que llego pero del que no se sabe nada.

    Se construye en vez de lanzar excepcion para que el archivo no se pierda:
    esto termina en la cola con motivo, que es donde un humano lo resuelve.
    """
    logger.warning("documento ilegible: %s", motivo)
    return TicketExtractionResult(
        provider_name=UNKNOWN_PROVIDER,
        provider_tax_id=None,
        total_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        expense_date=None,
        category=None,
        raw_text=raw_text or f"[no se pudo leer: {motivo}]",
        confidence=None,
        confidence_source=ConfidenceSource.LLM,
    )


def _mejor_de(primero: TicketExtractionResult, segundo: TicketExtractionResult):
    """El mejor de dos intentos parciales.

    "Mejor" quiere decir el que mas dice de verdad: el que tiene total. Entre
    dos resultados igual de incompletos se queda el primero, que es el del
    escalon mas barato y por tanto el que menos esta inventando.
    """
    return segundo if segundo.total_amount > primero.total_amount else primero


# ---------------------------------------------------------------------------
# La cascada
# ---------------------------------------------------------------------------


async def capture_ticket(
    content: bytes,
    file_type: str,
    *,
    extract_from_image: ExtractFn | None = None,
    extract_from_text: ExtractTextFn | None = None,
) -> TicketExtractionResult:
    """Punto de entrada unico de un comprobante.

    Los extractores se inyectan para poder probar la politica de cascada sin un
    modelo. Sin ellos se usa el extractor real.

    Args:
        content: bytes del archivo tal como lo subio el usuario.
        file_type: `image`, `pdf` o `text`.

    Returns:
        `TicketExtractionResult` con `confidence` y `confidence_source`
        resueltos. No lanza por no poder leer el contenido: eso se traduce en
        un resultado vacio con motivo, para que el documento llegue a la cola.
        Si ni siquiera se puede abrir el archivo, lanza
        `ExtractionUnavailable`, que si es un error de verdad.

    Raises:
        ExtractionUnavailable: tipo de archivo desconocido.
    """
    if file_type == "image":
        return await _desde_imagen(content, extract_from_image)

    if file_type == "text":
        texto = content.decode("utf-8", errors="replace")
        return await _cascada_texto(texto, ConfidenceSource.RULES, extract_from_text)

    if file_type != "pdf":
        raise ExtractionUnavailable(f"tipo de archivo no soportado: {file_type!r}")

    try:
        texto = extract_pdf_text(content)
    except Exception as exc:
        # Un PDF que pdfplumber no abre puede estar corrupto, cifrado, o no ser
        # un PDF. Puede tambien ser un escaneado raro que otro lector si abre.
        # Se intenta vision antes de rendirse: un ticket vale mas que una
        # excepcion.
        logger.info("pdfplumber no pudo abrir el PDF (%s); se intenta vision", exc)
        return await _vision_pdf(content, extract_from_image)

    if len(texto.strip()) < PDF_MIN_CHARS_PARA_INTENTAR:
        # Escaneado: no hay nada que parsear. Un regex sobre cadena vacia
        # devolveria ceros, y un ticket de ceros es un ticket falso.
        return await _vision_pdf(content, extract_from_image)

    return await _cascada_texto(texto, ConfidenceSource.PDF_TEXT, extract_from_text)


async def _cascada_texto(
    texto: str,
    source: ConfidenceSource,
    extract_from_text: ExtractTextFn | None,
) -> TicketExtractionResult:
    """Hay texto: primero las reglas, el modelo solo si las reglas no pueden.

    El orden no es una preferencia de rendimiento. Un regex sobre texto
    extraido no inventa un total: o lo encuentra o no lo encuentra. Un modelo
    puede inventarlo. Cuando las reglas fallan, lo que el modelo devuelve ya
    no esta leyendo el documento con reglas, esta suponiendo, y su respuesta
    hay que leerla desconfiando.
    """
    reglas = _marcar_por_reglas(_parse_receipt_text(texto), source)

    if _es_extraccion_util(reglas):
        return reglas

    if extract_from_text is None:
        return reglas

    logger.info(
        "el parseo por reglas no sirvio (proveedor=%r total=%s); se pide al modelo",
        reglas.provider_name, reglas.total_amount,
    )
    try:
        invoice = await extract_from_text(texto)
    except Exception as exc:
        # El modelo fallo y las reglas tampoco. Se conserva lo que las reglas
        # si encontraron: un total sin proveedor es mejor que nada, y la cola
        # lo va a marcar con `provider_missing`.
        logger.warning("el modelo no pudo leer el texto (%s); se guarda lo parcial", exc)
        return reglas

    motivo = motivo_de_fallo_del_modelo(invoice)
    if motivo:
        # Fallo del sistema, no del documento. Las reglas ya se probaron y no
        # pudieron, asi que no queda mas escalon: se guarda lo que hay.
        logger.warning("el modelo fallo sobre texto legible: %s", motivo)
        return reglas

    modelo = invoice_to_result(invoice)

    if not _es_extraccion_util(modelo):
        # Ni reglas ni modelo dieron un comprobante. Se devuelve el mejor
        # intento para que la cola pueda decir algo concreto de por que.
        return _mejor_de(reglas, modelo)

    return modelo


async def _vision_pdf(content: bytes, extract_from_image: ExtractFn | None) -> TicketExtractionResult:
    """PDF sin texto: se renderizan las paginas y se leen con vision."""
    if extract_from_image is None:
        return _ilegible(
            "el PDF no tiene capa de texto y no hay extractor de vision disponible"
        )

    try:
        paginas = render_pdf_pages(
            content,
            max_pages=PDF_MAX_PAGINAS_A_VISION,
            scale=PDF_ESCALA_RENDER,
        )
    except Exception as exc:
        logger.warning("no se pudo renderizar el PDF a imagen: %s", exc)
        return _ilegible(f"PDF ilegible y no renderizable: {exc}")

    if not paginas:
        return _ilegible("el PDF no tiene paginas")

    resultados: list[TicketExtractionResult] = []
    fallos_del_modelo: list[str] = []
    errores_de_pagina: list[str] = []
    for indice, pagina in enumerate(paginas):
        try:
            invoice = await extract_from_image(pagina, mime_type="image/jpeg")
        except Exception as exc:
            # El motivo se acumula ademas de registrarse en el log. La cola
            # tiene que poder decir por que no se pudo leer, no solo "no se
            # pudo leer": un error que se queda en el log del servidor obliga
            # a que alguien vaya a buscarlo, y si nadie busca, se repite
            # para siempre.
            errores_de_pagina.append(f"pagina {indice + 1}: {exc}")
            logger.warning("la pagina %d del PDF no se pudo leer: %s", indice + 1, exc)
            continue

        motivo = motivo_de_fallo_del_modelo(invoice)
        if motivo:
            fallos_del_modelo.append(motivo)
            continue

        resultados.append(invoice_to_result(invoice))

    if not resultados and fallos_del_modelo:
        # Las paginas se renderizaron bien y el extractor las recibio. Lo que
        # fallo es el modelo, y eso se dice con su palabra, no como
        # "documento ilegible".
        return _ilegible("; ".join(dict.fromkeys(fallos_del_modelo)))

    utiles = [r for r in resultados if _es_extraccion_util(r)]
    if not utiles:
        if resultados:
            # Las paginas se leyeron pero ninguna dio algo entendible. Se
            # devuelve el primer intento para que la cola muestre el motivo que
            # produjo el modelo, no un "ilegible" generico.
            return resultados[0]
        detalle = "; ".join(errores_de_pagina) if errores_de_pagina else (
            "sin error registrado, pero ninguna pagina devolvio un comprobante"
        )
        return _ilegible(f"ninguna pagina del PDF se pudo leer con vision ({detalle})")

    if len(utiles) == 1:
        return utiles[0]

    # Varias paginas con datos: se toma la de mayor total, que en un comprobante
    # multi-pagina es la del final, donde esta el resumen. Es un supuesto
    # documentado y no una certeza, asi que la confianza no se sube: lo que se
    # tiene aqui es una lectura entre varias, no una lectura verificada.
    mejor = max(utiles, key=lambda r: r.total_amount)
    mejor.confidence_source = ConfidenceSource.LLM
    return mejor


async def _desde_imagen(content: bytes, extract_from_image: ExtractFn | None) -> TicketExtractionResult:
    """Foto o escaneo suelto: vision, no hay otra forma."""
    if extract_from_image is None:
        return _ilegible("no hay extractor de vision disponible")

    try:
        invoice = await extract_from_image(content)
    except Exception as exc:
        # Un archivo que se subio y no se pudo leer no se pierde: entra a la
        # cola. Perderlo es el unico resultado inaceptable, porque nadie llega a
        # saber que hubo que subir.
        return _ilegible(f"la IA no pudo leer la imagen: {exc}")

    motivo = motivo_de_fallo_del_modelo(invoice)
    if motivo:
        return _ilegible(motivo, invoice.raw_text)

    return invoice_to_result(invoice)
