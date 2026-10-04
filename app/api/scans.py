"""Los endpoints del escaneo de carpeta.

Router delgado a proposito: decide que se pide y como se responde, y nada mas.
La logica de leer el disco, decidir que hacer con cada archivo y escribir el
registro esta en `app/services/scan_service.py`.

Sobre el orden de las rutas, que en FastAPI importa y no es decorativo:

En este archivo las rutas literales (`/stats`, `/config`, `/ocr`) van ANTES que
las de placeholder (`/files/{file_id}`). El primer match gana, asi que
`GET /files/stats` sin este orden se resolveria contra `/files/{file_id}` y
`stats` se buscaria como un UUID. Ya se rompio una vez en este proyecto, en
`reconciliations.py`; ver `docs/known-issues.md` seccion 1.

Y lo que este router NO tiene, por seguridad:

- Ningun endpoint que acepte una carpeta. `TICKETS_INPUT_DIR` es la unica ruta
  que el escaner conoce y sale de la configuracion, no de la peticion. Un
  `POST /scan {"folder_path": "/"}` seria lectura arbitraria del disco del
  servidor: el `.env` del proyecto subiria como si fuera un comprobante.
- Ningun endpoint que borre archivos. El escaner es de solo lectura. Borrar el
  comprobante original porque el OCR salio mal es irreversible, y el papel no
  se reimprime.

Lo que si hay es un endpoint que relee un archivo ya registrado, y ese `id` es
un UUID de la base, no una ruta. La ruta se reconstruye desde
`TICKETS_INPUT_DIR` en el servicio y se vuelve a comprobar que cae dentro de
la carpeta.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import ScanStatus
from app.models.company import CompanyModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.schemas.scan import (
    ReprocessResponse,
    ScanEventResponse,
    ScanFileDetailResponse,
    ScanFileListResponse,
    ScanFileResponse,
    ScanItemResponse,
    ScanRequest,
    ScanResponse,
    ResumenScan,
    ScanStatsResponse,
)
from app.services import scan_registry, scan_service
from app.services.ocr import disponibilidad as disponibilidad_ocr

router = APIRouter(tags=["Scan"])

# Tope de la pagina de `GET /files`. Es el mismo numero que el tope por corrida
# del escaneo y por la misma razon: un `GET /files` sin tope sobre una carpeta de
# diez mil comprobantes se come la memoria del proceso, que es el mismo proceso
# que sirve el resto de la API.
TOPE_PAGINA = 200


async def _archivo_o_404(db: AsyncSession, file_id: UUID) -> ScanFileModel:
    """El archivo, o un 404 con nombre.

    Un UUID que no existe devuelve 404 y no una lista vacia. La diferencia
    importa en un endpoint de reproceso: "no hay nada" y "no existe" piden
    acciones distintas, y con una lista vacia el operador no sabe si escribio mal
    el id o si el archivo ya no esta.

    Se usa `execute` + `scalar_one_or_none` y NO `db.get`. `AsyncSession.get` es
    una corrutina: sin `await` devuelve el objeto corrutina, que no es `None`, y
    el `if` de abajo no lo atrapa. El 404 "funciona" y lo que se devuelve
    despues es una corrutina, que `model_validate` rechaza con un
    `ValidationError` y sale un 500. El sintoma es "el endpoint responde 500 en
    vez de 404" y la causa esta a una linea del `if`, asi que se documenta.
    """
    fila = (
        await db.execute(select(ScanFileModel).where(ScanFileModel.id == file_id))
    ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no hay ningun archivo escaneado con id {file_id}",
        )
    return fila


def _de_resumen(detalle: scan_service.ResumenArchivo) -> ScanItemResponse:
    """El dataclass del servicio al schema de la API, en un solo lugar."""
    return ScanItemResponse(
        relative_path=detalle.relative_path,
        scan_file_id=detalle.scan_file_id,
        status=detalle.status.value,
        accion=detalle.accion,
        ticket_id=detalle.ticket_id,
        confianza=detalle.confianza,
        origen=detalle.origen.value if detalle.origen is not None else None,
        detalle=detalle.detalle,
        archivado=detalle.archivado,
        ruta_archivo=detalle.ruta_archivo,
        extraction_status=detalle.extraction_status,
        estaba_pendiente=detalle.estaba_pendiente,
        solo_simulado=detalle.solo_simulado,
        # El servicio lo arma como `dict` para no importar schemas; aqui es
        # donde se valida. `None` cuando el archivo no produjo ticket.
        datos=detalle.datos,
    )


# ---------------------------------------------------------------------------
# Literales primero. Ver la nota de orden al inicio del modulo.
# ---------------------------------------------------------------------------


@router.get("/runs")
async def listar_corridas(
    usuario: UsuarioActual,
    limite: int = Query(10, ge=1, le=50, description="Cuantas corridas recientes."),
) -> dict:
    """Las corridas de escaneo recientes, la que esta corriendo primero.

    Es el canal de progreso. `POST /scan` es sincrono y con fotos tarda minutos,
    asi que sin esto el cliente no tiene nada que mostrar mientras espera: solo un
    `cargando` que puede ser "va bien" o "se trabo en el archivo 3 de 200".

    Lo que responde, en una linea: `terminada=false` con `actual` puesto es una
    corrida en marcha, y `actual` es el archivo que se esta leyendo ahora.

    EN MEMORIA, Y POR QUE NO ES UN TRABAJO EN COLA
    ----------------------------------------------
    Al reiniciar el contenedor esta lista sale vacia aunque la carpeta tenga 200
    archivos a medio leer. Es correcto: un proceso que murio no sigue trabajando,
    y fingir lo contrario seria peor. Lo que NO se pierde es el estado real de cada
    archivo, que esta en `scan_files` y `scan_events` y es lo que evita repetir
    trabajo en la proxima pasada.
    """
    del usuario  # La puerta la pone api_router; aqui el usuario no se usa.
    corridas = scan_registry.listar(limite)
    return {
        "corridas": [c.a_dict() for c in corridas],
        "en_curso": [c.id for c in corridas if not c.terminada],
    }


@router.get("/runs/{corrida_id}")
async def ver_una_corrida(corrida_id: str, usuario: UsuarioActual) -> dict:
    """El progreso de UNA corrida.

    Es la llamada que hace el cliente cada dos o tres segundos mientras el
    `POST /scan` sigue abierto. Devuelve el resumen final cuando ya termino, asi
    que el mismo endpoint sirve para el progreso y para el resultado.

    Un 404 aqui NO es "no existio": el registro es en memoria y se descarta a las
    dos horas, y tambien al reiniciar. El mensaje lo dice, para que el cliente no
    lo presente como un error del servidor.
    """
    del usuario  # La puerta la pone api_router; aqui el usuario no se usa.
    corrida = scan_registry.consultar(corrida_id)
    if corrida is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Esta corrida no esta en el registro. Puede haber terminado hace "
                "mas de dos horas, o el servidor se reinicio mientras corria. "
                "Los datos de cada archivo siguen en /scan/files."
            ),
        )
    return corrida.a_dict()


@router.get("/ocr")
async def estado_del_ocr(usuario: UsuarioActual) -> dict:
    """Que motores de OCR se pueden usar ahora mismo.

    Existe porque "OCR no funciona" y "OCR esta apagado" piden acciones
    opuestas, y verlas separadas evita la diagnostica de quince minutos. Cuando
    Tesseract no esta instalado, esto dice como instalarlo, en vez de dejar que
    cada foto falle con un error que hay que buscar en el log del servidor.
    """
    del usuario  # La puerta la pone api_router; aqui el usuario no se usa.
    return disponibilidad_ocr()


@router.get("/config")
async def configuracion_del_escaneo(usuario: UsuarioActual) -> dict:
    """La configuracion efectiva del escaneo.

    Se expone porque el valor por defecto de la carpeta es una ruta de una
    maquina concreta (`/Users/carloslott/...`). En cualquier otra maquina, en
    un contenedor o en la de otra persona, hay que cambiar la variable, y sin
    este endpoint la unica forma de saber si el cambio tomo efecto es correr un
    escaneo y mirar la ruta que salio en el resumen.
    """
    del usuario
    return {
        "carpeta": scan_service.raiz().as_posix(),
        "carpeta_existe": scan_service.raiz().is_dir(),
        "recursivo": settings.TICKETS_SCAN_RECURSIVO,
        "max_por_corrida": settings.TICKETS_SCAN_MAX_ARCHIVOS,
        "max_bytes_por_archivo": settings.TICKETS_SCAN_MAX_BYTES,
    }


@router.get("/stats", response_model=ScanStatsResponse)
async def estadisticas(
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> ScanStatsResponse:
    """Como va el escaneo: conteos por estado, por motor y por antiguedad.

    Va antes que `/files/{file_id}` por el orden de rutas. Sin eso,
    `GET /scan/stats` se buscaria como un UUID.
    """
    del usuario
    return await _estadisticas(db)


async def _estadisticas(db: AsyncSession) -> ScanStatsResponse:
    por_estado = {
        estado: cantidad
        for estado, cantidad in (await db.execute(
            select(ScanFileModel.status, func.count())
            .group_by(ScanFileModel.status)
        )).all()
    }

    por_motor = {
        motor: cantidad
        for motor, cantidad in (await db.execute(
            select(ScanFileModel.read_by, func.count())
            .where(ScanFileModel.read_by.is_not(None))
            .group_by(ScanFileModel.read_by)
        )).all()
    }

    intentos = (
        await db.execute(select(func.coalesce(func.sum(ScanFileModel.attempts), 0)))
    ).scalar_one()

    con_ticket = (
        await db.execute(
            select(func.count()).select_from(ScanFileModel)
            .where(ScanFileModel.ticket_id.is_not(None))
        )
    ).scalar_one()

    # "Desactualizado" es un archivo PROCESADO con `last_error` puesto. Existe
    # porque el caso es real y no cabe en ningun estado: el archivo cambio, el
    # ticket se leyo bien en su momento, pero el ticket ya no corresponde al
    # papel. Sin este conteo, ese archivo aparece como un PROCESADO mas, igual
    # que uno al dia, y el operador no tiene forma de saber que hay trabajo.
    desactualizados = (
        await db.execute(
            select(func.count()).select_from(ScanFileModel)
            .where(
                ScanFileModel.status == ScanStatus.PROCESADO.value,
                ScanFileModel.last_error.is_not(None),
            )
        )
    ).scalar_one()

    mas_reciente = (
        await db.execute(select(func.max(ScanFileModel.processed_at)))
    ).scalar_one_or_none()

    total = sum(por_estado.values())

    return ScanStatsResponse(
        total_archivos=total,
        por_estado=por_estado,
        por_motor=por_motor,
        intentos_totales=int(intentos or 0),
        archivos_con_error=por_estado.get(ScanStatus.ERROR.value, 0),
        archivos_desactualizados=int(desactualizados),
        tickets_vinculados=int(con_ticket),
        archivos_sin_ticket=total - int(con_ticket),
        processed_at_mas_reciente=mas_reciente,
    )


@router.post("/", response_model=ScanResponse)
async def escanear(
    cuerpo: ScanRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> ScanResponse:
    """Recorre la carpeta y procesa lo que corresponda.

    El body NO lleva carpeta. Se lee `TICKETS_INPUT_DIR` de la configuracion y
    no de la peticion, por seguridad: ver la nota del modulo.

    Sin `company_id` el escaneo inventaria y NO crea tickets. Es a proposito, y
    es el primer paso que conviene: correrlo para ver que hay en la carpeta antes
    de atribuir los gastos a una empresa.

    Y sin `company_id` NO se archiva nada, aunque `archivar` veniga en `true`:
    mover un comprobante sin que se haya guardado su contenido deja el papel en
    un sitio donde nadie lo va a buscar y sin registro de lo que se leyo.

    COMO USAR `simular`
    ====================

    Con `archivar` activo por omision, la primera corrida deja la carpeta de
    entrada vacia. Eso conviene verlo antes de que ocurra:

        POST /scan {"company_id": "...", "simular": true}

    Devuelve exactamente lo que moveria, con la ruta de destino de cada archivo
    y si su lectura quedo pendiente, y no mueve nada.
    """
    actor = usuario.email or "sistema"

    if cuerpo.company_id is not None:
        existe = await db.get(CompanyModel, cuerpo.company_id)
        if existe is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no hay ninguna empresa con id {cuerpo.company_id}",
            )

    resumen = await scan_service.escanear(
        db,
        cuerpo.company_id,
        actor,
        reprocesar=cuerpo.reprocesar,
        solo_pendientes=cuerpo.solo_pendientes,
        archivar=cuerpo.archivar,
        simular=cuerpo.simular,
    )

    return ScanResponse(
        carpeta=resumen.carpeta,
        archivos_vistos=resumen.archivos_vistos,
        nuevos=resumen.nuevos,
        actualizados=resumen.actualizados,
        sin_cambios=resumen.sin_cambios,
        duplicados=resumen.duplicados,
        con_error=resumen.con_error,
        no_soportados=resumen.no_soportados,
        omitidos_por_tope=resumen.omitidos_por_tope,
        leidos=resumen.leidos,
        lecturas_por_motor=resumen.lecturas_por_motor,
        archivados=resumen.archivados,
        archivados_pendientes=resumen.archivados_pendientes,
        carpeta_destino=resumen.carpeta_destino,
        simulado=resumen.simulado,
        detalles=[_de_resumen(d) for d in resumen.detalles],
        # El total, validado aqui: el servicio lo arma como `dict` para no
        # importar de `schemas/`.
        resumen=resumen.resumen,
        corrida_id=resumen.corrida_id,
    )


# ---------------------------------------------------------------------------
# El registro de archivos
# ---------------------------------------------------------------------------


@router.get("/files", response_model=ScanFileListResponse)
async def listar_registro(
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    estado: str | None = Query(
        None, description="Filtra por estado. Uno de ScanStatus."
    ),
    company_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=TOPE_PAGINA),
    offset: int = Query(0, ge=0),
) -> ScanFileListResponse:
    """El registro de archivos, filtrado y paginado.

    `limit` tiene tope porque no hay tope sin el: un `GET /files` sin limite
    sobre una carpeta de diez mil comprobantes devuelve diez mil filas y se come
    la memoria del proceso, que es el mismo proceso que sirve el resto de la API.
    """
    del usuario

    consulta = select(ScanFileModel)
    conteo = select(func.count()).select_from(ScanFileModel)

    if estado is not None:
        # Se valida contra el enum y no se pasa la cadena a la consulta. Un
        # `estado=loquesea` devoltiende una lista vacia hace creer que no hay
        # archivos en la carpeta; con validacion dice "no es un estado", que es
        # la verdad y deja saber que el error es del filtro.
        try:
            valido = ScanStatus(estado)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{estado!r} no es un estado de escaneo. Validos: "
                    + ", ".join(s.value for s in ScanStatus)
                ),
            ) from None
        consulta = consulta.where(ScanFileModel.status == valido.value)
        conteo = conteo.where(ScanFileModel.status == valido.value)

    if company_id is not None:
        consulta = consulta.where(ScanFileModel.company_id == company_id)
        conteo = conteo.where(ScanFileModel.company_id == company_id)

    total = (await db.execute(conteo)).scalar_one()
    archivos = (
        await db.execute(
            consulta.order_by(ScanFileModel.relative_path)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return ScanFileListResponse(
        total=int(total),
        limit=limit,
        offset=offset,
        archivos=[ScanFileResponse.model_validate(f) for f in archivos],
    )


@router.get("/files/{file_id}", response_model=ScanFileDetailResponse)
async def detalle_archivo(
    file_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> ScanFileDetailResponse:
    """Un archivo con su historico completo.

    El historico va aqui y no en un endpoint aparte de auditoria porque la
    pregunta casi siempre es "que le paso a ESTE archivo", y separarla obliga a
    dos requests para la misma pantalla.
    """
    del usuario
    fila = await _archivo_o_404(db, file_id)

    # Los eventos se cargan con una consulta explicita, NO tocando
    # `fila.events`. En SQLAlchemy asincrono, tocar una relacion lazy desde un
    # schema de Pydantic dispara IO dentro de un contexto que no admite `await`,
    # y sale un `MissingGreenlet` que se presenta como un error de validacion
    # ("Error extracting attribute: events"), apuntando a una linea que no tiene
    # nada que ver con eventos: el `model_validate`. Por eso la relacion se
    # declara con `lazy="selectin"` en el modelo, que tambien resuelve el
    # `MissingGreenlet` en el resto del codigo.
    eventos = (
        await db.execute(
            select(ScanEventModel)
            .where(ScanEventModel.scan_file_id == file_id)
            .order_by(ScanEventModel.created_at, ScanEventModel.action)
        )
    ).scalars().all()

    respuesta = ScanFileDetailResponse.model_validate(fila)
    respuesta.events = [ScanEventResponse.model_validate(e) for e in eventos]
    return respuesta


@router.post("/files/{file_id}/reprocess", response_model=ReprocessResponse)
async def reprocesar_archivo(
    file_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(
        None, description="Empresa a la que atribuir el ticket, si falta."
    ),
) -> ReprocessResponse:
    """Relee UN archivo. Este es el camino explicito para forzar una relectura.

    Es el unico endpoint que puede sobreescribir un ticket que ya tiene
    correcciones humanas, y lo hace porque la peticion es explicita: alguien
    pidio reprocesar ESTE archivo. El escaneo automatico nunca lo hace, y esa
    diferencia es el motivo de que los dos caminos existan.

    Si el archivo ya no esta en el disco, devuelve 409 con el motivo y no un
    exito: reprocesar el registro de un archivo que alguien movio tiene que
    verse, no reportarse como leido.
    """
    actor = usuario.email or "sistema"
    fila = await _archivo_o_404(db, file_id)

    resultado = await scan_service.reprocesar_uno(db, fila, company_id, actor)

    if resultado.accion == "ERROR":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=resultado.detalle or "no se pudo reprocesar el archivo",
        )

    return ReprocessResponse(archivo=_de_resumen(resultado))
