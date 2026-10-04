"""Guardar y devolver el comprobante original de un ticket.

La lectura y el guardado son dos hechos separados
-------------------------------------------------

`capture_ticket` devuelve un `TicketExtractionResult`: los campos que el sistema
entendio del documento. No devuelve el documento. Esa separacion es correcta y
no se va a tocar - la cascada no tiene por que arrastrar 10 MB por la funcion -
pero tiene una consecuencia que hay que hacer explícita: **alguien tiene que
guardar los bytes**, y si no se guardan, el documento desaparece.

Antes de este modulo, nadie los guardaba. El archivo se leia, se extraian cinco
campos y los bytes se perdian con el `request`. Un ticket ilegible se conservaba
(con el motivo en la cola), pero el papel del que venia no existia en ningun
lado. Ver el docstring de `app/models/ticket_document.py` para que se rompe sin
esto.

Este es ese "alguien", y son cuatro funciones:

    guardar_documento      la escritura, en la misma transaccion que el ticket
    _escribir_documento    la escritura de verdad, sin el try; falla ruidoso
    documento_de_ticket    la lectura, para revision y muestreo
    reemplazar_documento    la lectura del reintento: mismo gasto, mejor papel

Por que el guardado va en la MISMA transaccion que el ticket
-----------------------------------------------------------

No es una preferencia. Si el documento se guardara despues, en un `commit`
aparte, habria una ventana entre los dos commits en la que el ticket existe y
el documento no. En esa ventana, un ticket en la cola de revision aparece sin
papel que revisar, y la razon - "el sistema no lo guardo" - no se puede
distinguir de "el documento no se subio nunca".

Con el guardado en la misma transaccion, las dos cosas existen o ninguna: o el
ticket llega con su papel, o no llega ninguno de los dos. Un ticket sin
documento solo es posible cuando el documento no existia, y eso tiene una causa
unica y visible: la captura manual.

Por que se sobrescribe y no se lleva version
--------------------------------------------

Un ticket tiene un documento, no un historial de documentos. Cuando se
reextrae el mismo comprobante con un extractor distinto, lo que cambia es la
LECTURA, no el GASTO: es el mismo papel y el mismo total. Guardar las dos
lecturas como dos filas seria duplicar el gasto en el historico contable, que es
la ultima cosa que este sistema puede hacer.

Las dos escrituras compiten por el mismo `ticket_id`, que es UNIQUE, asi que un
INSERT repetido seria un IntegrityError. Por eso `_escribir_documento` borra
antes de insertar: no para esquivar la constraint, sino porque borrar antes y
fallar despues es indistinguible de no haber escrito nada, mientras que el
IntegrityError es explicito.

Y la que se queda es la ultima lectura, no la mejor. Elegir la mejor exigiria
comparar extracciones, y comparar no es una operacion definida: el sistema no
tiene forma de saber que una lectura es mejor que otra sin que una persona lo
diga. Predecible y sin sorpresas: la ultima.

Por que `guardar_documento` no falla la captura
-----------------------------------------------

Si la base no acepta los bytes, el ticket se guarda igual y el documento no. Es
la decision de la ruta de captura: un comprobante que se leyo bien y no se
puede archivar es un gasto real, y perderlo por un problema de almacenamiento es
peor que un gasto sin comprobante adjunto, que ademas esta visible en la cola.

Un ticket sin documento tiene que poder existir. Por eso `guardar_documento`
devuelve `False` en vez de propagar la excepcion, y por eso la UI tiene que
poder mostrar "no hay documento" sin que sea un error.

Y no es un `except` generico por pereza: cada fallo se distingue, y el que se
distingue es el que va al log con su excepcion. Un `except Exception: pass`
dejaria el sistema entero en silencio y nadie sabria si el documento se guardo o
no, que es el estado en el que no se puede operar.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import media_type_real
from app.core.subida import TICKET_MAX_BYTES
from app.models.ticket import TicketModel
from app.models.ticket_document import TicketDocumentModel

logger = logging.getLogger(__name__)


async def guardar_documento(
    db: AsyncSession,
    ticket: TicketModel,
    contenido: bytes,
    *,
    content_type: str | None = None,
    nombre_archivo: str | None = None,
    actor: str | None = None,
    motivo: str | None = None,
) -> bool:
    """Guarda los bytes del comprobante. Devuelve si se guardaron.

    Va en la misma transaccion que el ticket: la razon esta en el docstring.

    Los tickets manuales no tienen documento y no se les inventa uno. Un ticket
    tecleado por una persona no viene de un archivo, y guardar un texto
    inventado como si fuera el papel seria fabricar evidencia: el muestreo
    compara la lectura contra el documento, y un documento inventado siempre le
    daria la razon al sistema.

    Un archivo que no cabe no se guarda, y tampoco es un error: si el contenido
    no cumplio el tope al subirlo, es practicamente imposible que llegue aqui. Se
    comprueba igual, porque un `INSERT` de 2 GB en Postgres no falla: se escribe,
    y el problema aparece cuando alguien mas intenta guardar.

    EL `CONTENT_TYPE` SE DEDUCE DE LOS BYTES CUANDO FALTA
    ------------------------------------------------------
    El `content_type` que se guarda sale de `media_type_real(contenido)`, no del
    cliente, cuando el cliente no dio uno. Sin esto, todo lo que entra por el
    escaner de carpeta se guardaba con `content_type=NULL` y se servia como
    `application/octet-stream`, que el navegador DESCARGA en vez de pintar: la
    foto existia, se podia bajar, y el revisor veia un recuadro vacio.

    Medido: 7 de 9 documentos guardados tenian `content_type` NULL. Los otros 2
    habian entrado por una subida HTTP, que si lo manda.

    Por que se deduce y no se copia el declarado: lo mismo que en
    `content_type_servible` —el declarado viene de fuera— y una razon extra, que
    aqui es la de `archivo_real`: **el nombre del archivo tampoco dice nada**. Hay
    un `IMG_4253 2.HEIC` que por sus bytes es un JPEG.

    Lo que se deduce NO es lo que se sirve: `content_type_servible()` sigue
    aplicando la lista cerrada al final. Esta funcion dice que ES el archivo; el
    allowlist dice si se sirve como tal. Un HEIC deduce `image/heic`, cae fuera de
    la lista y se sirve como octet-stream, que es lo correcto porque ningun
    navegador lo pinta.

    Y lo mas importante: **esto NO es lo que arregla las fotos**. Escribir la
    columna no alcanza para los documentos ya guardados, porque
    `ticket_documents` es append-only por trigger y backfillear exige un `UPDATE`
    que Postgres rechaza a proposito. El arreglo que de verdad las ve es deducir
    al SERVIR, en `TicketDocumentModel.content_type_servible`, que no toca la base
    y por eso tambien arregla las 7 que ya estaban ahi.

    Guardar el tipo deducido se queda porque es una mejora real para lo que se
    suba de aqui en adelante, y porque deja la columna con un dato en vez de con
    un hueco. Pero no es la defensa: si alguien quita esto, lo unico que se pierde
    es la columna, y las fotos siguen viéndose.
    """
    if not contenido:
        return False

    if content_type is None:
        content_type = media_type_real(contenido)

    if len(contenido) > TICKET_MAX_BYTES:
        logger.warning(
            "el documento del ticket %s mide %d bytes y no se guarda (tope %d)",
            ticket.id, len(contenido), TICKET_MAX_BYTES,
        )
        return False

    try:
        await _escribir_documento(
            db, ticket, contenido,
            content_type=content_type,
            nombre_archivo=nombre_archivo,
            actor=actor,
            motivo=motivo,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # Se registra y se devuelve False. La excepcion no sube porque el
        # ticket ya se creo y ya se decidio su estado: reviertiendolo por un
        # problema de almacenamiento se perderia un gasto que si se leyo bien.
        #
        # El motivo exacto va al log y no a la cola, y hay una razon para esa
        # diferencia con los fallos de lectura: un fallo de lectura dice algo
        # del PAPEL (esta borroso, no trae fecha) y le compete a quien revisa el
        # ticket. Un fallo de escritura dice algo del SISTEMA y le compete a
        # quien lo opera. Meterlo en `raw_text` haria que un revisor perdiera
        # tiempo intentando arreglar un comprobante que esta perfecto.
        logger.error(
            "no se pudo guardar el documento del ticket %s: %s",
            ticket.id, exc, exc_info=True,
        )
        # No hay rollback aqui a proposito: el savepoint de arriba ya revirtio
        # solo el documento, y un rollback a secas desharia tambien el ticket.
        # Lo que queda por limpiar es el `add()` a medias, que un commit
        # posterior podria intentar insertar otra vez.
        for pendiente in list(db.new):
            if isinstance(pendiente, TicketDocumentModel):
                db.expunge(pendiente)
        return False


async def _escribir_documento(
    db: AsyncSession,
    ticket: TicketModel,
    contenido: bytes,
    *,
    content_type: str | None = None,
    nombre_archivo: str | None = None,
    actor: str | None = None,
    motivo: str | None = None,
) -> None:
    """La escritura de verdad. Lanza si falla; el `try` vive arriba.

    Va aparte, y no dentro de la misma funcion, por una razon que es la misma
    que hay en `app/core/subida.py`: lo que se prueba tiene que ser una pieza
    identificable. Aqui la pieza es "escribir los bytes", y se puede hacer que
    falle para comprobar que el ticket sobrevive. Con el `try` en la misma
    funcion, la unica forma de provocar el fallo seria romper la sesion entera,
    que es justo el error que este diseno evita.

    Un SAVEPOINT, no la transaccion de verdad. Un `rollback()` a secas deshace
    todo lo hecho en la sesion desde el ultimo commit, y aqui el ticket todavia
    no se ha confirmado: se perderia un gasto que si se leyo bien, por un
    problema de almacenamiento. Con `begin_nested` lo que se revierte es solo el
    documento.

    No es una precaucion teorica. Un comprobante que no cabe en el TOAST de
    Postgres lanza aqui, y sin el savepoint la respuesta seria un 500 con el
    ticket perdido: el peor resultado posible, porque ni el gasto existe ni el
    error le dice a nadie que hubo que volver a subirlo.
    """
    async with db.begin_nested():
        # ANTES BORRABA EL DOCUMENTO ANTERIOR. AHORA LO APENDE.
        #
        # Hacia `DELETE` del documento previo y luego `INSERT` del nuevo: los
        # bytes originales desaparecian y no quedaba nada de ellos. Ahora inserta
        # una version nueva apuntando a la vigente con `reemplaza_a`, y la
        # anterior se queda.
        #
        # Por que NO un upsert: `ON CONFLICT (ticket_id) DO UPDATE` necesita un
        # indice unico sobre `ticket_id`, y ese indice es justo lo que se elimino
        # (una fila por ticket) para poder tener una cadena. Ademas un upsert
        # SOBRESCRIBE, que es lo que se quiere dejar de hacer.
        #
        # La vigente se busca en la base y no en `ticket.documentos` porque esa
        # relationship es de solo lectura desde `0009` (la escritura la lleva
        # Postgres y este servicio, no el ORM). Es la de mayor `version`: ver la
        # nota de `documento_de_ticket`.
        actual = (
            await db.execute(
                select(TicketDocumentModel)
                .where(TicketDocumentModel.ticket_id == ticket.id)
                .order_by(TicketDocumentModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        db.add(TicketDocumentModel(
            ticket_id=ticket.id,
            contenido=contenido,
            content_type=content_type,
            nombre_archivo=nombre_archivo or ticket.source_file,
            tamano=len(contenido),
            # El hash DE LOS BYTES QUE SE ESTAN GUARDANDO, y no una copia del
            # `tickets.source_hash`.
            #
            # Era un bug: la columna se llenaba con `ticket.source_hash`, que es
            # el hash de lo que EL ESCANER leyo. En la ruta del escaner coinciden
            # por casualidad, asi que el bug dormia. Pero al reemplazar un
            # documento, los bytes nuevos se sellaban con el hash de los bytes
            # VIEJOS, y la columna dejaba de describir lo que estaba guardada:
            # "verificable, no decorativo", decia el docstring, y no lo era.
            # Ahora es el hash del contenido, que es lo que si se puede verificar
            # mas tarde sin volver a pedirle el archivo a nadie.
            sha256=hashlib.sha256(contenido).hexdigest(),
            reemplaza_a=actual.id if actual is not None else None,
            version=(actual.version + 1) if actual is not None else 1,
            actor=actor,
            motivo=motivo,
        ))
        # El `flush` explicito es lo que hace que el error aparezca AQUI y no en
        # el commit del endpoint, que esta mas lejos del contexto y donde el
        # error seria un 500 sin relacion aparente con el archivo.
        await db.flush()


async def documento_de_ticket(
    db: AsyncSession,
    ticket_id,
) -> TicketDocumentModel | None:
    """El documento VIGENTE de un ticket, o `None` si no tiene.

    `None` es un resultado normal, no un error: los tickets de captura manual no
    tienen documento, y una captura antigua tampoco. La razon esta en
    `guardar_documento`.

    El vigente es el de mayor `version`, y no "el que nadie apunta". Con la
    cadena append-only la version 1 es la unica con `reemplaza_a IS NULL` para
    siempre, asi que esa definicion daria SIEMPRE la primera, que es justo la
    que se quiere dejar de servir. Ademas, ordenar por version y tomar una sola
    fila no puede dar `MultipleResultsFound`, que es lo que pasaria con un
    `scalar_one_or_none()` sin filtrar.
    """
    return (
        await db.execute(
            select(TicketDocumentModel)
            .where(TicketDocumentModel.ticket_id == ticket_id)
            .order_by(TicketDocumentModel.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def documentos_del_ticket(
    db: AsyncSession,
    ticket_id,
) -> list[TicketDocumentModel]:
    """LA CADENA COMPLETA de documentos de un ticket, en orden de version.

    Es lo que responde "¿que papel guardamos y como llegamos a el?". No devuelve
    los bytes: eso lo hace el endpoint del vigente, que es el que se descarga.
    Esta funcion es el historial, y su unico trabajo es que el cambio sea visible
    en lugar de silencioso.
    """
    return list(
        (
            await db.execute(
                select(TicketDocumentModel)
                .where(TicketDocumentModel.ticket_id == ticket_id)
                .order_by(TicketDocumentModel.version)
            )
        ).scalars()
    )


async def reemplazar_documento(
    db: AsyncSession,
    ticket_id,
    contenido: bytes,
    *,
    actor: str,
    motivo: str,
    content_type: str | None = None,
    nombre_archivo: str | None = None,
) -> bool:
    """AGREGA una version nueva del documento. La anterior NO se borra.

    Es la operacion que hace posible el punto 3 del docstring del modulo:
    reintentar la lectura con un extractor distinto. Un PDF que no se pudo leer
    porque el extractor estaba apagado no queda ilegible para siempre; se le
    vuelve a subir el archivo y este lo agrega.

    No crea un ticket nuevo ni toca los datos que ya extrajo la lectura
    anterior, y esa es la parte que hay que entender: reextraer es una decision
    que todavia no existe en la API. Lo que cambia con esto es el PAPEL, no la
    LECTURA. Cambiar los dos a la vez haria que un veredicto de muestreo ya
    registrado dejara de corresponder a lo que el sistema leyo, y el muestreo
    estaria midiendo algo que nadie reviso.

    `actor` y `motivo` son OBLIGATORIOS, sin valor por omision. Son lo que
    convierte esto de "el papel cambio" a "el papel cambio, lo hizo esta persona
    por esta razon". Sin ellos, la cadena registra que algo paso y no quien ni
    por que, que es el mismo silencio que venia corregiendo. La base tambien los
    exige: `ck_ticket_documents_actor_si_reemplaza` y
    `ck_ticket_documents_motivo_si_reemplaza`.

    `False` si no hay ticket. `False` tambien si el ticket ya fue revisado por
    una persona: cambiar el papel que alguien ya contrasto invalidaria su
    revision, y el unico camino para eso es `POST /scan/files/{id}/reprocess` o
    el endpoint de revision, que son explicitos. Es la misma regla 7 de
    `AGENTS.md`, aplicada al papel.
    """
    ticket = (
        await db.execute(
            select(TicketModel).where(TicketModel.id == ticket_id)
        )
    ).scalar_one_or_none()

    if ticket is None:
        return False

    if ticket.reviewed_at is not None:
        logger.warning(
            "no se reemplaza el documento del ticket %s: ya fue revisado por una "
            "persona y cambiar el papel invalidaria esa revision", ticket_id,
        )
        return False

    return await guardar_documento(
        db, ticket, contenido,
        content_type=content_type,
        nombre_archivo=nombre_archivo,
        actor=actor,
        motivo=motivo,
    )
