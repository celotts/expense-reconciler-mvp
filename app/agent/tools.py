import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any


def _to_decimal(valor: Any) -> Decimal:
    limpio = str(valor).strip().replace("$", "").replace(",", "")
    return Decimal(limpio)


def verificar_consistencia_aritmetica(
    subtotal: Any,
    impuestos: Any,
    total: Any,
    tolerancia: Any = "0.01",
) -> dict[str, Any]:
    """Valida con Decimal exacto si subtotal + impuestos coincide con el total."""
    try:
        sub_d = _to_decimal(subtotal)
        imp_d = _to_decimal(impuestos)
        tot_d = _to_decimal(total)
        tol_d = _to_decimal(tolerancia)
    except InvalidOperation as exc:
        return {"cuadra": False, "error": f"Monto decimal inválido: {exc}"}

    calculado = sub_d + imp_d
    diferencia = abs(calculado - tot_d)
    cuadra = diferencia <= tol_d
    return {
        "cuadra": cuadra,
        "total_calculado": str(calculado),
        "total_reportado": str(tot_d),
        "diferencia": str(diferencia),
    }


async def _consultar_resumen_proveedor_async(
    patron_nombre: str,
    limite: int = 20,
) -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal
    from app.models.ticket import TicketModel

    async with AsyncSessionLocal() as session:
        stmt = (
            select(
                func.count(TicketModel.id).label("total_tickets"),
                func.coalesce(func.sum(TicketModel.total_amount), Decimal("0.00")).label("suma_total"),
                func.coalesce(func.avg(TicketModel.total_amount), Decimal("0.00")).label("promedio_total"),
                func.max(TicketModel.expense_date).label("ultima_fecha"),
            )
            .where(TicketModel.provider_name.ilike(f"%{patron_nombre}%"))
        )
        row = (await session.execute(stmt)).one()

        recientes_stmt = (
            select(
                TicketModel.provider_name,
                TicketModel.total_amount,
                TicketModel.expense_date,
            )
            .where(TicketModel.provider_name.ilike(f"%{patron_nombre}%"))
            .order_by(TicketModel.expense_date.desc(), TicketModel.created_at.desc())
            .limit(limite)
        )
        recientes_rows = (await session.execute(recientes_stmt)).all()

    total_tickets = int(row.total_tickets or 0)
    suma_dec = _to_decimal(row.suma_total).quantize(Decimal("0.01"))
    prom_dec = _to_decimal(row.promedio_total).quantize(Decimal("0.01"))

    muestra = [
        {
            "provider_name": r.provider_name,
            "total_amount": str(_to_decimal(r.total_amount).quantize(Decimal("0.01"))),
            "expense_date": r.expense_date.isoformat() if r.expense_date else None,
        }
        for r in recientes_rows[:5]
    ]

    return {
        "modo": "solo_lectura",
        "proveedor_consultado": patron_nombre,
        "total_tickets": total_tickets,
        "suma_total": str(suma_dec),
        "promedio_total": str(prom_dec),
        "ultima_fecha": row.ultima_fecha.isoformat() if row.ultima_fecha else None,
        "muestra_recientes": muestra,
    }


def _ejecutar_corutina_segura(coro: Any) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def obtener_resumen_proveedor(nombre_normalizado: str) -> dict[str, Any]:
    """Consulta de solo lectura del historial de tickets por provider_name."""
    limpio = " ".join(str(nombre_normalizado).strip().lower().split())
    if not limpio:
        return {
            "modo": "solo_lectura",
            "proveedor": "",
            "estado_consulta": "error",
            "error": "El nombre de proveedor está vacío.",
        }

    try:
        resultado = _ejecutar_corutina_segura(_consultar_resumen_proveedor_async(limpio))
        resultado["estado_consulta"] = "ok"
        return resultado
    except Exception as exc:
        return {
            "modo": "solo_lectura",
            "proveedor": limpio,
            "estado_consulta": "bd_no_disponible",
            "detalle": str(exc),
        }
