# Skill Patterns - Expense Reconciler MVP

## Reusable Development Patterns

### 1. FastAPI + SQLAlchemy Async Project Structure
```
app/
├── main.py                 # App factory, middleware, router inclusion
├── core/
│   ├── config.py           # Pydantic Settings from .env
│   └── database.py         # Engine, SessionLocal, get_db dependency
├── models/                 # SQLAlchemy ORM models
├── schemas/                # Pydantic v2 request/response models
├── services/               # Business logic (no HTTP concerns)
└── api/                    # HTTP endpoints (thin controllers)
    ├── api_router.py       # Central router composition
    └── <entity>.py         # Per-entity CRUD + custom endpoints
```

### 2. Database Configuration Pattern
```python
# core/database.py
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

class Base(DeclarativeBase):
    pass

engine = create_async_engine(settings.DATABASE_URL, echo=True, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

### 3. Model Definition Pattern
```python
# models/entity.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, ForeignKey, TIMESTAMP, Numeric, Date, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base

class EntityModel(Base):
    __tablename__ = "entities"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    # ... fields
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    
    company = relationship("CompanyModel", backref="entities")
    # relationships with back_populates for bidirectional
```

### 4. Schema Definition Pattern (Pydantic v2)
```python
# schemas/entity.py
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class EntityBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)

class EntityCreate(EntityBase):
    company_id: UUID

class EntityUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=2)

class EntityResponse(EntityBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

### 5. Service Layer Pattern
```python
# services/entity_service.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.entity import EntityModel
from app.schemas.entity import EntityCreate

async def create_entity(db: AsyncSession, data: EntityCreate) -> EntityModel:
    entity = EntityModel(**data.model_dump())
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return entity

async def get_entities(db: AsyncSession, company_id: UUID, skip: int = 0, limit: int = 100):
    result = await db.execute(
        select(EntityModel)
        .where(EntityModel.company_id == company_id)
        .offset(skip).limit(limit)
    )
    return list(result.scalars().all())
```

### 6. API Endpoint Pattern
```python
# api/entity.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.entity_service import create_entity, get_entities
from app.schemas.entity import EntityCreate, EntityResponse

router = APIRouter(prefix="/entities", tags=["Entities"])

@router.post("/", response_model=EntityResponse, status_code=status.HTTP_201_CREATED)
async def create_entity_endpoint(
    entity_in: EntityCreate,
    db: AsyncSession = Depends(get_db)
):
    return await create_entity(db, entity_in)

@router.get("/", response_model=list[EntityResponse])
async def list_entities(
    company_id: UUID,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    return await get_entities(db, company_id, skip, limit)
```

### 7. File Upload Processing Pattern
```python
# services/parser_service.py
import io
import pandas as pd
from pydantic import BaseModel

class ParsedRow(BaseModel):
    field1: str
    field2: Decimal

def parse_csv(file_content: bytes, **config) -> list[ParsedRow]:
    df = pd.read_csv(io.BytesIO(file_content), **config)
    # validate columns, transform, return Pydantic models
    return [ParsedRow(**row) for row in df.to_dict('records')]

# api/endpoint.py
@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    config_param: str = Form("default"),
    db: AsyncSession = Depends(get_db)
):
    content = await file.read()
    try:
        parsed = parse_csv(content, config=config_param)
    except Exception as e:
        raise HTTPException(400, f"Parse error: {e}")
    
    # create DB records
    for row in parsed:
        db.add(Model(**row.model_dump()))
    await db.commit()
    return parsed
```

### 8. Excel Export Pattern
```python
# services/export_service.py
import io
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

async def export_to_excel(db: AsyncSession, company_id: UUID) -> bytes:
    data = await fetch_export_data(db, company_id)
    df = pd.DataFrame(data, columns=COLUMN_NAMES)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
        # auto-width columns
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            writer.sheets["Sheet1"].column_dimensions[chr(65 + idx)].width = min(max_len, 50)
    return output.getvalue()

# api/endpoint.py
from fastapi import Response

@router.get("/export/excel")
async def export_excel(company_id: UUID, db: AsyncSession = Depends(get_db)):
    content = await export_to_excel(db, company_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export.xlsx"}
    )
```

### 9. Reconciliation/Matching Engine Pattern
```python
# services/reconciliation_service.py
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

@dataclass
class MatchResult:
    source: SourceModel
    target: TargetModel
    status: str  # PERFECT | MANUAL | DISCREPANCY
    amount_diff: Decimal
    date_diff_days: int

async def run_matching(
    db: AsyncSession,
    company_id: UUID,
    amount_tolerance: Decimal,
    date_tolerance_days: int
) -> list[MatchResult]:
    sources = await get_unmatched_sources(db, company_id)
    targets = await get_unmatched_targets(db, company_id)
    
    matches = []
    matched_target_ids = set()
    
    for source in sources:
        best = find_best_match(source, targets, amount_tolerance, date_tolerance_days, matched_target_ids)
        if best:
            target, status, amt_diff, date_diff = best
            matches.append(MatchResult(source, target, status, amt_diff, date_diff))
            matched_target_ids.add(target.id)
    
    await persist_matches(db, matches)
    return matches
```

### 10. Docker Compose for Local Dev
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: app_db
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: secret
    ports: ["5434:5432"]
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./db:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d app_db"]
      interval: 5s; timeout: 5s; retries: 5

  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    ports: ["8000:8000"]
    volumes: [".:/app"]
    env_file: .env.dev
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:secret@postgres:5432/app_db
    depends_on:
      postgres: {condition: service_healthy}
```

## Key Libraries Used
| Purpose | Library |
|---------|---------|
| Web Framework | FastAPI |
| Async ORM | SQLAlchemy 2.0 + asyncpg |
| Validation | Pydantic v2 + pydantic-settings |
| Data Processing | pandas |
| PDF Parsing | pdfplumber |
| Excel Export | openpyxl |
| ASGI Server | uvicorn[standard] |
| File Upload | python-multipart |

## Common Gotchas Avoided
1. **Base import**: Define `Base = DeclarativeBase()` in `database.py`, not separate file
2. **Async sessions**: Use `async_sessionmaker` with `class_=AsyncSession`, `expire_on_commit=False`
3. **Pydantic v2**: Use `model_dump()` not `dict()`, `ConfigDict` not inner `Config` class
4. **Decimal handling**: Use `Decimal` type, not `float`, for monetary values
5. **UUID**: Use `UUID(as_uuid=True)` + `uuid.uuid4()` for Python-side generation
6. **Relationships**: Always use `back_populates` on both sides for consistency
7. **File uploads**: Read content once with `await file.read()`, don't try to re-read
8. **Response streaming**: Return `bytes` from services, wrap in `Response` with proper media type

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# Unit tests only
python3 -m pytest tests/unit/ -v

# Integration tests only
python3 -m pytest tests/integration/ -v

# Specific module
python3 -m pytest tests/unit/test_parser_service.py -v
```