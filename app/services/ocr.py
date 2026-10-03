"""OCR local: lee la foto de un ticket sin llamar a ningun modelo.

Existe por una razon economica y no tecnica. La cascada ya sabia leer un PDF
digital con reglas y sin IA, y ya sabia enviar una foto a un modelo de vision.
Lo que no existia era el escalon intermedio: una foto de un ticket leida en la
maquina, gratis, y con la misma confianza baja que corresponde a una lectura
por reconocimiento optico.

Sin este escalon, la foto de un ticket obliga a elegir entre dos cosas malas:
gastarle un modelo a una imagen que se puede leer con Tesseract, o mandarla al
muestreo de exactitud como si fuera una lectura fiable. Las dos opciones
producen un numero de exactitud que no describe lo que el sistema sabe hacer.

Lo que este modulo NO hace:

- No corrige ni completa lo que el OCR no leyó. Un total que el OCR no encontro
  es un total que no se sabe, y se queda en `None` para que el gate lo marque
  `total_not_positive`. Rellenarlo con un cero produce un ticket que existe y
  no significa nada, que es peor que un ticket en la cola.
- No reintenta con otro motor a ciegas. Si Tesseract no esta instalado, el
  error dice "tesseract no esta instalado", no "OCR fallo". Un error que no
  dice que arreglar hace que cada foto falle y nadie sepa que hay que instalar
  un paquete.

Sobre los motores:

- `tesseract` (pytesseract) es el de serie. Necesita el BINARIO del sistema, no
  solo el paquete de Python: `brew install tesseract tesseract-lang` en macOS,
  `apt-get install tesseract-ocr tesseract-ocr-spa` en Debian.
- `easyocr` esta soportado y apagado por omision. Importa torch, que son
  ~2 GB, y descarga ~100 MB de pesos la primera vez. El repositorio ya tiene
  una regla sobre no arrastrar torch al arrancar, y easyocr lo hace al
  importarlo, no al usarlo: por eso se importa dentro de la funcion y nunca en
  el nivel del modulo.

Por eso nada de esto se importa al importar el modulo. Un `import pytesseract`
en el nivel superior hace que el API no levante en una maquina sin Tesseract,
aunque la app no use OCR nunca. La app tiene que levantar para poder decir "no
tienes Tesseract", no para no poder contestar.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

# Idiomas que se le piden a Tesseract. `spa` es el que hace falta para un
# comprobante mexicano; `eng` esta porque los tickets deStores y gasoline
# mezclan ingles en el mismo papel, y quitarlo degrada la lectura de "TOTAL"
# sin costo apreciable.
IDIOMA_POR_DEFECTO = "spa+eng"


class OCRNoDisponible(RuntimeError):
    """No hay motor de OCR utilizable en esta maquina.

    Distinto de "el OCR leyo mal". Esto es un fallo de instalacion: el error
    lleva la instruccion de como arreglarlo, porque un `OCRNoDisponible` que no
    dice que instalar hace que se reporte como "muchas fotos fallan" cuando lo
    unico que falta es un paquete del sistema.
    """


@dataclass(frozen=True)
class ResultadoOCR:
    """Lo que el OCR devolvio, y con que motor.

    `motor` se persiste. El reporte de exactitud agrupa por origen, y dentro de
    `ocr` conviven motores con tasas de error distintas: si manana se agrega
    EasyOCR y los dos se guardan como `ocr`, el promedio de los dos no describe
    a ninguno.
    """

    texto: str
    motor: str
    confianza_media: float | None = None


# ---------------------------------------------------------------------------
# Motores
# ---------------------------------------------------------------------------
#
# Cada uno se construye una vez y se reutiliza. Tesseract se resuelve por
# subprocesso en cada llamada, y EasyOCR carga los pesos en su constructor
# (~100 MB): reconstruirlo por foto es la diferencia entre un escaneo de un
# minuto y uno de diez. El lock es porque los dos son de estado compartido y
# una carpeta con varias fotos se procesa en el mismo hilo de forma secuencial,
# pero un `POST /scan` simultaneo los encontraria en la misma inicializacion.

_CACHES: dict[str, object] = {}
_CANDADO = threading.Lock()


def _motor_tesseract():
    """Tesseract via pytesseract, o `OCRNoDisponible` con la instruccion."""
    try:
        import pytesseract
    except ImportError as exc:
        raise OCRNoDisponible(
            "pytesseract no esta instalado. Agrega 'pytesseract>=0.3.10' a "
            "requirements.txt. El paquete de Python solo; hace falta tambien "
            "el binario del sistema."
        ) from exc

    # El ImportError de arriba no dice si el BINARIO esta. Son dos instalaciones
    # distintas que fallan distinto, y esta es la que mas confunde: se instala
    # el paquete, sigue fallando, y no queda claro que faltaba la otra.
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise OCRNoDisponible(
            "el binario de tesseract no esta instalado. En macOS: "
            "brew install tesseract tesseract-lang. En Debian: apt-get install "
            "tesseract-ocr tesseract-ocr-spa. El error original fue: "
            f"{exc}"
        ) from exc

    return pytesseract


def _motor_easyocr():
    """EasyOCR, o `OCRNoDisponible` con la instruccion."""
    try:
        import easyocr
    except ImportError as exc:
        raise OCRNoDisponible(
            "easyocr no esta instalado. Agrega 'easyocr>=1.7.2' a "
            "requirements.txt. La primera vez descarga ~100 MB de pesos."
        ) from exc

    with _CANDADO:
        instancia = _CACHES.get("easyocr")
        if instancia is None:
            # Idioma: solo `es` e `en`. EasyOCR con la lista completa de
            # languages=[] carga 40+ modelos, y de los comprobantes que pasan
            # por aqui ninguno esta en_coreano.
            logger.info("cargando EasyOCR; la primera vez descarga los pesos")
            instancia = easyocr.Reader(["es", "en"], gpu=False)
            _CACHES["easyocr"] = instancia
    return instancia


def obtener_motor(nombre: str = "tesseract"):
    """Devuelve el motor pedido, construyolo la primera vez."""
    clave = nombre.strip().lower()
    if clave == "tesseract":
        # No se cachea el modulo de pytesseract: importarlo es barato y
        # cachearlo solo ocultaria el error de "no esta instalado" en el
        # momento de arrancar en vez de en el momento de usarlo.
        return _motor_tesseract()
    if clave == "easyocr":
        return _motor_easyocr()
    raise OCRNoDisponible(
        f"motor de OCR desconocido: {nombre!r}. Use 'tesseract' o 'easyocr'."
    )


# ---------------------------------------------------------------------------
# Preprocesado
# ---------------------------------------------------------------------------
#
# Un ticket es papel chico, con letra pequena y poca tinta. Sin preprocesar,
# Tesseract lee menos de la mitad de lo que hay, y el resultado son letras
# cambiadas en los numeros: "1,100.00" se vuelve "l,l00.00" o "1,1OO.OO", y un
# total mal leido que aun asi pasa el gate es un error financiero.
#
# Solo se usa Pillow. OpenCV se podria usar para deskew, y seria mejor en
# fotos torcidas, pero es una dependencia de ~60 MB que ademas necesita libGL
# para importar. La ganancia no paga ese costo mientras los tickets lleguen
# escaneados o de camara sin rotar; si empiezan a llegar rotados, el deskew es
# el primer lugar donde mirar, y se agrega aqui con opencv detras del mismo
# `_preparar` que hoy no lo necesita.


def _preparar(datos: bytes) -> "object":
    """Escala, pasa a gris y sube contraste.

    Devuelve una imagen de Pillow lista para Tesseract. Si Pillow no esta, se
    devuelven los bytes tal cual: Tesseract acepta la imagen original, va a
    leer peor, y es preferible a no leer nada.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.warning("Pillow no esta instalado; el OCR va sin preprocesar")
        return datos

    import io

    try:
        with Image.open(io.BytesIO(datos)) as original:
            imagen = ImageOps.exif_transpose(original) or original
            # Sin esto, una foto de celular queda en miles de pixeles y
            # Tesseract se toma segundos por imagen. 2000 px de ancho alcanza
            # para leer un total en letra pequena de ticket.
            if imagen.width > 2000:
                alto = round(imagen.height * 2000 / imagen.width)
                imagen = imagen.resize((2000, alto))
            if imagen.mode != "L":
                imagen = imagen.convert("L")
            # AutoContrast sobre un gris de un ticket sube la diferencia entre
            # la tinta y el papel sin inventar detalle: no agrega pixeles, solo
            # estira lo que hay.
            return ImageOps.autocontrast(imagen)
    except Exception as exc:
        logger.warning("no se pudo preprocesar la imagen (%s); se usa original", exc)
        return datos


