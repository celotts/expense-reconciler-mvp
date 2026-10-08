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
    OCR = "ocr"              # OCR local sobre la foto, y reglas sobre su texto

    # OCR es un valor aparte y no una variante de `LLM` por una razon concreta:
    # el reporte de exactitud agrupa por `confidence_source`. Si una foto leida
    # por OCR se guardara como `llm`, su acierto se sumaria al del modelo y la
    # cuenta "que tan bien lee el modelo" incluiria lecturas que el modelo no
    # hizo. Un numero de exactitud que mezcla dos lectores distintos no
    # describe a ninguno de los dos, y es exactamente el fallo que este enum
    # existe para que no se pueda cometer por descuido.
    #
    # El riesgo real es que el dia que se conecte EasyOCR y PaddleOCR, cada uno
    # con su tasa de error, el mismo valor "ocr" los promedie y el resultado no
    # signifique nada. Si aparece un segundo motor, se agrega el valor
    # (`ocr_tesseract`, `ocr_easyocr`) y no se reetiqueta el existente: cambiar
    # el significado de una cadena ya persistida hace que los historiales
    # antiguos se lean con el criterio de hoy.


class ScanStatus(str, Enum):
    """Estado de un archivo dentro de la carpeta escaneada.

    Es el estado del ARCHIVO, no el del ticket. No son la misma maquina y no se
    derivan una de la otra: un archivo puede estar `PROCESADO` y su ticket en
    `REQUIERE_REVISION`, porque el archivo se leyo bien y lo que habia en el
    papel no daba para cerrarlo. Confundir los dos fue el error de diseño de la
    primera version de esta tabla, y hacia que un reintento de OCR pareciera
    estar fallando cuando lo que estaba pendiente era la revision de una
    persona.

    Los estados:
    PENDIENTE     -> visto en la carpeta, todavia sin leer
    PROCESADO     -> leido; hay ticket ligado (nuevo o actualizado)
    DUPLICADO     -> mismos bytes que un archivo ya procesado; no se relee
    ERROR         -> se intento y no se pudo; `attempts` y `last_error` lo dicen
    NO_SOPORTADO  -> el formato no es del dominio (no es PDF ni imagen)
    """

    PENDIENTE = "PENDIENTE"
    PROCESADO = "PROCESADO"
    DUPLICADO = "DUPLICADO"
    ERROR = "ERROR"
    NO_SOPORTADO = "NO_SOPORTADO"

    @property
    def is_open(self) -> bool:
        """Estados en los que un reintento tiene sentido."""
        return self in (ScanStatus.PENDIENTE, ScanStatus.ERROR)


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


class MatchStatus(str, Enum):
    """Resultado de conciliar un ticket contra un movimiento bancario.

    PERFECT     -> el monto cae dentro de la tolerancia Y la fecha dentro de la
                   ventana. Es el unico estado que se da por bueno solo.
    MANUAL      -> el monto cae dentro de la tolerancia pero la fecha se sale de
                   la ventana, o al reves. Alguien tiene que confirmarlo.
    DISCREPANCY -> hay un movimiento en la fecha correcta con otro monto.
                   No se concilia: se reporta para que una persona vea que el
                   dinero salio por una cantidad distinta.

    El patron del schema (`ReconciliationBase.match_status`) y esta lista tienen
    que decir lo mismo. Si divergen, la API acepta un estado que el motor nunca
    produce, o el motor produce uno que la API no puede devolver.
    """

    PERFECT = "PERFECT"
    MANUAL = "MANUAL"
    DISCREPANCY = "DISCREPANCY"


