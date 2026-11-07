#!/usr/bin/env python3
"""Borra la empresa DEMO y todos sus datos, y opcionalmente la vuelve a crear (P0-1).

Uso:
    PYTHONPATH=. python3 scripts/reset_demo.py              # pregunta antes de borrar
    PYTHONPATH=. python3 scripts/reset_demo.py --si          # sin preguntar (cron)
    PYTHONPATH=. python3 scripts/reset_demo.py --si --resembrar

DOBLE SEGURO: solo borra la empresa con tax_id DEM010101XXX y plan 'demo'.
Si esa empresa no es plan demo (alguien le cambio el plan a mano), se niega:
este script no debe borrar jamas una empresa real. Con `--resembrar` vuelve
crear_demo.py y pide la contrasena del admin demo por teclado, salvo que
`DEMO_PASSWORD` este en el entorno (para el reset nocturno por cron).

RESET NOCTURNO (no se crea aqui: programar un cron necesita aprobacion):
    0 3 * * * cd /ruta/al/proyecto && DEMO_PASSWORD='...' PYTHONPATH=. python3 scripts/reset_demo.py --si --resembrar
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, engine
from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.company import CompanyModel
from app.models.inventario import CompraItemModel, CompraModel, MovimientoInventarioModel, ProductoModel
from app.models.lote import LoteModel
from app.models.reconciliation import ReconciliationModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.models.tipo_gasto import TipoGastoModel
from app.models.user import UserModel
from scripts.crear_demo import DEMO_TAX_ID, crear_demo

# El orden importa: los hijos antes que los padres, y los tickets antes que
# sus documentos (el trigger append-only de 0009 prohibe borrar un documento
# mientras su ticket exista; al borrar el ticket, los documentos caen en
# cascada). Mismo orden que `scan_reset.limpiar`.
_BORRADOS = [
    ReconciliationModel,
    CompraItemModel,
    CompraModel,
    MovimientoInventarioModel,
    TicketModel,
    ScanEventModel,
    ScanFileModel,
    BankTransactionModel,
    AccountingMappingModel,
    CierrePeriodoModel,
    LoteModel,
    TipoGastoModel,
    ProductoModel,
    UserModel,
]


async def reset_demo(si: bool, resembrar: bool) -> int:
    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(
                select(CompanyModel).where(CompanyModel.tax_id == DEMO_TAX_ID)
            )
        ).scalar_one_or_none()
        if empresa is None:
            print("No hay empresa demo que borrar.")
            if resembrar:
                return await crear_demo(os.environ.get("DEMO_PASSWORD"))
            return 0
        # DOBLE SEGURO: si no es plan demo, no se toca. Un cambio de plan a
        # mano no convierte una empresa real en demo.
        if empresa.plan != "demo":
            print(
                f"La empresa {DEMO_TAX_ID} existe pero su plan es '{empresa.plan}', no 'demo'."
            )
            print("Este script no borra empresas reales. Revisa el plan a mano.")
            return 1

        if not si:
            respuesta = input(
                "Va a borrar la empresa DEMO y TODOS sus datos. "
                "Escribe BORRAR DEMO para confirmar: "
            )
            if respuesta.strip() != "BORRAR DEMO":
                print("Cancelado.")
                return 1

        for modelo in _BORRADOS:
            await session.execute(
                delete(modelo).where(modelo.company_id == empresa.id)
            )
        await session.delete(empresa)
        await session.commit()
        print(f"Empresa demo borrada ({empresa.name}).")

    if resembrar:
        return await crear_demo(os.environ.get("DEMO_PASSWORD"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Borra la empresa DEMO y sus datos")
    parser.add_argument("--si", action="store_true", help="No preguntar antes de borrar")
    parser.add_argument("--resembrar", action="store_true", help="Volver a crear la demo despues de borrarla")
    args = parser.parse_args()
    return asyncio.run(reset_demo(args.si, args.resembrar))


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        asyncio.run(engine.dispose())
    sys.exit(codigo)
