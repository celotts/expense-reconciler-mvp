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
    pytesseract = obtener_motor("tesseract")
    imagen = _preparar(datos)

    # `image_to_data` en vez de `image_to_string` porque trae la confianza por
    # palabra. El costo es el mismo OCR; lo unico que se agrega es el parseo de
    # la salida. Sin el se tendria que inventar una confianza, que es
    # precisamente lo que este modulo evita.
    datos_ocr = pytesseract.image_to_data(
        imagen,
        lang=settings.OCR_IDIOMA or IDIOMA_POR_DEFECTO,
        config=f"--psm {settings.OCR_PSM}",
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
    texto = "\n".join(
        " ".join(lineas[clave]) for clave in sorted(lineas)
    )

    return ResultadoOCR(texto=texto, motor="tesseract", confianza_media=media)


def _leer_easyocr(datos: bytes) -> ResultadoOCR:
    import io

    lector = obtener_motor("easyocr")
    try:
        from PIL import Image
    except ImportError:
        imagen = datos
    else:
        try:
            imagen = Image.open(io.BytesIO(datos)).convert("RGB")
        except Exception as exc:
            raise OCRNoDisponible(f"EasyOCR no pudo abrir la imagen: {exc}") from exc

    lineas = lector.readtext(imagen, detail=1)
    textos: list[str] = []
    confianza_total = 0.0
    for linea in lineas:
        # EasyOCR devuelve (bbox, texto, confianza); el orden de los elementos
        # no esta garantizado entre versiones, asi que se desempaca por posicion
        # en vez de por nombre.
        if len(linea) < 3:
            continue
        texto = linea[1]
        confianza = linea[2]
        if not texto or not str(texto).strip():
            continue
        textos.append(str(texto))
        try:
            confianza_total += float(confianza)
        except (TypeError, ValueError):
            continue

    media = (
        round(confianza_total / len(textos), 4) if textos else None
    )
    return ResultadoOCR(texto="\n".join(textos), motor="easyocr", confianza_media=media)


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
