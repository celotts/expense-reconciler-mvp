import uuid

from sqlalchemy import JSON, TIMESTAMP, Column, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.core.time import utcnow

# Planes que existen (P0-1). `demo` es la empresa de demostracion con datos
# semilla; `basico` es el plan con el que nace cada registro. Los nombres
# de plan superiores (pro, despacho) se anaden cuando exista la facturacion.
PLAN_BASICO = "basico"
PLAN_DEMO = "demo"


class CompanyModel(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    tax_id = Column(String(50), unique=True, nullable=False)
    # Configuracion de lotes de esta empresa (0019). JSON y no tabla aparte:
    # son cuatro escalares que se leen con la empresa y nunca se buscan por su
    # valor; ver `app/models/lote.py` para los defaults. NULL = sin configurar.
    lote_config = Column(JSON, nullable=True)
    # Plan comercial de la empresa (P0-1): 'basico' por defecto, 'demo' para
    # la empresa de demostracion. La facturacion no existe todavia; el plan
    # es hoy un dato, no una cerradura.
    plan = Column(String(20), nullable=False, default=PLAN_BASICO)
    # Hasta cuando dura la prueba gratuita. NULL = sin prueba (empresa de
    # pago o demo sin caducidad). El registro pone hoy + 30 dias.
    trial_hasta = Column(TIMESTAMP(timezone=True), nullable=True)
    # Tickets creados este mes por esta empresa. Contador cache, no verdad
    # contable: se reinicia cada mes y el conteo real sigue en `tickets`.
    # Existe para el limite del plan cuando llegue la facturacion; hoy no
    # bloquea nada.
    tickets_usados_este_mes = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)