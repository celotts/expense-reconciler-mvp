import logging
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Response,
    UploadFile, status,
)
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from app.core.archivo_real import resolver_tipo
from app.core.subida import leer_ticket
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import ConfidenceSource, ExtractionStatus, SourceType, SpotCheckStatus
from app.core.time import dias_desde, utcnow
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.schemas.lectura import (
    DiagnosticoLecturaResponse,
    LecturaCamposResponse,
    PasoLecturaResponse,
    VeredictoLecturaResponse,
)
from app.schemas.ticket import (
    ClasificarLoteRequest, ClasificarLoteResponse,
    DocumentoHistorialResponse,
    ExactitudPorOrigenResponse, ReporteExactitudResponse, SpotCheckItemResponse,
    SpotCheckQueueResponse, SpotCheckRequest, TicketCreate, TicketResponse,
    TicketReviewQueueResponse, TicketReviewRequest, TicketUpdate,
)
from app.services.confidence_gate import gate_manual_ticket
from app.services.accuracy_service import (
    NIVEL_CONFIANZA, ORIGENES_A_REPORTAR, SLO_EXACTITUD, MedidaPorOrigen,
    Veredicto,
)
from app.services.capture import ExtractionUnavailable, capture_ticket
from app.services.lectura_diagnostico import (
    ResultadoLectura,
    diagnosticar_lectura,
)
from app.services.document_service import (
    documento_de_ticket, documentos_del_ticket, guardar_documento,
    reemplazar_documento,
)
from app.services.parser_service import TicketExtractionResult
from app.services.ai_extractor import ai_extractor
from app.services.inventario_service import registrar_compra
from app.services.ticket_persistence import persistir_extraccion

router = APIRouter(tags=["Tickets"])

# Cuando el `file_type` declarado no coincide con los bytes, el motivo va aqui.
# No es un 400: el documento es valido y se procesa igual, pero el log deja
# constancia de quien esta etiquetando mal sus subidas.
logger = logging.getLogger(__name__)


async def _extract_from_upload(
    content: bytes, file_type: str
) -> TicketExtractionResult:
    """Unico camino de captura. Toda la politica vive en `capture.capture_ticket`.

    Antes esta funcion decidia: si era imagen la mandaba a la IA, si no la
    parseaba con regex, y si la IA fallaba devolvia un 500. Eso hacia tres
    cosas malas a la vez. Perdia el documento (un 500 y el archivo se
    olvida). Mandaba a la IA los PDF que ya traian el texto. Y no decia de
    donde habia salido el dato, asi que un parseo de regex se guardaba como
    `llm`.

    Ahora la cascada esta en un solo sitio y la API solo traduce sus errores.

    El `file_type` que llega aqui ya no es el que declaro el cliente: lo
    dedujo `_tipo_real_del_archivo` de los bytes. Ver el docstring de ahi para
    por que. Esta funcion no vuelve a decidir nada sobre el formato.
    """
    try:
        return await capture_ticket(
            content,
            file_type,
            extract_from_image=ai_extractor.extract_from_image,
            extract_from_text=ai_extractor.extract_from_text,
        )
    except ExtractionUnavailable as exc:
        # El archivo ni se pudo abrir. Si hay una excepcion que registrar, es
        # porque el cliente mando algo que no es un comprobante; eso si es un
        # error del cliente y tiene que verse como tal.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo procesar el archivo: {exc}",
        )


def _a_diagnostico(resultado: ResultadoLectura) -> DiagnosticoLecturaResponse:
    """El dataclass del servicio al schema de la API, en un solo lugar.

    Vive aqui y no en el servicio porque el servicio no importa `schemas/`: la
    separacion de capas que `scan_service` respeta tambien ("el resumen lo arma como
    `dict` para no importar schemas; aqui es donde se valida").

    La forma de la respuesta si es un `model_validate` por campo, porque los
    dataclasses del servicio son planos y los schemas tienen sus defaults. Lo que no es
    trivial son los `Decimal`: viajan como texto dentro de `_resumen` (el paso), y aqui
    se pasan como `Decimal` de verdad al schema. Se declara `Decimal` y no `float` en
    `LecturaCamposResponse`, y por el mismo motivo que en el resto del proyecto: un
    `Decimal` a texto y de vuelta es exacto, mientras que un `float` de ida ya perdio
    centavos. Un total que saliera como `4094.8` en vez de `4094.80` haria dudar de si
    el sistema perdio un centimo o si es el JSON.
    """
    datos = None
    if resultado.extraccion is not None:
        e = resultado.extraccion
        datos = LecturaCamposResponse(
            proveedor=e.provider_name,
            rfc=e.provider_tax_id,
            total=e.total_amount,
            subtotal=e.subtotal,
            iva=e.tax_amount,
            ieps=e.ieps_amount,
            fecha=e.expense_date,
            categoria=e.category,
            confianza=e.confidence,
            origen=e.confidence_source,
            lineas=e.items,
            muestra_texto=(e.raw_text or "")[:400],
        )

    veredicto = None
    if resultado.veredicto_status is not None:
        veredicto = VeredictoLecturaResponse(
            status=resultado.veredicto_status,
            confidence=resultado.veredicto_confidence or 0.0,
            confidence_persisted=resultado.veredicto_confidence_persisted,
            confidence_source=resultado.veredicto_confidence_source
            or ConfidenceSource.LLM,
            reasons=resultado.veredicto_reasons,
            checks_pasados=resultado.checks_pasados,
            checks_fallidos=resultado.checks_fallidos,
        )

    return DiagnosticoLecturaResponse(
        formato_detectado=resultado.formato_detectado,
        formato_declarado=resultado.formato_declarado,
        formato_corregido=resultado.formato_corregido,
        pasos=[
            PasoLecturaResponse(
                escalon=p.escalon,
                motor=p.motor,
                dio=p.dio,
                aceptado=p.aceptado,
                motivo=p.motivo,
                costo=p.costo,
            )
            for p in resultado.pasos
        ],
        datos=datos,
        veredicto=veredicto,
        error=resultado.error,
        ticket_id=resultado.ticket_id,
        ticket_status_actual=resultado.ticket_status_actual,
        ticket_source_actual=resultado.ticket_source_actual,
        ticket_leido_en=resultado.ticket_leido_en,
        coincide_con_guardado=resultado.coincide_con_guardado,
        guardado=False,
    )


def _tipo_real_del_archivo(contenido: bytes, declarado: str | None) -> str:
    """Deduce el formato de los BYTES y descarta el que declaro el cliente.

    Por que no puede ser el declarado: `file_type` decide el escalon de la
    cascada, y con el la regla 3 de `AGENTS.md` ("un PDF con texto no toca el
    modelo") la salta quien llama. Medido: el mismo PDF con `file_type=image`
    entra por vision, sale con `confidence_source=llm` y total 0.00, y esa
    lectura contaminaria el reporte de exactitud, que agrupa por origen.

    Cuando los bytes y lo declarado no coinciden, gana el sniffing y se deja
    rastro en el log. No es un 400: el documento es valido, lo que estaba mal
    era la etiqueta, y rechazar el comprobante de un contador porque su
    cliente mando `image` en vez de `pdf` seria un fallo nuestro.
    """
    formato, motivo = resolver_tipo(contenido, declarado)
    if motivo:
        logger.warning("Tipo de archivo corregido: %s", motivo)
    return formato


