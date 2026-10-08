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
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - solo para el type checker
    # Pillow se importa DENTRO de `_preparar`, y por una razon que no es de
    # estilo: si el paquete falta, el OCR sigue funcionando con los bytes
    # originales (ver el docstring de `_preparar`). Importarlo arriba para que el
    # type checker lo viera haria que el modulo no se pudiera ni cargar en esa
    # maquina, que es justo lo que se quiere evitar.
    from PIL import Image

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
    # Las MISMAS lineas de `texto`, pero con la geometria: cada palabra con su
    # `x0`/`x1`/`y0`/`y1`. Antes `_lineas_desde_cajas` calculaba esas cajas y las
    # tiraba, dejando solo el texto pegado.
    #
    # POR QUE SE CONSERVAN
    # ===================
    #
    # Para leer lineas de producto no hace falta entender el texto: hace falta
    # saber DONDE esta cada palabra. En un ticket, la descripcion esta a la
    # izquierda y los numeros alineados a la derecha, y eso es geometria.
    #
    # Y el detalle que hace que sirva con OCR mediocre: una `l` leida donde iba
    # un `1` **no mueve la caja**. Las coordenadas son tan fiables como el resto,
    # y el texto puede estar destruido mientras la posicion es correcta. En el
    # caso real de la base (`IMG_4220.jpeg`) el texto sale como
    # "G 1.028 HILANESA DE PECHU 94.90" pero los tres numeros —1.028, 94.90,
    # 97.56— son los correctos, y `1.028 * 94.90 == 97.56` cuadra al centavo.
    #
    # Ver `app/services/parser_lineas.py`, que es quien lo usa.
    lineas: tuple[dict, ...] = ()


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


# La caja del papel, por brillo. Ver `_recortar_el_papel`.
_UMBRAL_PAPEL = 140
# Se descartan estos quantiles de cada borde. No es un margen arbitrario: el
# marco de una pantalla o el borde de una mesa tambien son claros, y sin
# descartarlos la caja se sale del papel. Medido sobre IMG_4316: con el 2% la caja
# es de 1521x3664 sobre 3024x4032, y sin el la caja se come la pantalla entera.
_QUANTILES_BORDE = 0.02
# Por debajo de esta fraccion del area, NO se recorta. Ver la guarda.
_FRACCION_MINIMA = 0.15
# Por ENCIMA tampoco se recorta, y esto se olvido en la primera version.
#
# MEDIDO sobre las siete fotos reales de la carpeta, con y sin recorte:
#
#     IMG_4316   papel al  46%   0.00 -> 51.50   GANA
#     AA4D0E8F   papel al  90%   97.56 -> 0.00  PIERDE
#     EE2C6866   papel al  85%   117 -> 0.00    PIERDE
#     los otros cuatro                          igual
#
# Es decir: la primera version ARRASTRABA DOS comprobantes correctos por cada uno
# que arreglaba. Solo se vio al medir los siete; con los tres de `IMG_*` el
# resultado era "1 gana, 0 pierde" y el cambio habria entrado.
#
# La causa es que los quantiles al 2% recortan un 8-15% del area SIEMPRE. Cuando
# el papel ya llena el cuadro, ese 8-15% no es fondo: es el borde del
# comprobante, y ahi viven cosas como el importe de una linea de detalle. Por eso
# el recorte solo tiene sentido cuando hay algo que descartar de verdad.
_FRACCION_MAXIMA = 0.75


