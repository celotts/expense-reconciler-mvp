# Expense Reconciler MVP

## Visión del Producto

**Una alternativa simple, directa y de demanda real**

En lugar de crear un sistema operativo corporativo o una gran plataforma de IA, pensemos en una herramienta utilitaria de nicho. Algo que las personas y los pequeños negocios ya pagan por resolver, pero que hoy funciona mal o es muy costoso.

Un ejemplo claro es un **Automatizador Local de Facturas y Control de Gastos para Pequeños Negocios y Freelancers**:

**El problema real:** Cualquier persona que trabaje de forma independiente o tenga un pequeño comercio (como una taquería, una tienda local o un prestador de servicios) recibe decenas de comprobantes fiscales, tickets o PDFs por correo o WhatsApp. Ordenarlos, sumarlos y pasarlos a una hoja de cálculo o enviárselos al contador es un dolor de cabeza mensual.

**Cómo funciona la solución:** Es una aplicación donde arrastras tus archivos (PDFs o fotos de tickets), y la herramienta de forma automática extrae los datos clave (fecha, monto, concepto), los organiza, los concilia contra movimientos bancarios y te genera un reporte limpio listo para impuestos o para tu contador. Todo corriendo de forma **privada y local** en tu computadora.

**Cómo se monetiza:** Licencia única de pago único o suscripción muy accesible (mensual/anual) dirigida a profesionales independientes o pequeños comercios que prefieren pagar una pequeña cantidad antes de pasar horas haciendo cuentas a mano.

---

## Stack Tecnológico

- **API**: FastAPI (Python 3.11+)
- **Base de datos**: PostgreSQL 16 + pgvector
- **ORM**: SQLAlchemy 2.0 (async/asyncpg)
- **Validación**: Pydantic v2
- **Procesamiento**: pandas (CSV), pdfplumber (PDF), openpyxl (Excel)
- **Contenedores**: Docker Compose
- **Tests**: pytest + pytest-asyncio + httpx

---

## Estructura del Proyecto

```
expense-reconciler-mvp/
├── app/
│   ├── main.py                 # FastAPI app + routers
│   ├── core/
│   │   ├── config.py           # Settings (pydantic-settings)
│   │   └── database.py         # Async engine + session
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── company.py
│   │   ├── ticket.py
│   │   ├── bank_transaction.py
│   │   ├── reconciliation.py
│   │   └── accounting_mapping.py
│   ├── schemas/                # Pydantic v2 schemas
│   ├── services/               # Business logic
│   │   ├── parser_service.py   # CSV/PDF parsing
│   │   ├── reconciliation_service.py  # Matching engine
│   │   └── export_service.py   # Excel/CONTPAQI export
│   └── api/                    # REST endpoints
│       ├── companies.py
│       ├── tickets.py
│       ├── bank_transactions.py
│       └── reconciliations.py
├── db/
│   └── init.sql                # Schema + indexes
├── tests/
│   ├── unit/                   # 22 tests
│   └── integration/            # 36 tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── agent.md                    # Agent definition
├── memory.md                   # Project memory
├── skill.md                    # Reusable patterns
└── README.md                   # This file
```

---

## Quick Start

### Con Docker (Recomendado)
```bash
docker-compose up --build
```

La API estará en: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

### Variables de entorno (`.env.dev`)
```env
PROJECT_NAME="Expense Reconciler MVP"
DATABASE_URL=postgresql+asyncpg://postgres:fc100711@postgres-reconciler:5432/expense_db
API_V1_STR="/api/v1"
CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]
```

---

## Flujo de Trabajo Principal

### 1. Crear Empresa
```bash
POST /api/v1/companies/
{"name": "Mi Taquería", "tax_id": "MTA123456789"}
```

### 2. Registrar Tickets (manual o extracción automática)
```bash
# Manual
POST /api/v1/tickets/
{"company_id": "...", "provider_name": "PROVEEDOR", "total_amount": "150.00", "expense_date": "2025-01-15"}

# Extraer de PDF/imagen + crear
POST /api/v1/tickets/extract-and-create
multipart: file=@ticket.pdf, file_type=pdf, company_id=...
```

### 3. Importar Movimientos Bancarios (CSV)
```bash
POST /api/v1/bank-transactions/import-csv-and-create
multipart: file=@banco.csv, company_id=..., date_column=fecha, amount_column=importe, description_column=concepto
```

### 4. Ejecutar Conciliación Automática
```bash
POST /api/v1/reconciliations/run
{"company_id": "...", "amount_tolerance": "0.01", "date_tolerance_days": 3}
```

**Estados de match:**
- **PERFECT**: Monto exacto + fecha dentro de tolerancia
- **MANUAL**: Monto OK pero fecha fuera (o viceversa) - requiere revisión
- **DISCREPANCY**: Diferencia de monto > tolerancia

### 5. Exportar para Contabilidad
```bash
# Excel estándar
GET /api/v1/reconciliations/export/excel?company_id=...

# CONTPAQI (México)
GET /api/v1/reconciliations/export/contpaqi?company_id=...

# Personalizado
GET /api/v1/reconciliations/export/generic?company_id=...&columns=Fecha,Proveedor,Total,Estatus%20Conciliacion
```

---

## API Endpoints Summary

| Módulo | Prefijo | Endpoints |
|--------|---------|-----------|
| **Companies** | `/api/v1/companies` | CRUD completo |
| **Tickets** | `/api/v1/tickets` | CRUD + `/extract` + `/extract-and-create` |
| **Bank Transactions** | `/api/v1/bank-transactions` | CRUD + `/import-csv` + `/import-csv-and-create` |
| **Reconciliations** | `/api/v1/reconciliations` | `/run`, CRUD, `/export/*`, `/mappings` |

---

## Tests

```bash
# Todos (58 tests)
python3 -m pytest tests/ -v

# Unitarios
python3 -m pytest tests/unit/ -v

# Integración
python3 -m pytest tests/integration/ -v
```

---

## Documentación Técnica

- **agent.md**: Definición del rol/agent
- **memory.md**: Decisiones técnicas, estructura, próximos pasos
- **skill.md**: Patrones reutilizables (10 patrones con código)

---

## Próximos Pasos

1. **Alembic** para migraciones de BD
2. **Auth/JWT** para multi-usuario
3. **OCR real** (AWS Textract, Azure Form Recognizer)
4. **Frontend** (React/Vue + Tauri para desktop)
5. **Empaquetado** como app de escritorio (PyInstaller/Tauri)