"""El comprobante tal como lo subio la persona.

Por que existe
--------------

El sistema se olvidaba del documento en cuanto terminaba de leerlo. De la
subida solo quedaban tres cosas: el nombre del archivo (`tickets.source_file`),
su hash (`tickets.source_hash`) y el texto que la lectura habia sacado
(`tickets.raw_text`). Los bytes no.

Eso no se notaba al capturar, y hacia tres cosas imposibles:

  1. **El muestreo no se podia hacer.** `SpotCheckRequest` pregunta si la
     extraccion coincidio con el papel. Para responder hay que ver el papel. Sin
     el archivo, el revisor tiene el `raw_text` - que en la ruta de vision es lo
     que el modelo escribio, no lo que dice el papel - y decide a ciegas. Un
     veredicto dado sin ver el documento no mide la exactitud: mide lo que el
     revisor recuerda del ticket.
  2. **La revision de la cola era a ciegas tambien.** "Corrige contra el
     documento original" es lo que dice la pantalla, y el documento original no
     estaba en ninguna parte.
  3. **No se podia reintentar la lectura.** Un PDF que hoy no se lee porque el
     extractor estaba apagado queda ilegible para siempre, aunque manana se
     encienda. Sin los bytes no hay segunda oportunidad.

Que el archivo se borrara no era una decision de privacidad sino un descuido: no
habia donde guardarlo. Esta tabla es ese donde.

Por que los bytes van en la base y no en el disco
-------------------------------------------------

Un comprovante es de 10 MB (ver `TICKET_MAX_BYTES`) y el sistema es local, de un
solo proceso. Meterlo en la base tiene tres ventajas concretas:

  - **La copia de seguridad es una sola.** Si el archivo esta en el disco y los
    datos en Postgres, respaldar el historico contable sin los comprobantes deja
    una base que no se puede auditar. Y el producto promises poder auditar.
  - **No hay dos caminos que puedan divergir.** Un archivo en disco y una fila
    que lo apunta se desincronizan: alguien borra el archivo, o la fila se
    importa en otra maquina y el archivo no esta. Con los bytes en la tabla, la
    fila y el archivo son lo mismo y no pueden separarse.
  - **El borrado es una sola operacion.** `ON DELETE CASCADE` borra el archivo
    con el ticket. Con un archivo en disco, queda el huerfano o hace falta una
    tarea de limpieza que nunca se escribe.

El costo es real y hay que decirlo: la base crece con los documentos, y 10 MB
por comprobante es mucho mas que los 200 bytes de los datos del ticket. Quien
opte por un almacenamiento de objetos (S3, MinIO) tiene que cambiar esta tabla y
nada mas: `contenido` pasa a ser una ruta y `descargar_documento` pasa a hacer un
GET. La interfaz entre el servicio y el disco no cambia.

Por que `content_type` es de los que el sistema conoce
------------------------------------------------------

De lo que se sube se guarda el `Content-Type` declarado, pero el endpoint que lo
devuelve **no lo usa tal cual**. Sale de una lista cerrada, y si no esta en ella
se sirve como `application/octet-stream`.

El motivo es que el `Content-Type` viene del cliente, y un `Content-Type` controlado
por el cliente decides como lo interpreta el navegador. Servir
`text/html` con bytes que el sistema no ha revisado convierte el endpoint en un
sitio donde se puede ejecutar script: es XSS servido desde el propio dominio, con
sesion iniciada, y el usuario que lo dispara es una persona de confianza de la
empresa. Lo mismo con `image/svg+xml`, que es HTML con superpoderes de dibujo.
Por eso la lista es de los que el sistema sabe que son inertes, y lo que no
esta, se descarga como archivo en vez de abrirse en el navegador.

`tamano` y `sha256` estan porque hacen falta sin volver al archivo
------------------------------------------------------------------

`tamano` es lo que permite que la UI diga cuanto pesa antes de descargar, y
sobre todo lo que permite que un ticket de 10 MB no se descargue en un movil con
datos. `sha256` es el mismo valor de `tickets.source_hash` y esta ahi para poder
verificar la integridad **sin leer el archivo entero**: si alguien alterara los
bytes, el hash de la fila ya no coincidiria con el del contenido. Es
verificable, no decorativo.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    TIMESTAMP, Column, ForeignKey, Index, Integer, LargeBinary, String, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow


# Los unicos Content-Type que el endpoint puede devolver.
#
# Todos son formatos que el navegador NO puede ejecutar. `image/jpeg` y
# `image/png` se muestran, `application/pdf` lo abre el visor del navegador, y
# `text/plain` se muestra como texto. Todo lo demas se sirve como
# `application/octet-stream`, que el navegador descarga en vez de interpretarlo.
#
# La lista es cerrada a proposito: agregarle un tipo aqui es una decision de
# seguridad, y lo que hace falta para eso no es "dejar pasar los que conoce el
# sistema" sino "dejar pasar los que no pueden ejecutar nada".
CONTENT_TYPE_POR_DEFECTO = "application/octet-stream"
CONTENT_TYPES_SERVIBLES = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "text/plain",
})


def content_type_servible(declarado: str | None) -> str:
    """El `Content-Type` con el que se sirve, sea cual sea el que declaro el cliente.

    Lo declarado que no esta en la lista cerrada no se descarta: se sirve como
    octet-stream, que conserva los bytes intactos. Lo que no se hace es confiar
    en el, y la razon esta en el docstring del modulo.
    """
    if not declarado:
        return CONTENT_TYPE_POR_DEFECTO
    limpio = declarado.split(";")[0].strip().lower()
    if limpio in CONTENT_TYPES_SERVIBLES:
        return limpio
    return CONTENT_TYPE_POR_DEFECTO


class TicketDocumentModel(Base):
    """Los bytes de un comprobante, o NULL si ese ticket se tecleo a mano.

    Una fila por ticket, no un historico de versiones. Se sobrescribe cuando se
    reextrae, y por que se puede hacer sin pedir permiso es justo lo que esta
    tabla habilita: volver a leer el mismo archivo con un extractor distinto
    produce una lectura mejor, y el gasto es el mismo. Guardar las dos
    extracciones seria duplicar el gasto en el historico contable.

    `ticket_id` es UNIQUE y a la vez la llave foranea. Las dos cosas juntas dicen
    lo mismo desde dos angulos: `UNIQUE` impide que un ticket tenga dos
    documentos (que haria que "el documento de este ticket" no tenga respuesta
    unica), y la llave foranea con CASCADE hace que borrar el ticket borre el
    archivo, para que no queden comprobantes huerfanos de gastos que ya no
    existen. Con `ON DELETE CASCADE` el borrado del archivo no es una tarea
    pendiente: es una consecuencia de borrar el gasto.
    """

    __tablename__ = "ticket_documents"
    __table_args__ = (
        Index("ix_ticket_documents_ticket", "ticket_id", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Los bytes. `LargeBinary` mapea a BYTEA en Postgres, que es lo que se
    # quiere: el limite de Postgres es 1 GB por campo y aqui el tope lo pone
    # `TICKET_MAX_BYTES` (10 MB), que es una regla de negocio, no del motor.
    contenido = Column(LargeBinary, nullable=False)
    # Lo declaro el cliente, y solo se usa para el nombre del archivo y como
    # pista de lo que se guardo. Lo que decide como se sirve es
    # `content_type_servible`.
    content_type = Column(String(120), nullable=True)
    nombre_archivo = Column(String(500), nullable=True)
    tamano = Column(Integer, nullable=False)
    # El mismo SHA-256 que esta en `tickets.source_hash`. Se repite aqui para
    # poder verificar la integridad del contenido sin releer el archivo entero.
    sha256 = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    ticket = relationship("TicketModel", back_populates="documento")

    @property
    def content_type_servible(self) -> str:
        return content_type_servible(self.content_type)
