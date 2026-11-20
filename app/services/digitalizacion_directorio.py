"""Digitalizar un DIRECTORIO elegido por el operador.

Por que es un servicio aparte
-----------------------------
`scan_service.escanear` recorre SIEMPRE `TICKETS_INPUT_DIR` y guarda
`relative_path` relativo a esa carpeta. Meter una ruta arbitraria ahi romperia
el ledger: dos carpetas distintas con `factura.pdf` colisionarian en
`scan_files.relative_path`, y el segundo archivo saldria "sin cambios" aunque
sus bytes fueran otros.

Esta puerta trata los archivos de un directorio como trata el router de carga
por lote a los que sube el navegador: la clave de dedup es el SHA-256 de los
bytes (`<sha>.<extension>`), no la ruta. Por eso el mismo comprobante en dos
carpetas cuenta una sola vez, y moverlo no lo vuelve a leer.

La ruta NO es arbitraria
------------------------
`resolver_carpeta` solo acepta la configurada por omision o las que el servidor
puso en `TICKETS_SCAN_RUTAS_PERMITIDAS`, y comprueba `is_relative_to` DESPUES
de `resolve()`. Un `..` o un symlink a `/etc` no cuela. El campo de texto de la
pantalla elige DENTRO de lo que el servidor decidio exponer; no abre el disco.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import detectar_tipo_real
from app.core.config import settings
from app.core.enums import SourceType
from app.services import scan_service
from app.services.periodo_contable_service import OverridePeriodoCerrado
from app.services.scan_service import (
    ArchivoVista,
    ResumenArchivo,
    procesar_archivo,
    resolver_datos_de_tickets,
)

logger = logging.getLogger(__name__)

# Extensiones que el sistema SABE leer. No se decidio con el nombre: el lector
# sigue eligiendo escalon por los bytes (`archivo_real.detectar_tipo_real`).
# Esta lista solo contesta "¿este archivo entra en la cola de digitalizacion?",
# que es una pregunta distinta y previa. Incluye TIFF y BMP porque sus firmas
# estan en `_FIRMAS_IMAGEN`; un `.xml` entra porque el CFDI se lee por firma.
EXTENSIONES_PERMITIDAS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg", ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".tif", ".tiff",
        ".heic", ".heif",
        ".psd",
        ".xml",
    }
)

Validador = Callable[..., Awaitable[OverridePeriodoCerrado | None]]


class CarpetaNoPermitida(ValueError):
    """La ruta escrita no existe o no esta dentro de la lista blanca."""


def raices_permitidas() -> list[Path]:
    """La raiz configurada mas las extra, todas resueltas y sin duplicados."""
    raices = [Path(settings.TICKETS_INPUT_DIR).expanduser().resolve()]
    for valor in settings.TICKETS_SCAN_RUTAS_PERMITIDAS.split(os.pathsep):
        if valor.strip():
            raices.append(Path(valor).expanduser().resolve())
    vistas: set[Path] = set()
    unicas: list[Path] = []
    for raiz in raices:
        if raiz not in vistas:
            vistas.add(raiz)
            unicas.append(raiz)
    return unicas


def resolver_carpeta(carpeta: str | None) -> Path:
    """La carpeta pedida, o la de configuracion cuando no se escribio nada.

    La comparacion es por `is_relative_to` sobre rutas YA resueltas: `str`
    empieza con daria por buena una carpeta hermana con el mismo prefijo, y un
    symlink dentro de la raiz podria apuntar fuera sin que el texto lo diga.
    """
    raices = raices_permitidas()
    if not carpeta or not carpeta.strip():
        return raices[0]

    candidata = Path(carpeta).expanduser().resolve()
    if not any(
        candidata == raiz or candidata.is_relative_to(raiz) for raiz in raices
    ):
        permitidas = ", ".join(str(raiz) for raiz in raices)
        raise CarpetaNoPermitida(
            f"La ruta '{carpeta}' no esta permitida. Solo se pueden leer "
            f"carpetas dentro de: {permitidas}."
        )
    if not candidata.is_dir():
        raise CarpetaNoPermitida(
            f"La ruta '{carpeta}' existe dentro de las permitidas pero no es "
            "una carpeta."
        )
    return candidata


def extension_de(nombre: str) -> str:
    return Path(nombre).suffix.lower()


def listar_directorio(carpeta: Path) -> tuple[list[Path], list[dict]]:
    """`(archivos a leer, rechazados con nombre y motivo)`.

    Los rechazados se enumeran TODOS, aunque haya tope de lectura: el punto de
    esta respuesta es decir cuales archivos no se miraron, y callar los que
    quedaron despues del tope seria el mismo silencio que se quiere evitar.
    Los que si entran se devuelven en orden estable para que la respuesta sea
    reproducible y el front pueda emparejar por indice.
    """
    if not carpeta.is_dir():
        return [], []

    admitidos: list[Path] = []
    rechazados: list[dict] = []

    def recorrer(base: Path) -> None:
        for ruta in sorted(base.iterdir(), key=lambda p: p.name):
            # Un symlink, aunque apunte a un archivo permitido, se salta: es la
            # forma mas corta de leer fuera de la carpeta autorizada.
            if ruta.is_symlink():
                rechazados.append(
                    {
                        "nombre": ruta.name,
                        "extension": extension_de(ruta.name) or "(sin extension)",
                        "motivo": "es un enlace simbolico",
                    }
                )
                continue
            if ruta.is_dir():
                if settings.TICKETS_SCAN_RECURSIVO:
                    recorrer(ruta)
                continue
            if not ruta.is_file():
                continue
            if scan_service._es_ruido(ruta.name):
                continue

            extension = extension_de(ruta.name)
            if extension not in EXTENSIONES_PERMITIDAS:
                rechazados.append(
                    {
                        "nombre": ruta.name,
                        "extension": extension or "(sin extension)",
                        "motivo": (
                            "extension no permitida; solo se digitalizan "
                            + ", ".join(sorted(EXTENSIONES_PERMITIDAS))
                        ),
                    }
                )
                continue
            admitidos.append(ruta)

    recorrer(carpeta)
    return admitidos, rechazados


def vista_de(ruta: Path, contenido: bytes) -> ArchivoVista:
    """La vista que consume `procesar_archivo`.

    `relative_path` es el SHA-256 y no el nombre, igual que en la carga por
    lote. El nombre no entra en la clave porque dos personas pueden subir el
    mismo comprobante con distinto nombre y porque la misma ruta relativa en
    otra carpeta no puede hacer que el archivo nuevo parezca "sin cambios".
    """
    sha256 = hashlib.sha256(contenido).hexdigest()
    extension = extension_de(ruta.name).lstrip(".") or "bin"
    info = ruta.stat()
    return ArchivoVista(
        ruta=ruta,
        relative_path=f"{sha256}.{extension}",
        tamano=len(contenido),
        mtime=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc),
        extension=extension,
    )


async def digitalizar_directorio(
    db: AsyncSession,
    carpeta: Path,
    company_id,
    actor: str,
    validar_antes_de_guardar: Validador,
) -> tuple[list[ResumenArchivo], list[str], list[str], list[dict], int]:
    """Lee los archivos permitidos y devuelve resumenes, nombres, rechazados y tope.

    La fecha de cada archivo se valida con el MISMO callback que usa la carga
    por lote: el periodo cerrado se decide dentro de `procesar_archivo`,
    despues de leer la fecha del papel y antes de escribir el ticket.
    """
    admitidos, rechazados = listar_directorio(carpeta)
    tope = settings.TICKETS_SCAN_MAX_ARCHIVOS
    omitidos = max(0, len(admitidos) - tope)
    resumenes: list[ResumenArchivo] = []
    nombres: list[str] = []
    formatos: list[str] = []

    for ruta in admitidos[:tope]:
        try:
            contenido = ruta.read_bytes()
        except OSError as exc:
            logger.warning("no se pudo leer %s: %s", ruta, exc)
            rechazados.append(
                {
                    "nombre": str(ruta.relative_to(carpeta)),
                    "extension": extension_de(ruta.name) or "(sin extension)",
                    "motivo": f"no se pudo leer: {exc}",
                }
            )
            continue
        if len(contenido) > settings.TICKETS_SCAN_MAX_BYTES:
            rechazados.append(
                {
                    "nombre": str(ruta.relative_to(carpeta)),
                    "extension": extension_de(ruta.name) or "(sin extension)",
                    "motivo": (
                        f"tiene {len(contenido)} bytes, mas del tope de "
                        f"{settings.TICKETS_SCAN_MAX_BYTES}"
                    ),
                }
            )
            continue

        nombres.append(str(ruta.relative_to(carpeta)))
        formatos.append(detectar_tipo_real(contenido) or "desconocido")
        resumenes.append(
            await procesar_archivo(
                db,
                vista_de(ruta, contenido),
                contenido,
                company_id,
                actor,
                source_type=SourceType.BULK,
                validar_antes_de_guardar=validar_antes_de_guardar,
            )
        )

    await resolver_datos_de_tickets(db, resumenes)
    return resumenes, nombres, formatos, rechazados, omitidos
