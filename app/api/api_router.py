from fastapi import APIRouter

from app.api.bank_transactions import router as bank_transactions_router
from app.api.companies import router as companies_router
from app.api.reconciliations import router as reconciliations_router
from app.api.tickets import router as tickets_router

api_router = APIRouter()

api_router.include_router(companies_router, prefix="/companies", tags=["Companies"])
api_router.include_router(tickets_router, prefix="/tickets", tags=["Tickets"])
api_router.include_router(bank_transactions_router, prefix="/bank-transactions", tags=["Bank Transactions"])
api_router.include_router(reconciliations_router, prefix="/reconciliations", tags=["Reconciliations"])