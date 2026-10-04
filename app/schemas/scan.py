"""Los schemas del escaneo de carpeta.

`ScanFileResponse` no expone la ruta absoluta, solo `relative_path`. No es
decision de estilo: la ruta absoluta es `TICKETS_INPUT_DIR` mas la relativa, y
la API no necesita mandarla porque quien la pide ya sabe cual es la carpeta
configurada. Lo que si sale, en el resumen del escaneo, es la carpeta: es
informacion util para confirmar que el escaneo leyo del lado correcto, y en una
instalacion de una sola maquina y un solo contador no es un dato que haya que
proteger.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatosTicketResponse(BaseModel):
    """Lo que el sistema afirmo que dice el papel, y con que respaldo.

    Va anidado en `ScanItemResponse.datos` para que un `POST /scan` devuelva el
    dato ya dentro de la respuesta, sin un segundo llamado por ticket.

    DE DONDE VIENEN ESTOS CAMPOS
    ----------------------------
    De la fila de `tickets` despues de que el gate decidio, no de lo que el
    lector devolvio crudo. Es la diferencia que importa: si el gate mando el
    ticket a revision porque la aritematica no cuadraba, aqui se ve
    `extraction_status=REQUIERE_REVISION` CON los numeros que produjo la
    lectura. Devolver el crudo sin el veredicto dejaria a quien consume el JSON
    esperando que un ticket es trustworthy cuando el sistema mismo ya dijo que
    no.

    `raw_text` NO va aqui, a proposito. Son hasta 20 000 caracteres por ticket
    y no es un dato: es la evidencia. Quien la necesite, la pide con
    `GET /tickets/{id}`, que ademas la sirve con el documento al lado.

    `items` sale como `list[dict]` y no como una lista de lineas tipadas. No es
    pereza: es que las lineas se persisten CRUDAS, sin normalizar
    (`tickets.items` es JSON crudo y `compra_items` es la version revisada, que
    no existe todavia). Inventar aqui un modelo de linea con `descripcion`,
    `cantidad` y `precio_unitario` afirmaria una estructura que el sistema nunca
    verifico, y con OCR al 33% esa afirmacion seria falsa seguido. Lo que se
    devuelve es lo que hay, y el que lo normaliza es
    `inventario_service.interpretar_items`.
    """

    model_config = ConfigDict(from_attributes=True)

    provider_name: str | None = None
    provider_tax_id: str | None = None
    total_amount: Decimal | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    expense_date: date | None = None
    category: str | None = None
    items: list[dict] | None = None

    # El veredicto. Sin esto, los datos de arriba se leen como una afirmacion.
    confidence: Decimal | None = None
    confidence_source: str | None = None
    extraction_status: str | None = None
    validation_errors: str | None = None

    # Si una persona toco este ticket. `reviewed_at` con valor es la senal de que
    # los datos ya no son solo lo que dijo la maquina, y el que consume el JSON
    # necesita saberlo antes de confiar en los numeros.
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

class ScanFileResponse(BaseModel):
    """Un archivo del registro, tal como esta en la tabla."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    relative_path: str
    content_hash: str
    file_size: int | None = None
    file_mtime: datetime | None = None
    detected_format: str | None = None
    declared_extension: str | None = None
    status: str
    attempts: int
    read_by: str | None = None
    last_error: str | None = None
    ticket_id: UUID | None = None
    company_id: UUID | None = None
    first_seen_at: datetime
    last_scanned_at: datetime | None = None
    processed_at: datetime | None = None


