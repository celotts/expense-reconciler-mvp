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

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import media_type_real
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import ScanStatus
from app.models.company import CompanyModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.models.ticket_document import content_type_servible
from app.models.user import UserModel
from app.schemas.lectura import DiagnosticoLecturaResponse
from app.schemas.scan import (
    NO_SE_BORRA,
    ReprocessResponse,
    ScanEventResponse,
    ScanFileDetailResponse,
    ScanFileListResponse,
    ScanFileResponse,
    ScanItemResponse,
    ScanRequest,
    ScanResetRequest,
    ScanResetResponse,
    ScanResponse,
    ScanStatsResponse,
)
from app.services import scan_registry, scan_reset, scan_service, vista_previa
from app.services.archivado_service import carpeta_de_escaneados
from app.services.auditoria import registrar_auditoria
from app.services.document_service import documento_de_ticket
from app.services.ai_client import disponibilidad as disponibilidad_ia
from app.services.digitalizacion_directorio import (
    EXTENSIONES_PERMITIDAS,
    raices_permitidas,
)
from app.services.lectura_diagnostico import diagnosticar_lectura
from app.services.ocr import disponibilidad as disponibilidad_ocr
from app.services.scan_reset import FRASE_CONFIRMACION

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
            detail="Ese archivo no está en el historial de escaneos",
        )
    return fila


def de_resumen(detalle: scan_service.ResumenArchivo) -> ScanItemResponse:
    """El dataclass del servicio al schema de la API, en un solo lugar.

    Publica porque la usa tambien `app/routers/expenses.py`: la carga por lote
    devuelve el MISMO `ScanItemResponse` que el escaner de carpeta. Si cada
    router convirtiera el resumen por su cuenta, un campo nuevo se agregaria
    en uno y no en el otro, y el cliente recibiria dos formas distintas de
    describir el mismo archivo.
    """
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
        # Que le paso AL ARCHIVO, que es distinto de lo que le paso al TICKET.
        # Los tres van porque se contradecian entre si: `archivado` decia que si
        # y `ruta_archivo` que no habia destino.
        retiro=detalle.retiro,
        motivo_retiro=detalle.motivo_retiro,
        respaldo_verificado=detalle.respaldo_verificado,
        recuperable_desde=detalle.recuperable_desde,
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


def _reset_habilitado() -> None:
    """El cerrojo 1: el flag decide si el endpoint EXISTE.

    POR QUE ES UNA DEPENDENCIA Y NO UN `if` EN EL CUERPO
    ====================================================

    La primera version lo tenia como `if not settings...: raise 404` al principio
    del handler, y el test lo cazó: con el flag apagado devolvia **401**, no 404.

    La razon es el orden en que FastAPI resuelve las cosas. Las **dependencias se
    evaluan antes** que el cuerpo del handler —por eso `UsuarioVerificado` ya habia
    pedido el token y comprobado la contrasena cuando se llegaba al primer
    `if`—. Es decir: el `if` del cuerpo es el **tercer** cerrojo en ejecutarse,
    aunque en el codigo este el primero.

   Eso no es cosmetico: un visitante sin token recibe 401, lo que **confirma que
    la ruta existe**. Un 404 no lo hace. Y para un endpoint que borra la base,
    confirmar que hay una puerta de borrado es parte del daño.

    Y NO se resuelve "poniendo el parametro del flag primero", porque FastAPI no
    garantiza el orden entre un `Annotated` y un `Depends` suelto. Lo que si
    garantiza es el orden **dentro** de una misma dependencia, asi que
    `_reset_permitido` encadena las dos en el orden correcto.
    """
    if not settings.TICKETS_SCAN_RESET_HABILITADO:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Este endpoint no existe en esta instalacion. Para activarlo hay "
                "que poner TICKETS_SCAN_RESET_HABILITADO=true en el .env y "
                "reiniciar el API. Es deliberado: un endpoint que borra la base "
                "no debe estar disponible solo porque alguien lo dejo escrito."
            ),
        )