@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    ticket_in: TicketCreate,
    db: AsyncSession = Depends(get_db)
) -> TicketModel:
    """Create a new ticket manually.

    Una persona tecleo los datos, asi que no hay confianza que medir, pero si
    pasan los mismos checks deterministas que la IA. Escribirlos a mano no
    exime de que el total sea positivo.
    """
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == ticket_in.company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe"
        )

    decision = gate_manual_ticket(
        provider_name=ticket_in.provider_name,
        total_amount=ticket_in.total_amount,
        tax_amount=ticket_in.tax_amount,
        expense_date=ticket_in.expense_date,
        provider_tax_id=ticket_in.provider_tax_id,
    )
    if not decision.validation.ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Datos inválidos: {decision.validation.as_text()}",
        )

    ticket = TicketModel(
        **ticket_in.model_dump(),
        extraction_status=decision.status.value,
        confidence_source=decision.confidence_source.value,
        confidence=decision.persisted_confidence,
        source_type=SourceType.MANUAL.value,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.post(
    "/extract-diagnostico",
    response_model=DiagnosticoLecturaResponse,
    tags=["Tickets"],
    summary="Lee un comprobante y explica COMO se leyo. No guarda nada.",
)
async def extract_diagnostico(
    file: UploadFile = File(...),
    file_type: str = Form("pdf"),
    db: AsyncSession = Depends(get_db),
    ticket_id: UUID | None = Form(
        None,
        description=(
            "Ticket ya guardado con el que contrastar. Responde 'esta bien leido y "
            "el problema es otro': si releer daria el mismo estado, el problema no "
            "es el lector."
        ),
    ),
) -> DiagnosticoLecturaResponse:
    """Que se leyo, por que se leyo asi, y que haria el gate. Sin escribir nada.

    **Por que existe.** `POST /tickets/extract` devuelve el resultado, y con eso no se
    puede depurar una lectura mala: si el comprobante cae a `REQUIERE_REVISION`, la
    respuesta dice que no se pudo leer el proveedor, y no dice si fue que el OCR no
    vio texto, que el parser no encontro el folio, o que el extractor de vision esta
    apagado. Esas tres piden arreglos distintos y son indistinguibles desde la
    respuesta. Un error que se queda en el log del servidor obliga a que alguien vaya a
    buscarlo, y si nadie busca, el mismo comprobante falla igual mañana.

    **No guarda nada.** Ni ticket, ni documento, ni compra, ni movimiento. Es el
    mismo criterio que `simular` en `POST /scan`: con `archivar` activo por omision la
    primera corrida deja la carpeta vacia, y conviene ver eso antes de que pase. Aqui
    la razon es mas fuerte, porque **repetir el mismo archivo muchas veces no llenaria
    la base de duplicados** — que es justo lo que pasa al probar con
    `extract-and-create`.

    Por eso no hay forma de guardar desde aqui: un endpoint que "a veces guarda" es un
    endpoint del que nadie sabe que hace. Para guardar, `POST /tickets/extract-and-create`
    o `POST /scan/files/{id}/reprocess`, que son explicitos.

    **El `file_type` no decide nada.** El formato sale de los bytes, como en el resto de
    la API (regla 3 de `AGENTS.md`), y si el cliente declaro otra cosa, el motivo sale
    en `formato_corregido`. Un PDF subido como `image` entra por vision y sale con otra
    confianza; sin ese campo el cliente se lleva un resultado distinto del que esperaba
    sin enterarse.

    **El veredicto es el del gate de verdad**, no una copia de sus reglas: se corre
    `gate_ticket` con la lectura y se devuelve lo que habria pasado. Un diagnostico que
    dijera "pasaria" con checks distintos a los del gate seria el fallo de "el sistema
    afirma mas de lo que sostiene", en el sitio donde mas dano hace.
    """
    content = await leer_ticket(file, file.filename or "")
    resultado = await diagnosticar_lectura(
        db,
        content,
        declarado=file_type or None,
        nombre=file.filename,
        ticket_id=ticket_id,
    )
    return _a_diagnostico(resultado)


@router.post("/extract", response_model=TicketExtractionResult)
async def extract_ticket(
    file: UploadFile = File(...),
    file_type: str = Form("pdf")
) -> TicketExtractionResult:
    """Extract ticket data from uploaded file (PDF/Image).

    `file_type` se acepta por compatibilidad, pero no decide nada: el formato
    sale de los bytes. Ver `_tipo_real_del_archivo`.
    """
    content = await leer_ticket(file, file.filename or "")
    tipo_real = _tipo_real_del_archivo(content, file_type)
    try:
        return await _extract_from_upload(content, tipo_real)
    except Exception:
        # El detalle de la excepcion va al LOG, no a la pantalla. Antes el
        # `detail` era f"Failed to extract ticket data: {e!s}": en ingles, y con
        # el texto crudo de la excepcion dentro de la respuesta.
        #
        # Eso ultimo es lo que importa. Un `ConnectionError` de Ollama o una
        # `KeyError` de un campo del modelo son internos: se leen bien en un
        # log con traceback, y en la pantalla son ruido que ademas hace parecer
        # que el fallo es del comprobante del usuario cuando no lo es. El log
        # lleva el traceback entero, asi que la depuracion no pierde nada.
        logger.exception("No se pudo extraer el ticket de %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo leer el comprobante. Revisa que el archivo no este dañado.",
        ) from None


@router.post("/extract-and-create", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def extract_and_create_ticket(
    file: UploadFile = File(...),
    company_id: UUID = Form(...),
    file_type: str = Form("pdf"),
    db: AsyncSession = Depends(get_db)
) -> TicketModel:
    """Extract ticket data from file and create ticket.

    Antes insertaba directo sin validar. Un total en 0 o un RFC malformado
    llegaban a la BD sin filtro. Ahora pasa por el gate: si los checks no
    cuadran, el ticket se guarda igual pero en estado pendiente de revision,
    nunca como aprobado.
    """
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe"
        )

    content = await leer_ticket(file, file.filename or "")
    tipo_real = _tipo_real_del_archivo(content, file_type)
    try:
        extracted = await _extract_from_upload(content, tipo_real)
    except HTTPException:
        # Un `HTTPException` de `_extract_from_upload` ya es un mensaje pensado
        # para el usuario (tipo de archivo no soportado, y asi). Se relanza tal
        # cual y NO entra al `except Exception` de abajo, que es lo que
        # mantienen estos dos bloques alineados.
        raise
    except Exception:
        # Ver el otro bloque: el traceback va al log y la pantalla recibe un
        # mensaje que dice que hacer.
        logger.exception("No se pudo extraer el ticket de %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo leer el comprobante. Revisa que el archivo no este dañado.",
        ) from None

    return await _persist_extracted(
        db, company_id, extracted, content,
        # El `source_type` tambien sale de los bytes, no de la etiqueta del
        # cliente. Antes, ademas, un `text` caia al fallback y se guardaba como
        # "image": `text` no existe en el enum `SourceType`
        # (`docs/known-issues.md` §11). El fallback se mantiene explicito para
        # no cambiar el esquema en este commit; lo que si cambia es que el
        # valor de entrada ya no lo elige el cliente.
        source_type=(
            SourceType(tipo_real)
            if tipo_real in {t.value for t in SourceType}
            else SourceType.IMAGE
        ),
        source_file=file.filename,
        content_type=file.content_type,
    )


async def _persist_extracted(
    db: AsyncSession,
    company_id: UUID,
    extracted: TicketExtractionResult,
    content: bytes,
    source_type: SourceType,
    source_file: str | None = None,
    content_type: str | None = None,
) -> TicketModel:
    """Gate + persistencia. Unico camino de entrada para datos extraidos.

    Delegacion a `app/services/ticket_persistence.py`. La implementacion se movio
    ahi porque el escaner de carpeta necesita la misma operacion y no puede
    importar de un router sin invertir la capa: lo que se acababa teniendo eran
    dos "crear ticket desde extraccion", una con el muestreo de exactitud y otra
    sin el.

    `content` es el archivo original y `extracted` lo que se entendio de el. Los
    dos se guardan, y son cosas distintas: el segundo sin el primero no se puede
    revisar ni auditar. Ver `app/services/document_service.py`.

    El nombre y la firma se conservan porque tres archivos de test la importan
    desde aqui y porque la ruta que ya existe tiene que seguir siendo la misma
    ruta. No hay dos caminos de persistencia: hay uno y dos nombres.
    """
    return await persistir_extraccion(
        db,
        company_id,
        extracted,
        content,
        source_type=source_type,
        source_file=source_file,
        content_type=content_type,
    )


@router.get("/", response_model=list[TicketResponse])
async def list_tickets(
    company_id: UUID | None = None,
    extraction_status: str | None = None,
    only_open: bool = False,
    sin_categoria: bool = False,
    con_categoria: bool = False,
    categoria: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[TicketModel]:
    """List tickets with optional company filter and review-state filter.

    `sin_categoria` y `con_categoria` son las dos caras de la misma pregunta, que
    es la que hace el tablero con la barra gris. Se ponen los tres filtros
    excluyentes de forma explicita en vez de confiar en el orden en que llegan:
    `categoria` manda sobre los otros dos (es el mas especifico), y si vienen
    `sin_categoria` y `con_categoria` a la vez se anulan, porque no hay conjunto
    que cumpla las dos, y devolver vacio seria un fallo silencioso.
    """
    query = select(TicketModel)
    if company_id:
        query = query.where(TicketModel.company_id == company_id)
    if only_open:
        query = query.where(TicketModel.extraction_status.in_([
            ExtractionStatus.REQUIERE_REVISION.value,
            ExtractionStatus.PENDIENTE.value,
        ]))
    elif extraction_status:
        query = query.where(TicketModel.extraction_status == extraction_status)

    if categoria:
        query = query.where(TicketModel.category == categoria)
    elif sin_categoria and con_categoria:
        # Las dos a la vez: no hay conjunto que cumpla las dos. Se anulan y se
        # devuelve la lista completa, que es lo honesto ("no me dijiste que
        # filtrara") en vez de devolver vacio ("no hay nada").
        pass
    elif sin_categoria:
        # Solo lo que esta en blanco de verdad. Un `category = ''` (cadena
        # vacia) se cuela si se filtra por `IS NULL` unicamente, y un ticket con
        # la cadena vacia se ve igual de "sin clasificar" que uno con `NULL`,
        # pero en la base son filas distintas.
        query = query.where(
            or_(
                TicketModel.category.is_(None),
                func.trim(TicketModel.category) == "",
            )
        )
    elif con_categoria:
        query = query.where(
            and_(
                TicketModel.category.is_not(None),
                func.trim(TicketModel.category) != "",
            )
        )

    query = query.offset(skip).limit(limit).order_by(TicketModel.expense_date.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/review-queue", response_model=TicketReviewQueueResponse)
async def get_review_queue(
    company_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> TicketReviewQueueResponse:
    """Cola agrupada de tickets pendientes de revision.

    Sin esto, los documentos que no se pudieron leer son invisibles: el usuario
    no tiene forma de saber que existen. Un error silencioso es peor que un
    error visible porque nadie lo va a corregir.

    Contrato de los conteos, para que la pantalla no sea ambigua:
      - `tickets`     viene filtrado por `status` si se paso
      - `por_estado`  y `total_open` son SIEMPRE globales

    Asi el badge del encabezado no salta de 40 a 3 al filtrar, y el usuario ve
    de entrada quantos hay de cada tipo. El filtro es para trabajar, no para
    perder de vista el tamano de la cola.
    """
    base = select(TicketModel)
    if company_id:
        base = base.where(TicketModel.company_id == company_id)

    open_states = [
        ExtractionStatus.REQUIERE_REVISION.value,
        ExtractionStatus.PENDIENTE.value,
    ]
    open_q = base.where(TicketModel.extraction_status.in_(open_states))
    if status:
        open_q = open_q.where(TicketModel.extraction_status == status)
    open_q = open_q.order_by(TicketModel.created_at.asc()).limit(limit)

    rows = (await db.execute(open_q)).scalars().all()

    counts_q = base.with_only_columns(
        TicketModel.extraction_status, func.count().label("n")
    ).group_by(TicketModel.extraction_status)
    counts = {row.extraction_status: row.n for row in (await db.execute(counts_q)).all()}

    # Antiguedad promedio de lo que se esta mostrando. Solo sobre filas con
    # fecha utilizable: created_at admite NULL y una fila sin fecha no puede
    # tener antiguedad, pero tampoco puede tumbar la pantalla.
    ahora = utcnow()
    ages = []
    for r in rows:
        creado = r.created_at
        if creado is None:
            continue
        if creado.tzinfo is None:
            # SQLite devuelve naive. Asumir UTC y no fallar.
            creado = creado.replace(tzinfo=timezone.utc)
        ages.append((ahora - creado).days)

    avg_age = round(sum(ages) / len(ages), 1) if ages else None

    return TicketReviewQueueResponse(
        company_id=company_id,
        total_open=sum(counts.get(s, 0) for s in open_states),
        por_estado=counts,
        antiguedad_promedio_dias=avg_age,
        tickets=[TicketResponse.model_validate(r) for r in rows],
    )


@router.patch("/categoria", response_model=ClasificarLoteResponse)
async def clasificar_lote(
    datos: ClasificarLoteRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> ClasificarLoteResponse:
    """Pone la misma categoria a varios tickets de una vez.

    **No cambia `extraction_status` ni toca la cola de revision.** Clasificar es
    un dato del gasto, no una validacion de la lectura: un ticket puede estar
    AUTO_APROBADO y no tener categoria, y clasificarlo no lo convierte en
    revisado ni al reves. Mezclar las dos cosas haria que "ya lo clasifique"
    pareciera una entrada mas en la cola, y el trabajo de verdad se perderia de
    vista.

    **Un `UPDATE` con `IN (...)`, no un bucle de PATCH.** Con 200 tickets son 200
    viajes de ida y vuelta, y cada uno abre su propia transaccion. Ademas un
    bucle que falla en el ticket 150 deja los 149 anteriores escritos y el 150 sin
    hacer, y el cliente no puede saber cuales. Con un solo `UPDATE` es todo o
    nada.

    `None` desclasifica: deja la categoria en NULL y el ticket vuelve a la barra
    gris. Es lo que permite corregir una clasificacion equivocada.
    """
    ids = list(dict.fromkeys(datos.ticket_ids))  # sin repetidos, y conservando orden

    resultado = await db.execute(
        update(TicketModel)
        .where(TicketModel.id.in_(ids))
        .values(category=datos.category)
        # `synchronize_session=False` porque no hay objetos de ticket cargados en
        # la sesion: sin esto SQLAlchemy intenta refrescarlos uno por uno y con
        # 500 filas eso es trabajo que no se usa para nada.
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    return ClasificarLoteResponse(actualizados=resultado.rowcount, pedidos=len(ids))


@router.patch("/{ticket_id}/review", response_model=TicketResponse)
async def review_ticket(
    ticket_id: UUID,
    review_in: TicketReviewRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> TicketModel:
    """Revision humana de un ticket en cola.

    Aprobar re-corre los checks deterministas sobre los datos finales. Si
    siguen rotos, no se aprueba: la revision es una opinion, los checks son
    una ley.
    """
    result = await db.execute(select(TicketModel).where(TicketModel.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El ticket no existe")

    # Correcciones aplicadas durante la revision.
    corrections = review_in.model_dump(
        exclude_unset=True,
        exclude={"action", "notes"},
    )
    for field, value in corrections.items():
        if value is not None:
            setattr(ticket, field, value)

    if review_in.action == "reject":
        ticket.extraction_status = ExtractionStatus.RECHAZADO.value
    elif review_in.action == "request_info":
        ticket.extraction_status = ExtractionStatus.REQUIERE_REVISION.value
    else:  # approve
        decision = gate_manual_ticket(
            provider_name=ticket.provider_name,
            total_amount=ticket.total_amount,
            tax_amount=ticket.tax_amount,
            expense_date=ticket.expense_date,
            provider_tax_id=ticket.provider_tax_id,
            # El subtotal se pasa porque el aprobador puede acabarlo de corregir, y
            # sin el `subtotal + IVA == total` no se comprueba. Medido: con el
            # total en 234.00 y el subtotal en 97.56 la aprobacion pasaba sin que
            # nadie notara que la aritmetica no cuadraba.
            subtotal=ticket.subtotal,
            # El IEPS tambien se pasa, y por la misma razon: es lo que hace que
            # un ticket con IVA e IEPS pueda aprobarse. Sin esta linea, una
            # persona tendria que haber puesto un `tax_amount` que NO es el IVA
            # para que la cuenta cuadrara, y entonces el contador recibiria un
            # IVA inventado. Ver db/migrations/0012_el_impuesto_es_de_la_partida.sql.
            ieps_amount=ticket.ieps_amount,
        )
        if not decision.validation.ok:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"No se puede aprobar: {decision.validation.as_text()}. "
                    "Corrige los datos e intenta de nuevo."
                ),
            )
        ticket.extraction_status = ExtractionStatus.APROBADO.value
        ticket.validation_errors = None

    ticket.review_notes = review_in.notes
    ticket.reviewed_at = utcnow()
    # La identidad sale del token, no de un texto fijo. Antes era la cadena
    # "user" en todas las revisiones, lo que hacia que la columna no dijera
    # nada: veinte revisiones de tres personas son indistinguibles. Con esto,
    # "quien rechazo esto" tiene respuesta, y es la misma que uso el token que
    # autorizo la peticion.
    ticket.reviewed_by = usuario.email

    await db.commit()
    await db.refresh(ticket)
    return ticket




# =====================================================================
# Muestreo de exactitud
# =====================================================================
#
# La cola de revision de arriba responde "que necesita una persona". Esta
# responde "el automatismo sirve". Son colas distintas y no se mezclan:
#
# - La de revision se vacia. Cada ticket que se corrige sale, y lo que sale
#   desaparece de la vista de pendientes.
# - La de muestreo no se vacia. Los tickets revisados se quedan para siempre
#   porque son la evidencia de la exactitud. Un ticket que sale de la muestra
#   es una medicion que se tira, y medir es lo unico que hace este modulo.
#
# Por eso tampoco corrigen el ticket. Una revision humana arregla un gasto; un
# muestreo solo dice si la extraccion coincidia con el papel. Si el muestreo
# corrigiera, la exactitud medida dependeria de quien reviso, y dejaria de
# medir el automatismo.


def _campos_incorrectos_de(row: TicketModel) -> list[str]:
    """Los campos anotados, de una columna de texto separada por comas."""
    if not row.spot_check_wrong_fields:
        return []
    return [c.strip() for c in row.spot_check_wrong_fields.split(",") if c.strip()]


@router.get("/spot-check", response_model=SpotCheckQueueResponse)
async def get_spot_check_queue(
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = None,
    status_filter: str | None = Query(
        None,
        alias="status",
        description=(
            "Sin valor: solo lo PENDIENTE, que es el trabajo. "
            "CORRECTO o INCORRECTO para ver el historico de veredictos."
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
) -> SpotCheckQueueResponse:
    """La muestra a revisar, y el conteo de lo ya verificado.

    La lista se filtra SIEMPRE por un estado de la muestra, y por defecto es el
    PENDIENTE. Ese unico filtro hace las dos cosas:

    - Saca los tickets que NO estan en la muestra. El 95% que no fue elegido
      tiene `spot_check_status` en NULL, y NULL no es igual a ningun estado, asi
      que no puede colarse. Sin esto el endpoint devolvia los 10 000 tickets de
      una empresa para que el revisor descartara a mano los que no tocan.

    - Saca lo ya revisado. Una cola de trabajo es trabajo: con 500 muestreados y
      400 ya hechos, mezclar los dos deja la primera pagina con 20 cosas que
      hacer entre 100 filas. Es el mismo criterio que usa la cola de revision,
      y por la misma razon.

    Antes habia dos filtros, `IS NOT NULL` y el de estado. El primero resulto
    ser redundante: quitarlo no cambia ninguna consulta, porque
    `status = 'PENDIENTE'` ya excluye los NULL. Se quito porque un filtro que
    no hace nada, con un comentario que afirma que es imprescindible, es peor
    que no tenerlo: el que lo lea creera que la garantia depende de ahi, y
    cambiara el otro sin avisar. La mutacion que borra el filtro de estado
    (`scripts/verify_spot_check_mutations.py`) es la que protege la garantia.

    Contrato de los conteos, para que la pantalla no sea ambigua:
      - `tickets` viene filtrado por estado
      - `total_pendientes`, `total_revisados`, `aciertos` e `incorrectos` son
        SIEMPRE globales

    Asi el encabezado no salta de 200 a 3 al filtrar, y se ve de entrada
    cuantos hay de cada clase. El filtro es para trabajar, no para perder de
    vista el tamano de la cola.
    """
    base = select(TicketModel)
    if company_id is not None:
        base = base.where(TicketModel.company_id == company_id)
    # Sin `status` explicito se trabaja lo pendiente. Un `status` explicito pide
    # ese estado, que es como se llega al historico de veredictos.
    base = base.where(
        TicketModel.spot_check_status == (status_filter or SpotCheckStatus.PENDIENTE.value)
    )

    rows = (await db.execute(
        base.order_by(TicketModel.created_at.asc()).limit(limit)
    )).scalars().all()

    # Los conteos se hacen con una consulta aparte y SIN filtro de estado: el
    # `total_pendientes` que se muestra arriba de la lista tiene que contar todo
    # lo pendiente, no solo lo que cabe en `limit`. Con 200 pendientes y
    # limit=50, la pantalla tiene que decir 200, no 50.
    async def _contar(estado: str) -> int:
        return await db.scalar(
            select(func.count()).select_from(TicketModel)
            .where(
                TicketModel.spot_check_status == estado,
                *([TicketModel.company_id == company_id] if company_id else []),
            )
        ) or 0

    pendientes = await _contar(SpotCheckStatus.PENDIENTE.value)
    aciertos = await _contar(SpotCheckStatus.CORRECTO.value)
    revisados = aciertos + await _contar(SpotCheckStatus.INCORRECTO.value)

    antiguedad = None
    if pendientes:
        mas_viejo = (await db.execute(
            select(func.min(TicketModel.created_at)).where(
                TicketModel.spot_check_status == SpotCheckStatus.PENDIENTE.value,
                *([TicketModel.company_id == company_id] if company_id else []),
            )
        )).scalar()
        if mas_viejo is not None:
            # `dias_desde` normaliza la zona. La columna esta declarada con
            # zona horaria, asi que Postgres devuelve el instante con tz y la
            # resta cruda funciona; pero SQLite no tiene tipos con zona y
            # entrega el valor naive, y ahi `utcnow() - mas_viejo` revienta con
            # TypeError. El sintoma era una pantalla en blanco al pedir la
            # cola, sin error de validacion y sin nada que apuntara al reloj:
            # el fallo estaba en el almacenamiento y se veía en la
            # presentacion. Ver `app/core/time.py`.
            antiguedad = dias_desde(mas_viejo)

    return SpotCheckQueueResponse(
        company_id=company_id,
        total_pendientes=pendientes,
        total_revisados=revisados,
        aciertos=aciertos,
        incorrectos=revisados - aciertos,
        antiguedad_promedio_dias=antiguedad,
        tickets=[
            SpotCheckItemResponse(
                ticket=TicketResponse.model_validate(row),
                spot_check_status=row.spot_check_status,
                spot_checked_at=row.spot_checked_at,
                spot_check_notes=row.spot_check_notes,
                spot_check_wrong_fields=_campos_incorrectos_de(row),
            )
            for row in rows
        ],
    )


@router.patch("/{ticket_id}/spot-check", response_model=SpotCheckItemResponse)
async def registrar_veredicto(
    ticket_id: UUID,
    payload: SpotCheckRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> SpotCheckItemResponse:
    """Registra si la extraccion coincidia con el papel. No modifica el ticket.

    Un ticket que no estaba en la muestra se puede registrar igual: el sistema
    no le cierra la puerta a quien ya lo reviso de rebote. Se acepta solo si el
    ticket es AUTO_APROBADO, que es la unica condicion bajo la que la respuesta
    mide algo. Un ticket en la cola no se puede "dar por correcto": su estado
    ya lo decidio el gate.
    """
    row = (await db.execute(
        select(TicketModel).where(TicketModel.id == ticket_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="El ticket no existe")

    if row.extraction_status != ExtractionStatus.AUTO_APROBADO.value:
        raise HTTPException(
            status_code=409,
            detail=(
                "solo se puede verificar lo que el sistema aprobo solo. Este "
                f"ticket esta en {row.extraction_status}, asi que su lectura ya "
                "la reviso una persona y no mide el automatismo"
            ),
        )

    if not payload.correct and not payload.campos_incorrectos:
        raise HTTPException(
            status_code=422,
            detail=(
                "marca el ticket como incorrecto pero no dice que campo fallo. "
                "Sin eso el reporte dice que hay error y no dice que arreglar, "
                "que es un dato que no sirve para nada"
            ),
        )

    row.spot_check_status = (
        SpotCheckStatus.CORRECTO.value if payload.correct
        else SpotCheckStatus.INCORRECTO.value
    )
    row.spot_checked_at = utcnow()
    # Quien lo registro, no "user". El reporte promedia veredictos de varias
    # personas: sin esto, "el sistema es 96% exacto" es un promedio sin dueno
    # y cuando sale mal no hay a quien preguntarle.
    row.spot_checked_by = usuario.email
    row.spot_check_notes = payload.notes
    row.spot_check_wrong_fields = (
        ",".join(payload.campos_incorrectos) if payload.campos_incorrectos else None
    )
    await db.commit()
    await db.refresh(row)

    return SpotCheckItemResponse(
        ticket=TicketResponse.model_validate(row),
        spot_check_status=row.spot_check_status,
        spot_checked_at=row.spot_checked_at,
        spot_check_notes=row.spot_check_notes,
        spot_check_wrong_fields=_campos_incorrectos_de(row),
    )


@router.get("/accuracy", response_model=ReporteExactitudResponse)
async def get_reporte_exactitud(
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = None,
) -> ReporteExactitudResponse:
    """Lo que la muestra sostiene sobre el objetivo de exactitud.

    El reporte no devuelve un porcentaje. Devuelve cuantos se revisaron, cuantos
    acertaron, y el intervalo que esos datos sostienen. Es menos vistoso y es la
    unica forma de que el numero signifique algo: con 25 revisiones, "96%" tiene
    un intervalo de [80%, 99%], y publicar el 96% sin el 80% seria afirmar una
    certeza que nadie midio.

    Y nunca promedia entre metodos de lectura. Un ticket leido con regex y uno
    leido con un modelo se reportan por separado, porque mezclarlos sube el
    promedio con el metodo que casi no falla y esconde al que falla.
    """
    condiciones = (
        [TicketModel.company_id == company_id] if company_id is not None else []
    )

    # Una sola consulta agrupada: el reporte lee toda la evidencia de la
    # empresa y la cuenta en memoria. Con miles de tickets revisados, trae solo
    # lo que el indice parcial ix_tickets_spot_check_report cubre.
    filas = (await db.execute(
        select(
            TicketModel.confidence_source,
            TicketModel.spot_check_status,
            func.count(),
        )
        .where(
            TicketModel.spot_check_status.is_not(None),
            *condiciones,
        )
        .group_by(TicketModel.confidence_source, TicketModel.spot_check_status)
    )).all()

    # Los campos que mas fallan, por origen. Es la parte que convierte una
    # estadistica en una lista de que arreglar.
    campos = (await db.execute(
        select(TicketModel.confidence_source, TicketModel.spot_check_wrong_fields)
        .where(
            TicketModel.spot_check_status == SpotCheckStatus.INCORRECTO.value,
            *condiciones,
        )
    )).all()

    medidas: dict[str, MedidaPorOrigen] = {}
    for origen in ORIGENES_A_REPORTAR:
        medidas[origen] = MedidaPorOrigen(origen=origen)
    for origen_extra in {f[0] for f in filas if f[0]}:
        medidas.setdefault(origen_extra, MedidaPorOrigen(origen=origen_extra))

    for origen, estado, cantidad in filas:
        medida = medidas.setdefault(origen or "desconocido", MedidaPorOrigen(origen=origen or "desconocido"))
        if estado == SpotCheckStatus.PENDIENTE.value:
            medida.pendientes += cantidad
        else:
            medida.revisados += cantidad
            if estado == SpotCheckStatus.CORRECTO.value:
                medida.aciertos += cantidad
            else:
                medida.incorrectos += cantidad

    for origen, anotados in campos:
        if not anotados:
            continue
        medida = medidas.setdefault(origen or "desconocido", MedidaPorOrigen(origen=origen or "desconocido"))
        for campo in anotados.split(","):
            campo = campo.strip()
            if campo:
                medida.campos_fallidos[campo] = medida.campos_fallidos.get(campo, 0) + 1

    por_origen = [
        ExactitudPorOrigenResponse(
            origen=m.origen,
            revisados=m.revisados,
            aciertos=m.aciertos,
            incorrectos=m.incorrectos,
            pendientes=m.pendientes,
            exactitud=m.exactitud,
            intervalo_inferior=(m.limites[0] if m.limites else None),
            intervalo_superior=(m.limites[1] if m.limites else None),
            veredicto=m.veredicto,
            motivo_faltante=m.faltantes.razon,
            total_revisiones_necesarias=m.faltantes.total_necesario,
            campo_mas_fallido=(m.campo_mas_fallido[0] if m.campo_mas_fallido else None),
            conteo_por_campo=m.campos_fallidos,
        )
        for m in medidas.values()
    ]

    return ReporteExactitudResponse(
        company_id=company_id,
        objetivo=SLO_EXACTITUD,
        nivel_confianza=NIVEL_CONFIANZA,
        veredicto_global=_veredicto_global(por_origen),
        explicacion=_explicacion_global(por_origen),
        por_origen=por_origen,
    )


def _veredicto_global(por_origen: list[ExactitudPorOrigenResponse]) -> str:
    """El PEOR veredicto, no el promedio.

    Un sistema con la via de PDF al 100% y la de vision al 90% no cumple el
    objetivo. Promediar las dos daria algo intermedio y esconderia justo la via
    que hay que arreglar, que es la que manda.
    """
    from app.services.accuracy_service import Veredicto

    veredictos = [m.veredicto for m in por_origen if m.revisados > 0]
    if not veredictos:
        return Veredicto.SIN_EVIDENCIA
    if Veredicto.NO_CUMPLE in veredictos:
        return Veredicto.NO_CUMPLE
    if Veredicto.CUMPLE in veredictos and Veredicto.INCONCLUYENTE not in veredictos:
        return Veredicto.CUMPLE
    return Veredicto.INCONCLUYENTE


def _explicacion_global(por_origen: list[ExactitudPorOrigenResponse]) -> str:
    """El veredicto en palabras, con la accion que corresponde.

    Un veredicto sin texto obliga a quien lo lee a buscar en otro lado de donde
    salio, y casi siempre termina creyendolo. Peor: "INCONCLUYENTE" sin mas se
    lee como "algo fallo", y no es lo mismo que "hace falta medir mas".
    """
    from app.services.accuracy_service import Motivo, Veredicto

    con_datos = [m for m in por_origen if m.revisados > 0]
    if not con_datos:
        return (
            "No hay ninguna revision de muestreo todavia, asi que no se puede "
            "afirmar nada sobre la exactitud. El objetivo sigue sin medirse, "
            "que es distinto de cumplirse."
        )

    if _veredicto_global(con_datos) == Veredicto.CUMPLE:
        return (
            "La evidencia alcanza para afirmar que el automatismo esta por "
            f"encima de {int(SLO_EXACTITUD * 100)}% en cada via de lectura."
        )

    if _veredicto_global(con_datos) == Veredicto.NO_CUMPLE:
        malos = [m for m in con_datos if m.veredicto == Veredicto.NO_CUMPLE]
        detalle = ", ".join(
            f"{m.origen} ({m.aciertos}/{m.revisados})" for m in malos
        )
        return (
            f"El automatismo esta por debajo de {int(SLO_EXACTITUD * 100)}% en: "
            f"{detalle}. Revisar mas no lo arregla: hay que corregir el extractor."
        )

    lineas = []
    for m in con_datos:
        if m.veredicto != Veredicto.INCONCLUYENTE:
            continue
        if m.motivo_faltante == Motivo.ACIERTO_EN_LA_LINEA:
            lineas.append(
                f"{m.origen}: el acierto medido ({m.aciertos}/{m.revisados}) esta "
                f"exactamente en {int(SLO_EXACTITUD * 100)}%. Ninguna cantidad de "
                "revisiones lo sube de ahi, porque el objetivo dice 'mas de' y "
                "el dato dice 'exactamente'. Hay que mejorar el extractor: "
                "midiendo mas, el numero se acerca al limite pero nunca lo pasa."
            )
        elif m.motivo_faltante == Motivo.ACIERTO_POR_DEBAJO:
            lineas.append(
                f"{m.origen}: el acierto medido ({m.aciertos}/{m.revisados}) ya "
                "esta por debajo del objetivo. Medir mas no lo arregla."
            )
        else:
            falta = m.total_revisiones_necesarias
            # Los pendientes van al texto, no solo al JSON. El reporte es de lo
            # que se muestra en pantalla y lo que se copia a un correo, y si
            # el texto no los menciona, el 96% de hoy se lee como el de
            # manana. Con 25 revisados y 40 sin tocar, el numero va a cambiar.
            con_pendientes = (
                f" Hay {m.pendientes} mas en la muestra sin revisar, asi que el "
                "numero puede moverse."
                if m.pendientes
                else ""
            )
            lineas.append(
                f"{m.origen}: faltan {falta} revisiones en total para poder "
                f"afirmarlo (llevas {m.revisados}).{con_pendientes}"
                if falta
                else f"{m.origen}: falta evidencia para poder afirmar nada."
            )
    return " ".join(lineas)


# =====================================================================
# El documento original
# =====================================================================
#
# Sin esto, las dos pantallas que existen para revisar un documento --
# la cola de revision y el muestreo de exactitud -- dicen "contrasta contra el
# documento original" sin que haya documento. Ver el docstring de
# `app/models/ticket_document.py` para que se rompia y por que esto lo arregla.


@router.get("/{ticket_id}/documento", response_class=Response)
async def get_documento(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Los bytes del comprobante, tal como los subio la persona.

    Es lo que hace posible el muestreo: la pregunta "¿la extraccion coincidio con
    el papel?" solo tiene respuesta si hay papel. Y es lo que hace auditable un
    veredicto meses despues, cuando el papel ya no esta en la carpeta de
    quien lo subio.

    El `Content-Type` sale de una lista cerrada y no de lo que declaro el
    cliente. Ver `app.models.ticket_document.content_type_servible`: servir
    `text/html` con bytes sin revisar seria XSS desde el propio dominio, con
    sesion iniciada, disparado por alguien de confianza de la empresa.
    """
    # El ticket primero. Sin el, el `404` seria indistinguible del "no hay
    # documento", y quien llama no podria saber si se equivoco en el id o si el
    # ticket es de captura manual.
    ticket = (await db.execute(
        select(TicketModel).where(TicketModel.id == ticket_id)
    )).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El ticket no existe"
        )

    documento = await documento_de_ticket(db, ticket_id)
    if documento is None:
        # 404 y no 204. Un `204` parece un exito, y el cliente lo trata como
        # "no hay nada que ver", que es exactamente el bug que se quiere evitar.
        # El mensaje distingue las dos causas, que piden cosas distintas: un
        # ticket tecleado a mano no tiene documento y no deberia, uno de una
        # captura antigua si deberia y se puede reintentar.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Este ticket no tiene documento guardado. "
                + (
                    "Es de captura manual, asi que no hay ningun archivo que revisar."
                    if ticket.source_type == SourceType.MANUAL.value
                    else "Vuelve a subir el comprobante para poder revisarlo."
                )
            ),
        )

    return Response(
        content=documento.contenido,
        media_type=documento.content_type_servible,
        headers={
            # `inline` y no `attachment`: el objetivo es que el revisor pueda
            # VER el papel, que es lo que hace el muestreo posible. Con
            # `attachment` tendria que descargarlo, abrirlo en otro programa, y
            # volver: en un celular, en una pantalla de la cola, es el camino que
            # hace que la gente termine revisando a ciegas.
            "Content-Disposition": (
                f'inline; filename="{_nombre_seguro(documento.nombre_archivo)}"'
            ),
            # El nombre que el navegador propone al guardar. Va aparte porque
            # `filename*` es la forma estandar y la que entiende el rango de
            # caracteres no ASCII, que es justo el caso de un archivo con
            # acentos.
            "Access-Control-Expose-Headers": "Content-Disposition",
            # Cache privado y en el navegador, nunca en un proxy compartido: el
            # documento es un comprobante fiscal de una empresa concreta.
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get(
    "/{ticket_id}/documentos",
    response_model=list[DocumentoHistorialResponse],
    tags=["Tickets"],
    summary="Historial de versiones del comprobante",
)
async def listar_documentos(
    ticket_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentoHistorialResponse]:
    """La cadena de papeles de un ticket, de la mas antigua a la vigente.

    No devuelve bytes, sino el recorrido: que version hay, quien la puso, por
    que, y cuando. Sin esto, un cambio de comprobante queda escrito en la base
    pero invisible para quien opera el sistema, que es el mismo problema que
    hacia peligroso al reemplazo silencioso.

    Es tambien lo que responde "¿este gasto se puede auditar?". Si hay una sola
    version, el papel es el que subio el escaner. Si hay mas de una, la cadena
    dice quien lo cambio y por que, y el hash de cada version permite verificar
    los bytes que quedaron.
    """
    existe = (
        await db.execute(
            select(TicketModel.id).where(TicketModel.id == ticket_id)
        )
    ).scalar_one_or_none()
    if existe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El ticket no existe"
        )

    documentos = await documentos_del_ticket(db, ticket_id)
    return [
        DocumentoHistorialResponse(
            id=documento.id,
            version=documento.version,
            sha256=documento.sha256,
            tamano=documento.tamano,
            content_type=documento.content_type,
            nombre_archivo=documento.nombre_archivo,
            actor=documento.actor,
            motivo=documento.motivo,
            created_at=documento.created_at,
            # Vigente = el ultimo de la cadena. Con `reemplaza_a IS NULL` solo
            # la version 1 daria True, que es exactamente la que no se descarga.
            vigente=documento is documentos[-1],
        )
        for documento in documentos
    ]


@router.put("/{ticket_id}/documento", response_model=TicketResponse)
async def put_documento(
    ticket_id: UUID,
    # `usuario` va antes que `file` porque en Python un parametro sin default no
    # puede ir despues de uno que lo tiene, y `File(...)` cuenta como default.
    usuario: UsuarioActual,
    file: UploadFile = File(...),
    motivo: str = Form(
        ...,
        description=(
            "Por que se cambia el papel. Queda escrito en la version nueva y es "
            "obligatorio: es la pregunta que un contador hace despues."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> TicketModel:
    """AGREGA una version nueva del documento. La anterior no se borra.

    Es lo que hace recuperable un documento que no se pudo leer. Un PDF que no
    llego a leerse porque el extractor estaba apagado queda ilegible para
    siempre aunque manana se encienda: los bytes se habian perdido con el
    `request`. Con este endpoint, el mismo archivo se vuelve a subir sobre el
    ticket que ya existe y el documento queda disponible para que alguien lo
    revise o para que se reextraiga.

    NO REEMPLAZA: APENDA. Antes este endpoint hacia `DELETE` + `INSERT` y los
    bytes originales desaparecian sin dejar ni hash, ni autor, ni fecha. Ahora
    la tabla es una cadena (`db/migrations/0009_documento_inmutable.sql`): el
    documento vigente es el que no fue reemplazado, el anterior queda apuntado,
    y un trigger de Postgres prohibe `UPDATE` y `DELETE` mientras el ticket
    exista. El comprobante es la evidencia contra la que se contrasta cualquier
    lectura, y una evidencia que se borra en silencio no es evidencia.

    Por eso `motivo` es obligatorio y por eso se guarda `usuario.email`. Sin
    ellos la cadena diria que algo paso sin decir quien ni por que, que es
    exactamente el silencio que esta regla viene a cerrar. La base tambien los
    exige, asi que un INSERT directo por SQL tampoco puede saltarselos.

    No crea un ticket nuevo ni cambia los datos que ya se extrajeron. Reextraer
    es otra operacion y otra decision: la lectura guardada es la que se sometio
    a muestreo, y cambiarla en silencio haria que el veredicto registrado no
    correspondiera a lo que el sistema leyo.

    El `PUT` y no el `POST` porque el recurso es el documento de ESE ticket, y
    hay exactamente uno vigente: es un cambio de papel, no un agregado. El
    historial se consulta en `GET /tickets/{id}/documentos`.
    """
    ticket = (await db.execute(
        select(TicketModel).where(TicketModel.id == ticket_id)
    )).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El ticket no existe"
        )

    # El mismo lector con el mismo tope que la subida original. No se reusa la
    # constante aqui: si el tope de la subida subiera y este no, la diferencia
    # seria un endpoint que acepta archivos que el otro rechaza, y el que
    # fallaria seria el que no parece tener limite.
    contenido = await leer_ticket(file, file.filename or "")

    guardado = await reemplazar_documento(
        db, ticket_id, contenido,
        actor=usuario.email,
        motivo=motivo,
        content_type=file.content_type,
        nombre_archivo=file.filename,
    )
    if not guardado:
        # El servicio devuelve `False` por dos motivos que NO son el mismo
        # problema, y responder 422 a los dos le diria a la persona que su archivo
        # estaba mal cuando lo que paso es que el ticket ya habia sido revisado.
        # El 409 es el que corresponde a un conflicto de estado, no a un archivo
        # invalido.
        if ticket.reviewed_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Este ticket ya fue revisado por una persona y su documento "
                    "no se cambia por esta via. Reemplazar el papel invalidaria "
                    "esa revision. Usa el endpoint de reproceso, que es "
                    "explicito."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No se pudo guardar el documento. El ticket sigue como estaba, "
                "con los datos que ya tenia."
            ),
        )

    await db.commit()
    await db.refresh(ticket)
    return ticket


def _nombre_seguro(nombre: str | None) -> str:
    """El nombre del archivo, sin caracteres que rompan la cabecera HTTP.

    Va dentro de `Content-Disposition`, que es una cabecera de texto: un nombre
    con comillas, salto de linea o backslash la rompe, y con una respuesta mal
    formada el navegador muestra un error en vez del documento. Se sustituye
    en vez de rechazar: el nombre es informacion, el documento es el dato, y
    perder el documento por un caracter raro en el nombre seria disparar a lo
    que no importa.
    """
    if not nombre:
        return "comprobante"
    limpio = "".join(
        c if c.isprintable() and c not in '"\\' else "_" for c in nombre
    ).strip()
    return limpio[:200] or "comprobante"


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> TicketModel:
    """Get a ticket by ID."""
    result = await db.execute(select(TicketModel).where(TicketModel.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El ticket no existe"
        )
    return ticket


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: UUID,
    ticket_in: TicketUpdate,
    db: AsyncSession = Depends(get_db)
) -> TicketModel:
    """Update a ticket."""
    result = await db.execute(select(TicketModel).where(TicketModel.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El ticket no existe"
        )

    update_data = ticket_in.model_dump(exclude_unset=True)
    # `items` se aparta antes del bucle: sus elementos son `LineaTicketUpdate`, y
    # un `model_dump` los deja como diccionarios, que es justo lo que espera la
    # columna JSON. Setearlo como objeto y dejar que lo caste el driver seria
    # escribir `[{'LineaTicketUpdate': ...}]`.
    lineas = update_data.pop("items", None)
    for field, value in update_data.items():
        setattr(ticket, field, value)

    if lineas is not None:
        # El `JSONConDecimal` de la columna convierte los importes a texto al
        # guardar; `_sin_decimal` es ese mismo paso y se hace aqui para que el
        # objeto que se devuelve en la respuesta sea el mismo que quedo guardado.
        # Sin esto, la respuesta y la fila discreparían en el formato del importe.
        lineas = [{k: str(v) if isinstance(v, Decimal) else v for k, v in l.items()} for l in lineas]
        ticket.items = lineas

    # Un ticket en cola se revalida con los datos nuevos. Si ahora ya cuadran,
    # sale de la cola: dejarlo pending cuando ya no necesita nada es como se
    # llena la cola de cosas que nadie revisa y luego nadie se cree.
    if ticket.extraction_status in (
        ExtractionStatus.PENDIENTE.value,
        ExtractionStatus.REQUIERE_REVISION.value,
    ):
        decision = gate_manual_ticket(
            provider_name=ticket.provider_name,
            total_amount=ticket.total_amount,
            tax_amount=ticket.tax_amount,
            expense_date=ticket.expense_date,
            provider_tax_id=ticket.provider_tax_id,
            # El subtotal se pasa porque la correccion puede acabarlo de traer, y
            # sin el `subtotal + IVA == total` no se comprueba. Medido: con el
            # total corregido a 234.00 y el subtotal en 97.56, la correccion pasaba
            # a APROBADO sin que nadie notara que la aritmetica no cuadraba.
            subtotal=ticket.subtotal,
            # El IEPS tambien se pasa, y por la misma razon: es lo que hace que
            # un ticket con IVA e IEPS pueda aprobarse. Sin esta linea, una
            # persona tendria que haber puesto un `tax_amount` que NO es el IVA
            # para que la cuenta cuadrara, y entonces el contador recibiria un
            # IVA inventado. Ver db/migrations/0012_el_impuesto_es_de_la_partida.sql.
            ieps_amount=ticket.ieps_amount,
        )
        if not decision.validation.ok:
            ticket.extraction_status = ExtractionStatus.REQUIERE_REVISION.value
            ticket.validation_errors = decision.validation.as_text()
        else:
            # Una persona corrigio los datos: eso es una decision humana, y los
            # checks ya pasaron. Queda APROBADO, no pendiente de nada.
            ticket.extraction_status = ExtractionStatus.APROBADO.value
            ticket.confidence = decision.persisted_confidence
            ticket.confidence_source = decision.confidence_source.value
            ticket.validation_errors = None

    await db.commit()

    # La compra se (re)intenta SOLO si el ticket quedo con lineas.
    #
    # Va despues del commit del ticket y no antes, por una razon que es de este
    # endpoint y no de `registrar_compra`: si la compra fallara, el ticket
    # corregido tiene que estar guardado igual. Perder la correccion de una
    # persona por un problema de inventario es peor que tener un gasto sin compra
    # asociada, que ademas se ve en la cola de inventario.
    #
    # Y va porque `items` acaba de cambiar y con el: antes, un ticket de OCR con
    # `items=NULL` no podia tener compra, porque no habia forma de cargar las
    # lineas. Ahora si, y esta llamada es la que abre el inventario por foto.
    #
    # Idempotente por `compras.ticket_id` UNIQUE: una segunda correccion devuelve
    # la compra existente en vez de reventar con IntegrityError.
    if lineas is not None and lineas:
        compra = await registrar_compra(db, ticket)
        if compra is not None:
            logger.info(
                "Ticket %s abrio la compra %s en %s al corregirlo a mano.",
                ticket.id, compra.id, compra.estado,
            )

    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a ticket."""
    result = await db.execute(select(TicketModel).where(TicketModel.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El ticket no existe"
        )
    
    await db.delete(ticket)
    await db.commit()