class ScanEventResponse(BaseModel):
    """Un cambio de estado en el historico del archivo."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    detail: str | None = None
    actor: str | None = None
    confidence: Decimal | None = None
    created_at: datetime


class ScanFileDetailResponse(ScanFileResponse):
    """El archivo con su historico, para la pantalla de detalle."""

    events: list[ScanEventResponse] = Field(default_factory=list)


class ScanRequest(BaseModel):
    """Que escanear y que hacer con lo que ya se leyo.

    No hay ningun campo de carpeta. La carpeta es `TICKETS_INPUT_DIR` y no se
    pide: ver la nota de seguridad al inicio de `app/services/scan_service.py`.
    """

    company_id: UUID | None = Field(
        None,
        description=(
            "Empresa a la que atribuir los tickets. Si se omite, el escaneo solo "
            "inventaria los archivos y NO creara tickets: sirve para ver que hay "
            "en la carpeta antes de decidir a quien pertenece cada gasto."
        ),
    )
    reprocesar: bool = Field(
        False,
        description=(
            "Releer tambien los archivos cuyo contenido cambio desde la ultima "
            "lectura. Apagado por omision porque sobreescribir un ticket es una "
            "decision, no un efecto de correr un escaneo. Un ticket ya revisado "
            "por una persona no se sobreescribe ni con esto: hace falta el "
            "endpoint de reproceso con forzar=true."
        ),
    )
    solo_pendientes: bool = Field(
        False,
        description="No tocar los archivos ya PROCESADOS, aunque hayan cambiado.",
    )
    archivar: bool | None = Field(
        None,
        description=(
            "Mover a la carpeta de escaneados los archivos que el sistema "
            "digitalizo. Sin valor usa TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR. "
            "Mover NO es borrar: el archivo sigue existiendo con los mismos "
            "bytes, en la carpeta de escaneados."
        ),
    )
    simular: bool = Field(
        False,
        description=(
            "Decir QUE se moveria sin mover nada. Es el modo para ver el "
            "efecto antes de que ocurra: con el archivado activado por omision, "
            "la primera corrida deja la carpeta de entrada vacia, y conviene "
            "ver eso antes de que ocurra."
        ),
    )


class ScanItemResponse(BaseModel):
    """Que le paso a un archivo en la corrida."""

    relative_path: str
    scan_file_id: UUID | None = None
    status: str
    accion: str
    ticket_id: UUID | None = None
    confianza: float | None = None
    origen: str | None = None
    detalle: str | None = None

    # --- El archivado ---------------------------------------------------
    #
    # Van por item y no solo como un contador porque "se movieron 6 archivos" no
    # dice cuales, y la pregunta al vaciar la carpeta de entrada es siempre
    # "¿que se movio y donde quedo?".
    #
    # `estaba_pendiente` es el que hay que mirar: True significa que el archivo
    # se movio aunque la lectura NO fuera confiable. Con el OCR al 33.3% eso es
    # frecuente, y por eso el campo esta expuesto en vez de escondido en un log.
    archivado: bool = False
    ruta_archivo: str | None = None
    # Lo que el gate decidio de esa lectura. Se devuelve junto al movimiento
    # porque la pregunta "movi este papel aunque el sistema no lo entendiera?" se
    # responde mirando los dos juntos, no por separado.
    extraction_status: str | None = None
    estaba_pendiente: bool | None = None
    # Cuando fue `simular`, esto dice "se moveria aqui" sin que se mueva nada.
    solo_simulado: bool = False

    # Los datos que el lector obtuvo del papel. Es `None` cuando este archivo no
    # produjo ticket —ERROR, NO_SOPORTADO, o leido sin `company_id`— y tambien
    # en `simular`, que por definicion no lee nada. La ausencia es informacion:
    # un `null` aqui significa "no hay ticket del que sacar datos", no "no se
    # pudo leer".
    datos: DatosTicketResponse | None = None


class ScanResponse(BaseModel):
    """El resumen de una corrida de escaneo."""

    carpeta: str
    archivos_vistos: int
    nuevos: int
    actualizados: int
    sin_cambios: int
    duplicados: int
    con_error: int
    no_soportados: int
    omitidos_por_tope: int
    leidos: int
    lecturas_por_motor: dict[str, int]
    detalles: list[ScanItemResponse]

    # --- El archivado ---------------------------------------------------
    #
    # `archivados` y `archivados_pendientes` van juntos porque el segundo es el
    # que da miedo: son los comprobantes que se movieron SIN que el sistema
    # hiciera bien la lectura. Con OCR al 33.3% no es un numero raro.
    #
    # `carpeta_destino` y `simulado` para que el que llama sepa si lo que ve ya
    # ocurrio o es un plan.
    archivados: int = 0
    archivados_pendientes: int = 0
    carpeta_destino: str | None = None
    simulado: bool = False
    # El total, que es lo que se pregunta al terminar. Va en la MISMA respuesta y
    # no en un endpoint aparte porque la pregunta es de una vez: "ya termino, que
    # salio". Pedirlo en una segunda llamada obliga a correlacionar dos respuestas
    # con dos momentos distintos, y el total deja de ser el de la corrida que se
    # acaba de ver.
    resumen: ResumenScan | None = None
    # Con este id se puede consultar el progreso de la corrida mientras se espera
    # con `GET /scan/runs/{id}`. Viaja en la respuesta para que el cliente no tenga
    # que inventar una manera de correlacionar la corrida con su total: salen
    # juntos, y el total es el de ESTA corrida.
    corrida_id: str | None = None


class ResumenScan(BaseModel):
    """El total de la corrida: que salio, cuanto dinero y que necesita una persona.

    ESTE ES EL "TOTAL DE LO QUE SE PROCESO", y esta dividido por lo que el numero
    puede y no puede decir:

    - `importe_total_leido` es lo que el SISTEMA leyo, no lo que se gasto. Va
      separado de `importe_total_confiable` a proposito, y eso es lo que evita el
      uso peligroso: un total unico invita a sumarlo y apuntarlo, y con OCR al
      33% ese numero miente. Quien lee la respuesta tiene que poder ver, sin
      sumar nada, cuanto de esto es de fiar.

    - `importe_total_confiable` solo cuenta los tickets que quedaron
      `AUTO_APROBADO`, o sea los que pasaron TODOS los checks. Los `APROBADO` de a
      pie NO entran: son los que corrigio una persona, y meterlos aqui haria que
      un dato humano pareciera automatico — que es exactamente la confusion que el
      sistema no puede tolerar en su reporte de exactitud.

    - `importe_requiere_revision` es el que NO se puede sumar sin mirar el papel.

    Y los conteos por estado con `sin_ticket` y `requiere_accion` son la parte
    accionable: dicen si la corrida fue bien y, si no, quantos papeles hay que
    mirar. Un escaneo que "termino bien" con 30 archivos en `ERROR` no fue un
    escaneo bueno, y sin este desglose no se distingue.
    """

    # --- Los conteos ---
    archivos_vistos: int = 0
    leidos: int = 0
    nuevos: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    duplicados: int = 0
    con_error: int = 0
    no_soportados: int = 0
    omitidos_por_tope: int = 0

    # --- El dinero ---
    #
    # `Decimal` y no `float`, por la convencion del proyecto y porque un total
    # monetario en binario no redondea donde debe. None cuando no hay ningun
    # ticket con total leido, que es distinto de cero.
    importe_total_leido: Decimal | None = None
    importe_total_confiable: Decimal | None = None
    importe_requiere_revision: Decimal | None = None
    tickets_con_total: int = 0
    tickets_sin_total: int = 0

    # --- El veredicto, que es lo que decide si se puede confiar ---
    #
    # `tickets_por_estado` con las claves de `ExtractionStatus`. Va completo y no
    # derivado para que se pueda leer la verdad y no un resumen de la verdad.
    tickets_por_estado: dict[str, int] = Field(default_factory=dict)
    tickets_por_motor: dict[str, int] = Field(default_factory=dict)
    tickets_con_rfc: int = 0
    tickets_con_lineas: int = 0

    # --- Lo accionable ---
    #
    # Los tres que una persona tiene que hacer algo con ellos. `sin_ticket` es el
    # que mas importa: un archivo que no produjo ticket sigue en la carpeta sin
    # haber entrado en ningun sitio, y sin que nadie lo note.
    requiere_revision: int = 0
    requiere_accion: int = 0
    sin_ticket: int = 0

    # --- Donde mirar ---
    #
    # Las rutas de las pantallas, no ids sueltos. Con una ruta, el cliente puede
    # ofrecer un boton que lleve a donde hay que actuar; con ids, cada pantalla
    # tiene que construir su propia URL y se desincroniza de la API.
    colas: dict[str, str] = Field(default_factory=dict)


class ScanStatsResponse(BaseModel):
    """Como va el escaneo, para la pantalla de resumen.

    Los conteos por estado y por motor de lectura van juntos a proposito. "Hay
    30 errores" no dice si se instalo Tesseract mal o si las fotos estan borrosas,
    y esa es la diferencia entre un rato installing y un rato limpiando fotos.
    Por eso `errores_por_motor` existe al lado de `por_estado`.
    """

    total_archivos: int
    por_estado: dict[str, int]
    por_motor: dict[str, int]
    intentos_totales: int
    archivos_con_error: int
    archivos_desactualizados: int
    tickets_vinculados: int
    archivos_sin_ticket: int
    processed_at_mas_reciente: datetime | None = None


class ReprocessResponse(BaseModel):
    """El resultado de reprocesar un archivo puntual."""

    archivo: ScanItemResponse
    forzado: bool = Field(
        True,
        description=(
            "Siempre True en este endpoint. Este es el camino explicito para "
            "releer un archivo, y es el unico que puede sobreescribir un ticket "
            "que ya tiene correcciones humanas."
        ),
    )


class ScanFileListResponse(BaseModel):
    """Una pagina del registro, con el total para el paginado."""

    total: int
    limit: int
    offset: int
    archivos: list[ScanFileResponse]
