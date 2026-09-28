from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import UNKNOWN_PROVIDER, ExtractionStatus, SourceType
from app.core.time import utcnow
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.schemas.ticket import (
    TicketCreate, TicketResponse, TicketReviewQueueResponse, TicketReviewRequest, TicketUpdate,
)
from app.services.confidence_gate import (
    compute_source_hash, gate_manual_ticket, gate_ticket,
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