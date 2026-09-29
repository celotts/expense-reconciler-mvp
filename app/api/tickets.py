from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Response,
    UploadFile, status,
)
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.subida import leer_ticket
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import (
    UNKNOWN_PROVIDER, ExtractionStatus, SourceType, SpotCheckStatus,
)
from app.core.time import dias_desde, utcnow
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.schemas.ticket import (
    ClasificarLoteRequest, ClasificarLoteResponse,
    ExactitudPorOrigenResponse, ReporteExactitudResponse, SpotCheckItemResponse,
    SpotCheckQueueResponse, SpotCheckRequest, TicketCreate, TicketResponse,
    TicketReviewQueueResponse, TicketReviewRequest, TicketUpdate,
)
from app.services.confidence_gate import (
    compute_source_hash, gate_manual_ticket, gate_ticket,
)
from app.services.accuracy_service import (
    NIVEL_CONFIANZA, ORIGENES_A_REPORTAR, SLO_EXACTITUD, MedidaPorOrigen,
    Veredicto, en_muestra,
)
from app.services.capture import ExtractionUnavailable, capture_ticket
from app.services.document_service import (
    documento_de_ticket, guardar_documento, reemplazar_documento,
)
from app.services.parser_service import TicketExtractionResult
from app.services.ai_extractor import ai_extractor

router = APIRouter(tags=["Tickets"])


