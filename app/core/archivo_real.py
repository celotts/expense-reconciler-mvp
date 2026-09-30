"""Que tipo de archivo ES, deduced de sus bytes. No de lo que el cliente dice.

El problema
-----------

Los tres endpoints de comprobante reciben `file_type` como campo de formulario:

    POST /tickets/extract              file_type=pdf|image|text
    POST /tickets/extract-and-create   file_type=pdf|image|text

Y ese valor se usa literal para elegir el escalon de la cascada
(`capture.py:289-297`). Es decir: **quien llama a la API decide por donde se
lee el documento**, no los bytes.

Medido en este repo, con el mismo PDF de las dos formas:

    file_type=pdf   ->  pdf_text  ->  confianza 0.97  ->  total 4094.80
    file_type=image ->  llm       ->  confianza None  ->  total 0.00

Tres cosas malas salen de ahi, y las tres importan mas que un error visible:

1. **By-pass de la barrera conceptual mas importante del sistema.** Regla 3 de
   `AGENTS.md`: "un PDF con texto no toca el modelo". Quien llama puede
   saltarsela, y con ella el ahorro de inferencia que la hace valiosa.

2. **Contamina la medicion.** El ticket del caso de arriba entra con
   `confidence_source=llm`. El reporte de exactitud agrupa por origen
   (`accuracy_service.py:346-357`), asi que una lectura que no fue de una
   lectura contaminaria la estadistica que sostiene el SLO.

3. **Amplificador de DoS.** Forzar el camino de vision sobre un archivo de
   10 MB obliga a renderizar, reescalar e inferir. Repetido, es la forma mas
   barata de quemar la maquina: solo hace falta un token valido.

Que se resuelve
---------------

Se deduce del contenido, con una lista cerrada de firmas, y el `file_type` del
cliente solo se acepta si COINCIDE con lo que dicen los bytes. Si no coincide,
se usa el deducido.

Es el mismo patron que ya existe dos veces en este repo, por el mismo motivo:

- `ENCODINGS_PERMITIDOS` en `parser_service.py`: allowlist, no denylist.
- `content_type_servible()` en `models/ticket_document.py:115`: el tipo que se
  SIRVE no es el que declaro el cliente.

Un sniffed que devuelve UNKNOWN cae a la lista de imagenes, que es donde va
un comprobante escaneado o fotografiado. Es degradar, no fallar: el documento
se procesa, lo que se pierde es lavia rapida de pdf_text. Preferimos eso a
rechazar el archivo.
"""

from __future__ import annotations

from dataclasses import dataclass

# Formatos que la cascada sabe procesar hoy. La lista es cerrada a proposito:
# anadir uno exige que `capture_ticket` sepa leerlo, y anadirlo aqui sin eso
# seria aceptar archivos que caen a `ExtractionUnavailable` con un 400.
FORMATO_PDF = "pdf"
FORMATO_IMAGEN = "image"
FORMATO_TEXTO = "text"
FORMATO_DESCONOCIDO = "desconocido"

# Las firmas se leen del principio del archivo. Cada entrada es el prefijo que
# tiene que aparecer para que el tipo se considere ese, con su desplazamiento.
#
# PDF: "%PDF" en el byte 0. Es deliberado que no se acepte con preambulo: un
# PDF con basura delante es un archivo manipulado, no un PDF. Asi lo pide la
# especificacion y asi lo pone el visor del sistema, no el nuestro.
_FIRMA_PDF = (b"%PDF", 0)

# Las imagenes se reconocen por un numero magico, no por una extension. HEIC
# entra porque el producto se define por "la foto del telefono" y un iPhone
# produce HEIC: sin esto, el primer usuario no puede subir su comprobante.
# El bloque de marca `ftyp` va en el byte 4, con bytes de compatibilidad entre
# el tipo y el 4, que es lo que exige ISO-BMFF.
_FIRMAS_IMAGEN: tuple[tuple[bytes, int], ...] = (
    (b"\xff\xd8\xff", 0),                     # JPEG
    (b"\x89PNG\r\n\x1a\n", 0),                # PNG
    (b"GIF87a", 0),
    (b"GIF89a", 0),
    (b"BM", 0),                               # BMP
    (b"II*\x00", 0),                          # TIFF little-endian
    (b"MM\x00*", 0),                          # TIFF big-endian
    # HEIC / HEIF / AVIF. Toda caja ISO-BMFF empieza con una caja de tamano de
    # 4 bytes y despues el tipo de 4 letras. La entrada se ancla al byte 4 y no
    # al 0 a proposito: el tamano de esa caja cambia con el archivo
    # (`\x00\x00\x00\x18`, `\x00\x00\x00\x20`, ...), asi que comprobar `ftyp` en
    # una posicion FIJA es lo unico que cubre todos los HEIC sin enumerar
    # tamanos que se quedarian cortos al cambiar de telefono.
    (b"ftyp", 4),
    (b"8BPS", 0),                             # PSD
)

