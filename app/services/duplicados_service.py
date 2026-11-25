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
from typing import Any

from sqlalchemy import and_, func, or_, select
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


def calcular_hash_del_ticket(ticket: TicketModel) -> str | None:
    """La huella de un ticket ya persistido, sin inventar campos ausentes.

    `provider_tax_id` y `identificacion_fiscal` pueden divergir en tickets
    antiguos: se prefiere el canonico y se cae al RFC si no hay.
    """
    fiscal = getattr(ticket, "identificacion_fiscal", None) or ticket.provider_tax_id
    return calcular_hash_duplicado(
        identificacion_fiscal=fiscal,
        fecha=ticket.expense_date,
        folio=ticket.folio,
        total=ticket.total_amount,
    )


async def buscar_duplicados_fiscales(
    db: AsyncSession,
    *,
    company_id,
    identificacion_fiscal: str | None,
    fecha,
    folio: str | None,
    total,
    excluir_ticket_id=None,
) -> list[TicketModel]:
    """Candidatos a duplicado por la huella fiscal de `0021`.

    La busqueda va por `hash_duplicado` (indice `ix_tickets_company_fiscal_huella`
    en la columna compuesta) y **nunca solo por monto**: dos gastos del mismo
    dia y el mismo total en comercios distintos son gastos distintos. Sin fecha
    o sin monto no se calcula huella y no se afirma duplicado: un falso positivo
    que bloquea la carga es mas caro que un falso negativo que se reporta.
    """
    # La busqueda va por los CAMPOS del indice compuesto, no solo por
    # `hash_duplicado`. Los tickets antiguos no traen hash, y un falso negativo
    # aqui deja al operador contar dos veces el mismo papel sin aviso.
    monto = _monto_canonico(total)
    if fecha is None or monto is None:
        return []

    condiciones = [
        TicketModel.company_id == company_id,
        TicketModel.expense_date == fecha,
        TicketModel.total_amount == Decimal(monto),
    ]
    fiscal = _normalizar_texto(identificacion_fiscal)
    if fiscal:
        condiciones.append(
            or_(
                func.upper(TicketModel.provider_tax_id) == fiscal,
                func.upper(TicketModel.identificacion_fiscal) == fiscal,
            )
        )
    folio_norm = _normalizar_texto(folio)
    if folio_norm:
        condiciones.append(func.upper(TicketModel.folio) == folio_norm)

    stmt = select(TicketModel).where(and_(*condiciones))
    if excluir_ticket_id is not None:
        stmt = stmt.where(TicketModel.id != excluir_ticket_id)
    stmt = stmt.order_by(TicketModel.created_at)
    return list((await db.execute(stmt)).scalars().all())


def datos_item_duplicado(ticket: TicketModel) -> dict[str, Any]:
    """Los datos del papel que hacen comparable un duplicado a simple vista."""
    fiscal = getattr(ticket, "identificacion_fiscal", None) or ticket.provider_tax_id
    return {
        "ticket_id": ticket.id,
        "company_id": ticket.company_id,
        "provider_name": ticket.provider_name,
        "provider_tax_id": ticket.provider_tax_id,
        "folio": ticket.folio,
        "expense_date": ticket.expense_date,
        "total_amount": ticket.total_amount,
        "hash_duplicado": calcular_hash_del_ticket(ticket),
        "estado_conciliacion": getattr(ticket, "estado_conciliacion", None),
        "source_file": ticket.source_file,
        "created_at": ticket.created_at,
    }


async def resumen_duplicados_fiscales(
    db: AsyncSession, *, company_id
) -> "ResumenDuplicados":
    """Agrupa por huella fiscal y cuenta cada gasto UNA sola vez.

    `total_conciliado_sin_duplicados` no suma todas las filas: suma un
    representante por grupos, y solo si ESE representante esta conciliado. Dos
    reenvios del mismo comprobante no son dos gastos, y un ticket pendiente no
    es gasto confirmado aunque su copia si lo este.
    """
    from app.schemas.caja_chica import (
        DuplicadoFiscalItem,
        GrupoDuplicados,
        ResumenDuplicados,
    )

    stmt = (
        select(TicketModel)
        .where(TicketModel.company_id == company_id)
        .order_by(TicketModel.created_at)
    )
    tickets = list((await db.execute(stmt)).scalars().all())

    grupos: dict[str, list[TicketModel]] = {}
    for ticket in tickets:
        hash_duplicado = calcular_hash_del_ticket(ticket)
        if hash_duplicado is None:
            continue
        grupos.setdefault(hash_duplicado, []).append(ticket)

    duplicados = {h: ts for h, ts in grupos.items() if len(ts) > 1}
    # El total no puede contar SOLO los grupos repetidos: entonces un ticket
    # unico conciliado desapareceria del resumen. Se cuentan todas las unidades
    # de gasto, una por huella; las que no tienen huella se cuentan por separado.
    unidades = list(grupos.values())
    unidades.extend(
        [ticket] for ticket in tickets if calcular_hash_del_ticket(ticket) is None
    )
    grupos_respuesta: list[GrupoDuplicados] = []
    total = Decimal("0.00")

    for _ts in unidades:
        conciliados = [
            t for t in _ts if getattr(t, "estado_conciliacion", None) == "CONCILIADO"
        ]
        representante = conciliados[0] if conciliados else _ts[0]
        monto = Decimal(representante.total_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if conciliados:
            total += monto
        if len(_ts) < 2:
            continue
        hash_grupo = calcular_hash_del_ticket(representante)
        grupos_respuesta.append(
            GrupoDuplicados(
                hash_duplicado=hash_grupo,
                tickets=[
                    DuplicadoFiscalItem.model_validate(datos_item_duplicado(t))
                    for t in _ts
                ],
                monto_total=monto,
            )
        )

    advertencia = None
    if duplicados:
        advertencia = (
            f"Hay {len(duplicados)} comprobantes repetidos. Cada gasto se cuenta "
            "una sola vez en el total conciliado."
        )
    return ResumenDuplicados(
        company_id=company_id,
        grupos=grupos_respuesta,
        total_conciliado_sin_duplicados=total.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        advertencia=advertencia,
    )