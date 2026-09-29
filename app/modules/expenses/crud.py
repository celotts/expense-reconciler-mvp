"""CRUD operations for expenses batch processing"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ticket import TicketModel
from app.core.enums import ExtractionStatus
from .schemas import BatchUploadRequest, JobStatusResponse


class ExpenseJobModel:
    """In-memory job tracking (replace with DB table if needed)"""
    _jobs: dict[int, dict] = {}
    _counter = 0

    @classmethod
    def create(cls, folder_path: str, options: dict = None) -> int:
        cls._counter += 1
        job_id = cls._counter
        cls._jobs[job_id] = {
            "id": job_id,
            "folder_path": folder_path,
            "status": "pending",
            "total_images": 0,
            "processed_count": 0,
            "failed_count": 0,
            "error_log": None,
            "started_at": None,
            "completed_at": None,
            "created_at": datetime.now(),
            "options": options or {}
        }
        return job_id

    @classmethod
    def get(cls, job_id: int) -> Optional[dict]:
        return cls._jobs.get(job_id)

    @classmethod
    def update(cls, job_id: int, **kwargs) -> Optional[dict]:
        if job_id in cls._jobs:
            cls._jobs[job_id].update(kwargs)
            return cls._jobs[job_id]
        return None

    @classmethod
    def list(cls, skip: int = 0, limit: int = 100) -> List[dict]:
        jobs = list(cls._jobs.values())
        jobs.sort(key=lambda x: x["created_at"], reverse=True)
        return jobs[skip:skip + limit]


async def get_tickets(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    merchant: Optional[str] = None,
) -> List[TicketModel]:
    query = select(TicketModel)
    conditions = []
    if category:
        conditions.append(TicketModel.category == category)
    if status:
        conditions.append(TicketModel.extraction_status == status)
    if date_from:
        conditions.append(TicketModel.expense_date >= date_from)
    if date_to:
        conditions.append(TicketModel.expense_date <= date_to)
    if merchant:
        conditions.append(TicketModel.provider_name.ilike(f"%{merchant}%"))
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(TicketModel.expense_date.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def count_tickets(
    db: AsyncSession,
    category: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    merchant: Optional[str] = None,
) -> int:
    query = select(func.count(TicketModel.id))
    conditions = []
    if category:
        conditions.append(TicketModel.category == category)
    if status:
        conditions.append(TicketModel.extraction_status == status)
    if date_from:
        conditions.append(TicketModel.expense_date >= date_from)
    if date_to:
        conditions.append(TicketModel.expense_date <= date_to)
    if merchant:
        conditions.append(TicketModel.provider_name.ilike(f"%{merchant}%"))
    if conditions:
        query = query.where(and_(*conditions))
    result = await db.execute(query)
    return result.scalar_one()


async def create_ticket_from_extraction(
    db: AsyncSession,
    extraction_data: dict,
    image_path: str,
    job_id: Optional[int] = None
) -> TicketModel:
    from app.core.enums import ExtractionStatus, SourceType
    from app.core.time import utcnow
    import uuid

    ticket = TicketModel(
        id=uuid.uuid4(),
        company_id=extraction_data.get("company_id"),
        provider_name=extraction_data.get("provider_name", "DESCONOCIDO"),
        provider_tax_id=extraction_data.get("provider_tax_id"),
        total_amount=extraction_data.get("total_amount", Decimal("0")),
        tax_amount=extraction_data.get("tax_amount", Decimal("0")),
        subtotal=extraction_data.get("subtotal"),
        expense_date=extraction_data.get("expense_date", datetime.now().date()),
        category=extraction_data.get("category"),
        raw_text=extraction_data.get("raw_text"),
        confidence=extraction_data.get("confidence_score"),
        extraction_status=ExtractionStatus.AUTO_APROBADO.value if extraction_data.get("confidence_score", 0) > 0.8 else ExtractionStatus.PENDIENTE.value,
        source_type=SourceType.AUTO.value,
        source_file=image_path,
        validation_errors=extraction_data.get("validation_errors"),
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket