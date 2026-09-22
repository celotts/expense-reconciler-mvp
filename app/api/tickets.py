from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.schemas.ticket import TicketCreate, TicketResponse, TicketUpdate
from app.services.parser_service import TicketExtractionResult, extract_ticket_data
from app.services.ai_extractor import ExtractedInvoice, ai_extractor

router = APIRouter(tags=["Tickets"])


def _extracted_invoice_to_result(extracted: ExtractedInvoice) -> TicketExtractionResult:
    """Map AI-extracted invoice to the API result schema."""
    return TicketExtractionResult(
        provider_name=extracted.provider_name,
        provider_tax_id=extracted.provider_tax_id,
        total_amount=extracted.total if extracted.total is not None else Decimal("0.00"),
        tax_amount=extracted.tax_amount if extracted.tax_amount is not None else Decimal("0.00"),
        expense_date=extracted.invoice_date or date.today(),
        category=None,
        raw_text=extracted.raw_text,
    )


async def _extract_from_upload(content: bytes, file_type: str) -> TicketExtractionResult:
    """Extract ticket data, routing images through the AI vision extractor."""
    if file_type == "image":
        try:
            extracted = await ai_extractor.extract_from_image(content, mime_type="image/jpeg")
            result = _extracted_invoice_to_result(extracted)
            if extracted.provider_name == "ERROR_PARSING" or result.total_amount == Decimal("0.00"):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "AI image extraction failed after retries: the model returned invalid JSON. "
                        f"Check API/Ollama logs. Raw response: {extracted.raw_text[:500]}"
                    ),
                )
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"AI image extraction failed: {e!s}",
            )
    return extract_ticket_data(content, file_type=file_type)


@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    ticket_in: TicketCreate,
    db: AsyncSession = Depends(get_db)
) -> TicketModel:
    """Create a new ticket manually."""
    company_result = await db.execute(
        select(CompanyModel).where(CompanyModel.id == ticket_in.company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    ticket = TicketModel(**ticket_in.model_dump())
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
    """Extract ticket data from file and create ticket in database."""
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
    
    ticket = TicketModel(
        company_id=company_id,
        provider_name=extracted.provider_name,
        provider_tax_id=extracted.provider_tax_id,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        expense_date=extracted.expense_date,
        category=extracted.category,
        raw_text=extracted.raw_text
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get("/", response_model=list[TicketResponse])
async def list_tickets(
    company_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[TicketModel]:
    """List tickets with optional company filter."""
    query = select(TicketModel)
    if company_id:
        query = query.where(TicketModel.company_id == company_id)
    query = query.offset(skip).limit(limit).order_by(TicketModel.expense_date.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


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