# Los grados que se prueban, en el orden en que se prueban.
#
# El orden NO es el de las frecuencias: es el que hace que el caso comum se
# resuelva en la primera vuelta. Una foto de ticket sale casi siempre derecha,
# asi que 0 va primero y sale barato; las demas se prueban solo si esa no sirvio.
ROTACIONES = (0, 90, 180, 270)

# Modos de segmentacion alternativos, para la segunda vuelta.
#
# 12 = "buscar texto disperso, sin asumir el orden de lectura", que es lo que
#      funciona en un comprobante con membrete, columnas y lineas punteadas.
# 11 = igual pero en un unico bloque, y es el mas tolerante con el ruido de
#      fondo (una foto con la carpeta y la mesa alrededor del papel).
#
# No se prueban mas porque cada uno multiplica el costo: son cuatro rotaciones
# por modo, y la segunda vuelta con dos modos son ocho lecturas extra. En una
# carpeta grande eso son minutos, y solo se llega aqui cuando la primera vuelta
# no produjo NINGUN comprobante, que es el caso raro.
_PSM_ALTERNATIVOS = (12, 11)


def _variantes(imagen: "object") -> list[tuple[int, "object"]]:
    """La misma imagen en las cuatro orientaciones.

    Por que hace falta, con el dato que lo motivo. En las tres fotos reales de
    la carpeta, Tesseract reporto por su cuenta la rotacion que necesitaba:

        1C2C52A2  Rotate: 270  (confianza 0.94)  -> se leyo basura
        IMG_4220   Rotate: 0                    -> se leyo bien
        IMG_4222   Rotate: 90    (confianza 0.53) -> se leyo basura

    O sea: la rotacion correcta ya estaba calculada y no se estaba usando. Un
    ticket de lado es un ticket donde no hay lineas horizontales de texto, y el
    OCR no inventa: devuelve ruido con una confianza que parece decente. Por eso
    la confianza sola NO alcanza para decidir, y por eso se prueban todas.
    """
    # `_preparar` devuelve los BYTES originales si Pillow no esta o si la
    # imagen no se pudo abrir. En ese caso no hay a quien rotar, y las cuatro
    # "variantes" serian la misma entrada leida cuatro veces: se devuelve solo
    # una, con su rotacion en cero, y el flujo sigue igual.
    if not hasattr(imagen, "rotate"):
        return [(0, imagen)]

    return [(g, imagen if g == 0 else imagen.rotate(g, expand=True)) for g in ROTACIONES]


