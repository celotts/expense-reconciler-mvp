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
from app.services.ocr import OCRNoDisponible
from app.services.parser_service import (
    TicketExtractionResult,
    _parse_receipt_text,
    extract_pdf_text,
    render_pdf_pages,
)

logger = logging.getLogger(__name__)

ExtractFn = Callable[[bytes, str], Awaitable["ExtractedInvoice"]]
ExtractTextFn = Callable[[str], Awaitable["ExtractedInvoice"]]

# El OCR es sincrono (Tesseract y EasyOCR no son awaitables) y devuelve su
# propio tipo. Se tipa aparte de los otros dos extractores a proposito: es el
# unico que no es `async`, y declararlo `async` obligaria a envolverlo en un
# hilo sin ganar nada.
OcrFn = Callable[[bytes], "ResultadoOCR"]


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
    # El archivo llego como imagen pero no se pudo decodificar. Antes este caso
    # caia en un `except: pass` y los bytes crudos se mandaban al modelo
    # declarados `image/jpeg`: basura que el modelo podia devolver con
    # apariencia de lectura. Con nombre propio, el ticket va a la cola con el
    # motivo real en vez de con un "Unknown Provider" de pantalla.
    "IMAGEN_ILEGIBLE": "el archivo es una imagen que no se pudo decodificar",
}

# Debajo de esto no hay documento que leer, solo ruido de escaner. Por debajo
# de unos 40 caracteres un comprobante no tiene proveedor, ni total, ni fecha;
# lo que suele estar es la marca de agua o el nombre del archivo del escaner.
PDF_MIN_CHARS_PARA_INTENTAR = 40

# Lo mismo para el OCR, pero con un piso mas alto que el del PDF.
#
# El OCR devuelve texto aunque la foto sea una pared: leeria el ruido como
# letras sueltas y, con el piso del PDF (40), pasaria, se parsearia y con suerte
# saldria un comprobante sin proveedor que se guarda en la cola. Con este piso
# mas alto, una foto que no es un ticket se queda en "no se leyo" y se va a
# vision, que si tiene forma de decir "esto no es un comprobante".
#
# EL NUMERO ESTA MEDIDO, NO ESTIMADO. Se puso en 120 y habia que rechazaba
# tickets de verdad: un comprobante minimo con proveedor, RFC, fecha, subtotal,
# IVA y total son 112 caracteres. Un cafe de 50 pesos son menos de 40, y ahi si
# toca aceptar que vision decida. El piso tiene que quedar debajo del ticket mas
# chico que se quiere aceptar y encima del ruido, y con 120 el primero de los
# dos estaba mal: 60 esta debajo de los 112 del ticket minimo real y encima de
# los ~40 de una linea suelta de un muro.
#
# Si se sube, hay que medir contra receipts reales, no subirlo "por seguridad":
# un piso que rechaza comprobantes legitimos no es conservatism, es un escaner
# que pierde gastos y manda el ticket a vision a pagar un modelo.
OCR_MIN_CHARS_PARA_INTENTAR = 60

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


# La misma evidencia, leida por OCR, vale menos. Y no por distrusto del OCR en
# abstracto, sino por una cosa concreta y medible: el OCR cambia caracteres sin
# avisar. Un "1" se vuelve "l", un "0" se vuelve "O", y un total de 1,100.00
# puede salir 1,1OO.OO. El regex de total no lo detecta, porque para el regex
# "1,1OO.OO" es un total tan valido como el otro: no hay forma de saber que
# esta mal desde el texto.
#
# En un PDF con capa de texto eso no pasa: los bytes son los que el emisor
# escribio. Por eso la tabla de arriba no le aplica a OCR, y usar la misma para
# los dos seria afirmar que leer una foto es tan seguro como leer el PDF, que es
# justo lo que este proyecto no hace en ningun otro lado.
#
# Que la tabla este descontada no significa que un ticket con OCR no se pueda
# cerrar solo. Significa que para cerrarlo hace falta la evidencia completa
# (RFC, subtotal y fecha), y que el gate tiene quenadar la aritmetica. Un OCR
# que se equivoco en un digito casi siempre rompe `subtotal + IVA == total`, y
# esa es la comprobacion que atrapa el error. El descuento y la aritmetica
# hacen el mismo trabajo por los dos lados: el numero baja, y lo que lo baja
# esta verificado.
#
# Esta tabla tambien se escribe entera, con las ocho combinaciones, por la misma
# razon que la de arriba.
CONFIANZA_POR_CAMPOS_OCR: tuple[tuple[tuple[bool, bool, bool], float], ...] = (
    # (rfc, subtotal, fecha) -> confianza, leyendo de una FOTO
    ((False, False, False), 0.60),  # un total suelto de OCR: es ruido, no un dato
    ((False, False, True), 0.68),   # fecha y ya: no dice de quien ni cuanto
    ((True, False, False), 0.72),   # RFC sin subtotal no deja verificar nada
    ((False, True, False), 0.76),   # el subtotal solo, con fecha perdida
    ((True, False, True), 0.80),
    ((False, True, True), 0.86),    # dos de tres: a revision
    ((True, True, False), 0.88),    # RFC + subtotal, sin fecha
    ((True, True, True), 0.93),     # completo, y el gate exige que la aritmetica cuadre
)


