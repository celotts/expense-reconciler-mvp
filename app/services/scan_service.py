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
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import detectar_tipo_real
from app.core.config import settings
from app.core.enums import ConfidenceSource, ScanStatus, SourceType
from app.core.time import utcnow
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.services.ai_extractor import ai_extractor
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
    # da True para `/Users/carlos/Documents/Tickets_app_secreto` cuando la base
    # es `/Users/carlos/Documents/Tickets_app`, porque una ruta es prefijo
    # textual de otra. Ese es el error clasico de un `startswith` aplicado a
    # rutas, y aqui abriria la carpeta hermana.
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
        confidence=extracted.confidence,
        source=extracted.confidence_source,
    )

    ticket.provider_name = extracted.provider_name
    ticket.provider_tax_id = extracted.provider_tax_id
    ticket.total_amount = extracted.total_amount
    ticket.tax_amount = extracted.tax_amount
    ticket.subtotal = extracted.subtotal
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
) -> ResumenEscaneo:
    """Recorre la carpeta y procesa lo que corresponda.

    Args:
        company_id: a que empresa atribuir los tickets. `None` inventaria sin
            crear tickets.
        reprocesar: releer tambien los archivos cuyo contenido cambio. Apagado
            por omision porque reescribir un ticket es una decision, no un
            efecto secundario de correr un escaneo.
        solo_pendientes: no tocar lo ya `PROCESADO` aunque haya cambiado.
    """
    resumen = ResumenEscaneo(carpeta=str(raiz()))

    async with _CANDADO_DE_ESCANEO:
        archivos = listar_archivos()
        resumen.archivos_vistos = len(archivos)

        for indice, vista in enumerate(archivos):
            if indice >= settings.TICKETS_SCAN_MAX_ARCHIVOS:
                resumen.omitidos_por_tope = len(archivos) - indice
                logger.info(
                    "tope por corrida alcanzado (%d); %d archivos quedan para la "
                    "siguiente", settings.TICKETS_SCAN_MAX_ARCHIVOS,
                    resumen.omitidos_por_tope,
                )
                break

            try:
                contenido = vista.ruta.read_bytes()
            except OSError as exc:
                logger.warning("no se pudo leer %s: %s", vista.relative_path, exc)
                resumen.con_error += 1
                continue

            if solo_pendientes:
                fila = await _buscar(db, vista.relative_path)
                if fila is not None and ScanStatus(fila.status) == ScanStatus.PROCESADO:
                    continue

            detalle = await procesar_archivo(
                db, vista, contenido, company_id, actor, forzar=reprocesar
            )
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

    return resumen


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
    return await procesar_archivo(
        db, vista, ruta.read_bytes(), company_id, actor, forzar=True
    )