@dataclass(frozen=True)
class Lectura:
    """Una lectura completa, con su nota de por que se puntua asi."""

    texto: str
    confianza: float | None
    rotacion: int
    puntos: int


def _calidad_del_texto(texto: str) -> int:
    """Cuanto se parece esto a texto de verdad y no a ruido. De 0 a 5.

    Existe por un motivo concreto y medido. En la foto girada 270 grados, las
    CUATRO rotaciones sacaron 0 puntos de evidencia: ninguna produjo un
    comprobante. Al empatar todas, el codigo se quedaba con la primera, que era
    la que estaba de lado y era la peor de las cuatro. La foto se leia bastante
    bien a 270 grados y se estaba tirando a la basura.

    Sin este desempate, "ninguna rotacion sirvio" se convierte en "se quedo con
    la primera, por casualidad". Con el, se queda con la que mas texto util
    produjo. Y esto NO afirma que esa lectura este bien: afirmar eso es lo que
    hace el gate, con los datos del ticket. Aqui solo se ordena cual de las
    lecturas probadas es la menos mala. Si tampoco llega al corte, el ticket va
    a la cola con su motivo.
    """
    if not texto:
        return 0

    alfanumericos = sum(1 for c in texto if c.isalnum() or c in " .,;:-")
    proporcion = alfanumericos / max(len(texto), 1)

    # Palabras que solo aparecen en un comprobante en espanol, y que son lo que
    # distingue texto real de las letras sueltas que devuelve el OCR cuando no
    # hay nada que leer.
    marcas = ("total", "subtotal", "iva", "importe", "rfc", "fecha", "caja", "cajero")
    minusculas = texto.lower()
    encontradas = sum(1 for m in marcas if m in minusculas)

    puntos = 0
    if proporcion > 0.75:
        puntos += 2
    elif proporcion > 0.6:
        puntos += 1
    if len(texto) > 200:
        puntos += 1
    if len(texto) > 600:
        puntos += 1
    # Las marcas pesan mas que la proporcion: hay OCR que lee 200 caracteres de
    # ruido con 95% de alfanumericos, y "TOTAL" en medio de eso vale mas.
    puntos += min(encontradas, 2)
    return min(puntos, 5)


