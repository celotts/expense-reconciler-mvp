"""Explicaciones de ticket con IA asistida: solo lectura, nunca una decision.

Un ticket ajeno se reporta como 404, igual que en `tickets.py`: decir "no tienes
permiso" confirmaria que el id adivinado existe. La comprobacion se hace aqui,
en el adaptador, porque el servicio no conoce empresas ni peticiones HTTP.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import EmpresaVisible
from app.models.ticket import TicketModel
from app.schemas.ia_asistida import ExplicacionTicketResponse
from app.services.ia_asistida import ExplicadorIATicket

router = APIRouter()


def get_explicador_ia() -> ExplicadorIATicket:
    """Dependencia separada para poder probar el endpoint sin un modelo real."""
    return ExplicadorIATicket()


@router.get(
    "/tickets/{ticket_id}/explicacion",
    response_model=ExplicacionTicketResponse,
    summary="Explicacion asistida de un ticket (solo lectura)",
)
async def explicar_ticket(
    ticket_id: UUID,
    empresa: EmpresaVisible,
    db: AsyncSession = Depends(get_db),
    explicador: ExplicadorIATicket = Depends(get_explicador_ia),
) -> ExplicacionTicketResponse:
    consulta = select(TicketModel).where(TicketModel.id == ticket_id)
    if empresa is not None:
        consulta = consulta.where(TicketModel.company_id == empresa)
    ticket = (await db.execute(consulta)).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El ticket no existe"
        )
    return await explicador.explicar(ticket)
