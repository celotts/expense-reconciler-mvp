from fastapi import APIRouter, UploadFile, HTTPException
from app.services.processor import process_pdf, process_image, calculate_sha256
from app.services.ai_client import AIClient
from app.schemas import Order, Report
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import asyncio

router = APIRouter()

@router.post("/upload-batch")
async def upload_batch(files: List[UploadFile]):
    semaphore = asyncio.Semaphore(3)
    async with get_db() as session:
        async with semaphore:
            tasks = []
            for file in files:
                content_type = file.content_type
                if content_type == "application/pdf":
                    pages = await process_pdf(file)
                    for page in pages:
                        tasks.append(process_file(session, page, file.filename))
                elif content_type.startswith("image/"):
                    tasks.append(process_file(session, await process_image(file), file.filename))
                else:
                    raise HTTPException(status_code=400, detail="Unsupported file type")
            await asyncio.gather(*tasks)
            return {"message": "Files processed successfully"}

async def process_file(session: AsyncSession, content: bytes, filename: str):
    sha256 = calculate_sha256(content)
    existing_file = await session.execute(select(Report).where(Report.hash_sha256 == sha256))
    if existing_file.scalars().first():
        raise HTTPException(status_code=400, detail="Duplicate file detected")

    ai_client = AIClient()
    data = await ai_client.extract_data(content)
    order = Order(**data)
    report = Report(
        country="MEX",  # Change to CHL if needed
        rfc_rut="ABC123456789",  # Change to actual RUT if needed
        date="2023-10-01",  # Change to actual date if needed
        items=order.items,
        payment_methods=["CASH", "CREDIT_CARD"],  # Change to actual payment methods if needed
        total=order.total,
        hash_sha256=sha256
    )
    session.add(report)
    await session.commit()