def _puntuar(texto: str, confianza: float | None) -> int:
    """Cuanto se acerca esta lectura a ser un comprobante.

    Se puntua con EVIDENCIA del documento, no con la confianza de Tesseract. Y
    no es un detalle: la confianza de Tesseract dice cuanto esta seguro el OCR
    de cada PALABRA, no si el resultado es un comprobante. En las tres fotos
    reales, la de mayor confianza de rotacion (0.94, la que estaba de lado) leyo
    basura con una confianza de palabras de 0.54: media, no baja. Si se eligiera
    por confianza, se habria elegido mal.

    La evidencia vale 10 VECES mas que la calidad del texto, y eso define el
    orden: primero que sea un comprobante, y solo si ninguna lectura lo es, que
    sea la que mas texto se leyo.

      40  el total cuadra con subtotal + IVA
      20  hay un total (> 0)
      10  hay un RFC con la forma completa
      0-5 calidad del texto (desempate)
    """
    from app.services.parser_service import _parse_receipt_text

    puntos = 0
    try:
        r = _parse_receipt_text(texto)
    except Exception:
        return _calidad_del_texto(texto)

    if r.total_amount > 0:
        puntos += 20
    if r.provider_tax_id:
        puntos += 10
    if (
        r.subtotal is not None
        and r.tax_amount is not None
        and r.total_amount > 0
        and r.subtotal + r.tax_amount == r.total_amount
    ):
        puntos += 40
    return puntos + _calidad_del_texto(texto)


def _es_buena(lectura: Lectura) -> bool:
    """Suficientemente buena para dejar de probar.

    El corte es 70: un comprobante completo (total + RFC + aritmetica) mas algo
    de calidad. Con eso el ticket tiene los tres datos que hacen falta para
    contabilizarlo, asi que seguir probando no puede mejorarlo: solo gasta CPU.
    """
    return lectura.puntos >= 70


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def leer_imagen(datos: bytes, motor: str = "tesseract") -> ResultadoOCR:
    """Saca el texto de una imagen.

    Args:
        datos: bytes de la imagen (JPEG, PNG, TIFF...).
        motor: `tesseract` (default) o `easyocr`.

    Returns:
        `ResultadoOCR` con el texto leido, el motor que lo leyo y la confianza
        media reportada, que en Tesseract sale de las palabras individuales y no
        es comparable con la confianza del modelo: por eso va en su propio campo
        y no se copia a `TicketExtractionResult.confidence`. Esa confianza la
        calcula `confianza_por_campos` a partir de la evidencia, y mezclar las
        dos seria sumar dos cosas que no son comparables.

    Raises:
        OCRNoDisponible: el motor no esta instalado o el binario no existe.
    """
    if not datos:
        raise OCRNoDisponible("la imagen esta vacia")

    if not settings.OCR_ENABLED:
        raise OCRNoDisponible("OCR esta apagado (OCR_ENABLED=false)")

    if motor.strip().lower() == "easyocr":
        return _leer_easyocr(datos)
    return _leer_tesseract(datos)