def _reset_permitido(
    usuario: UsuarioActual = None,  # type: ignore[assignment]
    contrasena: Annotated[str | None, Header(alias="X-Contrasena-Actual")] = None,
):
    """Autenticacion y contrasena del actor (401 si falta o no coincide)."""
    from app.core.security import verificar_contrasena

    if not contrasena:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Falta X-Contrasena-Actual. Dar de baja una cuenta o cambiar una "
                "contrasena pide la contrasena de quien lo hace."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verificar_contrasena(contrasena, usuario.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La contrasena no es la correcta",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


@router.post("/reset", dependencies=[Depends(_reset_habilitado)])
async def limpiar_base_de_datos(
    cuerpo: ScanResetRequest,
    request: Request,
    db: Annotated[AsyncSession | None, Depends(get_db)] = None,
    usuario: UserModel = Depends(_reset_permitido),
) -> ScanResetResponse:
    """Deja la base limpia para volver a probar el escaneo.

    CUATRO CERRADJOS, Y EN QUE ORDEN SE EJECUTAN
    =============================================

    1. **El flag** (`_reset_habilitado`, dependencia). Apagado -> **404**, y sin
       llegar a mirar el token. Ver el docstring de esa funcion: es el primero que
       corre, y por eso es una dependencia y no un `if`.
    2. **La contrasena del que llama** (`UsuarioVerificado`): token **y**
       `X-Contrasena-Actual`. Es el mismo cerrojo de `usuarios.py`, y por el mismo
       motivo —un token robado no debe alcanzar para borrar el historico
       contable—.
    3. **La frase.** `confirmar` tiene que decir `BORRAR_YA` exacta. No un
       booleano: un `true` se manda con un clic de mas y no distingue "me
       confirme a mi mismo" de "el sistema me lo pregunto".
    4. **No hay trabajo humano.** Si hay un ticket con `reviewed_at` o una compra
       `PROCESADO`, responde **409** y no borra nada. Es el unico cerrojo que
       protege lo irrepetible: una correccion contra el papel, o una compra que
       alguien autorizo para que el stock empezara a contar.

    Y ADEMAS, el modo por omision (`ejecutar=false`) solo cuenta. Un endpoint
    destructivo cuyo modo por omision es el destructivo es del que hay que
    acordarse de no usar.

    LO QUE NO SE BORRA
    ------------------
    Ni las cuentas, ni las empresas, ni el catalogo de productos. Las empresas
    porque el escaneo necesita una para atribuir (sin ella `POST /scan` solo
    inventaria); las cuentas porque borrarlas deja el sistema sin puerta de
    entrada; el catalogo porque son horas de dar de alta y no tiene que ver con
    probar el escaneo. La respuesta los lista en `lo_que_no_se_borra`.

    El detalle de por que el borrado vive en `services/scan_reset.py` y no aqui:
    el orden de los `DELETE` no es estilo, porque `ticket_documents` es append-only
    y **solo** se puede borrar cuando su ticket ya no existe.
    """
    # --- Cerrojo 3: la frase ---
    if cuerpo.confirmar != FRASE_CONFIRMACION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"La confirmacion no es la correcta. Para borrar hay que escribir "
                f"exactamente {FRASE_CONFIRMACION}. No se borro nada."
            ),
        )

    try:
        resultado = await scan_reset.limpiar(db, confirmar=cuerpo.ejecutar)
    except scan_reset.HayTrabajoHumano as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No se borro nada: hay trabajo de una persona en la base que no "
                "se puede rehacer. "
                f"{exc.conteo.tickets_revisados} ticket(s) revisados a mano y "
                f"{exc.conteo.compras_procesadas} compra(s) autorizadas. "
                "Revisalos y borralos uno por uno si de verdad quieres que "
                "desaparezcan."
            ),
        ) from exc

    # Solo la ejecucion real deja rastro de borrado: en modo simulado
    # (`ejecutar=false`) no se borro nada, y auditarlo como borrado mentiria.
    # `db` puede ser None: en ese caso `registrar_auditoria` abre su propia
    # sesion, igual que los demas llamadores que no la pasan.
    if not resultado.simulado:
        await registrar_auditoria(
            accion="limpiar_base",
            modulo="scan",
            usuario_id=str(usuario.id),
            detalles={
                "borrado": resultado.borrado,
                "conteo": resultado.conteo.a_dict(),
            },
            rol=usuario.perfil,
            ip=request.client.host if request.client else "desconocida",
            resultado="exito",
            db=db,
        )

    return ScanResetResponse(
        simulado=resultado.simulado,
        conteo=resultado.conteo.a_dict(),
        borrado=resultado.borrado,
        empresas_conservadas=resultado.empresas_conservadas,
        usuarios_conservados=resultado.usuarios_conservados,
        lo_que_no_se_borra=list(NO_SE_BORRA),
    )


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