def _recortar_el_papel(imagen):
    """Recorta al papel claro. Devuelve la imagen sin tocar si no se puede.

    ## POR QUE ESTO EXISTE

    Medido sobre `IMG_4316.HEIC`, una foto real de un Oxxo: el comprobante
    ocupa el 46% del cuadro y el resto es la pantalla de un editor, con texto de
    alto contraste. Tesseract lee LAS DOS SUPERFICIES, y el parser agarra la
    linea que reconoce. El total salia `1.50` —un fragmento de `51.50` que cayo
    en la linea siguiente— cuando el papel decia `51.50`.

    `1.50` es peor que `0.00`: con cero, el gate emite `total_not_positive` y hay
    una razon para desconfiar. Con un positivo, ese check no dice nada y el
    gasto queda subreportado sin que nada lo delate. Recortando, el mismo
    archivo da `51.50`.

    ## POR QUE NO SE USA UN MODELO DE VISION

    Medido en esta maquina: `moondream` (1.7 GB) degenera en repeticiones y no
    lee el comprobante, y un modelo que si lo lee necesita 8-11 GB de RAM contra
    un tope de 4 GB. El recorte es CPU pura, no pide memoria, y dio el numero
    correcto. Es la opcion que no degrada el equipo.

    ## LA GUARDA, Y POR QUE NO ES OPCIONAL

    El recorte por brillo tiene un fallo obvio: una foto OSCURA de un papel
    claro deja poca region por encima del umbral, la caja sale diminuta y se
    recorta el ticket en vez del fondo — o sea, se empeora justo el caso que se
    queria arreglar.

    Por eso, si la region clara cubre menos de `_FRACCION_MINIMA` del area, no
    se recorta y se devuelve la imagen tal cual. Sin esa guarda, este cambio
    arregla una foto y rompe otras, y nadie lo notaria porque las otras ya
    salian mal.

    ## POR QUE QUANTILES Y NO EL BBOX EXACTO

    El rectangulo exacto de "pixeles claros" se va hasta el pixel que tenga
    ruido, y una sola fila clara suelta (un brillo en el fondo) estira la caja
    hasta el borde. Los quantiles tiran el 2% de cada lado, que es lo que se
    midio que funciona en las fotos reales.
    """
    import numpy as np

    gris = imagen if imagen.mode == "L" else imagen.convert("L")
    claros = np.array(gris) > _UMBRAL_PAPEL
    if not claros.any():
        return imagen, 0.0

    ys, xs = np.where(claros)
    x0 = float(np.quantile(xs, _QUANTILES_BORDE))
    x1 = float(np.quantile(xs, 1 - _QUANTILES_BORDE))
    y0 = float(np.quantile(ys, _QUANTILES_BORDE))
    y1 = float(np.quantile(ys, 1 - _QUANTILES_BORDE))

    caja = (int(x0), int(y0), int(x1), int(y1))
    fraccion = ((caja[2] - caja[0]) * (caja[3] - caja[1])) / (imagen.width * imagen.height)

    # LA GUARDA, de los dos lados.
    #
    # Por abajo: una foto oscura de un papel claro deja poca region clara, la
    # caja sale diminuta y se recorta el ticket en vez del fondo. Sin esta linea,
    # este cambio arregla una foto y rompe otras, y nadie lo notaria porque las
    # otras ya salian mal.
    #
    # Por arriba: si el papel ya llena el cuadro, el recorte de los quantiles se
    # come el borde del comprobante. MEDIDO: con solo el piso, el recorte perdia
    # `AA4D0E8F` (97.56 -> 0.00) y `EE2C6866` (117 -> 0.00) mientras arreglaba
    # `IMG_4316` (0.00 -> 51.50). Arreglar uno y romper dos es peor que no
    # arreglar ninguno.
    if fraccion < _FRACCION_MINIMA or fraccion > _FRACCION_MAXIMA:
        return imagen, fraccion

    recortada = imagen.crop(caja)
    # Una caja degenerada (un solo pixel de alto o de ancho) no es una foto: es
    # ruido que paso el umbral, y recortar ahi deja una imagen que Tesseract
    # rechaza. Se descarta igual que una region demasiado chica.
    if recortada.width < 50 or recortada.height < 50:
        return imagen, fraccion

    return recortada, fraccion


