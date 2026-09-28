from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import (
    UNKNOWN_PROVIDER, ExtractionStatus, SourceType, SpotCheckStatus,
)
from app.core.time import dias_desde, utcnow
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.schemas.ticket import (
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
    content = await file.read()
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

    content = await file.read()
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
    )


async def _persist_extracted(
    db: AsyncSession,
    company_id: UUID,
    extracted: TicketExtractionResult,
    content: bytes,
    source_type: SourceType,
    source_file: str | None = None,
) -> TicketModel:
    """Gate + persistencia. Unico camino de entrada para datos extraidos."""
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
    existing = await db.execute(
        select(TicketModel).where(TicketModel.source_hash == source_hash)
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
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get("/", response_model=list[TicketResponse])
async def list_tickets(
    company_id: UUID | None = None,
    extraction_status: str | None = None,
    only_open: bool = False,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[TicketModel]:
    """List tickets with optional company filter and review-state filter."""
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


@router.patch("/{ticket_id}/review", response_model=TicketResponse)
async def review_ticket(
    ticket_id: UUID,
    review_in: TicketReviewRequest,
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
    ticket.reviewed_at = datetime.now(timezone.utc)
    ticket.reviewed_by = "user"  # TODO: inyectar identidad del usuario autenticado

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