@router.get("/ia")
async def estado_de_la_ia(usuario: UsuarioActual) -> dict:
    """Que modelo de IA esta configurado, y si de verdad esta disponible.

    Es el mismo motivo que `/ocr`: "el modelo no responde" y "el modelo esta mal
    configurado" se ven igual desde afuera y piden acciones opuestas.

    ## POR QUE RESPONDE LO CONFIGURADO Y LO INSTALADO POR SEPARADO

    MEDIDO en esta maquina: la `.env` declara `OLLAMA_MODEL=qwen2.5:3b` y
    `OLLAMA_VISION_MODEL=moondream`, pero en el contenedor **solo esta
    `moondream`**. Ese modelo de texto no ha contestado ni una sola vez, y no hay
    forma de saberlo mirando un log de lectura: `confidence_source` dice `llm`
    para los dos motores y nadie puede distinguir "no leyo" de "no existe".

    Por eso `configurado` y `disponible` son dos preguntas, y el endpoint contesta
    las dos. Un booleano diria "la IA funciona" con la mitad de la verdad.

    ## POR QUE DICE COMO ARREGLARLO

    Un `ollama pull` equivocado en un ticket de incidencia cuesta media hora. El
    nombre va en la respuesta, copiado y pegable.
    """
    del usuario  # La puerta la pone api_router; aqui el usuario no se usa.
    #
    # EL `await` NO ES OPCIONAL, y este bug estuvo escondido mucho tiempo.
    # `ai_client.disponibilidad` es `async` porque le pregunta a Ollama por HTTP.
    # Sin `await`, esto devuelve el objeto coroutine y FastAPI falla al
    # serializar: `ResponseValidationError: Input should be a valid dictionary,
    # input: <coroutine object disponibilidad at 0x...>`.
    #
    # MEDIDO: `GET /api/v1/scan/ia` respondia **500**, y no se notaba porque el
    # componente `EstadoDeIa` del front **se traga el error a proposito** —no
    # ensucia la pantalla con el fallo de un endpoint opcional—. Es decir: la
    # defensa del front tapo el bug del backend, que es justo lo contrario de lo
    # que ese componente queria evitar.
    #
    # Y el endpoint existe para responder "la IA esta bien puesta?", o sea que
    # un 500 aqui es peor que inútil: es el que diría qué modelo falta.
    return await disponibilidad_ia()