def confianza_por_campos_ocr(
    tiene_rfc: bool,
    tiene_subtotal: bool,
    tiene_fecha: bool,
) -> float:
    """La confianza de un parseo hecho sobre texto que viene de OCR.

    Es una tabla aparte y no `confianza_por_campos` con un factor, porque un
    factor no puede expresar lo que pasa aqui: la diferencia entre "un total
    suelto de PDF" y "un total suelto de OCR" no es la misma que la diferencia
    entre "todo de PDF" y "todo de OCR". Un total sin RFC ni fecha es un dato
    dudoso en cualquier papel, y es ruido en una foto.
    """
    for (rfc, subtotal, fecha), confianza in CONFIANZA_POR_CAMPOS_OCR:
        if (rfc, subtotal, fecha) == (tiene_rfc, tiene_subtotal, tiene_fecha):
            return confianza
    raise AssertionError(
        "falta una combinacion en CONFIANZA_POR_CAMPOS_OCR: "
        f"rfc={tiene_rfc} subtotal={tiene_subtotal} fecha={tiene_fecha}"
    )


def _confianza_de_evidence(source: ConfidenceSource) -> float:
    """Un quinto escalon para las tablas de confianza: de donde salio el texto.

    Va en un solo punto a proposito. Si cada llamador eligiera la tabla, un
    escalon nuevo podria marcar con la tabla de PDF lo que leyo de una foto, y
    el numero seria mas alto que el de un PDF con la misma evidencia.
    """
    return confianza_por_campos_ocr if source is ConfidenceSource.OCR else confianza_por_campos