def _leer_tesseract(datos: bytes) -> ResultadoOCR:
    """Lee la imagen probando las orientaciones y quedandose con la mejor.

    El usuario no ajusta nada: el sistema prueba, puntua y decide. Ver `_puntuar`
    para por que se puntua con evidencia del documento y no con la confianza de
    Tesseract.
    """
    pytesseract = obtener_motor("tesseract")
    base = _preparar(datos)

    mejor: Lectura | None = None
    psm_por_usar = settings.OCR_PSM

    for rotacion, imagen in _variantes(base):
        lectura = _una_lectura(pytesseract, imagen, rotacion, psm_por_usar)
        if mejor is None or lectura.puntos > mejor.puntos:
            mejor = lectura
        # Corte temprano. Una lectura con total, RFC y aritmetica cuadrada ya
        # tiene los tres datos que hacen falta para contabilizar el ticket, y
        # las otras rotaciones no pueden mejorar eso: solo pueden gastar CPU. Es
        # lo que hace que el caso comum (una foto derecha y limpia) cueste una
        # sola lectura y no cuatro.
        if _es_buena(lectura):
            logger.info(
                "OCR: rotacion %d gana con %d puntos (total+RFC+aritmetica)",
                rotacion, lectura.puntos,
            )
            break

    # Segunda vuelta con otros modos de segmentacion, SOLO si ninguna rotacion
    # produjo un comprobante.
    #
    # Por que existe. `PSM 6` supone "un bloque de texto uniforme", que es un
    # ticket normal. Un comprobante impreso con dos columnas, lineas
    # punteadas y un membrete es otra cosa, y con `PSM 6` se leen palabras sueltas
    # de las lineas meaningful. En la foto real girada, `PSM 6` saco 108
    # palabras y ninguna armaba un comprobante, mientras que `PSM 12` (buscar
    # texto disperso) si leyo el nombre del emisor completo.
    #
    # Se hace al final y no por rotacion porque duplica el costo: en el caso
    # normal, que es una foto bien tomada, la primera vuelta ya corta en la
    # primera rotacion y esto nunca corre.
    if mejor is not None and mejor.puntos < 20:
        for psm in _PSM_ALTERNATIVOS:
            for rotacion, imagen in _variantes(base):
                lectura = _una_lectura(pytesseract, imagen, rotacion, psm)
                if lectura.puntos > mejor.puntos:
                    mejor = lectura
                    logger.info(
                        "OCR: rotacion %d con psm %d (%d puntos) supera al psm %d",
                        rotacion, psm, lectura.puntos, settings.OCR_PSM,
                    )
                if _es_buena(lectura):
                    break
            if mejor.puntos >= 70:
                break

    if mejor is None:  # pragma: no cover - _variantes siempre devuelve al menos una
        raise OCRNoDisponible("no se pudo leer la imagen en ninguna orientacion")

    if mejor.puntos < 20:
        # Ninguna rotacion produjo algo con forma de comprobante. Se devuelve el
        # mejor intento igual, para que la cascada siga y el gate mande el
        # ticket a la cola con su motivo: el archivo NO se pierde y queda
        # visible que no se pudo leer. Perderlo seria peor que mostrarlo mal.
        logger.info(
            "OCR: ninguna orientacion produjo un comprobante (mejor=%d puntos, "
            "rotacion=%d); el ticket ira a la cola",
            mejor.puntos, mejor.rotacion,
        )

    return ResultadoOCR(
        texto=mejor.texto,
        motor="tesseract",
        confianza_media=mejor.confianza,
    )


