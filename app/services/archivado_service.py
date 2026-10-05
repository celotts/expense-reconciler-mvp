"""Mover el comprobante ya resuelto a la carpeta de escaneados.

QUE HACE Y QUE NO
=================

Mueve, no borra. Un comprobante autorizado se va de la carpeta de entrada a la
carpeta de escaneados, y sigue existiendo exactamente igual: mismo archivo,
mismos bytes. Es lo que hace posible una auditoria ("de donde salio esta entrada
de inventario") sin depender de que el sistema no hayamintido.

Por que existe, y por que NO antes
==================================

Sin esto, la carpeta de entrada se vuelve un deposito: los comprobantes ya
resueltos se quedan mezclados con los que faltan por revisar, y la unica forma de
saber cuales son cuales es consultar el estado en la base. Un archivo movido es
una senal visible de que ya se resolvio.

Y por que se movia solo al final, y por que no antes
---------------------------------------------------

El montaje de la carpeta de entrada estuvo en `:ro` desde el principio, y el
motivo esta escrito en `docker-compose.yml`:

    "el escaner no borra ni escribe nada, y montarla de lectura lo hace
     imposible aunque un bug lo intentara. Un comprobante original que se borre
     porque el OCR salio mal es irreversible."

Mover no es borrar —el archivo sigue ahi— pero con la exactitud de OCR medida
(33.3% sobre fotos reales) el efecto practico habria sido el mismo: en el
momento en que se escribio esto, practicamente todos los comprobantes salian
PENDIENTE o REQUIERE_REVISION, y moverlos habia enterrado 67% de los papeles sin
que nadie los hubiera revisado nunca.

Por eso el movimiento exige `PROCESADO`: que una persona haya confirmado la
compra y las lineas ya hayan entrado al inventario. `EN_REVISION` se queda donde
esta, porque es exactamente el papel que alguien tiene que mirar.

POR QUE LA CARPETA DE DESTINO ESTA FUERA DEL ARBOL QUE SE ESCANEA
================================================================

Porque el escaneo es recursivo (`TICKETS_SCAN_RECURSIVO = True` por omision). Si
`Ticket_Scan` fuera una subcarpeta de la carpeta de entrada, el siguiente
`POST /scan` volveria a ver los archivos movidos, su `relative_path` habria
cambiado, el ledger no los reconoceria como los mismos y **re-OCR** cada corrida.

No duplica el ticket —lo detiene `ix_tickets_source_hash`— pero gasta la lectura
para siempre. Es el problema que AGENTS.md describe como regla 6, y la razon de
que el destino sea una CARPETA HERMANA.

Idempotencia
============

Mover un archivo que ya no esta en la carpeta de entrada no es un error: es el
caso normal de una segunda corrida. Devuelve `None` y no pasa nada.

Y el registro: `scan_events`
----------------------------

Cada movimiento deja una fila con la accion `BORRADO` —el nombre que ya existia
en el enum de `ck_scan_events_action` y que hasta ahora no usaba nadie. Se llama
asi y no `MOVIDO` porque `ck_scan_events_action` es una constraint cerrada en la
base: anadir un valor al enum del DDL sin migrar deja el INSERT rechazado en
produccion, y una accion nueva que no se puede escribir es una accion que no
existe.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)


class ErrorDeArchivado(Exception):
    """El archivo no se pudo mover.

    Distinto de "no hay nada que mover": esto es un fallo y el escaner lo
    reporta, no lo traga.
    """


@dataclass(frozen=True)
class ResultadoDelMovimiento:
    """Que paso con un archivo. `movido=False` con `motivo` es lo normal."""

    movido: bool
    ruta_origen: Path
    ruta_destino: Path | None = None
    motivo: str | None = None


def carpeta_de_escaneados() -> Path:
    """La carpeta de destino, resuelta y creada si no existe.

    VACIA significa "una carpeta hermana de la de entrada", y se calcula en
    vez de fijarse en un default. La razon esta medida: un default como
    `/tickets_scan` solo existe DENTRO del contenedor, y fuera de Docker —los
    tests, `uvicorn` directo— es un path del sistema donde crear da "Read-only
    file system" en macOS. Una omision tiene que funcionar en los dos lados.

    Se resuelve cada vez y no se cachea, por el mismo argumento que
    `scan_service.raiz()`: un test puede cambiar el ajuste y un operador puede
    mover la carpeta con la app corriendo.
    """
    if settings.TICKETS_SCAN_OUTPUT_DIR:
        destino = Path(settings.TICKETS_SCAN_OUTPUT_DIR).expanduser().resolve()
    else:
        entrada = Path(settings.TICKETS_INPUT_DIR).expanduser().resolve()
        destino = entrada.parent / "Tickets_Scan"

    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _destino_libre(destino: Path) -> Path:
    """Una ruta que no exista todavia, y nunca sobreescribiendo.

    Por que nunca se sobreescribe, y no "porque es lo correcto": un comprobante
    pisado por otro es un comprobante perdido, y se pierde en silencio —el
    escaner devuelve exito, el archivo desaparecio y nadie lo sabe hasta que un
    contador pregunta por una compra que no aparece.

    El caso que lo dispara: dos fotos de un iPhone se exportan como
    `IMG_4220.jpeg` y `IMG_4220 2.jpeg`, y en la carpeta de destino —plana, por
    subcarpeta— colisionan. La segunda desplazaria a la primera.

    Se anade un sufijo numerico en vez de comparar fechas: comparar
    `st_mtime` para decidir cual se queda es una heuristica que funciona hasta
    que alguien copie un archivo y le pise la fecha. Anadir sufijo no necesita
    suponer nada.
    """
    if not destino.exists():
        return destino

    for n in range(1, 1000):
        candidato = destino.with_name(f"{destino.stem}_{n}{destino.suffix}")
        if not candidato.exists():
            return candidato

    raise ErrorDeArchivado(
        f"hay mas de 1000 archivos llamados {destino.name} en "
        f"{destino.parent}; no se elige uno al azar."
    )


def eliminar_de_la_entrada(relative_path: str) -> ResultadoDelMovimiento:
    """Borra un comprobante YA respaldado en la base, de la carpeta de entrada.

    LA REGLA DE ESTA FUNCION
    ========================
    **Solo borra si el documento ya esta en `ticket_documents`.** Esa comprobacion
    la hace el que llama —`scan_service`, que tiene la sesion de base— y esta
    funcion es el ultimo paso: el `unlink`.

    No es un detalle de implementacion. El archivo del disco es la COPIA; la
    evidencia es la fila de `ticket_documents`, con sus bytes y su `sha256`. Sin
    esa fila, borrar el archivo deja un ticket sin comprobante y un muestreo de
    exactitud que ya no se puede hacer nunca — porque la pregunta "la lectura
    coincidio con el papel?" deja de tener respuesta.

    Y hay un caso real donde la fila NO esta: `guardar_documento` usa un savepoint
    y devuelve `False` si el documento no se pudo guardar (por ejemplo, siTodavia
    no cabe). El ticket sobrevive sin su comprobante, es un fallo deliberado, y
    ese ticket tiene su archivo intacto justamente por eso.

    NUNCA borra la carpeta. Solo un archivo, y solo del nivel que dice
    `relative_path`. Un `shutil.rmtree` por un nombre mal formado seria la forma
    mas corta de perder la carpeta de entrada entera.

    `FileNotFoundError` no es un fallo: es la segunda corrida, o el archivo ya se
    borro. Se devuelve como "no borrado, ya no estaba", que es lo que es.
    """
    # `scan_service` importa a este modulo, asi que la importacion de
    # `ruta_de_relativo` va DENTRO de la funcion. A nivel de modulo seria un
    # ciclo, y el fallo aparece como "partially initialized module", que no
    # senala el archivo que lo Provoco. Es el mismo patron que
    # `capture._ocr_por_defecto`.
    from app.services.scan_service import ruta_de_relativo

    origen = ruta_de_relativo(relative_path)

    if not origen.exists():
        return ResultadoDelMovimiento(
            movido=False,
            ruta_origen=origen,
            ruta_destino=None,
            motivo="el archivo ya no esta en la carpeta de entrada",
        )

    if not origen.is_file():
        # Un symlink o un directorio con el nombre del comprobante. No se toca.
        return ResultadoDelMovimiento(
            movido=False,
            ruta_origen=origen,
            ruta_destino=None,
            motivo="no es un archivo regular: no se borra",
        )

    try:
        origen.unlink()
    except OSError as exc:
        return ResultadoDelMovimiento(
            movido=False,
            ruta_origen=origen,
            ruta_destino=None,
            motivo=f"no se pudo borrar: {exc}",
        )

    return ResultadoDelMovimiento(
        movido=True,
        ruta_origen=origen,
        # No hay destino: el archivo desaparecio de proposito, no esta en otra
        # carpeta. Se dice explicitamente para que nadie lo busque en
        # `Tickets_Scan`, donde no va a estar nunca.
        ruta_destino=None,
        motivo="borrado de la entrada: el comprobante esta respaldado en la base",
    )


async def esta_respaldado(db, ticket, relative_path: str) -> tuple[bool, str]:
    """¿Está el comprobante de este archivo guardado en `ticket_documents`?

    Y no solo "¿hay un documento?": el `sha256` del archivo tiene que COINCIDIR
    con el guardado. Un ticket puede tener varias versiones del comprobante
    (regla 18: el papel se apila, nunca se altera), y borrar el archivo cuya
    version no es la vigente dejaria al ticket con un papel que no es el que se
    leyo. La coincidencia de bytes es lo que dice "este archivo es exactamente el
    que respaldamos".

    La ultima version es la que importa: `version` mas alto del ticket.

    Devuelve `(respaldo, motivo_si_no)`. El motivo va al log y al evento, porque
    "no borre porque no habia respaldo" es una decision que alguien tiene que
    poder revisar despues: es la diferencia entre "se borro lo que ya estaba
    guardado" y "no se borro nada porque no habia nada guardado".
    """
    import hashlib

    from app.models.ticket_document import TicketDocumentModel

    documento = (
        await db.execute(
            select(TicketDocumentModel)
            .where(TicketDocumentModel.ticket_id == ticket.id)
            .order_by(TicketDocumentModel.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if documento is None:
        return False, (
            "el ticket no tiene documento guardado; el archivo se conserva porque "
            "es el unico comprobante que existe"
        )

    # `scan_service` importa a este modulo, asi que `ruta_de_relativo` va
    # importado DENTRO de la funcion. A nivel de modulo seria un ciclo de
    # importacion cuyo error dice "partially initialized module", sin senalar
    # este archivo. Es el mismo patron que `capture._ocr_por_defecto`.
    from app.services.scan_service import ruta_de_relativo

    if not documento.sha256:
        # Sin hash guardado no hay forma de comparar, y comparar de mas es
        # precisamente lo que este borrado tiene que impedir.
        return False, "el documento guardado no tiene sha256; no se puede verificar"

    ruta = ruta_de_relativo(relative_path)
    if not ruta.exists():
        return False, "el archivo ya no esta en la carpeta de entrada"

    try:
        digest = hashlib.sha256(ruta.read_bytes()).hexdigest()
    except OSError as exc:
        return False, f"no se pudo leer el archivo para verificarlo: {exc}"

    if digest != documento.sha256:
        return False, (
            "el sha256 del archivo no coincide con el documento guardado: es una "
            "version distinta y no se borra"
        )

    return True, "el comprobante esta guardado en la base con los mismos bytes"


def archivar(
    ruta_origen: Path,
    relative_path: str,
) -> ResultadoDelMovimiento:
    """Mueve un comprobante a la carpeta de escaneados.

    `relative_path` es el que tiene el registro del escaner, y se usa para elegir
    el subdirectorio de destino: un comprobante que estaba en `viaje/2025/03/`
    queda en `Ticket_Scan/viaje/2025/03/`, no aplastado contra otros tres que se
    llamen igual.

    Mueve y nunca borra. El archivo sale de donde estaba, pero sigue existiendo:
    es la evidencia de la que depende una auditoria.
    """
    origen = ruta_origen
    destino_raiz = carpeta_de_escaneados()

    if not origen.exists():
        # No es un fallo: es la segunda corrida, o el archivo ya se movio. Un
        # `raise` aqui haria que cada `POST /scan` posterior fallara.
        return ResultadoDelMovimiento(
            movido=False,
            ruta_origen=origen,
            ruta_destino=None,
            motivo="el archivo ya no esta en la carpeta de entrada",
        )

    # Se conserva la estructura de subdirectorios del origen. Aplanar perderia la
    # agrupacion por viaje o por mes, que es informacion que el escaner ya
    # respeta al leer (`TICKETS_SCAN_RECURSIVO` existe justo para eso).
    destino = destino_raiz / Path(relative_path).parent
    destino.mkdir(parents=True, exist_ok=True)

    try:
        destino_final = _destino_libre(destino / Path(relative_path).name)
        shutil.move(str(origen), str(destino_final))
    except OSError as exc:
        raise ErrorDeArchivado(
            f"no se pudo mover {origen} a {destino}: {exc}"
        ) from exc

    logger.info("Comprobante archivado: %s -> %s", origen, destino_final)
    return ResultadoDelMovimiento(
        movido=True, ruta_origen=origen, ruta_destino=destino_final
    )