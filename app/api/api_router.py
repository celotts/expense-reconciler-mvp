from fastapi import APIRouter

from fastapi import APIRouter, Depends

from app.api.auth import router as auth_router
from app.api.bank_transactions import router as bank_transactions_router
from app.api.companies import router as companies_router
from app.api.dashboard import router as dashboard_router
from app.api.reconciliations import router as reconciliations_router
from app.api.tickets import router as tickets_router
from app.core.deps import get_current_user

api_router = APIRouter()

# ---------------------------------------------------------------------------
# La puerta se instala primero y SIN proteccion: `POST /auth/login` es
# justamente la peticion que no puede traer un token, porque todavia no hay
# ninguno. `GET /auth/me` si lo exige, con su propia dependencia, para que
# "dice que soy yo" no dependa de que el router entero este protegido.
# ---------------------------------------------------------------------------
api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])

# A partir de aqui, TODO lo demas pide token. Se pone a nivel de router, no
# endpoint por endpoint: escrito a mano serian 35 oportunidades de que el
# endpoint 36 quede abierto y no se note en la revision, porque la ausencia se
# ve igual que la presencia. Y un endpoint nuevo nace protegido.
api_router.include_router(
    dashboard_router,
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    companies_router,
    prefix="/companies",
    tags=["Companies"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    tickets_router,
    prefix="/tickets",
    tags=["Tickets"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    bank_transactions_router,
    prefix="/bank-transactions",
    tags=["Bank Transactions"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    reconciliations_router,
    prefix="/reconciliations",
    tags=["Reconciliations"],
    dependencies=[Depends(get_current_user)],
)