#!/usr/bin/env python3
"""Crea la empresa DEMO con datos semilla (P0-1).

La demo es una empresa como cualquier otra, con plan 'demo': se entra con
su propia cuenta admin y solo se ven sus datos. Sirve para ensenar el
sistema sin tocar datos reales y para probar que un usuario de una empresa
no ve los de otra.

Uso:
    PYTHONPATH=. python3 scripts/crear_demo.py
    docker compose exec expense-api python scripts/crear_demo.py

La contrasena del admin demo se pide por teclado y no por argumento (igual
que `scripts/crear_usuario.py`): un `--password` queda en el historial del
shell. Idempotente: si la empresa demo ya existe, dice que existe y termina
sin duplicar nada. Para borrarla y volver a crearla: `scripts/reset_demo.py`.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hashear_contrasena
from app.core.time import utcnow
from app.models.bank_transaction import BankTransactionModel
from app.models.company import PLAN_DEMO, CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.services.tipos_gasto_service import asegurar_catalogo

DEMO_NOMBRE = "Empresa DEMO"
DEMO_TAX_ID = "DEM010101XXX"
DEMO_EMAIL = "demo@expense-reconciler.demo"


async def crear_demo(contrasena: str | None = None) -> int:
    """Crea la empresa demo. `contrasena` solo llega desde `reset_demo.py`
    con `DEMO_PASSWORD` (reset nocturno); en uso interactivo se pide por
    teclado. Nunca por argumento: quedaria en el historial del shell."""
    async with AsyncSessionLocal() as session:
        existe = (
            await session.execute(
                select(CompanyModel).where(CompanyModel.tax_id == DEMO_TAX_ID)
            )
        ).scalar_one_or_none()
        if existe is not None:
            print(f"La empresa demo ya existe ({existe.name}, plan={existe.plan}).")
            print("Para borrarla y volver a crearla: PYTHONPATH=. python3 scripts/reset_demo.py")
            return 0

        empresa = CompanyModel(
            name=DEMO_NOMBRE,
            tax_id=DEMO_TAX_ID,
            plan=PLAN_DEMO,
            trial_hasta=utcnow() + timedelta(days=30),
            tickets_usados_este_mes=0,
        )
        session.add(empresa)
        await session.flush()

        # El catalogo de tipos de gasto es por empresa y se siembra aqui.
        await asegurar_catalogo(session, empresa.id)

        contrasena = contrasena or getpass.getpass("Contrasena del admin demo: ")
        if len(contrasena) < 8:
            print("La contrasena necesita al menos 8 caracteres.")
            await session.rollback()
            return 1
        admin = UserModel(
            email=DEMO_EMAIL,
            nombre="Demo",
            password_hash=hashear_contrasena(contrasena),
            perfil="admin",
            grupo="administracion",
            company_id=empresa.id,
        )
        session.add(admin)

        # Datos de ejemplo: 5 gastos y 5 movimientos, 4 de los cuales
        # cuadran con 4 gastos (el quinto gasto queda sin conciliar a
        # proposito, para que la pantalla de conciliacion tenga algo que
        # mostrar). Son datos inventados para la demo, no gastos reales.
        gastos = [
            ("BODEGA AURRERA", "NWM970J244W4", "234.00", "37.44", 1, "SUPERMERCADO"),
            ("OXXO", "OXX010101000", "89.50", "14.32", 2, "TIENDA CONVENIENCIA"),
            ("SORIANA", "TS0991022PB6", "65.00", "10.40", 3, "SUPERMERCADO"),
            ("UBER", "UBR010101000", "150.00", "24.00", 4, "TRANSPORTE"),
            ("OFFICE DEPOT", "ODP010101000", "420.00", "67.20", 5, "PAPELERIA"),
        ]
        tickets = []
        for proveedor, rfc, total, iva, dias, categoria in gastos:
            ticket = TicketModel(
                id=uuid.uuid4(),
                company_id=empresa.id,
                provider_name=proveedor,
                provider_tax_id=rfc,
                total_amount=Decimal(total),
                tax_amount=Decimal(iva),
                expense_date=date.today() - timedelta(days=dias),
                category=categoria,
                raw_text=f"Ticket demo de {proveedor} - Total: {total}",
            )
            tickets.append(ticket)
            session.add(ticket)

        movimientos = [
            ("234.00", "BODEGA AURRERA COMPRA", "DEM-001", 1),
            ("89.50", "OXXO TIENDA 1234", "DEM-002", 2),
            ("65.00", "SORIANA SUCURSAL", "DEM-003", 3),
            ("150.00", "UBER VIAJE", "DEM-004", 4),
            ("75.50", "STARBUCKS CAFE", "DEM-005", 6),
        ]
        for monto, descripcion, referencia, dias in movimientos:
            session.add(
                BankTransactionModel(
                    id=uuid.uuid4(),
                    company_id=empresa.id,
                    transaction_date=date.today() - timedelta(days=dias),
                    amount=Decimal(monto),
                    description=descripcion,
                    reference=referencia,
                )
            )

        await session.commit()

    print("Empresa demo creada:")
    print(f"  Empresa: {DEMO_NOMBRE} ({DEMO_TAX_ID}, plan demo, prueba 30 dias)")
    print(f"  Admin:   {DEMO_EMAIL}")
    print(f"  Datos:   {len(tickets)} tickets, {len(movimientos)} movimientos bancarios")
    print("  Entra con ese correo y la contrasena que acabas de escribir.")
    return 0


if __name__ == "__main__":
    try:
        codigo = asyncio.run(crear_demo())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(codigo)

