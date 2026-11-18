"""Huella de negocio anti-duplicados (autoridad de servidor).

Complementa la huella de ARCHIVO (`tickets.source_hash` /
`ticket_documents.sha256`). Dos comprobantes distintos en bytes pueden ser el
mismo gasto (reimpresion, captura distinta), y el mismo archivo puede subirse
dos veces: las dos defensas responden preguntas distintas.

La huella de negocio se calcula como SHA-256 de una cadena canonica:
    identificacion_fiscal + "|" + fecha(YYYY-MM-DD) + "|" + folio + "|" + monto

- `identificacion_fiscal`: RFC/RUT normalizado (mayusculas, sin espacios).
- `fecha`: `expense_date` en ISO (YYYY-MM-DD).
- `folio`: folio/factura normalizado; si no hay, cadena vacia.
- `monto`: `total_amount` con 2 decimales fijos (punto decimal).

Si falta alguno de los campos minimos para identificar el gasto (fecha o monto),
no se inventa una huella: se devuelve None. Un hash inventado seria una colision
o un falso negativo sin forma de auditarlo.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import TicketModel

_CENTAVOS = Decimal("0.01")


def _normalizar_texto(valor: str | None) -> str:
    if valor is None:
        return ""
    return str(valor).strip().upper()


def _monto_canonico(valor: Decimal | int | float | str | None) -> str | None:
    if valor is None:
        return None
    try:
        dec = Decimal(str(valor)).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return format(dec, "f")


def calcular_hash_duplicado(
    *,
    identificacion_fiscal: str | None,
    fecha: date | None,
    folio: str | None,
    total: Decimal | None,
) -> str | None:
    """Calcula la huella de negocio SHA-256 o None si no es determinable.

    Se exige fecha y monto: sin ellos la huella no identifica un gasto concreto.
    La identificacion fiscal y el folio pueden faltar (se canonizan a vacio),
    porque en tickets sin RFC/folio la combinacion fecha+monto sigue siendo una
    senal util y mejor que no tener control.
    """
    monto = _monto_canonico(total)
    if fecha is None or monto is None:
        return None

    canon = "|".join(
        [
            _normalizar_texto(identificacion_fiscal),
            fecha.isoformat(),
            _normalizar_texto(folio),
            monto,
        ]
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


async def buscar_duplicado_por_hash(
    db: AsyncSession,
    *,
    company_id,
    hash_duplicado: str,
    excluir_ticket_id=None,
) -> TicketModel | None:
    """Busca un ticket existente con la misma huella de negocio en la empresa."""
    stmt = select(TicketModel).where(
        and_(
            TicketModel.company_id == company_id,
            TicketModel.hash_duplicado == hash_duplicado,
        )
    )
    if excluir_ticket_id is not None:
        stmt = stmt.where(TicketModel.id != excluir_ticket_id)
    return (await db.execute(stmt)).scalar_one_or_none()