def _una_lectura(pytesseract, imagen, rotacion: int, psm: int | None = None) -> Lectura:
    """Una pasada de OCR sobre una imagen, ya en una orientacion.

    El `image_to_data` es el mismo OCR que haria `image_to_string`; lo unico que
    agrega es la confianza por palabra. Sin el habria que inventar una
    confianza, que es justo lo que este modulo evita.
    """
    # `image_to_data` en vez de `image_to_string` porque trae la confianza por
    # palabra. El costo es el mismo OCR; lo unico que se agrega es el parseo de
    # la salida.
    datos_ocr = pytesseract.image_to_data(
        imagen,
        lang=settings.OCR_IDIOMA or IDIOMA_POR_DEFECTO,
        config=f"--psm {psm if psm is not None else settings.OCR_PSM}",
        output_type=pytesseract.Output.DICT,
    )

    # Las lineas se reconstruyen con los numeros de bloque/parrafo/linea que
    # Tesseract ya calculo, y NO se unen todas las palabras con saltos de linea.
    #
    # Esto no es cosmetico: es la diferencia entre que el parser entienda el
    # papel o no. El parser por reglas esta anclado a la forma "label: valor" en
    # una sola linea (`RFC: XXX...`, `TOTAL: 1,100.00`). Tesseract SI sabe que
    # esas dos palabras estaban en la misma linea y lo dice en `line_num`, pero
    # si se emite una palabra por linea el texto sale asi:
    #
    #     RFC:
    #     TRAM910101XXX
    #
    # y ningun regex del proyecto encuentra un RFC ahi. Peor: la linea del valor
    # sola parece un proveedor, asi que el ticket se guardaba con el RFC como
    # nombre de comercio y sin subtotal, sin IVA, sin total y sin fecha, con la
    # confianza mas alta que produce esta cascada. Un ticket vacio generado por
    # una lectura casi perfecta: el peor resultado posible, porque no parece un
    # fallo de OCR sino un comprobante raro.
    #
    # Se agrupa por (bloque, parrafo, linea) y no solo por linea, porque dos
    # columnas de un mismo ticket pueden tener lineas con el mismo numero.
    lineas: dict[tuple[int, int, int], list[str]] = {}
    confianza_total = 0.0
    palabras_con_confianza = 0

    for i, palabra in enumerate(datos_ocr.get("text", [])):
        if not palabra or not str(palabra).strip():
            continue

        clave = (
            int(datos_ocr["block_num"][i]),
            int(datos_ocr["par_num"][i]),
            int(datos_ocr["line_num"][i]),
        )
        lineas.setdefault(clave, []).append(str(palabra).strip())

        try:
            valor = float(datos_ocr["conf"][i])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if valor >= 0:
            confianza_total += valor
            palabras_con_confianza += 1

    media = (
        round(confianza_total / palabras_con_confianza / 100.0, 4)
        if palabras_con_confianza
        else None
    )

    # Se ordena por clave: en la practica Tesseract ya las entrega en orden de
    # lectura, pero depender de eso hace que un cambio de version del motor
    # reordene las lineas y el parser lea un comprobante al reves.
    texto = "\n".join(" ".join(lineas[clave]) for clave in sorted(lineas))

    return Lectura(
        texto=texto,
        confianza=media,
        rotacion=rotacion,
        puntos=_puntuar(texto, media),
    )


def _lineas_desde_cajas(detecciones: list) -> tuple[str, float | None]:
    """Reconstruye las LINEAS de una lista de cajas de EasyOCR.

    Por que hace falta, y por que no es un detalle de forma.

    EasyOCR devuelve `[bbox, texto, confianza]` por_region, sin decir que palabras
    estan en la misma linea. Tesseract si lo dice: `image_to_data` trae
    bloque, parrafo y linea, y `_una_lectura` los usa. Sin esa informacion, el
    texto sale una palabra por linea y el parser por reglas no encuentra nada,
    porque esta anclado a la forma `label: valor` en UNA linea.

    Medido con las fotos reales de la carpeta, antes y despues de agrupar:

        sin agrupar:  'P.TOTAL'      <- etiqueta sola
                     '97.56'        <- el importe, dos lineas mas abajo

        agrupado:     'TOTAL 97.56'  <- lo que el parser entiende

    Y el caso grave: `97.56` se lee con confianza **1.00** y sin agrupar no llega
    a ningun sitio. El numero estaba bien leido todo el tiempo; lo que faltaba
    era la linea.

    El criterio de agrupar es solapamiento VERTICAL, no proximidad: dos palabras
    estan en la misma linea si comparten banda vertical. La distancia horizontal
    no dice nada, porque en un ticket el importe esta en la columna de al lado,
    lejos de su etiqueta pero en la misma fila.

    `detail=1` de EasyOCR no garantiza orden de lectura, asi que el orden se
    reconstruye aqui: de arriba abajo, y dentro de cada linea de izquierda a
    derecha, que es como lo lee una persona.
    """
    cajas: list[dict] = []
    for deteccion in detecciones:
        if len(deteccion) < 2:
            continue
        bbox, texto = deteccion[0], str(deteccion[1])
        if not texto.strip():
            continue
        try:
            ys = [float(p[1]) for p in bbox]
            xs = [float(p[0]) for p in bbox]
        except (TypeError, ValueError, IndexError):
            continue
        confianza = None
        if len(deteccion) > 2:
            try:
                confianza = float(deteccion[2])
            except (TypeError, ValueError):
                confianza = None
        cajas.append({
            "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
            "texto": texto, "conf": confianza,
        })

    if not cajas:
        return "", None

    # De arriba abajo. El desempate por x es lo que hace que dos palabras en la
    # misma banda salgan en orden de lectura y no en el orden en que el detector
    # las devolvio.
    cajas.sort(key=lambda c: (c["y0"], c["x0"]))

    lineas: list[dict] = []
    for caja in cajas:
        alto_caja = caja["y1"] - caja["y0"]
        colocada = False
        for linea in lineas:
            solape = min(linea["y1"], caja["y1"]) - max(linea["y0"], caja["y0"])
            # La mitad del alto mas pequeno: dos textos comparten linea si se
            # pisan mas de la mitad de uno de los dos. Es lo que distingue "el
            # TOTAL y su importe" de "el TOTAL y la linea de debajo".
            if solape > 0.5 * min(linea["y1"] - linea["y0"], alto_caja):
                linea["cajas"].append(caja)
                linea["y0"] = min(linea["y0"], caja["y0"])
                linea["y1"] = max(linea["y1"], caja["y1"])
                colocada = True
                break
        if not colocada:
            lineas.append({"y0": caja["y0"], "y1": caja["y1"], "cajas": [caja]})

    textos: list[str] = []
    confianza_total = 0.0
    con_confianza = 0
    for linea in lineas:
        palabras = sorted(linea["cajas"], key=lambda c: c["x0"])
        textos.append(" ".join(p["texto"] for p in palabras))
        for p in palabras:
            if p["conf"] is not None:
                confianza_total += p["conf"]
                con_confianza += 1

    media = (
        round(confianza_total / con_confianza, 4) if con_confianza else None
    )
    return "\n".join(textos), media


