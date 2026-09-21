import pytest
import tempfile
import shutil
from pathlib import Path
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import get_db, Base
from app.core.config import settings
from app.models.company import CompanyModel


# Base de datos en memoria para tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def test_engine():
    """Create a new in-memory database engine per test."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest.fixture
async def db_session(test_engine):
    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

@pytest.fixture
async def test_company(db_session):
    unique_tax_id = f"TEST{uuid4().hex[:9].upper()}"
    company = CompanyModel(name="Test Company", tax_id=unique_tax_id)
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company

@pytest.fixture
async def async_client(db_session):
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    
    app.dependency_overrides.clear()

@pytest.fixture(scope="session")
def test_upload_dir():
    temp_dir = Path(tempfile.mkdtemp(prefix="expense_test_"))
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)

@pytest.fixture
def sample_bank_csv_content():
    return b"""fecha,importe,concepto,referencia
15/01/2025,-125.50,PAGO TARJETA WALMART,REF001
16/01/2025,-89.90,COMPRA AMAZON MX,REF002
17/01/2025,-245.00,TRANSFERENCIA PROVEEDOR ABC,REF003
18/01/2025,5000.00,NOMINA ENERO,REF004
"""

@pytest.fixture
def sample_ticket_text():
    return """WALMART SUPERCENTER
RFC: WAL910101XXX
AV. INSURGENTES SUR 1234
CDMX, MEXICO

FECHA: 15/01/2025 14:32
TICKET: 0001-002345

ARTICULOS:
LECHE ENTERA 1L          $28.50
PAN BIMBO BLANCO         $45.00
HUEVOS BLANCOS 12PZ      $52.00
DETERGENTE ARIEL 1KG     $89.90

SUBTOTAL:           $215.40
IVA (16%):          $34.46
TOTAL:              $249.86

METODO PAGO: TARJETA DEBITO
REF: 123456789012
"""