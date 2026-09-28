"""Dashboard: estadísticas agregadas por período para la página principal.

Diseño
------

El dashboard no es un catálogo, es un **resumen ejecutivo**. La persona que
abre la app quiere saber de un vistazo:

1. ¿Cuánto proceso HOY? (tickets, monto, qué falta por revisar)
2. ¿Cómo va el MES actual vs el anterior? (tendencia)
3. ¿Cómo va el AÑO actual vs el anterior? (visión anual)
4. ¿Qué hay en la cola de revisión AHORA? (acción inmediata)
5. ¿Cumple el objetivo de exactitud? (SLO 96%)

Los datos se calculan en SQL con `func.count`/`func.sum` y filtros de fecha
en `created_at` (que es cuando el ticket entró al sistema, no `expense_date`,
porque el dashboard mide **actividad del sistema**, no contabilidad).

Si se pasa `company_id`, todo se filtra a esa empresa. Si no, es global
(todas las empresas del usuario). Como no hay multi-tenancy real, "global"
significa "todo lo que hay en la base".

La exactitud solo se calcula si hay empresa seleccionada, porque mezclar
empresas distintas en el mismo reporte de exactitud no tiene sentido
estadístico.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.company import CompanyModel
from app.models.ticket import TicketModel, ExtractionStatus
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.core.enums import MatchStatus
from app.schemas.ticket import ReporteExactitudResponse
from app.services.accuracy_service import compute_accuracy_report

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class PeriodStats(BaseModel):
    """Estadísticas para un período específico."""
    tickets_total: int = 0
    tickets_pendientes: int = 0
    tickets_aprobados: int = 0
    tickets_rechazados: int = 0
    tickets_auto_aprobados: int = 0
    monto_total: Decimal = Field(default=Decimal("0"))
    monto_pendiente: Decimal = Field(default=Decimal("0"))
    bank_transactions: int = 0
    reconciliations_perfect: int = 0
    reconciliations_manual: int = 0
    reconciliations_discrepancy: int = 0


class DashboardResponse(BaseModel):
    """Respuesta completa del dashboard."""
    company_id: Optional[UUID] = None
    company_name: Optional[str] = None
    
    hoy: PeriodStats
    mes_actual: PeriodStats
    mes_anterior: PeriodStats
    ano_actual: PeriodStats
    ano_anterior: PeriodStats
    
    review_queue: dict = Field(default_factory=dict)
    exactitud: Optional[dict] = None
    totales_acumulados: PeriodStats


def _period_bounds(periodo: str, company_id: Optional[UUID]) -> tuple[datetime, datetime]:
    """Devuelve (desde, hasta) en UTC para el período pedido."""
    ahora = datetime.now(timezone.utc)
    
    if periodo == "hoy":
        desde = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        hasta = desde + timedelta(days=1)
    elif periodo == "mes_actual":
        desde = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if ahora.month == 12:
            hasta = ahora.replace(year=ahora.year + 1, month=1, day=1)
        else:
            hasta = ahora.replace(month=ahora.month + 1, day=1)
    elif periodo == "mes_anterior":
        if ahora.month == 1:
            desde = ahora.replace(year=ahora.year - 1, month=12, day=1)
        else:
            desde = ahora.replace(month=ahora.month - 1, day=1)
        desde = desde.replace(hour=0, minute=0, second=0, microsecond=0)
        hasta = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "ano_actual":
        desde = ahora.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        hasta = ahora.replace(year=ahora.year + 1, month=1, day=1)
    elif periodo == "ano_anterior":
        desde = ahora.replace(year=ahora.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        hasta = ahora.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Período desconocido: {periodo}")
    
    return desde, hasta


async def _compute_period_stats(
    db: AsyncSession,
    company_id: Optional[UUID],
    desde: datetime,
    hasta: datetime,
) -> PeriodStats:
    """Calcula estadísticas para un rango de fechas."""
    company_filter = [TicketModel.company_id == company_id] if company_id else []
    
    # Tickets en el período
    ticket_q = select(
        func.count(TicketModel.id).label("total"),
        func.sum(TicketModel.total_amount).label("monto_total"),
        func.count(TicketModel.id).filter(TicketModel.extraction_status == ExtractionStatus.PENDIENTE.value).label("pendientes"),
        func.count(TicketModel.id).filter(TicketModel.extraction_status == ExtractionStatus.APROBADO.value).label("aprobados"),
        func.count(TicketModel.id).filter(TicketModel.extraction_status == ExtractionStatus.RECHAZADO.value).label("rechazados"),
        func.count(TicketModel.id).filter(TicketModel.extraction_status == ExtractionStatus.AUTO_APROBADO.value).label("auto_aprobados"),
        func.sum(TicketModel.total_amount).filter(TicketModel.extraction_status == ExtractionStatus.PENDIENTE.value).label("monto_pendiente"),
    ).where(
        and_(
            TicketModel.created_at >= desde,
            TicketModel.created_at < hasta,
            *company_filter
        )
    )
    ticket_result = (await db.execute(ticket_q)).one()
    
    # Movimientos bancarios en el período
    bank_filter = [BankTransactionModel.company_id == company_id] if company_id else []
    bank_q = select(func.count(BankTransactionModel.id)).where(
        and_(
            BankTransactionModel.created_at >= desde,
            BankTransactionModel.created_at < hasta,
            *bank_filter
        )
    )
    bank_count = (await db.execute(bank_q)).scalar() or 0
    
    # Conciliaciones en el período (por ticket creado en el período)
    recon_q = select(
        ReconciliationModel.match_status,
        func.count(ReconciliationModel.id)
    ).join(
        TicketModel, ReconciliationModel.ticket_id == TicketModel.id
    ).where(
        and_(
            TicketModel.created_at >= desde,
            TicketModel.created_at < hasta,
            *company_filter
        )
    ).group_by(ReconciliationModel.match_status)
    recon_results = (await db.execute(recon_q)).all()
    
    recon_counts = {status: count for status, count in recon_results}
    
    return PeriodStats(
        tickets_total=ticket_result.total or 0,
        tickets_pendientes=ticket_result.pendientes or 0,
        tickets_aprobados=ticket_result.aprobados or 0,
        tickets_rechazados=ticket_result.rechazados or 0,
        tickets_auto_aprobados=ticket_result.auto_aprobados or 0,
        monto_total=ticket_result.monto_total or Decimal("0"),
        monto_pendiente=ticket_result.monto_pendiente or Decimal("0"),
        bank_transactions=bank_count,
        reconciliations_perfect=recon_counts.get(MatchStatus.PERFECT.value, 0),
        reconciliations_manual=recon_counts.get(MatchStatus.MANUAL.value, 0),
        reconciliations_discrepancy=recon_counts.get(MatchStatus.DISCREPANCY.value, 0),
    )


@router.get("/", response_model=DashboardResponse)
async def get_dashboard(
    company_id: Optional[UUID] = Query(None, description="Filtrar por empresa"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
) -> DashboardResponse:
    """Dashboard con estadísticas por período y métricas clave."""
    
    company_name = None
    if company_id:
        company = await db.get(CompanyModel, company_id)
        company_name = company.name if company else None
    
    periodos = ["hoy", "mes_actual", "mes_anterior", "ano_actual", "ano_anterior"]
    stats = {}
    for p in periodos:
        desde, hasta = _period_bounds(p, company_id)
        stats[p] = await _compute_period_stats(db, company_id, desde, hasta)
    
    # Totales acumulados
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    totales = await _compute_period_stats(db, company_id, epoch, far_future)
    
    # Cola de revisión
    review_filter = [TicketModel.company_id == company_id] if company_id else []
    review_q = select(
        TicketModel.extraction_status,
        func.count(TicketModel.id)
    ).where(
        and_(
            TicketModel.extraction_status.in_([
                ExtractionStatus.PENDIENTE.value,
                ExtractionStatus.RECHAZADO.value
            ]),
            *review_filter
        )
    ).group_by(TicketModel.extraction_status)
    review_results = (await db.execute(review_q)).all()
    review_queue = {status: count for status, count in review_results}
    
    # Exactitud (solo si hay empresa)
    exactitud = None
    if company_id:
        try:
            report = await compute_accuracy_report(db, company_id)
            exactitud = {
                "objetivo": report.objetivo,
                "veredicto_global": report.veredicto_global,
                "explicacion": report.explicacion,
                "por_origen": [
                    {
                        "origen": o.origen,
                        "aciertos": o.aciertos,
                        "incorrectos": o.incorrectos,
                        "pendientes": o.pendientes,
                        "exactitud": o.exactitud,
                        "intervalo_inferior": o.intervalo_inferior,
                        "intervalo_superior": o.intervalo_superior,
                        "veredicto": o.veredicto,
                    }
                    for o in report.por_origen
                ]
            }
        except Exception:
            exactitud = None
    
    return DashboardResponse(
        company_id=company_id,
        company_name=company_name,
        hoy=stats["hoy"],
        mes_actual=stats["mes_actual"],
        mes_anterior=stats["mes_anterior"],
        ano_actual=stats["ano_actual"],
        ano_anterior=stats["ano_anterior"],
        review_queue=review_queue,
        exactitud=exactitud,
        totales_acumulados=totales,
    )