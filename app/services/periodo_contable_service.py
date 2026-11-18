"""Control de periodos contables cerrados.

Un ticket cuya fecha cae en un periodo cerrado (registro en `cierres_periodo`)
se rechaza por defecto. Solo se admite con override administrativo trazable:
motivo explicito y registro de quien autorizo y cuando.

La verificacion es por (empresa, periodo YYYY-MM) derivado de `expense_date`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.models.cierre_periodo import CierrePeriodoModel


class PeriodoCerradoError(ValueError):
    """La fecha cae en un periodo cerrado y no hay override valido/trazable."""


@dataclass(frozen=True)
class OverridePeriodoCerrado:
    autorizado_por: str
    autorizado_at: datetime
    motivo: str


def periodo_de_fecha(fecha: date) -> str:
    return fecha.strftime("%Y-%m")


async def periodo_esta_cerrado(
    db: AsyncSession, *, company_id, fecha: date
) -> bool:
    periodo = periodo_de_fecha(fecha)
    cierre = (
        await db.execute(
            select(CierrePeriodoModel.id).where(
                and_(
                    CierrePeriodoModel.company_id == company_id,
                    CierrePeriodoModel.periodo == periodo,
                )
            )
        )
    ).scalar_one_or_none()
    return cierre is not None


def validar_override_periodo_cerrado(
    *,
    override_solicitado: bool | None,
    motivo: str | None,
    autorizado_por: str | None,
) -> OverridePeriodoCerrado | None:
    """Valida y construye la traza del override cuando se solicita.

    `autorizado_por` debe venir del token/contexto autenticado (no del cuerpo).
    """
    if not override_solicitado:
        return None
    motivo_limpio = (motivo or "").strip()
    if not motivo_limpio:
        raise PeriodoCerradoError(
            "El periodo esta cerrado: se requiere motivo para el override administrativo."
        )
    if not autorizado_por or not str(autorizado_por).strip():
        raise PeriodoCerradoError(
            "El periodo esta cerrado: el override requiere un usuario autenticado trazable."
        )
    return OverridePeriodoCerrado(
        autorizado_por=str(autorizado_por).strip(),
        autorizado_at=utcnow(),
        motivo=motivo_limpio,
    )


async def asegurar_periodo_abierto_o_override(
    db: AsyncSession,
    *,
    company_id,
    fecha: date | None,
    override_solicitado: bool | None,
    motivo: str | None,
    autorizado_por: str | None,
) -> OverridePeriodoCerrado | None:
    """Rechaza si la fecha cae en periodo cerrado sin override trazable.

    Si el periodo esta abierto, limpia/ignora override (no hay nada que autorizar).
    Devuelve la traza del override cuando aplica; None en caso contrario.
    """
    if fecha is None:
        return None
    cerrado = await periodo_esta_cerrado(db, company_id=company_id, fecha=fecha)
    if not cerrado:
        return None

    traza = validar_override_periodo_cerrado(
        override_solicitado=override_solicitado,
        motivo=motivo,
        autorizado_por=autorizado_por,
    )
    if traza is None:
        raise PeriodoCerradoError(
            f"El periodo {periodo_de_fecha(fecha)} esta cerrado. "
            "Se requiere override administrativo trazable para registrar o modificar este ticket."
        )
    return traza


def aplicar_override_en_ticket(ticket, traza: OverridePeriodoCerrado | None) -> None:
    """Aplica o limpia campos de override trazable en un TicketModel."""
    if traza is None:
        ticket.periodo_cerrado_override = False
        ticket.periodo_cerrado_motivo = None
        ticket.periodo_cerrado_autorizado_por = None
        ticket.periodo_cerrado_autorizado_at = None
        return
    ticket.periodo_cerrado_override = True
    ticket.periodo_cerrado_motivo = traza.motivo
    ticket.periodo_cerrado_autorizado_por = traza.autorizado_por
    ticket.periodo_cerrado_autorizado_at = traza.autorizado_at