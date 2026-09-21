from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.ticket import TicketModel
from app.models.company import CompanyModel
from app.schemas.ticket import TicketCreate, TicketUpdate, TicketResponse
from app.services.parser_service import extract_ticket_data, TicketExtractionResult

router = APIRouter(tags=["Tickets"])


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
        result = extract_ticket_data(content, file_type=file_type)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract ticket data: {str(e)}"
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
        extracted = extract_ticket_data(content, file_type=file_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract ticket data: {str(e)}"
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


@router.get("/", response_model=List[TicketResponse])
async def list_tickets(
    company_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
) -> List[TicketModel]:
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