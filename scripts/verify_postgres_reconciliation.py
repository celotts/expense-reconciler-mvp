#!/usr/bin/env python3
"""Corre el motor de conciliacion contra Postgres real.

SQLite no sirve para comprobar lo que importa aqui. En SQLite los `Decimal`
llegan como `float` de la fila, y el cambio de `float(diff_monto) * 100 +
diff_dias` a una tupla de comparacion no se nota. En Postgres la columna es
`NUMERIC(12,2)` y llega como `Decimal` de verdad, que es exactamente el tipo
que el `float` estaba destruyendo en la comparacion que decide a que
comprobante se concilia.

Ademas comprueba lo que SQLite no comprueba: que el estado que se guarda en la
columna `String(50)` es el valor y no otra cosa, y que la consulta ordena de
verdad cuando hay un indice encima.

Uso:  PYTHONPATH=. python3 scripts/verify_postgres_reconciliation.py
Salida: 0 si todo cumple, 1 si algo falla.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal, engine
from app.core.enums import MatchStatus
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.reconciliation import ReconciliationRunRequest
from app.services.reconciliation_service import run_reconciliation

MARCA = "verify-postgres-recon"

fallos: list[str] = []
comprobaciones = 0


def revisar(condicion: bool, descripcion: str) -> None:
    global comprobaciones
    comprobaciones += 1
    if not condicion:
        fallos.append(descripcion)
        print(f"  FALLA  {descripcion}")


async def _limpiar(session) -> None:
    await session.execute(
        text("DELETE FROM reconciliations WHERE ticket_id IN (SELECT id FROM tickets WHERE provider_name LIKE :m)"),
        {"m": f"%{MARCA}%"},
    )
    await session.execute(
        text("DELETE FROM bank_transactions WHERE description LIKE :m"), {"m": f"%{MARCA}%"}
    )
    await session.execute(
        text("DELETE FROM tickets WHERE provider_name LIKE :m"), {"m": f"%{MARCA}%"}
    )
    await session.execute(text("DELETE FROM companies WHERE name LIKE :m"), {"m": f"%{MARCA}%"})
    await session.commit()


async def main() -> int:
    dsn = os.getenv("DATABASE_URL", "")
    # El motor se crea con echo=True en la configuracion del proyecto. Aqui
    # solo interesa el resultado, y el SQL crudo tapa los mensajes.
    engine.echo = False
    print(f"Postgres de conciliacion contra {dsn.split('@')[-1]}\n")

    async with AsyncSessionLocal() as session:
        await _limpiar(session)

        empresa = CompanyModel(name=f"{MARCA}-empresa", tax_id="VER001010X99")
        session.add(empresa)
        await session.flush()

        # NUMERIC(12,2) de verdad: la diferencia de un centavo tiene que
        # sobrevivir al viaje. Un centavo es el borde de la tolerancia por
        # defecto, asi que si el float lo degrada, este caso se concilia o no
        # segun como haya salido la cuenta.
        # Montos distintos a proposito. Con dos tickets del MISMO importe, el
        # reparto depende del orden de recorrido y este script mediria eso en
        # vez de lo que quiere medir.
        casos = [
            (f"{MARCA}-perfecto", Decimal("100.00"), Decimal("-100.00"), date(2025, 1, 15), "PAGO EXACTO"),
            (f"{MARCA}-centavo", Decimal("200.00"), Decimal("-200.01"), date(2025, 1, 15), "PAGO CENTAVO"),
            (f"{MARCA}-discrepancia", Decimal("300.00"), Decimal("-350.00"), date(2025, 1, 15), "PAGO OTRO MONTO"),
            # Lejos en monto Y en fecha: no hay nada que reportar. Si el
            # movimiento estuviera en la misma fecha, seria una discrepancia y
            # este caso estaria midiendo otra cosa.
            (f"{MARCA}-lejos", Decimal("400.00"), Decimal("-842.00"), date(2024, 3, 2), "RENTA ENERO"),
        ]
        for proveedor, total, monto, fecha, descripcion in casos:
            session.add(
                TicketModel(
                    company_id=empresa.id,
                    provider_name=proveedor,
                    total_amount=total,
                    expense_date=fecha,
                )
            )
            # El caso "lejos" se aparta a otra epoca a proposito: el filtro de
            # discrepancia mira la FECHA, asi que un movimiento en la misma
            # fecha con otro monto si se reportaria.
            fecha_movimiento = date(2024, 1, 5) if "lejos" in proveedor else fecha
            session.add(
                BankTransactionModel(
                    company_id=empresa.id,
                    transaction_date=fecha_movimiento,
                    amount=monto,
                    description=f"{MARCA}-{descripcion}",
                )
            )
        await session.commit()

        # Que los montos lleguen como Decimal y no como float. Si esto falla, el
        # resto de las comprobaciones de este script no significan nada.
        tipos = await session.execute(
            text(
                "SELECT pg_typeof(total_amount)::text AS t FROM tickets "
                "WHERE provider_name LIKE :m LIMIT 1"
            ),
            {"m": f"%{MARCA}%"},
        )
        tipo_total = tipos.scalar_one()
        revisar(
            tipo_total == "numeric",
            f"total_amount llega como numeric en Postgres (vino {tipo_total})",
        )

        tipos = await session.execute(
            text(
                "SELECT pg_typeof(amount)::text AS t FROM bank_transactions "
                "WHERE description LIKE :m LIMIT 1"
            ),
            {"m": f"%{MARCA}%"},
        )
        tipo_monto = tipos.scalar_one()
        revisar(
            tipo_monto == "numeric",
            f"amount llega como numeric en Postgres (vino {tipo_monto})",
        )

        response = await run_reconciliation(
            session, ReconciliationRunRequest(company_id=empresa.id)
        )

        por_proveedor = {m.ticket_provider: m for m in response.matches}
        revisar(len(por_proveedor) == 3, f"se esperaban 3 filas y salieron {len(por_proveedor)}")
        revisar(response.unmatched_tickets == 1, f"sin conciliar quedo {response.unmatched_tickets}, se esperaba 1")

        if f"{MARCA}-perfecto" in por_proveedor:
            m = por_proveedor[f"{MARCA}-perfecto"]
            revisar(m.match_status == MatchStatus.PERFECT.value, f"el exacto no es PERFECT: {m.match_status}")
            revisar(m.amount_diff == Decimal("0.00"), f"el exacto tiene diff {m.amount_diff}")

        if f"{MARCA}-centavo" in por_proveedor:
            m = por_proveedor[f"{MARCA}-centavo"]
            revisar(
                m.amount_diff == Decimal("0.01"),
                f"el de un centavo tiene diff {m.amount_diff}, no 0.01",
            )
            revisar(
                m.match_status == MatchStatus.PERFECT.value,
                f"un centavo con tolerancia 0.01 tiene que conciliar: {m.match_status}",
            )

        if f"{MARCA}-discrepancia" in por_proveedor:
            m = por_proveedor[f"{MARCA}-discrepancia"]
            revisar(
                m.match_status == MatchStatus.DISCREPANCY.value,
                f"otro monto el mismo dia no es discrepancia: {m.match_status}",
            )
            revisar(m.amount_diff == Decimal("50.00"), f"la discrepancia tiene diff {m.amount_diff}")

        revisar(
            f"{MARCA}-lejos" not in por_proveedor,
            "un movimiento de otra epoca y otro monto no se reporta",
        )

        # Lo que quedo escrito, leido de la base y no de la respuesta en
        # memoria. Los dos pueden diferir y solo este mira el segundo.
        guardadas = (
            await session.execute(
                text(
                    "SELECT match_status FROM reconciliations r "
                    "JOIN tickets t ON t.id = r.ticket_id "
                    "WHERE t.provider_name LIKE :m"
                ),
                {"m": f"%{MARCA}%"},
            )
        ).scalars().all()

        validos = [s.value for s in MatchStatus]
        revisar(
            all(e in validos for e in guardadas),
            f"en la base hay estados que no son del enum: {[e for e in guardadas if e not in validos]}",
        )
        revisar(
            MatchStatus.DISCREPANCY.value in guardadas,
            f"no quedo ninguna DISCREPANCY escrita y hay {guardadas}",
        )

        # El ORDER BY de los tickets: se comprueba contra el plan, no contra el
        # resultado. El resultado ya esta cubierto por los tests.
        plan = await session.execute(
            text(
                "EXPLAIN SELECT * FROM tickets WHERE company_id = :c "
                "ORDER BY expense_date, id"
            ),
            {"c": empresa.id},
        )
        revisar(
            any("Sort" in linea or "Index" in linea for linea in plan.scalars().all()),
            "la consulta de tickets no ordena en el plan de Postgres",
        )

        await _limpiar(session)

    print()
    if fallos:
        print(f"{len(fallos)} de {comprobaciones} comprobaciones fallaron:")
        for f in fallos:
            print(f"  - {f}")
        await engine.dispose()
        return 1

    print(f"{comprobaciones} comprobaciones contra Postgres real: CUMPLE")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