@router.get("/config")
async def configuracion_del_escaneo(usuario: UsuarioActual) -> dict:
    """La configuracion efectiva del escaneo.

    Se expone porque el valor por defecto de la carpeta es una ruta de una
    maquina concreta (`/Users/carloslott/...`). En cualquier otra maquina, en
    un contenedor o en la de otra persona, hay que cambiar la variable, y sin
    este endpoint la unica forma de saber si el cambio tomo efecto es correr un
    escaneo y mirar la ruta que salio en el resumen.

    `archivar_por_defecto` ESTA, y no es un dato de mas. Es lo que permite que la
    casilla de la pantalla muestre el valor **real** en vez de suponerlo: sin este
    campo, el front tendria que hardcodear `true` —porque asi viene por omision en
    `Settings`— y mentiria en cuanto alguien apagara `TICKETS_SCAN_ARCHIVAR` en el
    `.env`, que es justo lo que hace falta para probar el escaneo varias veces.

    Una casilla de archivado que arranca marcada cuando el archivado esta apagado es
    una forma de mover comprobantes de carpeta sin querer, y el operador no tendria
    forma de saber por que desaparecieron de la bandeja.

    `rutas_permitidas` y `extensiones_permitidas` son las dos listas que la
    pantalla de carga por lote necesita para no adivinar. Salen de las MISMAS
    funciones que defiende el endpoint (`raices_permitidas` y la constante del
    servicio): si el front tuviera su propia copia, una ruta cambiada en el
    `.env` seguiria apareciendo como sugerencia y el 400 posterior pareceria un
    error del servidor. Son informacion, no autorizacion: el back comprueba la
    ruta de nuevo en cada peticion.
    """
    del usuario
    return {
        "carpeta": scan_service.raiz().as_posix(),
        "rutas_permitidas": [r.as_posix() for r in raices_permitidas()],
        "extensiones_permitidas": sorted(EXTENSIONES_PERMITIDAS),
        "carpeta_existe": scan_service.raiz().is_dir(),
        "recursivo": settings.TICKETS_SCAN_RECURSIVO,
        "max_por_corrida": settings.TICKETS_SCAN_MAX_ARCHIVOS,
        "max_bytes_por_archivo": settings.TICKETS_SCAN_MAX_BYTES,
        # El valor EFECTIVO. `TICKETS_SCAN_ARCHIVAR` es el interruptor general y
        # manda sobre el de escanear: apagar el general apaga los dos.
        "archivar_por_defecto": bool(
            settings.TICKETS_SCAN_ARCHIVAR and settings.TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR
        ),
        # Y los dos por separado, porque son decisiones distintas: una es "mover al
        # digitalizar" y la otra "mover al confirmar la compra". Con las dos en
        # `true` —el default— la segunda casi nunca dispara, porque el archivo ya
        # se movio al escanear.
        "archivar_al_escanear": settings.TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR,
        "archivar_al_confirmar": settings.TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR,
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
    #
    # LO QUE SE EXCLUYE, Y POR QUE: MEDIDO, NO SUPUESTO
    # -------------------------------------------------
    # La condicion original era solo `last_error IS NOT NULL`, y eso contaba como
    # "desactualizado" un archivo al que solo le faltaba la **empresa** —el
    # resultado de "Escanear (solo inventariar)"—, porque ese camino escribe
    # `last_error = "leido correctamente pero sin empresa: no se creo ticket"`.
    #
    # Medido en esta maquina: 6 archivos, 0 tickets, y la pantalla de escaner
    # decía "6 archivos cambiaron desde que se leyeron, pero su ticket ya fue
    # corregido a mano y no se sobreescribio". **Es falso en las tres partes**: no
    # cambiaron, no hay ticket, y nadie corrigio nada. El contador estaba
    # confundiendo un mensaje informativo con un problema.
    #
    # El filtro correcto es el que ya usa `scan_service` para marcar un archivo
    # como desactualizado de verdad: `OMITIDO` **con** ticket. Sin ticket no hay
    # "correccion humana que no se sobreescribe"; hay una lectura que todavia no
    # se ha atribuido, y esa es otra cosa —la cuenta el boton de crear tickets—.
    desactualizados = (
        await db.execute(
            select(func.count()).select_from(ScanFileModel)
            .where(
                ScanFileModel.status == ScanStatus.PROCESADO.value,
                ScanFileModel.last_error.is_not(None),
                ScanFileModel.last_error.like("%sobreescribe%"),
                ScanFileModel.ticket_id.is_not(None),
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


@router.post(
    "/files/{file_id}/diagnostico",
    response_model=DiagnosticoLecturaResponse,
    tags=["Scan"],
    summary="Relee UN archivo y explica como se leyo. No guarda nada.",
)
async def diagnosticar_archivo(
    file_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> DiagnosticoLecturaResponse:
    """La lectura de un archivo ya escaneado, paso por paso, sin escribir nada.

    POR QUE ESTE Y NO SUBIR EL ARCHIVO DE NUEVO
    --------------------------------------------

    Por dos razones, y la segunda es la que importa:

      1. Desde Insomnia es un click. `{{ file_id }}` ya se autocompleta con
         `GET /scan/files`, y aqui no hay que elegir ningun archivo: la variable
         `producto_id`-de-archivo ya esta. Subir el archivo obliga a ir al disco a
         buscarlo en cada llamada.
      2. **El archivo ya se movio.** Con `TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR` en `true`
         (la omision), `POST /scan` mueve cada comprobante a `Tickets_Scan` al
         digitalizarlo. Volver a subirlo significaria que se puede diagnosticar un
         comprobante que el sistema ya no tiene delante, que no es el caso que
         interesa.

    Y es el mismo archivo que leyo el escaner, con el mismo contenido: el diagnostico
    no dice por que salio mal *ayer*, dice por que saldria mal ahora.

    **No guarda nada**, ni actualiza `scan_files` ni sus `attempts`. Es la diferencia
    con `POST /files/{id}/reprocess`, que si lo hace: ese es el unico camino que puede
    sobreescribir un ticket con correcciones humanas, y por eso es explicito. Este solo
    mira.

    **No pide `company_id`.** No escribe nada, asi que no hay a quien atribuir el
    ticket. Y el contraste con lo guardado sale del `ticket_id` que el propio registro
    ya tiene, que es la atribucion que hizo el escaner.
    """
    fila = await _archivo_o_404(db, file_id)

    # La comprobacion de contencion la hace `ruta_de_relativo`, con `is_relative_to`
    # DESPUES de resolver. Ver la nota larga de ahi: comprobar la cadena antes de
    # resolver daria una sensacion de seguridad que no existe, porque un symlink a
    # `/etc` no tiene un solo `..` en el texto.
    try:
        ruta = scan_service.ruta_de_relativo(fila.relative_path)
    except scan_service.ArchivoFueraDeLaCarpeta as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if not ruta.is_file():
        # 409 y no 404: el REGISTRO existe, el archivo no. Son hechos distintos y el
        # que importa es el segundo. Es el mismo codigo que usa `reprocesar_archivo`,
        # y por el mismo motivo: reprocesar el registro de un archivo que alguien movio
        # tiene que verse, no reportarse como leido.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"el archivo ya no esta en la carpeta de entrada: {ruta}. "
                "Si se archivo, se puede diagnosticar por el ticket con "
                "POST /tickets/extract-diagnostico, que lee el documento guardado."
            ),
        )

    resultado = await diagnosticar_lectura(
        db,
        ruta.read_bytes(),
        declarado=None,  # el registro ya sabe el formato; ver `detected_format`
        nombre=fila.relative_path,
        ticket_id=fila.ticket_id,
    )
    # El aplanado vive en `tickets.py`, junto a los demas, y se importa aqui para no
    # duplicarlo. Es la unica importacion cruzada entre routers del proyecto, y la
    # razon es que el diagnostico es el MISMO dato en los dos caminos: si cada uno
    # tuviera el suyo,responderian con distinta forma y el que compara las dos
    # tendria que saber cual es cual.
    from app.api.tickets import _a_diagnostico

    return _a_diagnostico(resultado)


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
                detail="La empresa indicada no existe",
            )

    resumen = await scan_service.escanear(
        db,
        cuerpo.company_id,
        actor,
        reprocesar=cuerpo.reprocesar,
        solo_pendientes=cuerpo.solo_pendientes,
        archivar=cuerpo.archivar,
        simular=cuerpo.simular,
        confirmar=cuerpo.confirmar,
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
        # MEDIDO el 2026-10-16: sin esto, una carpeta que no se puede leer
        # responde 200 con `archivos_vistos: 0` y no hay forma de saber que hay
        # archivos ahi que no se pudieron abrir.
        ilegibles=resumen.ilegibles,
        leidos=resumen.leidos,
        lecturas_por_motor=resumen.lecturas_por_motor,
        archivados=resumen.archivados,
        archivados_pendientes=resumen.archivados_pendientes,
        carpeta_destino=resumen.carpeta_destino,
        simulado=resumen.simulado,
        # Los tres que separan "se movio", "salio de la bandeja" y "lo puedo
        # recuperar". Sin ellos, `archivados: 1` con la carpeta de destino vacia
        # no decia si el comprobante estaba a salvo o se habia perdido.
        movidos_a_escaneados=resumen.movidos_a_escaneados,
        retirados_de_entrada=resumen.retirados_de_entrada,
        recuperables_desde_db=resumen.recuperables_desde_db,
        quedan_en_bandeja=resumen.quedan_en_bandeja,
        compras_confirmadas=resumen.compras_confirmadas,
        compras_no_confirmadas=resumen.compras_no_confirmadas,
        detalles=[de_resumen(d) for d in resumen.detalles],
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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

    # Resumen del ticket asociado a cada archivo, para que el listado muestre de
    # una pasada negocio, fecha y total sin abrir cada comprobante.
    tickets_ids = [f.ticket_id for f in archivos if f.ticket_id is not None]
    info_ticket: dict = {}
    if tickets_ids:
        q = await db.execute(
            select(TicketModel.id, TicketModel.provider_name, TicketModel.expense_date, TicketModel.total_amount)
            .where(TicketModel.id.in_(tickets_ids))
        )
        for tid, prov, fecha, total_amt in q.all():
            info_ticket[tid] = (prov, fecha, str(total_amt) if total_amt is not None else None)

    respuesta_archivos = []
    for f in archivos:
        fila = ScanFileResponse.model_validate(f)
        if f.ticket_id is not None and f.ticket_id in info_ticket:
            prov, fecha, total_amt = info_ticket[f.ticket_id]
            fila.ticket_negocio = prov
            fila.ticket_fecha = fecha
            fila.ticket_total = total_amt
        respuesta_archivos.append(fila)

    return ScanFileListResponse(
        total=int(total),
        limit=limit,
        offset=offset,
        archivos=respuesta_archivos,
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


async def _bytes_del_archivo_escaneado(
    db: AsyncSession, fila: ScanFileModel
) -> tuple[bytes, str] | None:
    """Los bytes del archivo escaneado, tal como estan en el disco.

    Solo lectura: no se mueve, no se borra y no se reescribe nada. Se busca
    primero en la carpeta de entrada y luego en la de escaneados (un archivo
    ya resuelto se mueve ahi con `archivar`, con los mismos bytes). Si ya no
    esta en ninguna de las dos, se usa el documento guardado en
    `ticket_documents` cuando el archivo produjo un ticket: esos bytes son el
    original respaldado, con su sha256. Sin disco ni respaldo, `None`.
    """
    try:
        ruta = scan_service.ruta_de_relativo(fila.relative_path)
    except scan_service.ArchivoFueraDeLaCarpeta:
        ruta = None
    if ruta is not None and ruta.is_file():
        return ruta.read_bytes(), ruta.name
    # Carpeta de escaneados: hermana de la de entrada, misma estructura de
    # subdirectorios que el origen. La comprobacion va DESPUES de resolver,
    # igual que en `ruta_de_relativo`: un symlink no se ve en la cadena.
    base_destino = carpeta_de_escaneados()
    candidato = (base_destino / fila.relative_path).resolve()
    if candidato.is_relative_to(base_destino) and candidato.is_file():
        return candidato.read_bytes(), candidato.name
    if fila.ticket_id is not None:
        documento = await documento_de_ticket(db, fila.ticket_id)
        if documento is not None:
            return (
                documento.contenido,
                documento.nombre_archivo or fila.relative_path,
            )
    return None


@router.get("/files/{file_id}/documento", response_class=Response)
async def documento_archivo_escaneado(
    file_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """El archivo escaneado, tal como esta, sin modificarlo.

    Es lo que faltaba en el Escaner: un archivo "omitido" (leido sin empresa,
    sin ticket) no tenia documento que mostrar, porque `VerDocumento` solo
    sabe mirar `ticket_documents`. Este endpoint sirve el papel de la carpeta
    —entrada o escaneados— para que en el detalle se vea el original al lado
    de los datos leidos, aunque todavia no haya ticket.

    El `Content-Type` sale de una lista cerrada por los BYTES, igual que en
    `GET /tickets/{id}/documento`: un archivo que no esta en ella se sirve
    como `application/octet-stream`, que conserva los bytes intactos.
    Requiere autenticacion, como el de tickets: un UUID no es un secreto.
    """
    # La importacion va aqui y no arriba: `tickets.py` importa cosas de este
    # modulo en otro camino, y a nivel de modulo seria un ciclo. Es el mismo
    # patron que `diagnosticar_archivo` con `_a_diagnostico`.
    from app.api.tickets import _nombre_seguro

    del usuario
    fila = await _archivo_o_404(db, file_id)
    hallado = await _bytes_del_archivo_escaneado(db, fila)
    if hallado is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El archivo ya no está en la carpeta de entrada ni en la de "
                "escaneados, y no tiene documento guardado."
            ),
        )
    contenido, nombre = hallado
    return Response(
        content=contenido,
        media_type=content_type_servible(media_type_real(contenido)),
        headers={
            "Content-Disposition": (
                f'inline; filename="{_nombre_seguro(nombre)}"'
            ),
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/files/{file_id}/documento/vista-previa", response_class=Response)
async def vista_previa_archivo_escaneado(
    file_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """El archivo escaneado en un formato que el navegador SI pinta.

    Solo HEIC/HEIF se convierte a JPEG, con `vista_previa.convertir_a_jpeg`:
    un PDF, un JPEG o un PNG salen con sus bytes originales, sin tocarlos.
    La conversion es una representacion para mostrar, no una reescritura:
    el archivo del disco y el documento guardado siguen intactos.
    """
    del usuario
    fila = await _archivo_o_404(db, file_id)
    hallado = await _bytes_del_archivo_escaneado(db, fila)
    if hallado is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El archivo ya no está en la carpeta de entrada ni en la de "
                "escaneados, y no tiene documento guardado."
            ),
        )
    contenido, _nombre = hallado
    tipo_real = media_type_real(contenido)
    if not vista_previa.es_convertible(tipo_real):
        return Response(
            content=contenido,
            media_type=content_type_servible(tipo_real),
            headers={"Cache-Control": "private, max-age=300"},
        )
    jpeg = vista_previa.convertir_a_jpeg(contenido)
    if jpeg is None:
        # Se sirve el original en vez de un 404: el archivo EXISTE y hay que
        # poder mirarlo, aunque sea descargando.
        return Response(
            content=contenido,
            media_type=content_type_servible(tipo_real),
            headers={"Cache-Control": "private, max-age=300"},
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": 'inline; filename="vista_previa.jpg"',
            "Cache-Control": "private, max-age=300",
        },
    )


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

    return ReprocessResponse(archivo=de_resumen(resultado))