def _rfc_en_forma(valor: str | None) -> str | None:
    """El RFC solo si tiene la forma completa de uno mexicano.

    Sin esta comprobacion, lo que devuelve el modelo se guarda tal cual. Se vio
    con una foto real: `vision` devolvio `provider_tax_id="1905-01-01"`, que es
    un numero de ticket mal leido como si fuera una fecha. El gate lo marca
    `malformed_rfc` y manda el ticket a la cola, asi que no se aprueba como
    bueno; pero el dato basura queda EN LA FILA, y lo que el operador ve en la
    cola de revision es un RFC inventado al lado de su total.

    No es lo mismo "el sistema no sabe el RFC" (vacio, y el gate dice
    `rfc_missing` o nada) y "el sistema guardo esto" (basura, con forma de
    dato). Lo primero se corrige a mano; lo segundo hace dudar de toda la fila.
    Por eso se descarta aqui y no se deja pasar a la base.
    """
    if not valor:
        return None
    from app.services.parser_service import RFC_CANDIDATO_RE

    candidato = valor.strip().upper()
    if RFC_CANDIDATO_RE.fullmatch(candidato):
        return candidato
    return None


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
        # Pasa por `_rfc_en_forma`: lo que devuelve el modelo no se guarda sin
        # comprobar su forma. Ver ahi el caso de una foto real.
        provider_tax_id=_rfc_en_forma(invoice.provider_tax_id),
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
    resultado.confidence = _confianza_de_evidence(source)(
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


def _ocr_por_defecto(datos: bytes) -> "ResultadoOCR":
    """El OCR real. Se resuelve aqui y no al importar, por dos razones.

    Una: `app.services.ocr` no importa pytesseract ni EasyOCR al cargarse, pero
    si lo hiciera, importar `capture` arrastraria los dos. La app tiene que
    poder arrancar en una maquina sin Tesseract para poder reportar que no lo
    tiene.

    Dos: los tests inyectan un OCR falso por `capture_ticket(ocr_reader=...)` y
    asi no dependen de que haya un binario instalado. Que la suite pase en una
    maquina sin Tesseract no es un detalle: si dependiera del binario, el que
    no lo tiene veria tests rojos y aprenderia a saltarselos.
    """
    from app.services.ocr import leer_imagen

    return leer_imagen(datos)


async def capture_ticket(
    content: bytes,
    file_type: str,
    *,
    extract_from_image: ExtractFn | None = None,
    extract_from_text: ExtractTextFn | None = None,
    ocr_reader: OcrFn | None = None,
) -> TicketExtractionResult:
    """Punto de entrada unico de un comprobante.

    Los extractores se inyectan para poder probar la politica de cascada sin un
    modelo. Sin ellos se usa el extractor real.

    Args:
        content: bytes del archivo tal como lo subio el usuario.
        file_type: `image`, `pdf` o `text`.
        ocr_reader: como leer texto de una foto. Por defecto el OCR local. Se
            inyecta para probar la politica de cascada sin un binario de
            Tesseract instalado.

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
        return await _desde_imagen(content, extract_from_image, ocr_reader)

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
        return await _vision_pdf(content, extract_from_image, ocr_reader)

    if len(texto.strip()) < PDF_MIN_CHARS_PARA_INTENTAR:
        # Escaneado: no hay nada que parsear. Un regex sobre cadena vacia
        # devolveria ceros, y un ticket de ceros es un ticket falso.
        return await _vision_pdf(content, extract_from_image, ocr_reader)

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


def _ocr_de_paginas(paginas: list[bytes], ocr: OcrFn) -> TicketExtractionResult | None:
    """El mejor resultado de OCR sobre las paginas renderizadas, o `None`.

    `None` significa "el OCR no dio nada utilizable" y es distinto de "el OCR
    no esta instalado": los dos llevan a vision, pero se registran distinto
    porque uno se arregla instalando un paquete y el otro mirando la foto.

    Una sola pagina legible basta. En un comprobante de varias paginas, la que
    tiene el resumen es la que tiene el total, y las demas son articulos o
    condiciones. Se elige la de mayor total por la misma razon que en
    `_vision_pdf`: el resumen esta al final.
    """
    utiles: list[TicketExtractionResult] = []

    for indice, pagina in enumerate(paginas):
        try:
            leido = ocr(pagina)
        except OCRNoDisponible as exc:
            logger.info("OCR no disponible para la pagina %d: %s", indice + 1, exc)
            return None
        except Exception as exc:
            # Una pagina que el OCR no puede leer no invalida las otras: un
            # comprobante de tres paginas con una ilegible sigue siendo legible
            # por las otras dos.
            logger.warning("el OCR fallo en la pagina %d: %s", indice + 1, exc)
            continue

        texto = (leido.texto or "").strip()
        if len(texto) < OCR_MIN_CHARS_PARA_INTENTAR:
            continue

        resultado = _marcar_por_reglas(_parse_receipt_text(texto), ConfidenceSource.OCR)
        if _es_extraccion_util(resultado):
            utiles.append(resultado)

    if not utiles:
        return None

    if len(utiles) == 1:
        return utiles[0]

    mejor = max(utiles, key=lambda r: r.total_amount)
    # El origen se vuelve a poner explicito. Viene de `_marcar_por_reglas` como
    # `ocr`, pero si otro dia `_mejor_de` o una comparacion de aqui se llevan el
    # resultado mas alto sin su `confidence_source`, esta linea es la que
    # impide que un multi-pagina se reporte como si lo hubiera leido un modelo.
    mejor.confidence_source = ConfidenceSource.OCR
    return mejor


async def _vision_pdf(
    content: bytes,
    extract_from_image: ExtractFn | None,
    ocr_reader: OcrFn | None = None,
) -> TicketExtractionResult:
    """PDF sin capa de texto: se renderiza y se lee con OCR, luego con vision.

    El mismo orden que una foto suelta, y por la misma razon: un PDF escaneado
    es una foto dentro de un PDF. Antes de que existiera el OCR, cada escaneo
    iba directo a un modelo de vision; ahora se lee en local primero y solo se
    paga el modelo cuando la lectura local no da un comprobante.

    Que las paginas se rendericen para el OCR y no solo para vision es lo que
    hace que esto no sea un feature aparte: el mismo `render_pdf_pages` alimenta
    los dos caminos, y no hay una segunda manera de convertir un PDF en imagen.
    """
    ocr = ocr_reader or _ocr_por_defecto

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

    por_ocr = _ocr_de_paginas(paginas, ocr)
    if por_ocr is not None:
        return por_ocr

    if extract_from_image is None:
        return _ilegible(
            "el PDF no tiene capa de texto y no hay extractor de vision disponible"
        )

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


async def _desde_imagen(
    content: bytes,
    extract_from_image: ExtractFn | None,
    ocr_reader: OcrFn | None = None,
) -> TicketExtractionResult:
    """Foto o escaneo: primero OCR local, y vision solo si el OCR no alcanza.

    El orden es el que decide el costo. Antes de este escalon, una foto
    SIEMPRE iba a un modelo de vision, incluso cuando era un ticket impreso
    legible que Tesseract leia sin equivocarse. Con OCR primero:

    - una foto buena no toca ningun modelo, y sale con `confidence_source=ocr`,
      que es la verdad sobre quien leyo el papel;
    - una foto mala (borrosa, rotada, con la tinta corrida) cae a vision, que
      es el unico escalon que puede con ella, y queda registrada como `llm`, que
      tambien es la verdad.

    Que los dos caminos esten etiquetados distinto es lo que permite que
    el reporte de exactitud diga algo. Si los dos se guardaran como `llm`, el
    numero mezcla "el modelo leyo esto" con "Tesseract leyo esto y la foto
    estaba bien", y promedia dos cosas que no se parecen.

    El OCR no se consulta si no hay quien lo lea. Un `OCRNoDisponible` no es un
    fallo de este escalon: es la configuracion de la maquina, y se sigue al
    siguiente escalon igual, que es lo que hacia que la app sirviera antes de
    que existiera este modulo.
    """
    ocr = ocr_reader or _ocr_por_defecto

    try:
        leido = ocr(content)
    except OCRNoDisponible as exc:
        logger.info("OCR no disponible, se va directo a vision: %s", exc)
    except Exception as exc:
        # Un OCR que revienta (imagen corrupta, motor caido) no puede tumbar la
        # lectura: todavia queda vision. Se registra y se sigue.
        logger.warning("el OCR fallo (%s); se intenta vision", exc)
    else:
        texto = (leido.texto or "").strip()
        if len(texto) < OCR_MIN_CHARS_PARA_INTENTAR:
            logger.info(
                "el OCR devolvio %d caracteres, menos que el minimo; se intenta vision",
                len(texto),
            )
        else:
            # Aqui NO se llama a `extract_from_text`. A diferencia de un PDF con
            # texto, el texto de una foto es una transcripcion con errores: si
            # las reglas no lo entienden, es porque el documento no se leyo bien,
            # y un modelo al que se le pasa esa transcripcion va a suponer un
            # total. Vision, que mira el papel, tiene mas probabilidades que un
            # modelo leyendo los errores de Tesseract. Y asi una foto cuesta un
            # modelo, no dos.
            resultado = _marcar_por_reglas(
                _parse_receipt_text(texto), ConfidenceSource.OCR
            )
            if _es_extraccion_util(resultado):
                return resultado
            logger.info(
                "el OCR leyo el papel pero las reglas no lo entendieron "
                "(proveedor=%r total=%s); se mira la imagen",
                resultado.provider_name, resultado.total_amount,
            )

    return await _vision_de_imagen(content, extract_from_image)


async def _vision_de_imagen(content: bytes, extract_from_image: ExtractFn | None) -> TicketExtractionResult:
    """Vision: el ultimo escalon, el que mas caro es."""
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
