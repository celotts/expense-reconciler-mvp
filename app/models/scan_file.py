"""El registro de los archivos de la carpeta: que se vio, que se leyo y que fallo.

Por que existe esta tabla y no basta con `tickets.source_hash`:

`source_hash` deduplica por CONTENIDO. Responde "este comprobante ya existe?" y
esta bien para una carga por HTTP, donde el cliente sube un archivo y el sistema
pregunta si ya lo vio. Un escaner de carpeta tiene dos preguntas mas que ese
hash no puede contestar:

1. ¿Este archivo ya fue mirado? Un archivo puede llevar tres semanas en la
   carpeta y tener un `source_hash` identico al de un ticket que alguien subio
   por HTTP el mes pasado. Sin un registro por ruta, el escaner no puede
   distinguir "nuevo" de "ya visto", y vuelve a gastar OCR en cada corrida.
2. ¿Por que fallo? `tickets.validation_errors` guarda los checks del GATE
   (`provider_missing`, `date_missing`), que son Opinion sobre el CONTENIDO.
   No dice nada de por que no se pudo LEER el archivo: un Tesseract que no esta
   instalado y una foto borrosa producen el mismo `provider_missing`, y son
   problemas opuestos. Sin `last_error` y `attempts`, un escaner que lleva tres
   dias fallando se ve igual a uno que no ha mirado nada.

Por eso la tabla tiene `relative_path` (no la absoluta) y `attempts`, y por eso
`last_error` esta separado de la opinion del gate.

Sobre `relative_path` y no la ruta absoluta:

- La ruta absoluta cambia si la carpeta se mueve o si el proyecto se corre
  desde otro usuario, y con una ruta absoluta cada fila queda huerfana al
  primer `mv`. La relativa sobrevive.
- Una ruta absoluta en la base es el home de alguien escrito 500 veces. En una
  tabla que se exporta o se consulta desde otra maquina, eso es un dato que no
  hacia falta tener.
- La unica forma de reconstruir la ruta es `TICKETS_INPUT_DIR` + la relativa, y
  esa concatenacion se hace en un solo lugar (`scan_service`), no en cada
  consulta.

Y el `CheckConstraint` del estado se construye desde el enum, igual que en
`ticket.py`: si `ScanStatus` gana un valor y la constraint no, el INSERT da
IntegrityError en produccion justo en el camino nuevo.
"""

import uuid

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.enums import ScanStatus
from app.core.time import utcnow

# Se construye desde el enum para que la constraint y el enum no puedan
# divergir. Si `ScanStatus` gana un valor y esta cadena no, la base lo rechaza
# con un IntegrityError que no dice que paso.
_ESTADOS_VALIDOS = ", ".join(f"'{s.value}'" for s in ScanStatus)


