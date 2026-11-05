"""La vista previa de un comprobante que el navegador no sabe pintar.

## EL PROBLEMA, MEDIDO

Las fotos de iPhone son HEIC. Ni Chrome ni Firefox las pintan, y el backend las
sirve como `application/octet-stream` —esta fuera de su lista cerrada de tipos
servibles, **a proposito**: `image/heic` no es ejecutable y la lista esta cerrada
por seguridad.

MEDIDO en la pantalla de revision, con 3 HEIC reales: el papel salia como
"Tu navegador no puede mostrar el comprobante aqui". Y el muestreo de exactitud
existe para **contrastar contra el papel**, asi que un comprobante que no se ve
es un comprobante que se revisa a ciegas: exactamente lo contrario de lo que el
producto promete.

## LA SALIDA: CONVERTIR PARA MOSTRAR, SIN TOCAR EL PAPEL

`pillow-heif` (ya en `requirements.txt`) abre el HEIC y lo devuelve como JPEG, que
si esta en la lista cerrada. Se sirve un **JPEG derivado** y el archivo guardado
sigue siendo el HEIC original, byte por byte.

**POR QUE NO SE REEMPLAZA EL ORIGINAL.** `ticket_documents` es append-only: el
trigger de Postgres prohibe `UPDATE` y `DELETE` mientras el ticket exista, porque
el comprobante es la evidencia contra la que se contrasta cualquier lectura.
Convertirlo en el sitio haria que "el papel" fuera un archivo que el sistema
reescribio al mirarlo, que es justo lo que esa regla existe para impedir.

La consecuencia util: la descarga sigue dando el HEIC original, y la vista previa
es una representacion derivada. Son dos preguntas y por eso son dos endpoints.

## LO QUE NO SE CONVIERTE

Solo HEIC/HEIF. Un JPEG, un PNG o un PDF ya se sirven tal cual y este servicio no
los toca. Convertir de mas seria gastar CPU y, peor, cambiar los bytes que el
revisor cree estar viendo.

## Y SI NO SE PUEDE

Devuelve `None` y quien llama sirve el original. Un fallo aqui significa "no hay
vista previa", nunca "no hay comprobante": esas dos cosas piden acciones
distintas y confundirlas hace que el revisor busque un archivo que si existe.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Los tipos que se convierten. Todo lo demas ya se sirve y se ve.
_CONVERTIBLES = frozenset({"image/heic", "image/heif"})

# Tamano maximo de la imagen convertida.
#
# Un HEIC de iPhone es de 12 MP y pesa 1.5 MB; el JPEG sale MAS grande que el
# original, no mas pequeno, porque el HEIC ya esta comprimido. Para una pantalla de
# revision no hace falta la resolucion completa, y bajarla es lo que mantiene el
# peso en algo razonable: se ven los numeros de un ticket, que es para lo que
# sirve.
#
# 2000 px de lado largo es el punto en el que un ticket de caja sigue leyendo su
# total y su RFC de sobra, medido sobre los 3 HEIC reales.
_LADO_MAXIMO = 2000

# Calidad del JPEG. 85 es el default de Pillow y baja de 85 se ve el ringing en
# los numeros, que en un comprobante es justo donde no puede haber.
_CALIDAD = 85


def es_convertible(content_type: str | None) -> bool:
    """Si este `Content-Type` necesita conversion para verse en un navegador.

    Solo mira el tipo que el **backend** determino por los bytes
    (`media_type_real`), no el que declaro el cliente: si el tipo sale de una
    lista cerrada que el cliente no controla, esta pregunta no se puede falsear
    mandando un `Content-Type` conveniente.
    """
    if not content_type:
        return False
    return content_type.split(";")[0].strip().lower() in _CONVERTIBLES


def _heic_registrado() -> bool:
    """Si `pillow-heif` se pudo registrar.

    Se pregunta antes de usar, y no se importa con `try` en cada conversion: el
    registro es una operacion global de Pillow y hacerla por foto seria
    repetirla 200 veces en un escaneo.
    """
    try:
        import pillow_heif  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _preparado() -> bool:
    """Registra el opener de HEIC una sola vez por proceso.

    `lru_cache(maxsize=1)` sin argumentos porque no hay ningun parametro que
    varia: el resultado es el mismo toda la vida del proceso. Sin el cache,
    `register_heif_opener()` se llamaria en cada conversion.
    """
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except ImportError:
        logger.warning(
            "pillow-heif no esta instalado: los comprobantes HEIC se seguiran "
            "sirviendo como application/octet-stream y el navegador no los "
            "pintara. Se instala con: pip install pillow-heif"
        )
        return False


def convertir_a_jpeg(contenido: bytes) -> bytes | None:
    """El mismo comprobante en JPEG, o `None` si no se pudo.

    `None` NUNCA significa "el archivo no existe" ni "no hay documento": es solo
    "no hay vista previa". Quien llama conserva los bytes originales y su tipo,
    asi que el comprobante sigue disponible para descargar.

    ## POR QUE NO HAY CACHE DE LOS BYTES CONVERTIDOS

    MEDIDO: convertir uno de tus HEIC reales (3024x4032, 1.3 MB) tarda **0.18-0.25
    s**. A 200 comprobantes de una cola son 40-50 s en total, repartidos en
    peticiones HTTP que nadie espera en un bucle. Meter un cache de 1.5 MB por
    foto en la RAM de un contenedor que ya va justo de memoria seria pagar RAM
    por ahorrar 0.2 s.

    El coste de red es el mismo en los dos casos (el JPEG pesa parecido al HEIC),
    y el `Cache-Control: private, max-age=300` que ya lleva el endpoint hace que
    el navegador no vuelva a pedirlo dentro de la sesion de revision.
    """
    if not _preparado():
        return None

    try:
        from PIL import Image

        with Image.open(io.BytesIO(contenido)) as imagen:
            # `convert("RGB")` y no un guardado directo: un HEIC con alfa
            # guardado como JPEG falla, y un `None` silencioso seria un ticket sin
            # vista previa sin decir por que.
            if imagen.mode not in ("RGB", "L"):
                imagen = imagen.convert("RGB")
            else:
                imagen = imagen.copy()

            # `thumbnail` NO agranda: si el HEIC ya es de 800 px, sale de 800.
            imagen.thumbnail((_LADO_MAXIMO, _LADO_MAXIMO), Image.LANCZOS)

            salida = io.BytesIO()
            imagen.save(salida, format="JPEG", quality=_CALIDAD, optimize=True)
            return salida.getvalue()

    except Exception as exc:  # noqa: BLE001 — aquí el fallo nunca debe tumbar la ruta
        # Un comprobante ilegible es un dato, no un error del sistema. Se registra
        # y se devuelve None: el original sigue serviéndose.
        logger.warning("No se pudo convertir el comprobante a JPEG: %s", exc)
        return None