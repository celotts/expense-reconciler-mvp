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


class SpotCheckStatus(str, Enum):
    """Veredicto de una revision de muestreo.

    No existe un valor para "fuera de la muestra": esa fila tiene
    `spot_check_status` en NULL. La mayoria de los tickets no se muestrean, y
    darles un valor tipo "NO" haria que el indice de la cola creciera con todo
    el historico y que "no fue elegido" fuera indistinguible de "elegido y sin
    revisar".

    La lista tiene que coincidir con la constraint `ck_tickets_spot_check_values`
    de db/migrations/0003_spot_check.sql. Si divergen, se escribe un estado
    nuevo en Python y la base lo rechaza al registrar la revision, en
    produccion. tests/unit/test_spot_check.py compara las dos listas.
    """

    PENDIENTE = "PENDIENTE"    # elegido por muestreo, sin revisar
    CORRECTO = "CORRECTO"      # la extraccion coincide con el papel
    INCORRECTO = "INCORRECTO"  # algo no coincide; se anotan los campos


# Fraccion de los tickets auto-aprobados que se mandan a revisar para poder
# medir la exactitud.
#
# Es un parametro y no un numero enterrado porque la tasa correcta depende del
# volumen, que todavia no se conoce. La eleccion no es gratuita en ningun
# sentido: subirla da evidencia antes y cuesta mas revisions; bajarla abarata
# y alarga el tiempo hasta poder afirmar el 96%.
#
# Lo que NO se debe hacer es subirla para "ver mas errores". Con 25 revisiones
# ya se detecta una caida grande, que es lo que un muestreo sirve. Lo que exige
# muestra grande es AFIRMAR un numero, y para eso el reporte dice cuantos
# faltan en vez de dejar que se reporte un porcentaje sin respaldo.
SPOT_CHECK_RATE = 0.05
