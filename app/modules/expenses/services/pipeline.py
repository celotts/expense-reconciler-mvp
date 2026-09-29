"""Pipeline for batch receipt processing"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from concurrent.futures import ProcessPoolExecutor

from sqlalchemy.ext.asyncio import AsyncSession

from .ocr_local import LocalOCRService, OCRResult, scan_images
from .vector_search import LocalVectorSearch, MeilisearchClient
from .crud import (
    ExpenseJobModel,
    create_ticket_from_extraction,
    get_tickets,
    count_tickets,
)
from .schemas import BatchUploadRequest, JobStatusResponse
from app.services.ai_extractor import ai_extractor, ExtractedInvoice
from app.services.ai_client import vendor_normalizer
from app.models.ticket import TicketModel
from app.core.enums import ExtractionStatus
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    success: bool
    ticket_id: Optional[str] = None
    error: Optional[str] = None
    image_path: str = ""
    extraction: Optional[ExtractedInvoice] = None


class ReceiptPipeline:
    def __init__(
        self,
        ocr_engine: str = "surya",
        llm_provider: str = "ollama",
        chroma_dir: str = "./data/chroma",
        meilisearch_url: str = "http://localhost:7700",
        max_workers: int = 4,
    ):
        self.ocr = LocalOCRService(engine=ocr_engine)
        self.vector = LocalVectorSearch(persist_dir=chroma_dir)
        self.meilisearch = MeilisearchClient(url=meilisearch_url)
        self.max_workers = max_workers
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

    async def initialize(self):
        await self.meilisearch.setup_index()
        logger.info("Pipeline initialized successfully")

    async def process_batch(self, request: BatchUploadRequest) -> Dict[str, Any]:
        job_id = ExpenseJobModel.create(request.folder_path, {
            "recursive": request.recursive,
            "auto_categorize": request.auto_categorize,
            "check_duplicates": request.check_duplicates,
        })

        ExpenseJobModel.update(job_id, status="processing", started_at=datetime.now())

        images = scan_images(request.folder_path, request.recursive)
        ExpenseJobModel.update(job_id, total_images=len(images))

        results = []
        errors = []
        processed = 0
        failed = 0

        semaphore = asyncio.Semaphore(self.max_workers)

        async def process_one(image_path: str) -> ProcessingResult:
            async with semaphore:
                return await self._process_single_image(image_path, job_id, request)

        tasks = [process_one(img) for img in images]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(batch_results):
            if isinstance(result, Exception):
                failed += 1
                error_msg = f"{images[i]}: {str(result)}"
                errors.append(error_msg)
                logger.error(error_msg)
            elif result.success:
                processed += 1
                results.append(result)
            else:
                failed += 1
                errors.append(f"{result.image_path}: {result.error}")

            ExpenseJobModel.update(
                job_id,
                processed_count=processed,
                failed_count=failed,
                error_log=errors[-1] if errors else None
            )

        ExpenseJobModel.update(
            job_id,
            status="completed" if failed == 0 else "completed_with_errors",
            completed_at=datetime.now(),
            error_log="\n".join(errors) if errors else None
        )

        return {
            "job_id": job_id,
            "total_images": len(images),
            "processed": processed,
            "failed": failed,
            "errors": errors,
            "tickets_created": [r.ticket_id for r in results if r.ticket_id]
        }

    async def _process_single_image(
        self,
        image_path: str,
        job_id: int,
        request: BatchUploadRequest
    ) -> ProcessingResult:
        try:
            logger.info(f"Processing {image_path}")

            # Step 1: OCR
            ocr_result = await self.ocr.extract_receipt(image_path)
            if not ocr_result.text.strip():
                return ProcessingResult(
                    success=False,
                    error="OCR returned empty text",
                    image_path=image_path
                )

            # Step 2: LLM extraction (vision or text)
            extraction = await ai_extractor.extract_from_image(
                open(image_path, "rb").read()
            )

            # Step 3: Normalize vendor
            extraction.provider_name = vendor_normalizer.normalize(extraction.provider_name)

            # Step 4: Check duplicates if requested
            is_duplicate = False
            duplicate_of = None
            if request.check_duplicates:
                async with async_session_factory() as db:
                    duplicates = await self._find_duplicates(db, extraction)
                    if duplicates:
                        is_duplicate = True
                        duplicate_of = str(duplicates[0].id)
                        extraction.confidence = min(extraction.confidence, 0.3)

            # Step 5: Save to DB
            async with async_session_factory() as db:
                ticket = await create_ticket_from_extraction(db, {
                    "company_id": None,  # TODO: get from context
                    "provider_name": extraction.provider_name,
                    "provider_tax_id": extraction.provider_tax_id,
                    "total_amount": extraction.total,
                    "tax_amount": extraction.tax_amount,
                    "subtotal": extraction.subtotal,
                    "expense_date": extraction.invoice_date,
                    "category": extraction.category if hasattr(extraction, 'category') else None,
                    "raw_text": extraction.raw_text,
                    "confidence_score": extraction.confidence,
                    "validation_errors": extraction.extraction_method if "error" in extraction.extraction_method else None,
                }, image_path)

                ticket_id = str(ticket.id)

            # Step 6: Index for search
            search_doc = {
                "id": ticket_id,
                "merchant": extraction.provider_name,
                "merchant_normalized": vendor_normalizer.normalize(extraction.provider_name).lower(),
                "category": extraction.category if hasattr(extraction, 'category') else "other",
                "total_amount": float(extraction.total),
                "expense_date": extraction.invoice_date.isoformat() if extraction.invoice_date else None,
                "status": "duplicate" if is_duplicate else "pending",
                "confidence_score": extraction.confidence,
                "source_image_path": image_path,
            }
            await self.vector.index_expense(search_doc)
            await self.meilisearch.index_expense(search_doc)

            logger.info(f"Successfully processed {image_path} -> ticket #{ticket_id}")
            return ProcessingResult(
                success=True,
                ticket_id=ticket_id,
                image_path=image_path,
                extraction=extraction
            )

        except Exception as e:
            logger.exception(f"Failed to process {image_path}: {e}")
            return ProcessingResult(
                success=False,
                error=str(e),
                image_path=image_path
            )

    async def _find_duplicates(
        self,
        db: AsyncSession,
        extraction: ExtractedInvoice,
        days_window: int = 30
    ) -> List[TicketModel]:
        from datetime import timedelta
        if not extraction.invoice_date:
            return []

        date_from = extraction.invoice_date - timedelta(days=days_window)
        date_to = extraction.invoice_date + timedelta(days=days_window)

        query = select(TicketModel).where(
            and_(
                TicketModel.provider_name.ilike(f"%{extraction.provider_name}%"),
                TicketModel.total_amount == extraction.total,
                TicketModel.expense_date.between(date_from, date_to),
                TicketModel.extraction_status != ExtractionStatus.RECHAZADO.value,
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def search_tickets(
        self,
        query: str,
        limit: int = 10,
        category: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        return await self.vector.search(
            query=query,
            n_results=limit,
            category=category,
            date_from=date_from,
            date_to=date_to,
            min_amount=min_amount,
            max_amount=max_amount,
        )

    def shutdown(self):
        self.executor.shutdown(wait=True)