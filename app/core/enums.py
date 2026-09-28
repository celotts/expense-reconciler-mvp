"""Enums compartidos del dominio de tickets.

Centralizados aqui para que modelo, schema, API y servicios usen las mismas
cadenas. Si cada capa define su propia constante, el filtro de la cola de
revision deja de encontrar registros silenciosamente.
"""

from enum import Enum


class ExtractionStatus(str, Enum):
    """Estado del ciclo de vida de un ticket.

    AUTO_APROBADO  -> la IA lo leyo con confianza alta y las validaciones de
                      consistencia pasaron. Entra directo a conciliacion.
    REQUIERE_REVISION -> legible pero con confianza media o una validacion
                      fallida. Va a la cola de revision humana.
    PENDIENTE      -> no se pudo extraer nada confiable. Cola de pendientes.
    APROBADO       -> revisado y confirmado por un humano.
    RECHAZADO      -> revisado y descartado (documento ilegible/duplicado).
    """

    AUTO_APROBADO = "AUTO_APROBADO"
    REQUIERE_REVISION = "REQUIERE_REVISION"
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"

    @property
    def is_open(self) -> bool:
        """Estados que aun requieren accion de una persona."""
        return self in OPEN_STATUSES


# Estados que aparecen en la cola de revision. Se declara antes de la clase por
# el `is_open` de arriba; es la lista de verdad y las demas se derivan de ella.
OPEN_STATUSES = (
    ExtractionStatus.REQUIERE_REVISION,
    ExtractionStatus.PENDIENTE,
)


class ConfidenceSource(str, Enum):
    """De donde salio el dato, para auditar que tan confiable es."""

    MANUAL = "manual"       # tecleo humano, no hay confianza que medir
    LLM = "llm"              # modelo de vision/LLM
    RULES = "rules"          # parser deterministico por regex
    LLM_VALIDATED = "llm_validated"  # LLM que ademas paso validacion de consistencia
    PDF_TEXT = "pdf_text"    # texto extraido de PDF digital (sin IA)


class SourceType(str, Enum):
    """Via de entrada del documento."""

    MANUAL = "manual"        # captura manual en el form
    PDF = "pdf"
    IMAGE = "image"
    DIRECTORY = "directory"  # carpeta escaneada
    BULK = "bulk"            # carga masiva de imagenes
    CAMERA = "camera"


# Umbrales del gate de confianza. Configurables aqui y no hardcodeados en el
# servicio, para poder ajustarlos con evidencia de produccion.
AUTO_APPROVE_CONFIDENCE = 0.90
REVIEW_CONFIDENCE = 0.60

# Lo que el parser escribe cuando no logra leer el emisor.
#
# No es un nombre de proveedor: es la ausencia de uno. Y tiene que ser SIEMPRE
# la misma cadena en el parser, en el gate, en los schemas y en la API, porque
# cada uno la compara contra algo distinto: si el parser emite una variante y el
# gate no la reconoce, un ticket ilegible pasa los checks y entra a
# conciliacion como si estuviera bien leido. Ese es el fallo exacto que el gate
# existe para evitar, y se reintroduce cambiando una palabra.
#
# El frontend tiene su copia en front/src/utils/validation.ts (UNKNOWN_PROVIDER)
# y la cola de revision la consume desde ahi. Los dos lados tienen que cambiar
# juntos.
UNKNOWN_PROVIDER = "Unknown Provider"

# Estados cuyo ticket puede entrar a conciliacion. Para estos, los datos
# tienen que ser aritmeticamente validos.
#
# La lista se define aqui y no en la constraint porque la constraint y el
# gate tienen que contar los estados igual. Si el gate gana un estado nuevo y
# la constraint no, aparece un IntegrityError en produccion justo en el
# camino que se acaba de construir.
#
# Los estados que NO estan aqui (PENDIENTE, REQUIERE_REVISION, RECHAZADO)
# pueden llevar datos rotos a proposito: un documento ilegible tiene que poder
# guardarse en la cola para que sea visible, y descartarse sin chocar.
SETTLED_STATUSES = (
    ExtractionStatus.AUTO_APROBADO,
    ExtractionStatus.APROBADO,
)
