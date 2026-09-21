# Agent Definition - Expense Reconciler MVP

## Role
**Senior Software Architect & Backend Developer** specializing in:
- Python 3.11+
- FastAPI (async)
- SQLAlchemy 2.0 (async/asyncpg)
- Pydantic v2
- PostgreSQL with pgvector
- Docker containerization

## Project Context
**expense-reconciler-mvp** - A local, private expense reconciliation and accounting export tool.

### Core Purpose
Automate matching of digitalized tickets/receipts against bank transactions, then export reconciled data to accounting formats (Excel standard, CONTPAQI for Mexico).

### Key Features
1. **Company Management** - Multi-tenant company profiles with tax IDs
2. **Ticket Processing** - Manual entry + OCR/PDF extraction (provider, amounts, dates, taxes)
3. **Bank Import** - CSV parsing with configurable column mapping
4. **Smart Reconciliation Engine** - Amount/date matching with 3 states:
   - `PERFECT` - Exact amount + date within tolerance
   - `MANUAL` - Amount match but date outside tolerance (or vice versa)
   - `DISCREPANCY` - Amount difference exceeds tolerance
5. **Accounting Export** - Multiple formats:
   - Standard Excel
   - CONTPAQI (Mexican accounting software)
   - Generic customizable templates

## Technical Stack
- **API**: FastAPI with async SQLAlchemy
- **Database**: PostgreSQL 16 + pgvector (for future embeddings)
- **ORM Models**: CompanyModel, TicketModel, BankTransactionModel, ReconciliationModel, AccountingMappingModel
- **Validation**: Pydantic v2 schemas with strict typing
- **File Processing**: pandas (CSV), pdfplumber (PDF), openpyxl (Excel)
- **Containerization**: Docker Compose with health checks

## Development Principles
- Strict type hints throughout
- Dependency injection (FastAPI `Depends`) for DB sessions
- Proper HTTP error handling (404, 409, 400)
- Async/await for all I/O operations
- Pydantic `ConfigDict(from_attributes=True)` for ORM integration
- Environment-based configuration (`.env.dev`)

## API Structure
All endpoints under `/api/v1` prefix:
- `/companies` - CRUD for companies
- `/tickets` - CRUD + extraction endpoints
- `/bank-transactions` - CRUD + CSV import
- `/reconciliations` - Run engine, CRUD, exports, mappings