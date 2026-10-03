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
    TIMESTAMP, Column, ForeignKey, Index, Integer, LargeBinary, String, Text, text,
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

    UNA CADENA DE VERSIONES, NO UNA FILA. Antes esta tabla era "una fila por
    ticket" y `PUT /documento` la sobrescribia con un `DELETE` + `INSERT`: los
    bytes originales desaparecian y no quedaba ni el hash anterior, ni quien lo
    cambio, ni cuando, ni por que. El endpoint ni siquiera tomaba el usuario
    autenticado.

    Eso hacia falsa la promesa del producto: que el comprobante es la evidencia
    contra la que se contrasta cualquier lectura. Un papel que se puede borrar en
    silencio no es evidencia, y el muestreo de exactitud estaria midiendo algo
    que nadie reviso sin que se pudiera demostrar.

    COMO SE LEE AHORA
    -----------------
    - `reemplaza_a` apunta HACIA ATRAS: la version nueva dice a cual reemplaza.
      Y va hacia atras porque la tabla es append-only y el `UPDATE` esta
      prohibido, asi que la version vieja no puede apuntar a la nueva. La version
      1 es la unica con `reemplaza_a IS NULL`, y lo segue siendo para siempre.
    - El documento **vigente** es el de mayor `version`. No es "el que nadie
      apunta": esa consulta habria que rehacerla en cada lectura, y con el indice
      unico sobre `reemplaza_a` la cadena no se puede bifurcar, asi que el mayor
      `version` ES la punta.
    - La version anterior no se borra: queda enlazada. La cadena se recorre por
      `version` en orden ascendente.
    - `actor` y `motivo` son obligatorios a partir de la segunda version. La
      primera la pone el escaner y no hay nadie detras; un reemplazo siempre lo
      hace una persona, y "por que cambiaste el papel" es la pregunta que un
      contador va a hacer.

    `ON DELETE CASCADE` se mantiene, y con un trigger de Postgres que prohibe
    borrar un documento mientras su ticket exista. Los dos juntos: borrar el
    gasto borra su papel (sin huerfanos), pero nadie puede borrar SOLO el papel.
    Ver `db/migrations/0009_documento_inmutable.sql`.

    QUE NO ES
    ---------
    No es un historico de LO QUE SE LEYO. Eso vive en `tickets` y en el
    `spot_check`. Aqui lo que se conserva es el PAPEL: cambiar el documento no
    cambia `tickets.source_hash` ni la lectura, y por eso un veredicto de
    muestreo registrado sigue correspondiendo a lo que el sistema leyo.
    """

    __tablename__ = "ticket_documents"
    __table_args__ = (
        # Un indice unico PARCIAL sobre `reemplaza_a`: cada version puede ser
        # reemplazada por UNA sola.version siguiente.
        #
        # NO se puede poner un unico sobre `reemplaza_a IS NULL` (es decir, "solo
        # una version sin padre") porque con la cadena append-only la version 1
        # es la unica sin `reemplaza_a` para siempre: no se puede volver a poner
        # en NULL sin un UPDATE, y el UPDATE esta prohibido. Ese indice
        # rechazaria la segunda insercion.
        #
        # Lo que si importa es que la cadena NO se bifurque: que no existan dos
        # versiones diciendo "reemplazo a la misma". Eso es lo que este indice
        # prohibe, y por eso el vigente es "la de mayor `version`" y no "la que
        # nadie apunta".
        Index(
            "ix_ticket_documents_sin_bifurcar",
            "reemplaza_a",
            unique=True,
            sqlite_where=text("reemplaza_a IS NOT NULL"),
            postgresql_where=text("reemplaza_a IS NOT NULL"),
        ),
        Index("ix_ticket_documents_cadena", "ticket_id", "version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # La version anterior de esta misma cadena. `None` = vigente.
    #
    # `ON DELETE SET NULL` y no `CASCADE`: borrar un documento (que el trigger
    # impide mientras el ticket exista) no puede llevarse la cadena entera. Con
    # SET NULL la fila se convierte en vigente, que es la unica lectura sensata si
    # algo se borrara.
    reemplaza_a = Column(
        UUID(as_uuid=True),
        ForeignKey("ticket_documents.id", ondelete="SET NULL"),
        nullable=True,
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
    #
    # Ojo con el nombre: es el hash DEL PAPEL DE ESTA VERSION, no del ticket. Si
    # se reemplaza el documento, este valor cambia y `tickets.source_hash` no: la
    # diferencia entre "que leyo el sistema" y "que papel se guardo" es
    # exactamente lo que esta tabla hace visible.
    sha256 = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # Quien agrego ESTA version y por que. Texto y no llave foranea, igual que
    # `tickets.spot_checked_by`: la firma tiene que sobrevivir a la baja de la
    # cuenta.
    actor = Column(String(255), nullable=True)
    motivo = Column(Text, nullable=True)
    # 1 para la version inicial; 2, 3... para cada reemplazo. Es la orden de la
    # cadena legible, que `reemplaza_a` ya implica pero no de un vistazo.
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))

    ticket = relationship("TicketModel", back_populates="documentos")

    @property
    def content_type_servible(self) -> str:
        return content_type_servible(self.content_type)
