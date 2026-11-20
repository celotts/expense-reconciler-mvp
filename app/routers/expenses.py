"""Carga explicita de comprobantes por lote.

Este router existia pero no estaba montado, no tenia tests y hacia una
segunda canalización de lectura:OpenCV + Ollama directo + Pydantic propio,
con dos TODO (dedup y periodo cerrado) que nunca se implementaron. El
resultado era un archivo que parecia una feature y no podia cumplir nada de
lo que prometia.

Ahora es un adaptador finito sobre `app.services.scan_service.procesar_archivo`,
que es el UNICO camino de captura del proyecto. No hay segundo OCR, segundo
modelo ni segunda tabla de dedup:

* **Dedup** lo hace `scan_files` (por `relative_path` derivado del SHA-256) y
  `_ticket_de_otro_archivo` (por el hash de los bytes), dentro de
  `procesar_archivo`. Es el mismo mecanismo que ya defendio el escaner de
  carpeta con sus mutaciones.
* **Periodo cerrado** lo decide `periodo_contable_service`, inyectado al
  pipeline como `validar_antes_de_guardar`. Se corre despues de leer la
  fecha del papel y antes de escribir el ticket, que es el unico momento en
  que el rechazo no deja un gasto contabilizado en un mes ya cerrado.
* **La respuesta** es `ScanItemResponse`, el mismo schema que devuelve
  `POST /scan`. Los datos del papel viajan JUNTOS con el veredicto del gate;
  devolver un `ExpenseReceiptSchema` aparte dejaria a quien lo lee creyendo
  que el sistema respondio por unos numeros que el gate mando a revision.

`ExpenseReceiptSchema` sigue vivo en `app/schemas/expense.py` como contrato
estrecho de la lectura de IA, pero NO es la respuesta de esta API.
"""

import hashlib
import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.scans import de_resumen
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import SourceType
from app.core.time import utcnow
from app.models.company import CompanyModel
from app.schemas.expense import DigitalizacionDirectorioResponse
from app.schemas.scan import ScanItemResponse
from app.services import digitalizacion_directorio as directorio
from app.services.digitalizacion_directorio import CarpetaNoPermitida
from app.services.parser_service import TicketExtractionResult
from app.services.periodo_contable_service import (
    OverridePeriodoCerrado,
    asegurar_periodo_abierto_o_override,
)
from app.services.scan_service import (
    ArchivoVista,
    ResumenArchivo,
    procesar_archivo,
    resolver_datos_de_tickets,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Expenses"])


async def _empresa_o_404(db: AsyncSession, company_id: UUID) -> None:
    """Un `company_id` inexistente es 404, no un lote de tickets huerfanos."""
    empresa = (
        await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    ).scalar_one_or_none()
    if empresa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe la empresa indicada",
        )


def _validador_de_periodo(
    db: AsyncSession,
    company_id: UUID,
    actor: str,
    override_solicitado: bool,
    motivo: str | None,
):
    """El mismo chequeo para la carga de archivos y la de directorio.

    Si cada puerta construyera su propio callback, una se podria olvidar del
    override o firmar con otro nombre y el cierre de periodo defenderia en una
    y no en la otra. Quien firma sale del token, nunca del formulario.
    """

    async def validar_periodo(
        extracted: TicketExtractionResult,
    ) -> OverridePeriodoCerrado | None:
        return await asegurar_periodo_abierto_o_override(
            db,
            company_id=company_id,
            fecha=extracted.expense_date,
            override_solicitado=override_solicitado,
            motivo=motivo,
            autorizado_por=actor,
        )

    return validar_periodo