class ScanFileModel(Base):
    """Un archivo de la carpeta de tickets y que se ha hecho con el."""

    __tablename__ = "scan_files"
    __table_args__ = (
        # El estado se valida aqui y no solo en Python por la razon de siempre:
        # los tests corren contra SQLite, que no mira esto, y el primer lugar
        # donde se descubre un valor mal escrito es un INSERT en produccion.
        CheckConstraint(
            f"status IN ({_ESTADOS_VALIDOS})",
            name="ck_scan_files_status",
        ),
        # Un archivo, una fila. Sin esto, dos corridas simultaneas del escaner
        # insertan dos filas para el mismo archivo y la idempotencia se pierde
        # justo cuando se necesita: cuando hay dos personas escaneando.
        UniqueConstraint("relative_path", name="uq_scan_files_relative_path"),
        # La cola de trabajo: lo que esta pendiente o fallo y hay que reintentar.
        Index("ix_scan_files_status", "status"),
        # Para encontrar "este contenido ya lo leimos en otro archivo". No es
        # UNIQUE a proposito: los bytes iguales en dos rutas son dos archivos
        # que el operador puede querer tener los dos registrados, y la
        # deduplicacion la decide el escaner, no una constraint.
        Index("ix_scan_files_content_hash", "content_hash"),
        Index("ix_scan_files_company", "company_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Relativa a `TICKETS_INPUT_DIR`. Ver la nota del modulo.
    relative_path = Column(String(500), nullable=False)

    # SHA-256 de los bytes. Es lo que decide si el archivo cambio desde la
    # ultima vez, y no `mtime`: un `touch` cambia la fecha sin cambiar el
    # contenido, y reprocesar por eso es tirar OCR a un archivo que ya se leyo.
    content_hash = Column(String(64), nullable=False)

    file_size = Column(BigInteger, nullable=True)
    file_mtime = Column(TIMESTAMP(timezone=True), nullable=True)

    # Que formato detectaron los BYTES, no la extension. `ticket.pdf` que es un
    # JPEG se registra como `image`; ver `app/core/archivo_real.py`.
    detected_format = Column(String(20), nullable=True)

    # La extension que traia el archivo. Se guarda aparte del formato real a
    # proposito: sirve para detectar el caso "el operador renombro el archivo
    # pero el contenido es el mismo", que no cambia el hash y por lo tanto no
    # dispara un reproceso, y para explicar en la UI por que un archivo con
    # nombre de PDF se leyo como imagen.
    declared_extension = Column(String(20), nullable=True)

    status = Column(
        String(20), nullable=False, default=ScanStatus.PENDIENTE.value
    )

    # Cuantas veces se intento leer. Es lo que separa "un archivo que fallo una
    # vez y paso" de "una foto que lleva quince corridas fallando". Sin el, el
    # escaner reintenta para siempre un archivo que no va a mejorar, y cada
    # corrida paga el mismo error.
    attempts = Column(Integer, nullable=False, default=0)

    # Que escalon lo leyo: `ocr`, `llm`, `pdf_text`, `rules`. Se guarda por
    # archivo y no solo en el ticket porque sirve para lo que el ticket no
    # puede: comparar el motor contra el resultado sin abrir cada ticket.
    read_by = Column(String(20), nullable=True)

    # Por que no se pudo LEER el archivo. Distinto de la validacion del gate,
    # que va en `tickets.validation_errors`: aqui va "tesseract no esta
    # instalado", que es un problema de la maquina, y no "falta el proveedor",
    # que es una opinion sobre el papel.
    last_error = Column(Text, nullable=True)

    # Donde acabo el ticket. `SET NULL` y no `CASCADE`: si alguien borra el
    # ticket, el archivo sigue en la carpeta y el registro de que se leyo tiene
    # que seguir ahí. Con `CASCADE` se perderia el rastro y el proximo escaneo
    # lo trataria como nuevo.
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
    )

    # La empresa a la que se atribuyo. No es `NOT NULL` porque el archivo esta
    # en el disco antes de que nadie decida a que empresa pertenece, y un
    # escaneo puede correr sin elegir empresa para solo inventariar. `SET NULL`
    # por la misma razon que `ticket_id`: borrar una empresa no debe borrar el
    # registro de los archivos que se leyeron.
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )

    first_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    last_scanned_at = Column(TIMESTAMP(timezone=True), nullable=True)
    processed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Estas dos tambien van precargadas, y por el mismo motivo que `events`
    # abajo: un `ScanFileResponse` construido con `from_attributes` toca estos
    # atributos, y si son diferidos aparece el mismo `MissingGreenlet`.
    ticket = relationship("TicketModel", lazy="selectin")
    company = relationship("CompanyModel", lazy="selectin")
    # `lazy="selectin"` y no el `select` por omision. Con el diferido, tocar
    # `fila.events` desde un schema de Pydantic dispara IO en un punto donde
    # SQLAlchemy asincrono no puede esperar, y el error que sale es
    # `MissingGreenlet`, presentado como "Error extracting attribute: events" y
    # blamed en el `model_validate` que no tiene nada que ver. Con `selectin` la
    # relacion viene cargada con el archivo y el atributo es un valor en memoria.
    events = relationship(
        "ScanEventModel",
        back_populates="scan_file",
        cascade="all, delete-orphan",
        order_by="ScanEventModel.created_at",
        lazy="selectin",
    )

    @property
    def is_open(self) -> bool:
        """¿Este archivo admite un reintento?"""
        return ScanStatus(self.status).is_open


class ScanEventModel(Base):
    """Un cambio de estado de un archivo, con quien y cuando.

    `attempts` dice cuantas veces se intento. No dice en que orden ni que paso
    entre un intento y otro. Para auditar hace falta la linea de tiempo: por que
    un archivo que ayer salio `PROCESADO` con 0.93 hoy esta en `ERROR` con tres
    intentos, es la pregunta que un contador no contesta.

    Sin esta tabla, "auditar los resultados" del escaneo seria leer el estado
    actual y suponer. Y el log de la aplicacion no sirve: se pierde en cada
    reinicio del contenedor.
    """

    __tablename__ = "scan_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('VISTO', 'CREADO', 'ACTUALIZADO', 'SIN_CAMBIOS', "
            "'OMITIDO', 'ERROR', 'REINTENTO', 'BORRADO')",
            name="ck_scan_events_action",
        ),
        # El historico se consulta por archivo y en orden cronologico. Sin este
        # indice, la pregunta "que le paso a este archivo" es un seq scan de
        # todos los eventos de todos los archivos.
        Index("ix_scan_events_file_created", "scan_file_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    scan_file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scan_files.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Que paso. La lista tiene que coincidir con la constraint de arriba.
    action = Column(String(20), nullable=False)

    # El detalle en texto: el motivo del error, o que se actualizo. Es lo que
    # hace que un `ERROR` sin texto sea inutil.
    detail = Column(Text, nullable=True)

    # Quien lo disparo: el correo del token, o `sistema` para el escaneo
    # automatico. Texto y no llave foranea por la misma razon que
    # `tickets.spot_checked_by`: la baja de una cuenta es `is_active = false`,
    # y el veredicto tiene que sobrevivir a que alguien borre la fila.
    actor = Column(String(255), nullable=True)

    # La confianza que produjo este intento, cuando la hubo. Se copia aqui para
    # que el historico no dependa de leer el ticket, que puede haber cambiado
    # desde entonces.
    confidence = Column(Numeric(4, 3), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    scan_file = relationship("ScanFileModel", back_populates="events")
