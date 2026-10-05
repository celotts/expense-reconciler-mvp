"""Escanea la carpeta de tickets y decide, archivo por archivo, que se hace.

Este modulo lee el disco. Eso lo hace distinto de todos los demas servicios del
proyecto, y por eso tiene una restriccion que no es negociable:

LA RUTA NO SE PIDE, SE CONFIGURA.

`TICKETS_INPUT_DIR` es lo unico que el escaner conoce. No hay endpoint, ni
parametro, ni campo en el body que acepte una carpeta, y no debe haberlos nunca.
Un escaner que acepta `folder_path` del cliente es lectura arbitraria del disco
del servidor: `POST /scan {"folder_path": "/"}` sube el `.env` del proyecto como
si fuera un comprobante, y `{"folder_path": "/Users/alguien/.ssh"}` lo sube
como foto. No hace falta ser_admin para intentarlo, porque el endpoint exige
autenticacion, no autorizacion.

Lo que si se acepta es un `relative_path` de un archivo YA registrado, y
aun asi se vuelve a construir la ruta y se vuelve a comprobar que cae dentro
de la carpeta. Esa comprobacion no es decorativa: la hace `os.path` con
`realpath`, que resuelve symlinks, y sin ella un archivo que se creo con un
enlace a `/etc` se leeria desde la carpeta sin haber salido de ella nunca.

Que se repitan las comprobaciones en dos capas (el servicio y el endpoint) es a
proposito. Una sola capa se puede refactorizar sin querer y se pierde en
silencio; dos capas, quitar una rompe la otra.

QUE NO HACE ESTE ESCANER

- No mira el periodo. Lee lo que haya en la carpeta, y el filtro de fechas es
  de la conciliacion. Un escaner con corte por mes obliga a correrlo cada mes y
  pierde los comprobantes que llegaron tarde.
- No borra nada de la carpeta. Es de solo lectura por definicion: un error de
  OCR que borre el comprobante original es irreversible y el papel no se
  reimprime.
- No repara lo que el gate dejo pendiente. Si un ticket va a la cola, va a la
  cola. Corregirlo es trabajo de una persona, y automatizarlo seria quitar el
  un control que si funciona.

LA DECISION POR ARCHIVO

Para cada archivo se lee su SHA-256 y se busca por `relative_path`:

    no estaba        -> se registra PENDIENTE y se lee
    estaba, mismo hash -> SIN_CAMBIOS: no se vuelve a leer (esta es la
                          idempotencia, y es la que evita gastar OCR en los
                          mismos 400 archivos en cada corrida)
    estaba, otro hash  -> el archivo cambio. Se relee SOLO si el escaneo vino
                          con `reprocesar=True`, porque reescribir el ticket de
                          un archivo que el operador todavia no ha revisado
                          puede ser lo que quiere, y reescribir el de uno que ya
                          fue corregido a mano le borra el trabajo.

Y hay un caso mas que no es un hash: los MISMOS bytes en una ruta que ya se
procesaron. Ahi no se crea un segundo ticket (el indice unico de
`tickets.source_hash` lo impide y `persistir_extraccion` lo devuelve), pero el
archivo queda como `DUPLICADO` y apuntando al ticket original, para que la
pregunta "este ticket, de que archivo salio?" tenga respuesta.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import detectar_tipo_real
from app.services import scan_registry
from app.core.config import settings
from app.core.enums import (
    SETTLED_STATUSES,
    ConfidenceSource,
    EstadoCompra,
    ExtractionStatus,
    ScanStatus,
    SourceType,
)
from app.core.time import utcnow
from app.models.inventario import CompraModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.services.ai_extractor import ai_extractor
from app.services.archivado_service import (
    ErrorDeArchivado,
    archivar,
    carpeta_de_escaneados,
)
from app.services.capture import ExtractionUnavailable, capture_ticket
from app.services.confidence_gate import gate_ticket
from app.services.ticket_persistence import persistir_extraccion

logger = logging.getLogger(__name__)

# Ruido que se produce al meter archivos en una carpeta desde Finder, Windows o
# un escaner. No se registran: no son comprobantes fallidos, son archivos que
# jamas fueron candidatos, y meterlos en la tabla hace que el conteo de errores
# diga una cosa que no es.
RUIDO_DE_SISTEMA = frozenset({
    ".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".gitignore",
})

# Nombres que empiezan con esto son temporales de un editor o del sistema.
PREFIJOS_TEMPORALES = ("~$", ".~lock.", "._")

# Un solo escaneo a la vez. Dos a la vez leen los mismos archivos, compiten por
# el UNIQUE de `relative_path` y una de las dos termina en IntegrityError. No es
# una optimisticidad: es que el escaneo es de lectura de disco y no gana nada
# con paralelizarse, solo se pelean el INSERT.
_CANDADO_DE_ESCANEO = asyncio.Lock()


class ArchivoFueraDeLaCarpeta(ValueError):
    """Se pidio una ruta que no cae dentro de `TICKETS_INPUT_DIR`.

    Es un error de programacion o de un cliente que mando un `relative_path` con
    `..`. Se lanza antes de tocar el disco, y el mensaje lleva las dos rutas
    porque "path traversal" no dice nada util de quien lo intento.
    """


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def raiz() -> Path:
    """La carpeta de entrada, ya resuelta.

    Se resuelve cada vez y no se cachea en un import: `TICKETS_INPUT_DIR` se
    valida al arrancar, pero un test puede cambiarlo y un operador puede mover
    la carpeta con la app corriendo. Cachear la raiz en el momento del import
    haria que el cambio no se viera hasta reiniciar, que es la forma mas
    confusa de tener una configuracion que no aplica.
    """
    return Path(settings.TICKETS_INPUT_DIR).expanduser().resolve()


def ruta_de_relativo(relative_path: str) -> Path:
    """De un `relative_path` del registro a una ruta real, comprobada.

    La comprobacion va DESPUES de resolver, no antes. Un `..` en el texto es
    visible; un symlink en el disco no, y `resolve()` es lo unico que lo
    revela. Comprobar la cadena antes de resolver daria una sensacion de
    seguridad que no existe: `/etc/passwd` se puede alcanzar sin un solo `..`.
    """
    base = raiz()
    candidata = (base / relative_path).resolve()

    # `is_relative_to` y no `str(candidata).startswith(str(base))`: el segundo
    # da True para `.../Tickets_app_secreto` cuando la base es `.../Tickets_app`,
    # porque una ruta es prefijo textual de otra. Ese es el error clasico de un
    # `startswith` aplicado a rutas, y aqui abriria la carpeta hermana.
    if not candidata.is_relative_to(base):
        raise ArchivoFueraDeLaCarpeta(
            f"la ruta {candidata} queda fuera de la carpeta de tickets ({base})"
        )

    return candidata


def relativo_de(ruta: Path) -> str:
    """De una ruta real al `relative_path` que se guarda.

    Se guarda en POSIX siempre, aunque la app corra en Windows: el registro se
    escribe a mano para leerlo y para comparar, y `a\\b` y `a/b` no son la misma
    clave. Un `relative_path` con barras invertidas haria que la misma carpeta
    tuviera dos nombres distintos segun quien escribiera la fila.
    """
    return ruta.relative_to(raiz()).as_posix()


# ---------------------------------------------------------------------------
# Recorrido
# ---------------------------------------------------------------------------


@dataclass
class ArchivoVista:
    """Un archivo de la carpeta, todavia sin leer entero."""

    ruta: Path
    relative_path: str
    tamano: int
    mtime: datetime
    extension: str


def _es_ruido(nombre: str) -> bool:
    if nombre.lower() in RUIDO_DE_SISTEMA:
        return True
    if nombre.startswith(PREFIJOS_TEMPORALES) or nombre.startswith("."):
        return True
    return False


def listar_archivos() -> list[ArchivoVista]:
    """Los archivos candidatos de la carpeta, ordenados por ruta.

    Ordenados, y no en el orden en que los devuelve el sistema de archivos: dos
    corridas sobre la misma carpeta tienen que mirar los archivos en el mismo
    orden, o el tope por corrida (`TICKETS_SCAN_MAX_ARCHIVOS`) corta en un
    punto distinto cada vez y los ultimos archivos de la lista no se ven nunca.

    Se usa `os.walk` y no `Path.rglob` por el `followlinks`. `rglob` sigue los
    enlaces simbolicos a directorios, y un enlace a `.` o a un padre dentro de
    la propia carpeta es un ciclo: el recorrido no termina y el escaner se queda
    sin memoria leyendo la misma foto para siempre. `os.walk(followlinks=False)`
    no sigue enlaces a directorios, y el unico enlace que se atraviesa es el de
    un archivo, que se comprueba aparte al resolver.
    """
    base = raiz()
    if not base.is_dir():
        return []

    encontrados: list[ArchivoVista] = []

    # `TICKETS_SCAN_RECURSIVO` apaga el descenso a subcarpetas. Se implementa
    # vaciando `dirnames` en la primera vuelta, no con un `break`: `os.walk`
    # decide su profundidad segun lo que queden en `dirnames` al terminar cada
    # vuelta, y vaciar la lista es la unica forma de decirle "este nivel y
    # ninguno mas" sin inventar un flag aparte.
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        # Se ordena en cada nivel para que el orden sea estable tambien dentro
        # de un subdirectorio.
        dirnames.sort()
        if not settings.TICKETS_SCAN_RECURSIVO:
            dirnames.clear()
        for nombre in sorted(filenames):
            if _es_ruido(nombre):
                continue

            ruta = Path(dirpath) / nombre
            try:
                # `stat` y no `is_file()`: `is_file()` sigue symlinks y hace
                # dos llamadas al sistema por una. Un symlink colgado
                # (apunta a un archivo que ya no existe) revienta con
                # FileNotFoundError y tumba el escaneo entero por un archivo
                # que el operador ni puede ver.
                info = ruta.lstat()
            except OSError as exc:
                logger.warning("no se pudo leer %s: %s", ruta, exc)
                continue

            # Los enlaces simbolicos a archivos se saltan aqui y no se resuelven
            # para leerlos. Es la decision conservadora: un symlink dentro de la
            # carpeta es la forma mas corta de leer algo de fuera, y un
            # comprobante real no llega como enlace.
            if os.path.islink(ruta):
                logger.info("se omite %s: es un enlace simbolico", ruta)
                continue

            if not info.st_mode & 0o100000:  # S_ISREG
                continue

            if info.st_size > settings.TICKETS_SCAN_MAX_BYTES:
                # No se registra: un video de 2 GB en la carpeta no es un
                # comprobante que se pueda procesar, y dejarlo en la tabla
                # como error haria que el conteo de errores mezclara "no se
                # pudo leer" con "no tiene sentido leerlo". El motivo va al log.
                logger.warning(
                    "se omite %s: %d bytes, mas del tope de %d",
                    ruta, info.st_size, settings.TICKETS_SCAN_MAX_BYTES,
                )
                continue

            try:
                relativo = relativo_de(ruta)
            except ValueError:
                # No deberia pasar: `ruta` sale de caminar `base`. Si pasa, es
                # que algo metio un enlace a directorio y `os.walk` lo siguió a
                # pesar de `followlinks=False`, y se salta en vez de confiar.
                logger.warning("se omite %s: no se pudo relativizar", ruta)
                continue

            encontrados.append(
                ArchivoVista(
                    ruta=ruta,
                    relative_path=relativo,
                    tamano=info.st_size,
                    mtime=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc),
                    extension=ruta.suffix.lower(),
                )
            )

    encontrados.sort(key=lambda v: v.relative_path)
    return encontrados


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass
class ResumenArchivo:
    """Que le paso a UN archivo en esta corrida."""

    relative_path: str
    scan_file_id: UUID | None = None
    status: ScanStatus = ScanStatus.PENDIENTE
    accion: str = "VISTO"
    ticket_id: UUID | None = None
    confianza: float | None = None
    origen: ConfidenceSource | None = None
    detalle: str | None = None

    # --- El archivado ---------------------------------------------------
    #
    # Van en la respuesta y no solo en un contador porque la pregunta al vaciar
    # la carpeta de entrada es siempre "¿que se movio, donde quedo, y el sistema
    # lo entendio?".
    #
    # `estaba_pendiente` es el que hay que mirar: True significa que el archivo
    # se movio aunque la lectura no fuera confiable. Con OCR al 33.3% eso es
    # frecuente, y esconderlo seria el error — la carpeta de escaneados no dice
    # "esto se leyo bien", dice "esto ya se intento y su veredicto quedo escrito".
    archivado: bool = False
    ruta_archivo: str | None = None
    extraction_status: str | None = None
    estaba_pendiente: bool | None = None
    solo_simulado: bool = False

    # --- Los datos del ticket -------------------------------------------
    #
    # Lo que el lector afirmo que dice el papel, para no tener que pedir un
    # `GET /tickets/{id}` por cada archivo de la corrida.
    #
    # Es un `dict` y no el schema de la API a proposito: `services/` no importa
    # de `schemas/`, la frontera esa la respeta el resto del servicio. El router
    # lo valida en `_de_resumen`.
    #
    # Lo arma `_resolver_datos_de_tickets`, DESPUES del bucle y en una sola
    # consulta, y no dentro de `procesar_archivo`. La razon es que
    # `procesar_archivo` tiene trece salidas y el dato depende de una fila que
    # a veces todavia no existe: el DUPLICADO no crea ticket y apunta al de otro
    # archivo, el OMITIDO por `company_id` no creo ninguno, y el ACTUALIZADO
    # puede haber encontrado el ticket ya borrado. Resolverlo al final, desde el
    # `ticket_id` que cada rama ya dejo escrito, es lo unico que cubre las trece
    # sin trece copias de la misma asignacion.
    datos: dict | None = None


@dataclass
class ResumenEscaneo:
    """Lo que devuelve un `POST /scan`."""

    carpeta: str
    archivos_vistos: int = 0
    nuevos: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    duplicados: int = 0
    con_error: int = 0
    no_soportados: int = 0
    omitidos_por_tope: int = 0
    lecturas_por_motor: dict[str, int] = field(default_factory=dict)
    detalles: list[ResumenArchivo] = field(default_factory=list)

    # El total de la corrida. Es un `dict` y no el schema de la API por la misma
    # razon que `ResumenArchivo.datos`: `services/` no importa de `schemas/`.
    resumen: dict | None = None
    # El id con el que esta corrida se puede consultar mientras corre, por
    # `GET /scan/runs/{id}`. Va en la respuesta para que el cliente no tenga que
    # inventar una manera de correlacionar: la corrida y su total salen juntos.
    corrida_id: str | None = None

    # El archivado. `archivados_pendientes` va al lado de `archivados` porque es
    # el que da miedo: son los comprobantes que se movieron SIN que el sistema
    # los leyera bien. `simulado` dice si lo que se lee ya ocurrio o es un plan.
    archivados: int = 0
    archivados_pendientes: int = 0
    # Los que NO se movieron porque su ticket aun necesita a una persona. Es el
    # numero que dice si la bandeja se esta vaciando o si se esta llenando.
    quedan_en_bandeja: int = 0
    # Los que se BORRARON de la carpeta de entrada por estar ya respaldados. Antes
    # esta carpeta se vaciaba moviendo a `Tickets_Scan`; ahora se vacia borrando.
    borrados_de_entrada: int = 0
    carpeta_destino: str | None = None
    simulado: bool = False

    @property
    def leidos(self) -> int:
        return self.nuevos + self.actualizados


# ---------------------------------------------------------------------------
# Persistencia del registro
# ---------------------------------------------------------------------------


async def _registrar(
    db: AsyncSession,
    fila: ScanFileModel,
    accion: str,
    detalle: str | None = None,
    actor: str | None = None,
    confianza: float | None = None,
) -> None:
    """Anota un evento del archivo. Nunca lanza: la auditoria no tumba el escaneo.

    Si el INSERT del evento falla, lo que se pierde es la linea de tiempo, no el
    escaneo. Al reves, si esto propagara el error, un problema de escritura en
    una tabla de historico detendria el procesamiento de archivos que si se
    pudieron leer, y el operador veria cero tickets en vez de algunos.
    """
    db.add(
        ScanEventModel(
            scan_file_id=fila.id,
            action=accion,
            detail=detalle,
            actor=actor,
            confidence=Decimal(str(confianza)) if confianza is not None else None,
        )
    )
    try:
        await db.flush()
    except Exception as exc:  # pragma: no cover - depende del motor
        logger.warning("no se pudo registrar el evento %s de %s: %s", accion, fila.relative_path, exc)
        await db.rollback()


async def _buscar(db: AsyncSession, relative_path: str) -> ScanFileModel | None:
    return (
        await db.execute(
            select(ScanFileModel).where(ScanFileModel.relative_path == relative_path)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# El escaneo
# ---------------------------------------------------------------------------


def _es_revisado_por_humano(ticket: TicketModel) -> bool:
    """¿Alguien ya corrigio este ticket a mano?

    Si la respuesta es si, un reproceso automatico NO lo sobreescribe. El
    operador corrigio el proveedor o la fecha a mano porque la lectura estaba
    mal, y volver a correr OCR sobre el mismo archivo va a volver a leerlo mal
    y a borrar la correccion. Perder trabajo humano en silencio es peor que no
    reprocesar: nadie se entera de que paso hasta que el cierre no cuadra.
    """
    return ticket.reviewed_at is not None


async def _actualizar_ticket(
    db: AsyncSession,
    ticket: TicketModel,
    extracted,
    contenido: bytes,
) -> TicketModel:
    """Re-lee un ticket que ya existe y cambia sus datos.

    No reutiliza `persistir_extraccion` porque esa funcion tiene una regla que
    aqui no aplica: si el contenido es identico devuelve el ticket existente sin
    tocarlo. Aqui el contenido cambio (por eso se llego), y lo que se quiere es
    exactamente lo contrario.

    El gate se vuelve a correr completo, no se copia el veredicto anterior: un
    archivo que cambio puede tener ahora el RFC que antes faltaba, y la
    confianza correcta es la de los datos nuevos.
    """
    decision = gate_ticket(
        provider_name=extracted.provider_name,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        expense_date=extracted.expense_date,
        provider_tax_id=extracted.provider_tax_id,
        subtotal=extracted.subtotal,
        ieps_amount=extracted.ieps_amount,
        confidence=extracted.confidence,
        source=extracted.confidence_source,
    )

    ticket.provider_name = extracted.provider_name
    ticket.provider_tax_id = extracted.provider_tax_id
    ticket.total_amount = extracted.total_amount
    ticket.tax_amount = extracted.tax_amount
    ticket.subtotal = extracted.subtotal
    # El IEPS tambien se reescribe. Sin esto, reprocesar un comprobante con IEPS
    # lo dejaria con el IEPS de la lectura anterior (o en NULL) mientras el total
    # y el IVA si se actualizan: la fila se contradiria a si misma y el gate ya
    # no podria comprobarla.
    ticket.ieps_amount = extracted.ieps_amount
    # Las LINEAS DE PRODUCTO tambien se actualizan. Antes no se copiaban, y eso
    # hacia que reprocesar un comprobante no sirviera de nada para el inventario:
    # el ticket se actualizaba (total, IVA, proveedor) pero `items` se quedaba en
    # el NULL que dejo la primera lectura por OCR.
    #
    # Medido: un PDF reprocesado salio con `confidence_source=llm` y confianza
    # 0.95, y `items` seguia NULL. Con esto, reprocesar es la via para llenar las
    # lineas de lo que el OCR no pudo leer.
    #
    # Se escribe SIEMPRE, incluso con None: si esta relectura tampoco trajo
    # lineas, lo honesto es que no hay lineas, y dejar las de la lectura anterior
    # seria mostrar contenido de un papel que ya se esta releyendo.
    ticket.items = extracted.items
    ticket.raw_text = extracted.raw_text
    ticket.confidence = decision.persisted_confidence
    ticket.confidence_source = decision.confidence_source.value
    ticket.extraction_status = decision.status.value
    ticket.validation_errors = decision.validation.as_text()
    # El hash se recalcula porque el contenido cambio. Si no, el indice unico
    # `ix_tickets_source_hash` dejaria apuntando al hash viejo, y el siguiente
    # escaneo de ESTE archivo lo compararia contra si mismo.
    ticket.source_hash = hashlib.sha256(contenido).hexdigest()
    # La fecha se escribe SIEMPRE, tambien cuando la lectura no trajo ninguna, y
    # con la misma convencion provisional que usa `persistir_extraccion`: el dia
    # local de hoy, con `date_missing` a la vista en `validation_errors`.
    #
    # Antes solo se escribia `if extracted.expense_date is not None`, y eso
    # dejaba una fila que se contradice a si misma. Medido en el reprocesado de
    # las tres fotos de la carpeta: la foto de Oxxo se relejo con OCR, la lectura
    # nueva NO trajo fecha (el gate puso `date_missing`), y el ticket seguia
    # guardando `2021-08-15` de una lectura anterior. O sea: una fecha de una
    # lectura mas vieja, con la fila diciendo que no hay fecha, y nadie que sepa
    # de donde salio ese 15 de agosto de 2021.
    #
    # No se puede conservar por dos razones, y las dos importan. Una: esa fecha
    # no la puso una persona, porque si la hubiera puesto el ticket tendria
    # `reviewed_at` y este codigo no habria llegado aqui (regla 7). Viene de una
    # lectura automatica anterior, que ya no es lo que dice el papel. Dos: dejarla
    # hace que el gasto entre en un periodo que la fila niega tener, y el cierre
    # mensual lo cuenta sin que nada avise.
    #
    # Y no es "quedarse con la fecha vieja" lo que evita la fabricacion: el
    # `date.today()` de aqui es la MISMA marca que ya acepta el alta de un ticket
    # (`ticket_persistence.py`), con su `date_missing` al lado. Fabricar es lo que
    # haria el gate si esto fuera `None`: un `date_missing` sin marcar, indistinguible
    # de un gasto real de ese dia.
    ticket.expense_date = extracted.expense_date or date.today()

    await db.commit()
    await db.refresh(ticket)
    return ticket


async def procesar_archivo(
    db: AsyncSession,
    vista: ArchivoVista,
    contenido: bytes,
    company_id: UUID | None,
    actor: str,
    *,
    forzar: bool = False,
) -> ResumenArchivo:
    """Lee UN archivo y deja su registro al dia. Nunca lanza por el archivo.

    Un archivo que no se pudo leer no puede tumbar la corrida. Si un TIFF
    corrupto parara el escaneo, los 200 archivos que si se pueden leer se
    quedarían sin procesar porque en la carpeta habia uno malo, y el operador
    no tendria forma de saber cual era: el error estaria en la cara de la
    peticion, no en la fila del archivo.
    """
    resumen = ResumenArchivo(relative_path=vista.relative_path)
    ahora = utcnow()

    tipo = detectar_tipo_real(contenido)

    # EL ESCANER SOLO ADMITE PDF E IMAGEN. NADA MAS.
    #
    # Antes, si los bytes no eran un PDF ni una imagen con firma conocida, se
    # preguntaba `es_texto_plano` y, si lo eran, se leia con la cascada de texto.
    # Eso hacia que CUALQUIER archivo UTF-8 en la carpeta se podia convertir en un
    # ticket. Medido y reproducido: un `verdad.json` de etiquetas escritas a mano
    # (con proveedor, RFC, subtotal, total y fecha) entro por el escaner y salio
    # `AUTO_APROBADO`:
    #
    #     provider_name = Cadena Comercial Oxxo, S.A. de C.V.
    #     total_amount  = 51.50
    #     extraction_status = AUTO_APROBADO
    #
    # Un gasto que no existe, afirmado con maxima confianza, y como
    # `AUTO_APROBADO` entra a libros y se exporta.
    #
    # La carpeta es un proceso DESATENDIDO. Si alguien deja ahi unas notas, un
    # export del banco o un `.csv`, el sistema lo lee como comprobante y lo
    # aprueba solo. Ese es el error humano que la carga en lote se supone que
    # evita: no lo evita, lo automatiza.
    #
    # Por que no se admite texto y ya:
    #
    # - El trabajo de este modulo es digitalizar ESCANEOS. Un `.txt` no es un
    #   escaneo.
    # - El comprobante en texto plano si existe (`test-files/factura_gas.txt`) y
    #   no se pierde: sigue entrando por `POST /tickets/extract` con
    #   `file_type=text`, donde quien lo sube lo DECLARA. La diferencia entre las
    #   dos vias es la que importa: en la API hay una persona diciendo "esto es un
    #   comprobante"; en la carpeta no hay nadie.
    # - Es la unica forma de que la afirmacion del sistema se corresponda con un
    #   papel. Sin esta comprobacion, "el sistema leyo" y "hay un comprobante" son
    #   la misma frase, y el muestreo de exactitud deja de medir nada.
    if tipo is None:
        tipo = None  # se deja None a proposito: cae en NO_SOPORTADO, abajo

    fila = await _buscar(db, vista.relative_path)
    nuevo = fila is None

    if nuevo:
        fila = ScanFileModel(
            relative_path=vista.relative_path,
            content_hash=hashlib.sha256(contenido).hexdigest(),
            file_size=vista.tamano,
            file_mtime=vista.mtime,
            detected_format=tipo,
            declared_extension=vista.extension or None,
            status=ScanStatus.PENDIENTE.value,
            attempts=0,
            first_seen_at=ahora,
        )
        db.add(fila)
        try:
            await db.flush()
        except Exception as exc:
            await db.rollback()
            logger.warning("no se pudo registrar %s: %s", vista.relative_path, exc)
            resumen.accion = "ERROR"
            resumen.status = ScanStatus.ERROR
            resumen.detalle = f"no se pudo registrar el archivo: {exc}"
            return resumen
    else:
        hash_actual = hashlib.sha256(contenido).hexdigest()
        # `forzar` gana aqui, y es lo que hace que `POST /scan/files/{id}/
        # reprocess` sirva de algo para un archivo que NO cambio.
        #
        # Sin esta condicion, el endpoint de reproceso seria un no-op: el
        # contenido es identico, se reportaba `SIN_CAMBIOS` y se volvia, sin
        # releer nada. Y ese es justo el caso para el que existe: se mejoro el
        # motor de OCR, se instalo Tesseract donde no habia, o el archivo salio
        # mal la primera vez y los bytes no van a cambiar por reintentarlo.
        # El atajo de "sin cambios" NO aplica cuando el archivo se leyo pero no
        # tiene ticket. Ese es el caso de un escaneo de inventario: el primer
        # `POST /scan` sin `company_id` lee los archivos y los deja registrados
        # con su hash, para que se vean en la lista. Si el atajo no mirara el
        # ticket, el siguiente escaneo CON empresa los encontraria "sin
        # cambios", no crearia ningun ticket y el archivo no volveria a
        # atribuirse nunca por la via normal. Habria que reprocesar uno por uno
        # a mano.
        #
        # Se comprueba `ticket_id`, no el estado: `PROCESADO` significa "se
        # leyo", no "esta contabilizado". Un archivo leido en el inventario esta
        # `PROCESADO` y sin ticket, y esos dos hechos juntos son los que
        # separan "ya esta" de "falta la empresa".
        falta_atribuir = fila.ticket_id is None and company_id is not None

        if hash_actual == fila.content_hash and not forzar and not falta_atribuir:
            fila.last_scanned_at = ahora
            fila.file_size = vista.tamano
            fila.file_mtime = vista.mtime
            await db.commit()
            resumen.scan_file_id = fila.id
            resumen.status = ScanStatus(fila.status)
            resumen.accion = "SIN_CAMBIOS"
            # El `ticket_id` se devuelve tambien en el atajo. Sin esto, el
            # archivado —que solo necesita "este archivo tiene ticket"— no
            # puede decidir nada en un archivo ya leido, y todos los
            # digitalizados antes de que existiera el archivado quedarian en la
            # carpeta de entrada para siempre.
            resumen.ticket_id = fila.ticket_id
            resumen.detalle = "el contenido no cambio desde la ultima lectura"
            await _registrar(db, fila, "SIN_CAMBIOS", resumen.detalle, actor)
            return resumen

        # El contenido cambio. Sin `forzar` no se relee: ver la nota del modulo.
        # La excepcion es `falta_atribuir`: el contenido NO cambio, lo que falta
        # es la empresa, y releer el archivo es justo lo que hace falta para
        # poder crear el ticket. Sin esta excepcion el atajo de arriba no
        # serviria de nada: aqui se volveria a salir igual.
        if not forzar and not falta_atribuir:
            fila.last_scanned_at = ahora
            fila.file_size = vista.tamano
            fila.file_mtime = vista.mtime
            await db.commit()
            resumen.scan_file_id = fila.id
            resumen.status = ScanStatus(fila.status)
            resumen.accion = "OMITIDO"
            resumen.detalle = (
                "el archivo cambio desde la ultima lectura; se relee solo con "
                "reprocesar=true o con POST /scan/files/{id}/reprocess"
            )
            await _registrar(db, fila, "OMITIDO", resumen.detalle, actor)
            return resumen

    resumen.scan_file_id = fila.id

    if tipo is None:
        fila.status = ScanStatus.NO_SOPORTADO.value
        fila.detected_format = None
        fila.last_scanned_at = ahora
        # El motivo dice LAS DOS cosas: que no se pudo leer y por que no se
        # intenta como texto. Un operador que ve "no soportado" tiene que poder
        # distinguir "el escaner esta roto" de "este archivo no es un
        # comprobante y no se va a intentar", que son problemas opuestos.
        fila.last_error = (
            "no es un PDF ni una imagen con firma conocida. El escaner de carpeta "
            "solo digitaliza escaneos: un archivo de texto se sube por "
            "POST /tickets/extract con file_type=text, donde quien lo sube lo "
            "declara."
        )
        await db.commit()
        resumen.status = ScanStatus.NO_SOPORTADO
        resumen.accion = "OMITIDO"
        resumen.detalle = fila.last_error
        await _registrar(db, fila, "OMITIDO", fila.last_error, actor)
        return resumen

    # A partir de aqui se intenta de verdad. Todo lo que falle se guarda como
    # ERROR con su motivo, y la corrida sigue con el siguiente archivo.
    fila.attempts += 1
    fila.last_scanned_at = ahora
    fila.detected_format = tipo
    fila.content_hash = hashlib.sha256(contenido).hexdigest()
    fila.file_size = vista.tamano
    fila.file_mtime = vista.mtime

    try:
        extracted = await capture_ticket(
            contenido,
            tipo,
            extract_from_image=ai_extractor.extract_from_image,
            extract_from_text=ai_extractor.extract_from_text,
        )
    except ExtractionUnavailable as exc:
        fila.status = ScanStatus.ERROR.value
        fila.last_error = str(exc)
        await db.commit()
        resumen.status = ScanStatus.ERROR
        resumen.accion = "ERROR"
        resumen.detalle = str(exc)
        await _registrar(db, fila, "ERROR", str(exc), actor)
        return resumen
    except Exception as exc:
        logger.exception("fallo inesperado leyendo %s", vista.relative_path)
        fila.status = ScanStatus.ERROR.value
        fila.last_error = f"fallo inesperado: {exc}"
        await db.commit()
        resumen.status = ScanStatus.ERROR
        resumen.accion = "ERROR"
        resumen.detalle = fila.last_error
        await _registrar(db, fila, "ERROR", fila.last_error, actor)
        return resumen

    resumen.confianza = extracted.confidence
    resumen.origen = extracted.confidence_source

    # Sin empresa no hay donde dejar el ticket. Se registra como leido para que
    # el operador vea que el archivo SI se entendio, y no como error: el
    # archivo no esta roto, lo que falta es la decision de a que empresa
    # pertenece, y esa la toma una persona.
    if company_id is None:
        fila.status = ScanStatus.PROCESADO.value
        fila.read_by = extracted.confidence_source.value
        fila.processed_at = ahora
        fila.last_error = (
            "leido correctamente pero sin empresa: no se creo ticket. Corre el "
            "escaneo con company_id para crear el ticket."
        )
        await db.commit()
        resumen.status = ScanStatus.PROCESADO
        resumen.accion = "OMITIDO"
        resumen.detalle = fila.last_error
        await _registrar(db, fila, "OMITIDO", fila.last_error, actor, resumen.confianza)
        return resumen

    try:
        if nuevo:
            # Antes de crear nada: ¿hay ya OTRO archivo con estos mismos bytes?
            #
            # Esto se pregunta por `scan_files`, no por `tickets.source_hash`.
            # El indice unico de `tickets` ya impediria el segundo ticket (y
            # `persistir_extraccion` devolveria el existente), pero sin esta
            # pregunta el archivo se reportaria como "CREADO" y el operador
            # veria dos archivos nuevos para un solo ticket. Con la pregunta, el
            # segundo queda `DUPLICADO` y apuntando al ticket original, y la
            # pregunta "este ticket salio de que archivo?" tiene respuesta.
            #
            # Se excluye la propia ruta a proposito: un archivo que se
            # reprocesa tiene su propia fila con el mismo hash, y contarse a si
            # mismo como duplicado haria que un reproceso nunca actualizara nada.
            ticket_hermano = await _ticket_de_otro_archivo(
                db, fila.content_hash, vista.relative_path
            )
            if ticket_hermano is not None:
                detalle = (
                    "mismos bytes que otro archivo ya procesado; se conserva el "
                    "ticket original y este archivo no se vuelve a leer"
                )
                fila.status = ScanStatus.DUPLICADO.value
                fila.ticket_id = ticket_hermano
                fila.company_id = company_id
                fila.read_by = extracted.confidence_source.value
                fila.processed_at = ahora
                fila.last_error = detalle
                await db.commit()
                resumen.status = ScanStatus.DUPLICADO
                resumen.accion = "OMITIDO"
                resumen.ticket_id = ticket_hermano
                resumen.detalle = detalle
                await _registrar(db, fila, "OMITIDO", detalle, actor, resumen.confianza)
                return resumen

            ticket = await persistir_extraccion(
                db, company_id, extracted, contenido,
                source_type=SourceType.DIRECTORY,
                source_file=vista.relative_path,
            )
            fila.status = ScanStatus.PROCESADO.value
            fila.ticket_id = ticket.id
            fila.company_id = company_id
            fila.read_by = extracted.confidence_source.value
            fila.processed_at = ahora
            fila.last_error = None
            await db.commit()
            resumen.status = ScanStatus.PROCESADO
            resumen.accion = "CREADO"
            resumen.ticket_id = ticket.id
            await _registrar(db, fila, "CREADO", None, actor, resumen.confianza)
            return resumen

        # El archivo ya estaba registrado y su contenido cambio: se relee el
        # ticket, si se puede.
        fila.status = ScanStatus.PROCESADO.value
        fila.company_id = fila.company_id or company_id
        fila.read_by = extracted.confidence_source.value
        fila.processed_at = ahora
        fila.last_error = None

        if fila.ticket_id is None:
            ticket = await persistir_extraccion(
                db, company_id, extracted, contenido,
                source_type=SourceType.DIRECTORY,
                source_file=vista.relative_path,
            )
            fila.ticket_id = ticket.id
            await db.commit()
            resumen.status = ScanStatus.PROCESADO
            resumen.accion = "CREADO"
            resumen.ticket_id = ticket.id
            await _registrar(db, fila, "CREADO", None, actor, resumen.confianza)
            return resumen

        ticket = (
            await db.execute(select(TicketModel).where(TicketModel.id == fila.ticket_id))
        ).scalar_one_or_none()

        if ticket is None:
            # El ticket se borro. Se crea uno nuevo con el contenido actual.
            nuevo_ticket = await persistir_extraccion(
                db, company_id, extracted, contenido,
                source_type=SourceType.DIRECTORY,
                source_file=vista.relative_path,
            )
            fila.ticket_id = nuevo_ticket.id
            await db.commit()
            resumen.status = ScanStatus.PROCESADO
            resumen.accion = "CREADO"
            resumen.ticket_id = nuevo_ticket.id
            await _registrar(
                db, fila, "CREADO",
                "el ticket anterior se habia borrado; se creo uno nuevo",
                actor, resumen.confianza,
            )
            return resumen

        if _es_revisado_por_humano(ticket):
            # El archivo cambio, pero el ticket ya tiene correcciones humanas.
            # No se tocan. Queda dicho en el registro, que es la unica forma de
            # que el operador se entere en vez de descubrirlo en el cierre.
            detalle = (
                "el archivo cambio desde la ultima lectura, pero el ticket ya "
                "fue revisado por una persona y no se sobreescribe. Usa "
                "POST /scan/files/{id}/reprocess con forzar=true si de verdad "
                "quieres reemplazar la correccion."
            )
            fila.status = ScanStatus.PROCESADO.value
            fila.last_error = detalle
            await db.commit()
            resumen.status = ScanStatus.PROCESADO
            resumen.accion = "OMITIDO"
            resumen.ticket_id = ticket.id
            resumen.detalle = detalle
            await _registrar(db, fila, "OMITIDO", detalle, actor, resumen.confianza)
            return resumen

        await _actualizar_ticket(db, ticket, extracted, contenido)
        fila.ticket_id = ticket.id
        await db.commit()
        resumen.status = ScanStatus.PROCESADO
        resumen.accion = "ACTUALIZADO"
        resumen.ticket_id = ticket.id
        await _registrar(db, fila, "ACTUALIZADO", None, actor, resumen.confianza)
        return resumen

    except Exception as exc:
        logger.exception("fallo guardando %s", vista.relative_path)
        await db.rollback()
        resumen.status = ScanStatus.ERROR
        resumen.accion = "ERROR"
        resumen.detalle = f"fallo guardando: {exc}"
        return resumen


async def _ticket_de_otro_archivo(
    db: AsyncSession, content_hash: str, relative_path: str
) -> UUID | None:
    """El ticket de otro archivo con los MISMOS bytes, o `None`.

    Se busca en `scan_files` y no en `tickets` porque la pregunta es "de que
    otro ARCHIVO salio este ticket", y esa informacion solo existe aqui: el
    ticket no guarda de que ruta vino mas que `source_file`, que es texto libre
    y no se indexa.
    """
    ticket_id = (
        await db.execute(
            select(ScanFileModel.ticket_id)
            .where(
                ScanFileModel.content_hash == content_hash,
                ScanFileModel.relative_path != relative_path,
                ScanFileModel.ticket_id.is_not(None),
                ScanFileModel.status == ScanStatus.PROCESADO.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return ticket_id


async def escanear(
    db: AsyncSession,
    company_id: UUID | None,
    actor: str,
    *,
    reprocesar: bool = False,
    solo_pendientes: bool = False,
    archivar: bool | None = None,
    simular: bool = False,
) -> ResumenEscaneo:
    """Recorre la carpeta y procesa lo que corresponda.

    Args:
        company_id: a que empresa atribuir los tickets. `None` inventaria sin
            crear tickets.
        reprocesar: releer tambien los archivos cuyo contenido cambio. Apagado
            por omision porque reescribir un ticket es una decision, no un
            efecto secundario de correr un escaneo.
        solo_pendientes: no tocar lo ya `PROCESADO` aunque haya cambiado.
        archivar: mover a la carpeta de escaneados lo digitalizado. `None` usa
            `TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR`.
        simular: decir que se moveria sin mover nada.

    EL ARCHIVADO Y LA EMPRESA
    =========================

    Sin `company_id` no se crea ningun ticket, y sin ticket no hay nada que
    archivar. `POST /scan` sin empresa es el modo de inventario —"que hay en la
    carpeta?"— y en ese modo **no se mueve nada**, porque mover un archivo sin
    que se haya guardado su contenido deja el comprobante en un sitio donde nadie
    lo va a buscar y sin registro de lo que se leyo.
    """
    resumen = ResumenEscaneo(carpeta=str(raiz()))

    if archivar is None:
        archivar = settings.TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR
    # Sin empresa no hay ticket, y sin ticket no hay nada archivable. Ver la nota.
    if company_id is None:
        archivar = False

    if archivar:
        resumen.carpeta_destino = str(carpeta_de_escaneados())
        resumen.simulado = simular
        if simular:
            logger.info(
                "SIMULACION de escaneo: no se creara ni se movera nada. "
                "Lo que se moveria ira a %s",
                resumen.carpeta_destino,
            )

    async with _CANDADO_DE_ESCANEO:
        archivos = listar_archivos()
        resumen.archivos_vistos = len(archivos)

        # El registro de la corrida se abre DENTRO del candado, no antes: si se
        # abriera antes, dos escaneos simultaneos anunciarian ambos "en curso" y
        # el canal mostraria el que perdio el candado como si trabajara, cuando
        # en realidad esta esperando. El candado es la verdad de quien esta
        # leyendo, y el registro tiene que decirlo.
        corrida = scan_registry.abrir_corrida(
            str(carpeta_de_escaneados()) if archivar else raiz().as_posix(),
            actor,
            simular,
        )
        resumen.corrida_id = corrida.id

        for indice, vista in enumerate(archivos):
            if indice >= settings.TICKETS_SCAN_MAX_ARCHIVOS:
                resumen.omitidos_por_tope = len(archivos) - indice
                logger.info(
                    "tope por corrida alcanzado (%d); %d archivos quedan para la "
                    "siguiente", settings.TICKETS_SCAN_MAX_ARCHIVOS,
                    resumen.omitidos_por_tope,
                )
                break

            scan_registry.marcar_archivo(corrida, vista.relative_path)

            try:
                contenido = vista.ruta.read_bytes()
            except OSError as exc:
                logger.warning("no se pudo leer %s: %s", vista.relative_path, exc)
                resumen.con_error += 1
                scan_registry.marcar_error(corrida)
                continue

            if solo_pendientes:
                fila = await _buscar(db, vista.relative_path)
                if fila is not None and ScanStatus(fila.status) == ScanStatus.PROCESADO:
                    continue

            detalle = await procesar_archivo(
                db, vista, contenido, company_id, actor, forzar=reprocesar
            )

            # El registro de la corrida se actualiza con el resultado de cada
            # archivo. Solo `leidos` y `con_error` se llevan la cuenta: lo demas
            # esta en el resumen del final.
            if detalle.ticket_id is not None:
                scan_registry.marcar_con_ticket(corrida)
            elif detalle.accion == "ERROR":
                scan_registry.marcar_error(corrida)

            # --- El archivado, DESPUES de registrar el resultado ----------
            #
            # Despues y no antes, por dos razones concretas:
            #
            # 1. Si se moviera antes y la lectura fallara, el comprobante queda
            #    en la carpeta de escaneados sin que nunca se haya guardado su
            #    contenido: el papel desaparece de donde se revisa y no hay
            #    ticket al que volver.
            # 2. `detalle.accion` y `detalle.ticket_id` solo existen despues de
            #    `procesar_archivo`. Antes no hay nada que archivar.
            #
            # Y se hace dentro del mismo paso del bucle para que el resumen y lo
            # que hay en disco no puedan quedar desincronizados.
            # --- Que se archiva -------------------------------------------
            #
            # La condicion NO es `accion in (CREADO, ACTUALIZADO)`. Es
            # "este archivo tiene ticket", y son cosas distintas:
            #
            # Un archivo digitalizado en una corrida ANTERIOR a este feature
            # reporta `SIN_CAMBIOS` en todas las corridas siguientes, porque su
            # contenido no cambio. Si solo archivara CREADO/ACTUALIZADO, esos
            # archivos nunca se moverian: quedarian en la carpeta de entrada para
            # siempre, que es justo lo que el archivado viene a evitar.
            #
            # La condicion real es "tiene ticket, no hay error, y el veredicto es
            # de los que ya no necesitan a nadie".
            #
            # EL TICKET TIENE QUE ESTAR RESUELTO, Y ESO ES UN CAMBIO DE FONDO
            # ===================================================================
            # Antes se archivaba cualquier archivo que tuviera ticket, y eso
            # movia a `Tickets_Scan` comprobantes en `PENDIENTE` y
            # `REQUIERE_REVISION`. Con OCR al 33-57% de exactitud eso era casi
            # todos: la carpeta de "escaneados" se llenaba de papeles que NADIE
            # habia revisado, y el nombre de la carpeta decia una cosa que el
            # contenido desmentia.
            #
            # El proyecto ya lo admitia y lo rodeaba de avisos
            # (`archivados_pendientes`, "el sistema ya lo intento"), que es una
            # forma de no romper la promesa sin cumplirla. Con esta regla la
            # promesa se cumple: **lo que esta en `Tickets_Scan` se leyo bien.**
            #
            # LO QUE NO SE MUEVE, Y POR QUE
            # ----------------------------
            # - ERROR / NO_SOPORTADO: no se pudo leer. Obvio: se quedan.
            # - DUPLICADO: ya esta en `Tickets_Scan` por el otro archivo. Moverlo
            #   otra vez lo duplica con sufijo, y `archivados` contaria dos veces
            #   el mismo comprobante.
            # - PENDIENTE / REQUIERE_REVISION: el gate lo paro. **ESTE ES EL QUE
            #   IMPORTA**: son los que alguien tiene que mirar, y un papel que
            #   necesita a una persona tiene que seguir en la bandeja, visible.
            #
            # Y el efectoPractico es el que hace util la bandeja: la de entrada
            # es la COLA DE TRABAJO. Un comprobante corregido a mano queda
            # `APROBADO`, y en la siguiente corrida si se mueve. Se vacia sola a
            # medida que se resuelve el trabajo, sin que nadie mueva archivos.
            #
            # Sin coste: el archivo que se queda ya esta en `scan_files` con su
            # `content_hash`, asi que la proxima corrida lo reporta `SIN_CAMBIOS`
            # sin volver a pagar OCR.
            if archivar and detalle.accion not in (
                "ERROR", "DUPLICADO", "NO_SOPORTADO", "VISTO"
            ) and detalle.ticket_id is not None:
                # Por `scan_file_id`, NO por `ticket_id`: un ticket puede tener
                # varios `scan_files` —el original y sus duplicados por
                # contenido— y buscar por `ticket_id` devuelve varios y revienta
                # con MultipleResultsFound. `procesar_archivo` ya sabe cual es la
                # fila de ESTE archivo, y eso es lo que hay que mover.
                fila = (
                    await db.execute(
                        select(ScanFileModel).where(
                            ScanFileModel.id == detalle.scan_file_id
                        )
                    )
                ).scalar_one_or_none()
                ticket = (
                    await db.execute(
                        select(TicketModel).where(TicketModel.id == detalle.ticket_id)
                    )
                ).scalar_one_or_none()

                if fila is not None and ticket is not None:
                    # El filtro de veredicto va AQUI y no en la condicion de
                    # arriba, porque el ticket se carga justo debajo: sin el no hay
                    # `extraction_status` que mirar. Y va aqui para que un archivo
                    # no resuelto conserve su `ruta_archivo` en None, que es como
                    # el cliente distingue "se movio" de "se queda en la bandeja".
                    #
                    # `if/else` Y NO `continue`: un `continue` aqui se comia el
                    # resto del bucle, y el resto del bucle es donde se hace
                    # `resumen.detalles.append(detalle)`. El archivo se quedaba
                    # correctamente en la bandeja pero DESAPARECIA de la respuesta:
                    # una corrida de 9 archivos reportaba `archivos_vistos: 3`.
                    #
                    # Medido, y hacia falta mirar el numero de la respuesta para
                    # notarlo: los 3 que quedaban eran justo los que si se
                    # movieron. Un filtro que esconde los casos que aplica parece
                    # un filtro que funciona.
                    if ExtractionStatus(ticket.extraction_status) not in SETTLED_STATUSES:
                        # No se toca, y se dice POR QUE: sin este motivo, un
                        # `ruta_archivo: null` no distingue "aun no toca" de "no
                        # se pudo leer", que son cosas distintas.
                        detalle.extraction_status = ticket.extraction_status
                        detalle.detalle = _motivo_de_bandeja(ticket.extraction_status)
                        resumen.quedan_en_bandeja += 1
                    else:
                        borrado, motivo = await _retirar_si_esta_respaldo(
                            db, fila, ticket, actor=actor, simular=simular
                        )
                        detalle.archivado = borrado
                        detalle.extraction_status = ticket.extraction_status
                        detalle.solo_simulado = borrado and simular
                        if motivo:
                            # El motivo va SIEMPRE, tambien cuando NO se borro.
                            # Un ticket resuelto que se queda en la bandeja sin
                            # explicación parece un bug del escaner, y el operador
                            # no tiene forma de saber que el sistema se nego a
                            # borrar porque no habia respaldo.
                            detalle.detalle = motivo
                        if borrado:
                            resumen.archivados += 1
                            resumen.borrados_de_entrada += 1

            resumen.detalles.append(detalle)

            if detalle.accion == "CREADO":
                resumen.nuevos += 1
            elif detalle.accion == "ACTUALIZADO":
                resumen.actualizados += 1
            elif detalle.accion == "SIN_CAMBIOS":
                resumen.sin_cambios += 1
            elif detalle.accion == "OMITIDO":
                if detalle.status is ScanStatus.DUPLICADO:
                    resumen.duplicados += 1
                elif detalle.status is ScanStatus.NO_SOPORTADO:
                    resumen.no_soportados += 1
            elif detalle.accion == "ERROR":
                resumen.con_error += 1

            if detalle.origen is not None:
                clave = detalle.origen.value
                resumen.lecturas_por_motor[clave] = (
                    resumen.lecturas_por_motor.get(clave, 0) + 1
                )

    await _resolver_datos_de_tickets(db, resumen.detalles)

    # El total va DESPUES de resolver los datos, porque necesita `datos` para
    # poder separar lo leido de lo confiable. Calcularlo antes daria un resumen
    # con todos los importes en cero, que es peor que no dar resumen.
    resumen.resumen = _calcular_resumen(
        resumen.detalles,
        quedan_en_bandeja=resumen.quedan_en_bandeja,
        archivados=resumen.archivados,
    )
    scan_registry.cerrar_corrida(corrida, resumen.resumen)

    return resumen


def _a_decimal(valor) -> Decimal | None:
    """Un importe de `tickets.total_amount` a `Decimal`, o `None`.

    Acepta `str` ademas de `Decimal` porque el valor viene de una fila de Postgres
    —que da `Decimal`— pero el mismo dato aparece serializado como texto en la
    respuesta de la API y en el JSON que consume el cliente. Un
    `isinstance(v, Decimal)` sin mas deja el total en `None` si algun dia el tipo
    cambia, y eso es un cero SILENCIOSO: el escaneo parece que no encontro
    ningun importe y no dice por que.

    `float` entra con `str()` y no con `float()` a proposito: el camino corto de
    `Decimal(0.1)` no es el problema, pero `str()` deja el numero exacto que se
    escribio, que es lo que importa en dinero.
    """
    if valor is None or isinstance(valor, bool):
        # `bool` antes que `int`: `True` es un `int` en Python y `Decimal("True")`
        # es un crash.
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _calcular_resumen(
    detalles: list[ResumenArchivo],
    *,
    quedan_en_bandeja: int = 0,
    archivados: int = 0,
) -> dict:
    """El total de la corrida: que salio, cuanto dinero y que hay que mirar.

    POR QUE HAY TRES IMPORTES Y NO UNO
    ----------------------------------
    Porque un solo total es un numero peligroso con OCR al 33%: invita a sumarlo y
    apuntarlo, y ese numero miente. Los tres separan lo que se LEYO de lo que se
    PUEDE CONFIRMAR de lo que hay que MIRAR:

    - `importe_total_leido` es lo que el sistema extrajo. Se muestra para que se
      vea completo, y el nombre dice "leido" para que nadie lo tome por el gasto.
    - `importe_total_confiable` cuenta SOLO lo que quedo `AUTO_APROBADO`, o sea
      lo que paso todos los checks sin que nadie lo tocara.
    - `importe_requiere_revision` es lo que NO se puede sumar sin mirar el papel.

    Y los `APROBADO` a proposito NO entran en el confiable: son los que corrigio
    una persona, y meterlos ahi haria que un dato humano pareciera automatico. Es
    la confusion que el reporte de exactitud no puede tolerar.

    EL DEDUPLICADO POR `ticket_id` NO ES OPCIONAL
    --------------------------------------------
    Un DUPLICADO no crea ticket: apunta al del otro archivo. Si la carpeta tiene
    `foto.jpg` y `foto-copia.jpg` con los mismos bytes, los dos detalles traen el
    MISMO `ticket_id` y `importe_leido` lo contaria dos veces. Con una factura de
    $10 000 eso son $20 000 de gasto que no existe.

    Es el mismo bug de otra forma que el indice unico de `scan_files` evita por
    abajo: aqui la fila es una sola, pero la respuesta la cuenta por archivo.

    `None` y no `0.00` cuando no hay ningun total: "no se leyo nada" y "se leyo
    cero" son cosas distintas, y la segunda es un ticket que hay que revisar.
    """
    leidos = [d for d in detalles if d.ticket_id is not None and d.datos]

    # Un ticket, una vez. Gana el primer detalle que lo menciona, que es el que
    # se leyo de verdad; los demas son duplicados que apuntan al mismo.
    por_ticket: dict[UUID, dict] = {}
    for detalle in leidos:
        if detalle.ticket_id not in por_ticket:
            por_ticket[detalle.ticket_id] = detalle.datos or {}

    total_leido = Decimal("0.00")
    total_confiable = Decimal("0.00")
    total_revision = Decimal("0.00")
    tickets_con_total = 0
    tickets_sin_total = 0

    por_estado: dict[str, int] = {}
    por_motor: dict[str, int] = {}
    con_rfc = 0
    con_lineas = 0

    for datos in por_ticket.values():
        estado = str(datos.get("extraction_status") or "")
        por_estado[estado] = por_estado.get(estado, 0) + 1

        motor = datos.get("confidence_source")
        if motor:
            por_motor[str(motor)] = por_motor.get(str(motor), 0) + 1
        if datos.get("provider_tax_id"):
            con_rfc += 1
        if datos.get("items"):
            con_lineas += 1

        monto = _a_decimal(datos.get("total_amount"))
        if monto is None or monto <= 0:
            tickets_sin_total += 1
            continue

        tickets_con_total += 1
        total_leido += monto
        if estado == ExtractionStatus.AUTO_APROBADO.value:
            total_confiable += monto
        elif estado in (
            ExtractionStatus.PENDIENTE.value,
            ExtractionStatus.REQUIERE_REVISION.value,
        ):
            total_revision += monto

    requiere_revision = (
        por_estado.get(ExtractionStatus.PENDIENTE.value, 0)
        + por_estado.get(ExtractionStatus.REQUIERE_REVISION.value, 0)
    )

    # Los contadores se derivan de `detalles` y no del `ResumenEscaneo` que
    # envuelve, y es a proposito: el reproceso de UN archivo construye el mismo
    # resumen sin tener una corrida, y si la funcion leyera los contadores de ahi
    # habria que inventar una corrida de mentira para poder totalizar. Ademas
    # "cuantos archivos dio error" tiene que salir de los mismos detalles que
    # "cuantos importes hay", o los dos numeros pueden discrepar.
    def _con(accion: str) -> int:
        return len([d for d in detalles if d.accion == accion])

    con_error = _con("ERROR")
    no_soportados = len([d for d in detalles if d.status is ScanStatus.NO_SOPORTADO])
    duplicados = len([d for d in detalles if d.status is ScanStatus.DUPLICADO])

    # Lo accionable es lo que ninguna persona hizo y no se va a resolver solo:
    # los que necesitan correccion, mas los que no se pudieron leer, mas los que
    # ni siquiera produjeron ticket.
    sin_ticket = len([d for d in detalles if d.ticket_id is None])
    # `quedan_en_bandeja` y `archivados` VIENEN del escaneo y no se recalculan.
    #
    # Se intento deducirlos de los detalles —"tiene ticket y no tiene ruta de
    # archivo"— y es incorrecto desde que el borrado sustituyo al movimiento: el
    # borrado no tiene ruta de destino, asi que `ruta_archivo` es None en TODOS
    # los casos y la cuenta daba 10 de 10 con 2 que si se iban. Medido.
    #
    # La cuenta correcta es la que lleva el escaneo, que es el unico sitio donde
    # se decide de verdad si un archivo se retiro o no.
    requiere_accion = requiere_revision + con_error + no_soportados + sin_ticket

    return {
        "archivos_vistos": len(detalles),
        "leidos": len([d for d in detalles if d.accion in ("CREADO", "ACTUALIZADO")]),
        "nuevos": _con("CREADO"),
        "actualizados": _con("ACTUALIZADO"),
        "sin_cambios": _con("SIN_CAMBIOS"),
        "duplicados": duplicados,
        "con_error": con_error,
        "no_soportados": no_soportados,
        "omitidos_por_tope": 0,
        "importe_total_leido": total_leido if tickets_con_total else None,
        "importe_total_confiable": total_confiable if tickets_con_total else None,
        "importe_requiere_revision": total_revision if tickets_con_total else None,
        "tickets_con_total": tickets_con_total,
        "tickets_sin_total": tickets_sin_total,
        "tickets_por_estado": por_estado,
        "tickets_por_motor": por_motor,
        "tickets_con_rfc": con_rfc,
        "tickets_con_lineas": con_lineas,
        "requiere_revision": requiere_revision,
        "requiere_accion": requiere_accion,
        "sin_ticket": sin_ticket,
        "quedan_en_bandeja": quedan_en_bandeja,
        # Los dos que van juntos y dicen lo mismo con palabras distintas:
        # `archivados` son los que se movieron, `borrados_de_entrada` los que
        # desaparecieron del disco. Hoy son el mismo numero porque el archivado
        # es un borrado, y se separan para que un dia el movimiento vuelva a
        # existir sin que el resumen tenga que cambiar de forma.
        "archivados": archivados,
        "borrados_de_entrada": archivados,
        # Rutas y no ids: el cliente puede ofrecer un boton que lleve a donde hay
        # que actuar, en vez de que cada pantalla arme su propia URL y se
        # desincronice de la API.
        "colas": {
            "revision": "/review-queue",
            "inventario": "/inventario",
            "escaneo": "/scan",
        },
    }


async def _retirar_si_esta_respaldo(
    db: AsyncSession,
    fila: ScanFileModel,
    ticket: TicketModel,
    *,
    actor: str | None = None,
    simular: bool = False,
) -> tuple[bool, str]:
    """Retira el archivo de la carpeta de entrada, pero SOLO si está respaldado.

    QUE ES ESTO Y POR QUE NO ES MOVER
    ===============================
    Antes el archivo se MOVIA a `Tickets_Scan`. Se pedia lo contrario: la carpeta
    de entrada debe quedar con **solo lo que no se digitalizo bien**, para
    reintentarlo, y lo que si se digitalizo desaparece de ahi porque ya no se
    vuelve a usar.

    La diferencia entre mover y borrar no es de estilo: es que **mover deja el
    archivo en el disco y borrar no**. Por eso el borrado necesita una prueba
    antes, y la prueba es que el comprobante este en `ticket_documents` con el
    MISMO `sha256` que el archivo que se va a borrar. Es lo unico que separa
    "ya tengo una copia fiel" de "estaba Nombre y ya no".

    SI NO HAY RESPALDO, NO SE BORRA Y SE DICE POR QUE
    -------------------------------------------------
    El caso real es `guardar_documento` devolviendo `False`: el ticket se creo
    pero su comprobante no se pudo guardar. Ahi el archivo del disco es el unico
    comprobante que existe, y borrarlo deja el ticket sin papel para siempre — y
    sin papel no hay muestreo de exactitud posible.

    `simular` calcula y devuelve lo que PASSARIA sin tocar el disco. Es lo que
    permite ver el efecto de una corrida antes de que ocurra, y con un borrado
    irreversible no es opcional.
    """
    from app.services import archivado_service

    if not fila.relative_path:
        return False, None

    respaldado, motivo = await archivado_service.esta_respaldado(
        db, ticket, fila.relative_path
    )

    if not respaldado:
        # No es un fallo del escaneo: es el sistema negandose a destruir la
        # unica copia. Se registra como evento para que la decision sea
        # auditable, y el archivo se queda donde esta.
        logger.info(
            "Ticket %s: %s se conserva en la carpeta de entrada (%s)",
            ticket.id, fila.relative_path, motivo,
        )
        return False, motivo

    if simular:
        return True, f"se borraria de la entrada: {motivo}"

    resultado = archivado_service.eliminar_de_la_entrada(fila.relative_path)
    if not resultado.movido:
        return False, resultado.motivo

    await _registrar(db, fila, "BORRADO", motivo, actor)
    return True, motivo


def _motivo_de_bandeja(extraction_status: str) -> str:
    """Por que este comprobante se queda en la bandeja en vez de archivarse.

    Va en el `detalle` del item y no solo en un contador porque la pregunta que
    responde es "este papel mio, por que sigue ahi?", y sin el motivo hay que
    abrir la cola de revision para Averiguarlo. Es el mismo motivo que ya lleva
    `archivados_pendientes`, pero al reves: ese decia cuantos se movieron sin
    leerse bien, y ahora lo relevante es cuantos NO se movieron porque falta
    que alguien los mire.

    El texto NO dice "error", y esa es la distincion que importa: un
    `PENDIENTE` no es un fallo del sistema, es una lectura que el gate no se
    atrevio a dar por buena. Decirle "error" al usuario seria pedirle que vaya a
    buscar un bug donde lo que hay es un papel que necesita un ojo humano.
    """
    if extraction_status == ExtractionStatus.PENDIENTE.value:
        return (
            "queda en la bandeja: el sistema leyo el comprobante pero el gate "
            "no lo dio por bueno. Corrigelo en la cola de revision y en la "
            "siguiente corrida se archiva solo."
        )
    if extraction_status == ExtractionStatus.REQUIERE_REVISION.value:
        return (
            "queda en la bandeja: la lectura tiene datos que no cuadran entre si. "
            "El motivo esta en el campo de errores de validacion del ticket. "
            "Corrigelo en la cola de revision y en la siguiente corrida se "
            "archiva solo."
        )
    if extraction_status == ExtractionStatus.RECHAZADO.value:
        return (
            "queda en la bandeja: el comprobante fue descartado por una persona "
            "y se conserva aqui para que la decision sea revisable."
        )
    return f"queda en la bandeja: el veredicto es {extraction_status}, no uno resuelto."


async def _resolver_datos_de_tickets(
    db: AsyncSession, detalles: list[ResumenArchivo]
) -> None:
    """Rellena `detalle.datos` con los datos del ticket de cada archivo.

    Recibe la LISTA y no el `ResumenEscaneo` porque lo usan dos caminos con
    alcances distintos: la corrida entera y el reproceso de un archivo suelto.
    Los dos devuelven la misma `ScanItemResponse`, y por eso los dos tienen que
    traer los mismos datos.

    Va despues del bucle y no dentro por lo que dice el comentario de
    `ResumenArchivo.datos`: hay una sola consulta para toda la corrida en vez de
    una por archivo, y `procesar_archivo` —que tiene trece salidas— no tiene que
    saber que esto existe.

    Se leen los DATOS y el VEREDICTO de la misma fila, y no lo que devolvio el
    lector, por una razon que es la que hace trustworthy la respuesta: si el
    gate mando la lectura a revision, aqui `extraction_status` lo dice al lado de
    los numeros. Un JSON con los datos pero sin el veredicto deja a quien lo
    consume creyendo que el sistema afirmo esos numeros, que es justo lo que el
    gate se nego a afirmar.

    Los archivos sin ticket —ERROR, NO_SOPORTADO, sin `company_id`— se quedan
    con `datos=None`, y eso es informacion, no un hueco.
    """
    pendientes = [d for d in detalles if d.ticket_id is not None]
    if not pendientes:
        return

    tickets = (
        await db.execute(
            select(TicketModel).where(TicketModel.id.in_({d.ticket_id for d in pendientes}))
        )
    ).scalars().all()
    por_id = {t.id: t for t in tickets}

    for detalle in pendientes:
        ticket = por_id.get(detalle.ticket_id)
        if ticket is None:
            # El ticket se borro entre el `procesar_archivo` y aqui. Se deja el
            # `None` en vez de inventar un dato: `ticket_id` sigue en la
            # respuesta y quien reintente lo vera como un 404 honesto.
            continue
        detalle.datos = {
            "provider_name": ticket.provider_name,
            "provider_tax_id": ticket.provider_tax_id,
            "total_amount": ticket.total_amount,
            "subtotal": ticket.subtotal,
            "tax_amount": ticket.tax_amount,
            "expense_date": ticket.expense_date,
            "category": ticket.category,
            "items": ticket.items,
            "confidence": ticket.confidence,
            "confidence_source": ticket.confidence_source,
            "extraction_status": ticket.extraction_status,
            "validation_errors": ticket.validation_errors,
            "reviewed_by": ticket.reviewed_by,
            "reviewed_at": ticket.reviewed_at,
        }


async def archivar_tras_lectura(
    db: AsyncSession,
    fila: ScanFileModel,
    ticket: TicketModel | None,
    *,
    actor: str | None = None,
    simular: bool = False,
) -> tuple[bool, str | None, bool]:
    """Mueve el comprobante a la carpeta de escaneados, ya digitalizado.

    Devuelve `(movido, ruta_destino, estaba_pendiente)`. Los tres van juntos
    porque el tercero es la advertencia: un archivo puede haberse movido SIN que
    el sistema lo leyera bien, y quien mira el resultado necesita verlo, no
    leerlo en un log.

    `simular=True` calcula el destino y lo devuelve SIN mover. Es lo que permite
    ver el efecto antes de que ocurra: con el archivado activo por omision, la
    primera corrida deja la carpeta de entrada vacia, y eso conviene verlo antes.

    QUE SE MUEVE Y QUE NO
    =====================

    Solo lo que produjo un ticket: `accion` CREADO o ACTUALIZADO. Un archivo
    ilegible, duplicado o no soportado se queda en la carpeta de entrada, porque
    no hay nada que archivar — y un archivo que el sistema no pudo leer es
    precisamente el que alguien tiene que mirar.

    LO QUE NO SE PIDE AQUI, Y ES A PROPOSITO
    ==========================================

    Que la lectura fuera *buena*. El OCR sobre fotos reales mide 33.3%
    (`AGENTS.md`; los cuatro caminos para mejorarlo estan descartados con
    medicion en `docs/known-issues.md` 21), asi que exigir un veredicto favorable
    equivaldria a no mover casi nada: en la practica, casi todos los comprobantes
    de una foto salen PENDIENTE.

    Se mueve igual, y por eso la respuesta trae `archivados_pendientes`: la
    carpeta de escaneados **no** significa "esto se leyo bien", significa "esto
    el sistema ya lo intento y dejo registrado su veredicto". El veredicto esta
    en `tickets.extraction_status` y en `scan_events`, que es donde se audita.

    Un archivo movido se puede devolver —esta en otra carpeta, no se borro— y
    `POST /scan/files/{id}/reprocess` lo vuelve a leer sin problema.
    """
    if ticket is None:
        return False, None, False

    # El estado del gate va en el evento, no solo en el log: es lo que responde
    # "movi este papel aunque el sistema no lo entendiera?".
    detalle_extra = ""
    if ticket.extraction_status in (ExtractionStatus.PENDIENTE.value,
                                   ExtractionStatus.REQUIERE_REVISION.value):
        detalle_extra = (
            f" (veredicto: {ticket.extraction_status}"
            f"{'; ' + ticket.validation_errors if ticket.validation_errors else ''})"
        )

    if simular:
        # Se calcula el destino sin tocar el disco. `carpeta_de_escaneados()`
        # crea la carpeta si no existe, y eso si es un efecto: se acepta, porque
        # una carpeta vacia no es un cambio de estado del sistema.
        destino = carpeta_de_escaneados() / fila.relative_path
        logger.info(
            "[SIMULAR] %s se moveria a %s%s", fila.relative_path, destino, detalle_extra
        )
        return (
            True,
            str(destino),
            ticket.extraction_status in (ExtractionStatus.PENDIENTE.value,
                                         ExtractionStatus.REQUIERE_REVISION.value),
        )

    try:
        resultado = archivar(ruta_de_relativo(fila.relative_path), fila.relative_path)
    except (ErrorDeArchivado, ArchivoFueraDeLaCarpeta) as exc:
        logger.warning("no se pudo archivar %s: %s", fila.relative_path, exc)
        await _registrar(db, fila, "ERROR", f"no se pudo archivar: {exc}", actor)
        return False, None, False

    if not resultado.movido:
        # No es un evento: es idempotencia. No se ensucia la linea de tiempo con
        # un BORRADO de algo que no se movio.
        return False, None, False

    await _registrar(
        db,
        fila,
        "BORRADO",
        f"archivado en {resultado.ruta_destino} tras digitalizar{detalle_extra}",
        actor,
    )
    return (
        True,
        str(resultado.ruta_destino),
        ticket.extraction_status in (ExtractionStatus.PENDIENTE.value,
                                     ExtractionStatus.REQUIERE_REVISION.value),
    )


async def archivar_si_ya_resuelto(
    db: AsyncSession,
    fila: ScanFileModel,
    *,
    actor: str | None = None,
) -> bool:
    """Mueve el comprobante a la carpeta de escaneados, si ya se resolvio.

    LA CONDICION ES `PROCESADO`, Y NO ES COSA QUE SE PUEDA SIMPLIFICAR
    =============================================================

    El archivo se mueve cuando su COMPRA esta PROCESADO, o sea cuando una
    persona confirmo que las lineas entraron al inventario. No cuando el ticket
    salio AUTO_APROBADO, ni cuando el archivo se leyo bien.

    La razon esta medida, no es prudencia generica. `AGENTS.md` mide la exactitud
    del OCR sobre fotos reales en **33.3%**, y los cuatro caminos para mejorarla
    estan descartados con medicion en `docs/known-issues.md` 21. Ademas:

    - `AUTO_APROBADO` lo decide el gate sobre los campos del ENCABEZADO. Nunca ha
      medido la confianza de una linea de producto, asi que no dice nada sobre si
      las lineas son las correctas.
    - La conciliacion bancaria valida el MONTO, nunca la COMPOSICION: un ticket
      puede dar PERFECT contra el banco y tener las lineas equivocadas.

    Asi que mover por veredicto de lectura habria enterrado dos de cada tres
    comprobantes en una carpeta que dice "escaneados", sin que nadie los hubiera
    revisado. `EN_REVISION` se queda donde esta: es exactamente el papel que
    alguien tiene que mirar.

    Idempotente: si el archivo ya no esta, no hace nada y devuelve False. Es el
    caso normal de la segunda corrida.

    Nunca tumba el escaneo: si el movimiento falla, se registra y se sigue. Un
    archivo que no se pudo mover es un annoyance; un escaneo que se detiene por
    eso deja de registrar gastos.
    """
    if not settings.TICKETS_SCAN_ARCHIVAR:
        return False
    if not settings.TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR:
        # Con el archivado al escanear activo, este camino casi nunca dispara:
        # el archivo ya se movio. Y con los dos apagados, no se mueve nada.
        return False

    ticket_id = fila.ticket_id
    if ticket_id is None:
        # Se leyo pero no produjo ticket. Sin compra no hay nada resuelto.
        return False

    compra = (
        await db.execute(
            select(CompraModel).where(CompraModel.ticket_id == ticket_id)
        )
    ).scalar_one_or_none()
    if compra is None or compra.estado != EstadoCompra.PROCESADO:
        return False

    try:
        resultado = archivar(
            ruta_de_relativo(fila.relative_path), fila.relative_path
        )
    except (ErrorDeArchivado, ArchivoFueraDeLaCarpeta) as exc:
        # Se anota y se sigue. Ver la docstring.
        logger.warning("no se pudo archivar %s: %s", fila.relative_path, exc)
        await _registrar(db, fila, "ERROR", f"no se pudo archivar: {exc}", actor)
        return False

    if not resultado.movido:
        # No es un evento: es idempotencia. No se ensucia la linea de tiempo con
        # un BORRADO de algo que no se movio.
        return False

    await _registrar(
        db,
        fila,
        # `BORRADO` y no `MOVIDO`: la accion esta en la constraint cerrada
        # `ck_scan_events_action`, y anadir un valor al enum del DDL sin migrar
        # deja el INSERT rechazado en produccion. Ademas el nombre ya era
        # exacto: de la carpeta de entrada, el archivo salio.
        "BORRADO",
        f"archivado en {resultado.ruta_destino}",
        actor,
    )
    return True


async def reprocesar_uno(
    db: AsyncSession,
    fila: ScanFileModel,
    company_id: UUID | None,
    actor: str,
) -> ResumenArchivo:
    """Relee un archivo puntual, exista o no su archivo en disco.

    Se usa desde `POST /scan/files/{id}/reprocess`, que es donde el operador
    dice "este archivo quiero releerlo". Si el archivo ya no esta en el disco se
    dice, en vez de reportar un exito: reprocesar un registro cuyo archivo
    desaparecio (alguien lo movio a otra carpeta) tiene que ser visible.
    """
    # Si el archivo ya fue atribuido a una empresa, se usa esa. Reprocesar un
    # archivo que ya tiene ticket NO tiene por qué pedir la empresa otra vez: si
    # el endpoint no la pasara, `procesar_archivo` caeria en la rama de "sin
    # empresa", devolveria OMITIDO y no relectura nada. El endpoint de reproceso
    # se volveria un no-op salvo que el cliente supiera de que habia que mandar
    # el `company_id` que ya esta en la fila.
    if company_id is None:
        company_id = fila.company_id

    ruta = ruta_de_relativo(fila.relative_path)

    if not ruta.is_file():
        detalle = f"el archivo ya no esta en la carpeta: {ruta}"
        await db.commit()
        return ResumenArchivo(
            relative_path=fila.relative_path,
            scan_file_id=fila.id,
            status=ScanStatus.ERROR,
            accion="ERROR",
            detalle=detalle,
        )

    info = ruta.stat()
    vista = ArchivoVista(
        ruta=ruta,
        relative_path=fila.relative_path,
        tamano=info.st_size,
        mtime=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc),
        extension=ruta.suffix.lower(),
    )
    resumen = await procesar_archivo(
        db, vista, ruta.read_bytes(), company_id, actor, forzar=True
    )
    # El reproceso devuelve la misma `ScanItemResponse` que el escaneo, asi que
    # tiene que traer los mismos datos. Sin esto, la relectura de un archivo
    # responderia sin `datos` mientras la corrida completa si los trae, y la
    # diferencia pareceria un bug del gate cuando es que este camino nunca los
    # resolvio.
    await _resolver_datos_de_tickets(db, [resumen])
    # `ResumenArchivo` NO tiene los contadores de la corrida: son de
    # `ResumenEscaneo`, y el reproceso de un archivo suelto no construye una.
    # Se pasan en cero a proposito —no hubo corrida, no hay nada que archivara—
    # en vez de inventar una `ResumenEscaneo` de mentira para poder contarlo.
    resumen.resumen = _calcular_resumen([resumen])
    return resumen