def _leer_easyocr(datos: bytes) -> ResultadoOCR:
    lector = obtener_motor("easyocr")

    # EasyOCR acepta una ruta, bytes o un array de numpy. Una imagen de Pillow
    # NO la acepta, y la version actual lo dice:
    #
    #     ValueError: Invalid input type. Supporting format = string, bytes, numpy array
    #
    # Este codigo le pasaba `Image.open(...)` a `readtext`, asi que la rama de
    # easyocr de este modulo **nunca funciono**: "easyocr esta soportado" era una
    # verdad a medias. Medido y reproducido; ver `scripts/medir_dos_motores.py`.
    #
    # Ademas EasyOCR no aplica el preprocesado de `_preparar` (exif_transpose,
    # escala, autocontraste). Sin el, comparar los dos motores seria comparar
    # dos motores con entradas distintas, y el resultado no diria nada sobre el
    # motor: una foto de iPhone llega rotada 90 grados.
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - easyocr arrastra numpy
        raise OCRNoDisponible("EasyOCR necesita numpy") from exc

    imagen = _preparar(datos)
    try:
        rgb = np.array(imagen.convert("RGB"), dtype=np.uint8)
    except Exception as exc:
        raise OCRNoDisponible(f"EasyOCR no pudo preparar la imagen: {exc}") from exc

    try:
        detecciones = lector.readtext(rgb, detail=1, paragraph=False)
    except Exception as exc:
        raise OCRNoDisponible(f"EasyOCR no pudo leer la imagen: {exc}") from exc

    texto, confianza = _lineas_desde_cajas(detecciones)
    return ResultadoOCR(texto=texto, motor="easyocr", confianza_media=confianza)


def disponibilidad() -> dict[str, object]:
    """Que motores se pueden usar ahora mismo, para el endpoint de estado.

    Se responde con esto y no con un booleano porque "OCR no funciona" y "OCR
    esta apagado" piden acciones opuestas, y un booleano las hace la misma.
    """
    motores: dict[str, dict[str, object]] = {}
    for nombre in ("tesseract", "easyocr"):
        try:
            obtener_motor(nombre)
        except OCRNoDisponible as exc:
            motores[nombre] = {"disponible": False, "motivo": str(exc)}
        except Exception as exc:  # pragma: no cover - solo motores exoticos
            motores[nombre] = {
                "disponible": False,
                "motivo": f"fallo inesperado al cargar el motor: {exc}",
            }
        else:
            motores[nombre] = {"disponible": True, "motivo": None}

    return {
        "ocr_habilitado": settings.OCR_ENABLED,
        "idioma": settings.OCR_IDIOMA,
        "motores": motores,
    }
