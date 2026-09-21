# Project Memory - Expense Reconciler MVP

## Session Summary
Built complete backend for expense reconciliation MVP from empty `/app` folder structure.

## Key Decisions Made

### 1. Database Models (`app/models/`)
- Used SQLAlchemy 2.0 `DeclarativeBase` with async support
- UUID primary keys with `uuid_generate_v4()` (PostgreSQL)
- Proper foreign keys with CASCADE/SET NULL behaviors
- Indexes on date and amount columns for reconciliation performance
- Relationships defined with `back_populates` for bidirectional access

### 2. Pydantic Schemas (`app/schemas/`)
- Separate Create/Update/Response models per entity
- `ConfigDict(from_attributes=True)` for direct ORM model mapping
- Field validation: `gt=0`, `max_digits`, `decimal_places`, `pattern` for match_status
- Optional fields with `Field(None, ...)` for partial updates

### 3. Service Layer (`app/services/`)

#### parser_service.py
- **CSV parsing**: Configurable column names, date formats, decimal/thousands separators, encoding
- **Ticket extraction**: PDF text extraction via pdfplumber + regex parsing for amounts, dates, RFC
- Returns structured Pydantic models (`BankTransactionRow`, `TicketExtractionResult`)

#### reconciliation_service.py
- **Matching algorithm**: 
  1. Filter unreconciled tickets & bank transactions by company/date range
  2. For each ticket, find best bank match within amount tolerance
  3. Score by `amount_diff * 100 + date_diff` (prioritize exact amounts)
  4. Classify: PERFECT (exact amount + date tolerance), MANUAL (amount ok, date off), DISCREPANCY (amount off)
- Persists `ReconciliationModel` records + marks bank transactions as reconciled
- Returns detailed `ReconciliationRunResponse` with summary counts

#### export_service.py
- **Standard Excel**: Human-readable columns with formatted dates, amounts
- **CONTPAQI**: Mexican accounting format with required columns (Fecha, Concepto, RFC, Nombre, Importe, IVA, Total, Cuenta, Referencia, TipoComprobante, Serie, Folio, Moneda, TipoCambio, MetodoPago, UsoCFDI)
- **Generic**: Custom column selection + rename mapping
- All exports return `bytes` for FastAPI `Response` streaming

### 4. API Layer (`app/api/`)
- Each router: prefix + tags for OpenAPI grouping
- Dependency injection: `db: AsyncSession = Depends(get_db)`
- Proper status codes: 201 Created, 204 No Content, 404, 409
- File upload endpoints using `UploadFile`, `Form` for multipart
- Query parameters for filtering (company_id, date ranges, status)

### 5. Routing (`app/api/api_router.py` + `main.py`)
- Central `api_router` includes all sub-routers
- Mounted in `main.py` with `settings.API_V1_STR` prefix (`/api/v1`)
- CORS configured from settings

## Configuration
- `Settings` class with `pydantic-settings` reading from `.env.dev`
- `DATABASE_URL` for asyncpg connection
- `CORS_ORIGINS` for frontend integration

## Docker Setup
- **postgres-reconciler**: pgvector/pgvector:pg16 with init.sql volume mount
- **expense-api**: Built from Dockerfile, uvicorn with reload, env_file + environment override
- Health check on PostgreSQL before API starts
- Shared bridge network

## Files Created
```
app/
├── main.py
├── core/
│   ├── config.py
│   └── database.py
├── models/
│   ├── __init__.py
│   ├── company.py
│   ├── ticket.py
│   ├── bank_transaction.py
│   ├── reconciliation.py
│   └── accounting_mapping.py
├── schemas/
│   ├── __init__.py
│   ├── company.py
│   ├── ticket.py
│   ├── bank_transaction.py
│   ├── reconciliation.py
│   └── accounting_mapping.py
├── services/
│   ├── __init__.py
│   ├── parser_service.py
│   ├── reconciliation_service.py
│   └── export_service.py
└── api/
    ├── __init__.py
    ├── api_router.py
    ├── companies.py
    ├── tickets.py
    ├── bank_transactions.py
    └── reconciliations.py
```

## Requirements Added
- `openpyxl>=3.1.2` for Excel export

## Next Steps (if continuing)
1. Add database migration tool (Alembic)
2. Add authentication/authorization (JWT)
3. Implement actual OCR integration (AWS Textract, Azure Form Recognizer)
4. Add webhook/callback for async processing
5. Add audit logging
6. Frontend integration

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

## Test Structure
```
tests/
├── conftest.py                    # Shared fixtures (async DB, client, sample data)
├── pytest.ini                     # Pytest config with asyncio_mode=auto
├── unit/
│   ├── test_parser_service.py     # 8 tests - CSV parsing, ticket extraction
│   ├── test_reconciliation_service.py  # 7 tests - matching logic, engine
│   └── test_export_service.py     # 7 tests - Excel, CONTPAQI, generic export
└── integration/
    ├── test_companies_api.py      # 8 tests - CRUD, duplicates, validation
    ├── test_tickets_api.py        # 10 tests - CRUD, extraction, file upload
    ├── test_bank_transactions_api.py  # 8 tests - CRUD, CSV import/preview
    └── test_reconciliations_api.py    # 12 tests - engine, CRUD, exports, mappings
```

**Total: 58 tests passing**