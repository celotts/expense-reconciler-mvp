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

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
