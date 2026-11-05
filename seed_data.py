#!/usr/bin/env python3
"""Seed script to populate the database with test data for Expense Reconciler."""
import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, engine, Base
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.bank_transaction import BankTransactionModel


async def seed_data():
    async with AsyncSession(engine) as db:
        # Check if companies already exist
        existing = await db.execute(select(CompanyModel))
        if existing.scalars().first():
            print("Database already has data, skipping seed.")
            return

        # Create test companies
        companies = [
            CompanyModel(
                id=uuid.uuid4(),
                name="Mi Taquería",
                tax_id="MTA123456789",
            ),
            CompanyModel(
                id=uuid.uuid4(),
                name="Tech Solutions SA",
                tax_id="TSS987654321",
            ),
            CompanyModel(
                id=uuid.uuid4(),
                name="Constructora El Sol",
                tax_id="CES111222333",
            ),
        ]
        for c in companies:
            db.add(c)
        await db.commit()
        print(f"Created {len(companies)} companies")

        # Create tickets for each company
        ticket_data = [
            # Mi Taquería tickets
            {"company_id": companies[0].id, "provider": "BODEGA AURRERA", "tax_id": "BAU010101000", "total": 44.00, "tax": 7.04, "days_ago": 1, "category": "SUPERMERCADO"},
            {"company_id": companies[0].id, "provider": "OXXO", "tax_id": "OXX010101000", "total": 89.50, "tax": 14.32, "days_ago": 2, "category": "TIENDA CONVENIENCIA"},
            {"company_id": companies[0].id, "provider": "COSTCO", "tax_id": "COS010101000", "total": 1250.00, "tax": 200.00, "days_ago": 5, "category": "MAYORISTA"},
            {"company_id": companies[0].id, "provider": "WALMART", "tax_id": "WAL010101000", "total": 345.75, "tax": 55.32, "days_ago": 7, "category": "SUPERMERCADO"},
            {"company_id": companies[0].id, "provider": "SAM'S CLUB", "tax_id": "SAM010101000", "total": 890.00, "tax": 142.40, "days_ago": 10, "category": "MAYORISTA"},
            
            # Tech Solutions SA tickets
            {"company_id": companies[1].id, "provider": "AMAZON WEB SERVICES", "tax_id": "AWS010101000", "total": 5000.00, "tax": 800.00, "days_ago": 3, "category": "SERVICIOS CLOUD"},
            {"company_id": companies[1].id, "provider": "GOOGLE CLOUD", "tax_id": "GCL010101000", "total": 3200.00, "tax": 512.00, "days_ago": 8, "category": "SERVICIOS CLOUD"},
            {"company_id": companies[1].id, "provider": "MICROSOFT AZURE", "tax_id": "AZU010101000", "total": 4500.00, "tax": 720.00, "days_ago": 12, "category": "SERVICIOS CLOUD"},
            {"company_id": companies[1].id, "provider": "DIGITALOCEAN", "tax_id": "DGO010101000", "total": 1200.00, "tax": 192.00, "days_ago": 15, "category": "SERVICIOS CLOUD"},
            
            # Constructora El Sol tickets
            {"company_id": companies[2].id, "provider": "HOME DEPOT", "tax_id": "HDE010101000", "total": 15000.00, "tax": 2400.00, "days_ago": 4, "category": "MATERIALES CONSTRUCCION"},
            {"company_id": companies[2].id, "provider": "CEMEX", "tax_id": "CMX010101000", "total": 25000.00, "tax": 4000.00, "days_ago": 6, "category": "MATERIALES CONSTRUCCION"},
            {"company_id": companies[2].id, "provider": "FERRETERIA EL TORNILLO", "tax_id": "FET010101000", "total": 3500.00, "tax": 560.00, "days_ago": 9, "category": "HERRAMIENTAS"},
            {"company_id": companies[2].id, "provider": "PAINT DEPOT", "tax_id": "PNT010101000", "total": 2800.00, "tax": 448.00, "days_ago": 14, "category": "PINTURAS"},
        ]

        tickets = []
        for td in ticket_data:
            t = TicketModel(
                id=uuid.uuid4(),
                company_id=td["company_id"],
                provider_name=td["provider"],
                provider_tax_id=td["tax_id"],
                total_amount=Decimal(str(td["total"])),
                tax_amount=Decimal(str(td["tax"])),
                expense_date=date.today() - timedelta(days=td["days_ago"]),
                category=td["category"],
                raw_text=f"Ticket de {td['provider']} - Total: {td['total']}",
            )
            tickets.append(t)
            db.add(t)
        await db.commit()
        print(f"Created {len(tickets)} tickets")

        # Create bank transactions matching some tickets (for reconciliation testing)
        bank_txns = [
            # Mi Taquería - matching transactions
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=1),
                amount=Decimal("44.00"),
                description="BODEGA AURRERA COMPRA",
                reference="REF001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=2),
                amount=Decimal("89.50"),
                description="OXXO TIENDA 1234",
                reference="REF002",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=5),
                amount=Decimal("1250.00"),
                description="COSTCO WHOLESALE",
                reference="REF003",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=7),
                amount=Decimal("345.75"),
                description="WALMART SUPERCENTER",
                reference="REF004",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=10),
                amount=Decimal("890.00"),
                description="SAMS CLUB MEXICO",
                reference="REF005",
            ),
            # Extra transactions without matching tickets (for reconciliation review)
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=11),
                amount=Decimal("150.00"),
                description="UBER EATS PEDIDO",
                reference="REF006",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[0].id,
                transaction_date=date.today() - timedelta(days=13),
                amount=Decimal("75.50"),
                description="STARBUCKS CAFE",
                reference="REF007",
            ),
            
            # Tech Solutions SA
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[1].id,
                transaction_date=date.today() - timedelta(days=3),
                amount=Decimal("5000.00"),
                description="AWS MARKETPLACE",
                reference="AWS-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[1].id,
                transaction_date=date.today() - timedelta(days=8),
                amount=Decimal("3200.00"),
                description="GOOGLE CLOUD BILLING",
                reference="GCP-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[1].id,
                transaction_date=date.today() - timedelta(days=12),
                amount=Decimal("4500.00"),
                description="MICROSOFT AZURE",
                reference="AZU-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[1].id,
                transaction_date=date.today() - timedelta(days=15),
                amount=Decimal("1200.00"),
                description="DIGITALOCEAN INC",
                reference="DO-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[1].id,
                transaction_date=date.today() - timedelta(days=18),
                amount=Decimal("500.00"),
                description="GITHUB COPILOT",
                reference="GIT-001",
            ),
            
            # Constructora El Sol
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[2].id,
                transaction_date=date.today() - timedelta(days=4),
                amount=Decimal("15000.00"),
                description="HOME DEPOT MEXICO",
                reference="HD-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[2].id,
                transaction_date=date.today() - timedelta(days=6),
                amount=Decimal("25000.00"),
                description="CEMEX MEXICO SA",
                reference="CMX-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[2].id,
                transaction_date=date.today() - timedelta(days=9),
                amount=Decimal("3500.00"),
                description="FERRETERIA EL TORNILLO",
                reference="FET-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[2].id,
                transaction_date=date.today() - timedelta(days=14),
                amount=Decimal("2800.00"),
                description="PAINT DEPOT MEXICO",
                reference="PNT-001",
            ),
            BankTransactionModel(
                id=uuid.uuid4(),
                company_id=companies[2].id,
                transaction_date=date.today() - timedelta(days=20),
                amount=Decimal("500.00"),
                description="FERRETERIA VARIOS",
                reference="FER-001",
            ),
        ]

        for btx in bank_txns:
            db.add(btx)
        await db.commit()
        print(f"Created {len(bank_txns)} bank transactions")

        print("\n✅ Seed completed successfully!")
        print(f"   Companies: {len(companies)}")
        print(f"   Tickets: {len(tickets)}")
        print(f"   Bank Transactions: {len(bank_txns)}")
        print("\nYou can now:")
        print("  1. List companies: GET /api/v1/companies/")
        print("  2. List tickets: GET /api/v1/tickets/?company_id=<id>")
        print("  3. List bank txns: GET /api/v1/bank-transactions/?company_id=<id>")
        print("  4. Run reconciliation: POST /api/v1/reconciliations/run")


if __name__ == "__main__":
    asyncio.run(seed_data())