async def _extract_from_upload(content: bytes, file_type: str) -> TicketExtractionResult:
    """Unico camino de captura. Toda la politica vive en `capture.capture_ticket`.

    Antes esta funcion decidia: si era imagen la mandaba a la IA, si no la
    parseaba con regex, y si la IA fallaba devolvia un 500. Eso hacia tres
    cosas malas a la vez. Perdia el documento (un 500 y el archivo se
    olvida). Mandaba a la IA los PDF que ya traian el texto. Y no decia de
    donde habia salido el dato, asi que un parseo de regex se guardaba como
    `llm`.

    Ahora la cascada esta en un solo sitio y la API solo traduce sus errores.
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
            detail="Company not found"
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
            detail=f"Datos invalidos: {decision.validation.as_text()}",
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


@router.post("/extract", response_model=TicketExtractionResult)
async def extract_ticket(
    file: UploadFile = File(...),
    file_type: str = Form("pdf")
) -> TicketExtractionResult:
    """Extract ticket data from uploaded file (PDF/Image)."""
    content = await leer_ticket(file, file.filename or "")
    try:
        return await _extract_from_upload(content, file_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract ticket data: {e!s}",
        )


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
            detail="Company not found"
        )

    content = await leer_ticket(file, file.filename or "")
    try:
        extracted = await _extract_from_upload(content, file_type)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract ticket data: {e!s}",
        )

    return await _persist_extracted(
        db, company_id, extracted, content,
        source_type=SourceType(file_type) if file_type in {t.value for t in SourceType} else SourceType.IMAGE,
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

    `content` es el archivo original y `extracted` lo que se entendio de el. Los
    dos se guardan, y son cosas distintas: el segundo sin el primero no se puede
    revisar ni auditar. Ver `app/services/document_service.py`.
    """
    # El origen lo decide la ruta de captura, no esta funcion. Antes todo se
    # guardaba como `llm` porque el gate recibia su default, y con eso la
    # columna `confidence_source` miente sobre de donde salio el dato: un PDF
    # leido con regex aparecia como lectura de modelo. Sin esa verdad no hay
    # forma de medir la exactitud de la IA, porque se promedia la IA con un
    # regex que casi nunca falla.
    decision = gate_ticket(
        provider_name=extracted.provider_name,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        # Un documento sin fecha no se fecha con la de hoy: se guarda con la
        # fecha que se va a resolver en la cola. Fabricar la de hoy esconde un
        # gasto de marzo en el cierre de septiembre, que es el error que este
        # producto no puede cometer.
        expense_date=extracted.expense_date,
        provider_tax_id=extracted.provider_tax_id,
        subtotal=extracted.subtotal,
        confidence=extracted.confidence,
        source=extracted.confidence_source,
    )
    source_hash = compute_source_hash(content)

    # Idempotencia de carga masiva: el mismo archivo no crea dos tickets.
    #
    # El filtro por `company_id` no es un refinamiento, es la garantia de que
    # el hash significa algo. `source_hash` es el SHA-256 del archivo y no lleva
    # empresa dentro, asi que el mismo comprobante subido por dos empresas es
    # el mismo hash. Buscando solo por hash, la segunda empresa se encontraba
    # con el ticket de la primera y lo devolvia: el gasto no se registraba para
    # ella y ademas le mostraba un ticket ajeno, de otra empresa. El indice
    # unico global (ix_tickets_source_hash) convertia ademas el caso contrario -
    # dos empresas con el mismo archivo, que es legitimo - en un IntegrityError.
    # Por eso la unica columna del indice ahora es `(company_id, source_hash)`.
    existing = await db.execute(
        select(TicketModel).where(
            TicketModel.source_hash == source_hash,
            TicketModel.company_id == company_id,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup is not None:
        return dup

    ticket = TicketModel(
        company_id=company_id,
        provider_name=extracted.provider_name or UNKNOWN_PROVIDER,
        provider_tax_id=extracted.provider_tax_id,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        # Se guarda, aunque antes no se guardara. Es el campo con el que el
        # gate valido que `subtotal + IVA == total`, asi que sin el no se puede
        # auditar por que el ticket se aprobo. Va como None cuando el
        # documento no trae subtotal: un 0 seria un dato falso con apariencia
        # de dato, y haria que la cuenta pareciera cuadrar sin comprobar nada.
        subtotal=extracted.subtotal,
        category=extracted.category,
        raw_text=extracted.raw_text,
        confidence=decision.persisted_confidence,
        confidence_source=decision.confidence_source.value,
        extraction_status=decision.status.value,
        source_type=source_type.value,
        source_file=source_file,
        source_hash=source_hash,
        # `expense_date` es NOT NULL, asi que un documento sin fecha necesita
        # un valor. Se guarda el dia local en que se registro, que es la unica
        # fecha que si se sabe de cierto, y el ticket queda en la cola con
        # `date_missing` a la vista. El tradeoff: un ticket fechado
        # provisionalmente, marcado como pendiente de fecha. El que se
        # descartaba antes era el otro: un ticket con fecha y sin nada que
        # advertise que la fecha es inventada, que es indistinguible de un
        # gasto real de ese dia.
        #
        # `date.today()` y no `utcnow().date()`. No es indistinto: `utcnow()` es
        # lo correcto para `created_at`, que es un instante, y lo equivocado
        # aqui, que es una fecha de negocio. Un servidor en UTC-6 que corre a
        # las 20:00 del dia 27 ya esta en el dia 28 en UTC, y el ticket
        # quedaria fechado manana. Eso es el mismo error que se esta
        # corrigiendo aqui, con un dia de diferencia.
        expense_date=extracted.expense_date or date.today(),
        validation_errors=decision.validation.as_text(),
        # Muestreo de exactitud: se marca al insertar, por hash de contenido, y
        # solo si el gate aprobo solo. Tres condiciones, y cada una importa:
        #
        # - Solo AUTO_APROBADO. Un ticket que esta en la cola no se puede medir:
        #   su exactitud ya la decidio el gate, no el extractor. Y meterlo
        #   inflaria el promedio con casos que nunca fueron automaticos.
        # - Solo si tiene hash. La captura manual no es automatismo.
        # - La decision va por el contenido, no al azar, para que sea
        #   reproducible y no se pueda elegir la muestra a conveniencia.
        spot_check_status=(
            SpotCheckStatus.PENDIENTE.value
            if decision.status == ExtractionStatus.AUTO_APROBADO
            and en_muestra(source_hash)
            else None
        ),
    )
    db.add(ticket)
    await db.flush()

    # El documento va antes del commit, en la misma transaccion.
    #
    # El orden importa y no es cosmetico. Un `commit` aqui seguido de un guardado
    # del documento dejaria una ventana en la que el ticket existe y el papel
    # no, y en esa ventana un ticket de la cola aparece sin nada que revisar sin
    # que se pueda distinguir de "nunca se subio". Con el guardado antes, el
    # ticket llega con su documento o no llega ninguno de los dos.
    #
    # Y si el guardado falla, el ticket NO se pierde: `guardar_documento` usa un
    # savepoint y devuelve False. Perder un gasto que se leyo bien por un
    # problema de almacenamiento seria peor que un gasto sin comprobante
    # adjunto, que ademas queda visible.
    await guardar_documento(
        db, ticket, content,
        content_type=content_type,
        nombre_archivo=source_file,
    )

    await db.commit()
    await db.refresh(ticket)
    return ticket


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

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
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

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
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
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


@router.put("/{ticket_id}/documento", response_model=TicketResponse)
async def put_documento(
    ticket_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> TicketModel:
    """Reemplaza el documento de un ticket que ya existe.

    Es lo que hace recuperable un documento que no se pudo leer. Un PDF que no
    llego a leerse porque el extractor estaba apagado queda ilegible para
    siempre aunque manana se encienda: los bytes se habian perdido con el
    `request`. Con este endpoint, el mismo archivo se vuelve a subir sobre el
    ticket que ya existe y el documento queda disponible para que alguien lo
    revise o para que se reextraiga.

    No crea un ticket nuevo ni cambia los datos que ya se extrajeron. Reextraer
    es otra operacion y otra decision: la lectura guardada es la que se sometio
    a muestreo, y cambiarla en silencio haria que el veredicto registrado no
    correspondiera a lo que el sistema leyo.

    El `PUT` y no el `POST` porque el recurso es el documento de ESE ticket y
    queda exactamente uno: es un reemplazo, no un agregado. Con `POST` el
    cliente no tendria forma de saber si el segundo archivo se sumo o se
    sustituyo, y de las dos respuestas solo una es la que se quiere.
    """
    ticket = (await db.execute(
        select(TicketModel).where(TicketModel.id == ticket_id)
    )).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )

    # El mismo lector con el mismo tope que la subida original. No se reusa la
    # constante aqui: si el tope de la subida subiera y este no, la diferencia
    # seria un endpoint que acepta archivos que el otro rechaza, y el que
    # fallaria seria el que no parece tener limite.
    contenido = await leer_ticket(file, file.filename or "")

    guardado = await reemplazar_documento(
        db, ticket_id, contenido,
        content_type=file.content_type,
        nombre_archivo=file.filename,
    )
    if not guardado:
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
            detail="Ticket not found"
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
            detail="Ticket not found"
        )

    update_data = ticket_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ticket, field, value)

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
            detail="Ticket not found"
        )
    
    await db.delete(ticket)
    await db.commit()