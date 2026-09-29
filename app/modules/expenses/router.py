"""Expenses API router - Batch processing and search"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.modules.expenses.schemas import (
    BatchUploadRequest, BatchUploadResponse, JobStatusResponse,
    SearchRequest, SearchResult
)
from app.modules.expenses.crud import (
    get_tickets, count_tickets, ExpenseJobModel
)
from app.modules.expenses.services import ReceiptPipeline

router = APIRouter(prefix="/expenses", tags=["Expenses"])


_pipeline: Optional[ReceiptPipeline] = None


async def get_pipeline() -> ReceiptPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ReceiptPipeline(
            ocr_engine="surya",
            chroma_dir="./data/chroma",
            meilisearch_url="http://localhost:7700",
            max_workers=4,
        )
        await _pipeline.initialize()
    return _pipeline


@router.post("/batch-upload", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def batch_upload(
    request: BatchUploadRequest,
    background_tasks: BackgroundTasks,
    pipeline: ReceiptPipeline = Depends(get_pipeline)
):
    images = scan_images(request.folder_path, request.recursive)
    job_id = ExpenseJobModel.create(request.folder_path, {
        "recursive": request.recursive,
        "auto_categorize": request.auto_categorize,
        "check_duplicates": request.check_duplicates,
    })

    background_tasks.add_task(pipeline.process_batch, request)
    return BatchUploadResponse(
        job_id=job_id,
        message="Procesamiento batch iniciado en background",
        total_images_found=len(images)
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: int):
    job = ExpenseJobModel.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@router.get("/jobs", response_model=List[JobStatusResponse])
async def list_jobs(skip: int = 0, limit: int = 100):
    return ExpenseJobModel.list(skip, limit)


@router.get("/tickets", response_model=List[dict])
async def list_tickets(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    merchant: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    tickets = await get_tickets(db, skip, limit, category, status, date_from, date_to, merchant)
    return [
        {
            "id": str(t.id),
            "provider_name": t.provider_name,
            "total_amount": float(t.total_amount),
            "tax_amount": float(t.tax_amount),
            "expense_date": t.expense_date.isoformat() if t.expense_date else None,
            "category": t.category,
            "extraction_status": t.extraction_status,
            "confidence": float(t.confidence) if t.confidence else None,
            "source_file": t.source_file,
        }
        for t in tickets
    ]


@router.post("/search", response_model=List[SearchResult])
async def search_tickets(
    request: SearchRequest,
    pipeline: ReceiptPipeline = Depends(get_pipeline)
):
    results = await pipeline.search_tickets(
        query=request.query,
        limit=request.limit,
        category=request.category.value if request.category else None,
        date_from=request.date_from.isoformat() if request.date_from else None,
        date_to=request.date_to.isoformat() if request.date_to else None,
        min_amount=float(request.min_amount) if request.min_amount else None,
        max_amount=float(request.max_amount) if request.max_amount else None,
    )
    return [
        SearchResult(
            id=r["id"],
            document=r["document"],
            metadata=r["metadata"],
            score=r["score"]
        )
        for r in results
    ]


@router.get("/stats/summary")
async def get_tickets_summary(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select, func
    from app.models.ticket import TicketModel
    from app.core.enums import ExtractionStatus

    query = select(
        TicketModel.category,
        func.count(TicketModel.id).label("count"),
        func.sum(TicketModel.total_amount).label("total")
    ).where(TicketModel.extraction_status != ExtractionStatus.RECHAZADO.value)

    if date_from:
        query = query.where(TicketModel.expense_date >= date_from)
    if date_to:
        query = query.where(TicketModel.expense_date <= date_to)

    query = query.group_by(TicketModel.category)
    result = await db.execute(query)

    summary = []
    for row in result:
        summary.append({
            "category": row.category,
            "count": row.count,
            "total": float(row.total) if row.total else 0
        })

    total_query = select(func.count(TicketModel.id), func.sum(TicketModel.total_amount)).where(
        TicketModel.extraction_status != ExtractionStatus.RECHAZADO.value
    )
    if date_from:
        total_query = total_query.where(TicketModel.expense_date >= date_from)
    if date_to:
        total_query = total_query.where(TicketModel.expense_date <= date_to)

    total_result = await db.execute(total_query)
    total_count, total_amount = total_result.one()

    return {
        "by_category": summary,
        "total_count": total_count,
        "total_amount": float(total_amount) if total_amount else 0,
        "period": {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None
        }
    }


# Import scan_images at module level
from app.modules.expenses.services.ocr_local import scan_images