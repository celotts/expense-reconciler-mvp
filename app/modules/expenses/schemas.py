"""Expense schemas for batch processing and search"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ExpenseCategory(str, Enum):
    FOOD = "food"
    TRANSPORT = "transport"
    ACCOMMODATION = "accommodation"
    OFFICE_SUPPLIES = "office_supplies"
    SOFTWARE = "software"
    MARKETING = "marketing"
    TRAVEL = "travel"
    MEALS = "meals"
    ENTERTAINMENT = "entertainment"
    OTHER = "other"


class ExpenseStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    RECONCILED = "reconciled"
    DUPLICATE = "duplicate"


class ReceiptItemBase(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    category: str | None = None


class ReceiptItemCreate(ReceiptItemBase):
    pass


class ReceiptItemResponse(ReceiptItemBase):
    id: int
    expense_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BatchUploadRequest(BaseModel):
    folder_path: str = Field(..., description="Ruta absoluta a la carpeta con tickets")
    recursive: bool = True
    auto_categorize: bool = True
    check_duplicates: bool = True
    policy_validation: bool = False


class BatchUploadResponse(BaseModel):
    job_id: int
    message: str
    total_images_found: int


class JobStatusResponse(BaseModel):
    id: int
    folder_path: str
    status: str
    total_images: int
    processed_count: int
    failed_count: int
    error_log: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SearchRequest(BaseModel):
    query: str = Field(..., description="Consulta en lenguaje natural")
    limit: int = 10
    category: ExpenseCategory | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


class SearchResult(BaseModel):
    id: str
    document: str
    metadata: dict
    score: float