@router.post("/upload-batch", response_model=list[ScanItemResponse])
async def upload_batch_expenses(
    usuario: UsuarioActual,
    files: list[UploadFile] = File(...),
    company_id: UUID = Form(...),
    periodo_cerrado_override: bool = Form(False),
    periodo_cerrado_motivo: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> list[ScanItemResponse]:
    """Digitaliza un lote de archivos y devuelve el resultado de cada uno.

    La empresa se comprueba aqui, igual que en `POST /tickets/extract-and-create`:
    un `company_id` inexistente es un 404, no un lote de tickets huerfanos.
    Quien firma el override sale del token, nunca del cuerpo (regla 23 de
    `AGENTS.md`), por eso `autorizado_por` es `usuario.email` y no un campo
    del formulario.

    Los archivos se procesan EN SECUENCIA y con una sola sesion. El router
    anterior usaba `asyncio.gather` con un semáforo de 3 sobre una sesion
    compartida: `AsyncSession` no es concurrency-safe, y un `commit` de un
    archivo podia intercalarse con el `flush` de otro. La concurrencia se
    consigue con mas workers, no compartiendo la sesion.
    """
    await _empresa_o_404(db, company_id)

    actor = usuario.email or "sistema"
    validar_periodo = _validador_de_periodo(
        db, company_id, actor, periodo_cerrado_override, periodo_cerrado_motivo
    )

    resumenes: list[ResumenArchivo] = []
    for file in files:
        contenido = await file.read()
        nombre = Path(file.filename or "archivo_sin_nombre").name
        sha256 = hashlib.sha256(contenido).hexdigest()
        extension = Path(nombre).suffix.lstrip(".").lower() or "bin"

        # `relative_path` se deriva de los BYTES, no del nombre que mando el
        # cliente. Dos personas pueden subir el mismo comprobante con distinto
        # nombre; si la clave fuera el nombre habria dos filas en `scan_files`
        # para el mismo papel y el dedup por `relative_path` no defenderia.
        # El nombre se conserva solo como extension para que la clave siga
        # diciendo que tipo de archivo era.
        vista = ArchivoVista(
            ruta=Path(nombre),
            relative_path=f"{sha256}.{extension}",
            tamano=len(contenido),
            mtime=utcnow(),
            extension=extension,
        )
        resumenes.append(
            await procesar_archivo(
                db,
                vista,
                contenido,
                company_id,
                actor,
                source_type=SourceType.BULK,
                validar_antes_de_guardar=validar_periodo,
            )
        )

    # Igual que en la corrida de escaneo: una sola consulta para todo el lote,
    # DESPUES del bucle, y los datos salen de la fila del ticket junto a su
    # veredicto. No se calcula nada aqui.
    await resolver_datos_de_tickets(db, resumenes)
    return [de_resumen(resumen) for resumen in resumenes]


@router.post("/digitalizar-directorio", response_model=DigitalizacionDirectorioResponse)
async def digitalizar_directorio_expenses(
    usuario: UsuarioActual,
    company_id: UUID = Form(...),
    carpeta: str | None = Form(None),
    periodo_cerrado_override: bool = Form(False),
    periodo_cerrado_motivo: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> DigitalizacionDirectorioResponse:
    """Lee un directorio y digitaliza solo lo que tiene extension permitida.

    `carpeta` VACIA usa la configurada (`TICKETS_INPUT_DIR`), que es el valor
    normal y el que ya usaba el escaner. Si se escribe otra, el servicio la
    acepta solo si cae dentro de las raices de
    `TICKETS_SCAN_RUTAS_PERMITIDAS`; fuera de ahi responde 400 y no toca el
    disco. Es la frontera que evita que un campo de texto se convierta en
    lectura arbitraria del servidor.

    Los archivos rechazados van en `archivos_no_permitidos` con su NOMBRE, no
    dentro de `archivos`: asi el cliente puede mostrar "estos no se leyeron"
    sin mezclarlos con los que si se leyeron y su veredicto.
    """
    await _empresa_o_404(db, company_id)
    actor = usuario.email or "sistema"

    try:
        ruta = directorio.resolver_carpeta(carpeta)
    except CarpetaNoPermitida as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    resumenes, nombres, formatos, rechazados, omitidos = await directorio.digitalizar_directorio(
        db,
        ruta,
        company_id,
        actor,
        _validador_de_periodo(
            db, company_id, actor, periodo_cerrado_override, periodo_cerrado_motivo
        ),
    )

    return DigitalizacionDirectorioResponse(
        carpeta=str(ruta),
        extensiones_permitidas=sorted(directorio.EXTENSIONES_PERMITIDAS),
        archivos=[de_resumen(resumen) for resumen in resumenes],
        nombres=nombres,
        formatos=formatos,
        archivos_no_permitidos=rechazados,
        archivos_omitidos_por_tope=omitidos,
    )