# Los estados que cuentan como conciliados. Lo que queda fuera (DISCREPANCY) se
# exporta aparte, porque mandarlo a CONTPAQI como si estuviera cuadrado seria
# inventar un cuadre.
MATCHED_STATUSES = (
    MatchStatus.PERFECT,
    MatchStatus.MANUAL,
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


# ---------------------------------------------------------------------------
# Compra: de la factura digitalizada a la entrada al inventario
# ---------------------------------------------------------------------------
#
# NO CONFUNDIR CON `ExtractionStatus` (arriba). Son dos maquinas de estado
# distintas y estan en tablas distintas, por una razon concreta:
#
# `ExtractionStatus` responde "como se leyo el papel". `EstadoCompra` responde
# "el inventario ya conto esto". Un ticket puede estar AUTO_APROBADO —el
# sistema se responsabiliza de la lectura— y aun asi su compra estar en
# EN_REVISION, porque que la lectura sea buena no dice nada sobre si las LINEAS
# son las correctas. El gate evalua los campos del encabezado y nunca miro una
# linea de producto. Fusionarlos seria afirmar que una lectura buena del total
# es una lectura buena del contenido, que es justo el salto que este proyecto
# no puede sostener (AGENTS.md, "Gate de confianza").
#
# La transicion que importa es EN_REVISION -> PROCESADO: es la unica que suma
# stock, y la unica que hace una persona.


class ProductoOrigen(str, Enum):
    """Quien puso el producto en el catalogo.

    No es decorativo: decide si el producto merece fe. `MANUAL` lo creo una
    persona, mirando el producto. `OCR` salio de una linea de comprobante leida
    a las 11 de la noche.

    La lista tiene que coincidir con la constraint `ck_productos_origen` de
    db/migrations/0011_productos_origen.sql, por el mismo motivo que
    `SpotCheckStatus` con la suya.
    """

    MANUAL = "MANUAL"
    OCR = "OCR"


class EstadoCompra(str, Enum):
    """Donde esta la orden de compra en su camino al inventario.

    La lista tiene que coincidir con la constraint `ck_compras_estado` de
    db/migrations/0010_inventario.sql, por el mismo motivo que
    `SpotCheckStatus` con la suya: si divergen, se escribe un estado nuevo en
    Python y la base lo rechaza al registrarlo, en produccion.
    """

    PROCESAR = "PROCESAR"        # se esta digitalizando y extrayendo
    EN_REVISION = "EN_REVISION"  # extraida; esperando que alguien la autorice
    PROCESADO = "PROCESADO"      # autorizada; YA sumo stock
    RECHAZADO = "RECHAZADO"      # descartada por una persona; NO suma stock

    @property
    def mueve_stock(self) -> bool:
        """Si estar en este estado significa que el inventario ya sumo.

        Es la pregunta que hace `confirmar_compra` y la que hace el archivado, y
        se responde con una propiedad y no con `== PROCESADO` repetido en cinco
        sitios: la dia que haya un cuarto estado que tambien mueva stock, o una
        transicion nueva, las cinco comparaciones no se actualizan juntas y
        queda una que miente.

        `RECHAZADO` y `PROCESAR` no mueven. `RECHAZADO` es el estado nuevo de
        `POST /inventario/compras/{id}/rechazar`, y existe para que "esta compra
        no es una compra" sea un dato guardable y no una compra olvidada en la
        cola. Ver `db/migrations/0013_compras_rechazable.sql`.
        """
        return self is EstadoCompra.PROCESADO


class TipoMovimiento(str, Enum):
    """El signo del movimiento en el kardex.

    `cantidad` SIEMPRE positiva. El signo lo da el tipo, no el numero, para que
    una resta nunca se confunda con "no hay cantidad" y para que el kardex se
    pueda sumar con un simple SUM sin depender del signo almacenado.

    ENTRADA suma stock (compra). SALIDA lo resta (venta). AJUSTE es la correccion
    manual cuando el conteo fisico y el sistema no coinciden.

    EL SIGNO DE `AJUSTE` NO ESTA DEFINIDO, Y POR ESO NO SE ESCRIBE DIRECTO
    ---------------------------------------------------------------------

    `AJUSTE` es la correccion cuando el conteo fisico y el sistema no coinciden, y
    eso pasa en las DOS direcciones: se conto de mas, o se conto de menos. Un
    solo valor de enum no puede decir cual de las dos es, y `cantidad` es positiva
    por `ck_movimientos_cantidad_positiva`, asi que el signo no puede viajar en el
    numero.

    Se resuelve en `inventario_service.registrar_ajuste`: un AJUSTE se escribe
    como el tipo que SI tiene signo (`ENTRADA` si se conto de menos, `SALIDA` si
    de mas) y con `referencia_tipo = 'AJUSTE'`, que es lo que dice que la fila es
    una correccion y no una compra.

    Por que no se agrega un tipo `AJUSTE_SALIDA`: porque entonces el signo
    estaria en el tipo Y en el nombre del tipo, y "suma" y "resta" dejarian de
    ser la pregunta de una sola palabra. Ver `TipoMovimiento.suma_stock`.

    El enum conserva `AJUSTE` porque es el nombre de `referencia_tipo`, que si es
    una categorizacion real: de donde viene la fila, no que le hace al stock.
    """

    ENTRADA = "ENTRADA"
    SALIDA = "SALIDA"
    AJUSTE = "AJUSTE"

    @property
    def suma_stock(self) -> bool:
        """Si este tipo mueve el stock hacia arriba.

        La unica fuente de verdad sobre el signo. `stock_de()` la consulta y el
        trigger de Postgres la replica en SQL, y las dos tienen que decir lo
        mismo: si divergen, la base rechaza un movimiento que la API acepto (o al
        reves) y el sintoma es un 409 sin explicacion.

        Y antes de que existiera esta propiedad, NO coincidian. Medido sobre el
        DDL de `db/init.sql`: el trigger hacia `IF tipo = 'ENTRADA' THEN suma
        ELSE resta`, o sea que trata `AJUSTE` como resta, mientras que `stock_de`
        lo suma con `tipo IN ('ENTRADA', 'AJUSTE')`. Era inerte porque la unica
        via que escribia movimientos era `confirmar_compra`, y esa solo produce
        `ENTRADA`. Se volvio vivo con `POST /inventario/movimientos`.

        Verificado contra Postgres real en `scripts/verify_postgres_inventario.py`.
        """
        return self is not TipoMovimiento.SALIDA
