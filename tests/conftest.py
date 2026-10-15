import pytest
import tempfile
import shutil
from sqlalchemy import event
from pathlib import Path
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import get_db, Base
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import hashear_contrasena
from app.models.company import CompanyModel
from app.models.user import UserModel


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

    # `PRAGMA foreign_keys=ON`. SQLite ignora las llaves foraneas por omision, y
    # sin esto los tests mienten sobre lo que hace la base.
    #
    # Por que se activo AHORA y no antes: `ticket_documents` solia borrarse desde
    # el ORM (`cascade="all, delete-orphan"` en la relationship), asi que el
    # borrado en cascada lo hacia Python y nunca se noto que SQLite no lo hacia.
    # Con `0009_documento_inmutable.sql` la relationship paso a `viewonly` y el
    # borrado es del `ON DELETE CASCADE` de la llave foranea, que en SQLite exige
    # este PRAGMA.
    #
    # Sin el, borrar un ticket dejaba el comprobante huerfano en los tests y no
    # en produccion: el test pasaba por el motivo equivocado.
    @event.listens_for(engine.sync_engine, "connect")
    def _activar_llaves_foraneas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
async def usuario_de_prueba(db_session):
    """La cuenta con la que corre la suite.

    Existe como fila de verdad en la base de prueba, no como un objeto
    manufactured: si los endpoints guardan el correo de quien reviso, el test
    tiene que poder leer esa columna de la base y compararla con algo. Un
    `MagicMock` de usuario no serviria para eso.
    """
    correo = f"operador{uuid4().hex[:8]}@test.local"
    usuario = UserModel(
        email=correo,
        nombre="Operador de Prueba",
        password_hash=hashear_contrasena("contrasena-de-prueba"),
    )
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario


@pytest.fixture
async def async_client(db_session, usuario_de_prueba):
    async def override_get_db():
        yield db_session

    async def override_get_current_user():
        return usuario_de_prueba

    app.dependency_overrides[get_db] = override_get_db
    # El cliente HTTP entra como un usuario ya autenticado, porque casi todos
    # los tests prueban otra cosa y no el login. Los que SI prueban el login
    # (tests/unit/test_auth.py) no piden este fixture, o piden
    # `async_client_sin_autenticar`, y asi se prueba la dependencia de verdad.
    app.dependency_overrides[get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def async_client_sin_autenticar(db_session):
    """Cliente con la sesion real: sin token, con token caducado, con token
    falsificado. Es el unico camino por el que se puede probar la proteccion.

    A diferencia de `async_client`, NO sobrescribe `get_current_user`. Si lo
    hiziera, todos los tests de seguridad estarian probando el override, que
    siempre deja pasar, y no el codigo que decide.
    """
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

# ---------------------------------------------------------------------------
# El archivado, aislado para TODAS las pruebas
# ---------------------------------------------------------------------------
#
# `autouse=True` y aqui, y no en cada archivo, por la razon de siempre: una
# defensa puesta en un solo lugar no se puede olvidar. Ponerlo en un solo archivo
# de tests deja los otros escribiendo en el disco real.
#
# Son DOS cosas y las dos hacen falta:
#
# 1. **DONDE.** `TICKETS_SCAN_OUTPUT_DIR` a un tmp. Sin esto, la carpeta de
#    escaneados se resuelve como hermana de `TICKETS_INPUT_DIR` —que en un test
#    es el tmp de pytest, asi que esto casi nunca falla— pero si el ajuste trae
#    una ruta absoluta de contenedor, `mkdir` en macOS da "Read-only file
#    system". Medido: 24 tests caidos.
#
# 2. **SI.** Los dos apagones en False. Un test de LECTURA no debe mover
#    archivos: `test_reprocesar_un_archivo_lo_relee` falla si el archivo ya no
#    esta donde lo dejo, y el archivado se lo llevo. Medido: 3 tests caidos.
#    Los tests que EXPLICITAMENTE prueban el archivado lo encienden.
@pytest.fixture(autouse=True)
def _aislar_el_archivado(tmp_path, monkeypatch):
    from app.core.config import settings

    destino = tmp_path / "escaneados"
    destino.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "TICKETS_SCAN_OUTPUT_DIR", str(destino))
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR", False)
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR", False)
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR", False)
    return destino