def _preparar(datos: bytes) -> "Image.Image | bytes":
    """Recorta al papel, escala, pasa a gris y sube contraste.

    Devuelve una imagen de Pillow lista para Tesseract. Si Pillow no esta, se
    devuelven los bytes tal cual: Tesseract acepta la imagen original, va a
    leer peor, y es preferible a no leer nada.

    Por eso el tipo de retorno es la union y no `Image.Image`: hay dos
    respuestas legitimas y quien llama tiene que saber distinguirla. Tesseract
    acepta las dos; EasyOCR no, y por eso `_leer_easyocr` comprueba antes de
    llamar a `.convert` en vez de descubrirlo con un `AttributeError` dentro de
    un `except` que dice "no se pudo preparar la imagen".

    El recorte va DESPUES de `exif_transpose` y ANTES del reescalado. En ese
    orden por dos motivos: la caja se mide con el papel derecho, que es donde
    esta el texto; y recortar antes de reducir hace que los 2000 px finales
    apunten al comprobante y no al escritorio entero, o sea que el total legible
    mejora aunque el ancho final sea el mismo.
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

            recortada, fraccion = _recortar_el_papel(imagen)
            if recortada is not imagen:
                logger.info(
                    "recorte de papel: %.0f%% del cuadro (%.0fx%.0f de %dx%d)",
                    fraccion * 100, recortada.width, recortada.height,
                    imagen.width, imagen.height,
                )
            imagen = recortada

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
    """Una lectura completa, con su nota de por que se puntua asi.

    `lineas` es la geometria de ESA lectura —la de la rotacion que gano—, no la
    de todas. Se propaga al `ResultadoOCR` para que `parser_lineas.py` pueda
    trabajar con las coordenadas del texto que realmente se eligio, que es el unico
    que se guardo.
    """

    texto: str
    confianza: float | None
    rotacion: int
    puntos: int
    lineas: tuple[dict, ...] = ()


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
        lineas=tuple(mejor.lineas),
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

    # La geometria de la MISMA pasada, con la misma clave de linea. Se construye
    # aqui y no aparte para que no puedan desincronizarse: si el texto se agrupa
    # por (bloque, parrafo, linea) y las cajas por otra cosa, `parser_lineas.py`
    # leeria las palabras de una linea con las coordenadas de otra, que es peor
    # que no devolver nada.
    geometria: dict[tuple[int, int, int], list[dict]] = {}
    ys_por_clave: dict[tuple[int, int, int], tuple[int, int]] = {}

    for i, palabra in enumerate(datos_ocr.get("text", [])):
        if not palabra or not str(palabra).strip():
            continue

        clave = (
            int(datos_ocr["block_num"][i]),
            int(datos_ocr["par_num"][i]),
            int(datos_ocr["line_num"][i]),
        )
        limpio = str(palabra).strip()
        lineas.setdefault(clave, []).append(limpio)

        # La geometria es MEJOR ESFUERZO y la confianza NO depende de ella.
        #
        # Antes esta parte iba con un `continue` en el `except`, y eso hacia que
        # un `image_to_data` sin claves `left`/`top`/`width`/`height` dejara la
        # confianza en `None` —porque el `continue` se comia tambien la palabra
        # que venia justo despues—. Medido: `test_la_confianza_del_ocr_se_promedia
        #_por_palabra` devolvio `None` en vez de `0.8`.
        #
        # Aqui el fallo se registra como lo que es: la palabra entra al texto,
        # sin caja. `parser_lineas.py` working con menos geometria es mejor que
        # perder la confianza de una lectura que si funciono.
        try:
            x0 = int(datos_ocr["left"][i])
            y0 = int(datos_ocr["top"][i])
            w = int(datos_ocr["width"][i])
            h = int(datos_ocr["height"][i])
        except (TypeError, ValueError, KeyError, IndexError):
            x0 = None
        if x0 is not None:
            geometria.setdefault(clave, []).append(
                {"x0": x0, "x1": x0 + w, "texto": limpio}
            )
            y0p, y1p = ys_por_clave.get(clave, (y0, y0 + h))
            ys_por_clave[clave] = (min(y0p, y0), max(y1p, y0 + h))

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
        # La geometria de ESTA lectura, ordenada igual que el texto. Se va con
        # ella a `ResultadoOCR` para que `parser_lineas.py` tenga las coordenadas
        # de la rotacion que gano, que es la unica que importa.
        lineas=tuple(
            {
                "clave": list(clave),
                # `y0` y `y1` se recuperan de `datos_ocr` con los indices de la
                # linea, porque `geometria` guarda solo `x0`/`x1`: la coordenada
                # vertical es lo que `parser_lineas.py` usa para saber si dos
                # palabras estan en la misma fila.
                "y0": ys_por_clave.get(clave, (0, 0))[0],
                "y1": ys_por_clave.get(clave, (0, 0))[1],
                "palabras": sorted(
                    geometria.get(clave, []), key=lambda c: c["x0"]
                ),
            }
            for clave in sorted(lineas)
        ),
    )


def _lineas_desde_cajas(detecciones: list) -> tuple[str, float | None, tuple[dict, ...]]:
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
        # TRES valores, siempre. La llamada de arriba desempaqueta tres
        # (`texto, confianza, geometria`) y este `return` devolvia dos: con
        # EasyOCR sin detecciones —una foto en blanco, o texto que el detector no
        # vio— eso era un `ValueError: not enough values to unpack` fuera del
        # `try` que lo rodea, y el motor reportaba un fallo de lectura en vez de
        # "no vi texto". La geometria vacia es la respuesta honesta.
        return "", None, ()

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
    # La geometria viaja con el texto. Antes se descartaba aqui, y
    # `parser_lineas.py` no tenia con que trabajar.
    geometria = tuple(
        {
            "y0": linea["y0"],
            "y1": linea["y1"],
            "palabras": [
                {
                    "x0": c["x0"],
                    "x1": c["x1"],
                    "texto": c["texto"],
                    "conf": c["conf"],
                }
                for c in sorted(linea["cajas"], key=lambda c: c["x0"])
            ],
        }
        for linea in lineas
    )
    return "\n".join(textos), media, geometria


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
    # `_preparar` devuelve los bytes sin tocar cuando Pillow no esta, y EasyOCR
    # no los acepta. Decirlo aqui evita que el error salga como "'bytes' object
    # has no attribute 'convert'" debajo de un mensaje que habla de la imagen.
    if isinstance(imagen, bytes):
        raise OCRNoDisponible("EasyOCR necesita Pillow para preparar la imagen")
    try:
        rgb = np.array(imagen.convert("RGB"), dtype=np.uint8)
    except Exception as exc:
        raise OCRNoDisponible(f"EasyOCR no pudo preparar la imagen: {exc}") from exc

    try:
        detecciones = lector.readtext(rgb, detail=1, paragraph=False)
    except Exception as exc:
        raise OCRNoDisponible(f"EasyOCR no pudo leer la imagen: {exc}") from exc

    texto, confianza, geometria = _lineas_desde_cajas(detecciones)
    return ResultadoOCR(
        texto=texto, motor="easyocr", confianza_media=confianza, lineas=geometria
    )


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