# Texto plano: se decide por el resultado de decodificar, no por firma. Un
# `.txt` no tiene numero magico y `factura_gas.txt` es un caso de uso real del
# proyecto (esta en `test-files/`), asi que la firma no puede ser la unica via.
# La decision final la toma `capture.py:292` con la cascada de texto.


@dataclass(frozen=True)
class TipoDetectado:
    """Lo que dicen los bytes, y con cuanto margen se dice.

    `margen` es el numero de bytes de firma que hubo que coincidir. Importa
    para el diagnostico: un JPEG de 3 bytes es una conjetura, uno de 8 no.
    """

    formato: str
    margen: int
    # Si los bytes NO son de un formato conocido y aun asi los trataramos como
    # imagen, esto dice por que. Es la diferencia entre "es una imagen" y
    # "no se que es, y el camino de imagen es el que degrada mejor".
    degradado_a_imagen: bool = False


def sniff_tipo(contenido: bytes) -> TipoDetectado:
    """Deduce el formato de los bytes. No lanza, no adivina por extension.

    Devolver SIEMPRE algo. Un archivo cuyo tipo no se reconoce cae a imagen,
    que es el unico escalon que degrada en silencio a algo util (una foto mal
    leida entra a la cola de revision con motivo, no se pierde). Rechazarlo
    seria peor: el contador pierde el comprobante.
    """
    if not contenido:
        return TipoDetectado(FORMATO_DESCONOCIDO, 0, degradado_a_imagen=True)

    firma, desplazamiento = _FIRMA_PDF
    if contenido[desplazamiento : desplazamiento + len(firma)] == firma:
        return TipoDetectado(FORMATO_PDF, len(firma))

    for firma, desplazamiento in _FIRMAS_IMAGEN:
        if contenido[desplazamiento : desplazamiento + len(firma)] == firma:
            return TipoDetectado(FORMATO_IMAGEN, len(firma))

    return TipoDetectado(FORMATO_DESCONOCIDO, 0, degradado_a_imagen=True)


def resolver_tipo(contenido: bytes, declarado: str | None) -> tuple[str, str | None]:
    """Devuelve `(formato_a_usar, motivo_si_se_corrijo)`.

    `declarado` es el `file_type` que mando el cliente. Solo manda cuando
    coincide con los bytes. Cuando no, gana el deducido y el motivo explica la
    diferencia, para que la razon viaje en el log y en el error.

    La excepcion deliberada: si los bytes no son de ningun formato conocido, el
    declarado se respeta, porque el cliente es la unica fuente que queda y
    rejectar un `.txt` de texto plano por no tener firma seria romper el caso
    de uso de `factura_gas.txt`.
    """
    detectado = sniff_tipo(contenido)

    normalizado = (declarado or "").strip().lower() or None

    if detectado.formato == FORMATO_DESCONOCIDO:
        if normalizado in (FORMATO_PDF, FORMATO_IMAGEN, FORMATO_TEXTO):
            return normalizado, None
        return FORMATO_TEXTO, (
            "No se reconocio el tipo por los bytes y el cliente no declaro uno "
            "valido. Se asume texto."
        )

    if normalizado is None or normalizado == detectado.formato:
        return detectado.formato, None

    return detectado.formato, (
        f"El cliente declaro file_type={normalizado!r} pero los bytes son "
        f"{detectado.formato!r} (firma de {detectado.margen} byte(s)). "
        "Manda lo que dicen los bytes: elegir la ruta de lectura desde el "
        "cliente permite saltarse la cascada y contaminar la medicion de "
        "origen."
    )
