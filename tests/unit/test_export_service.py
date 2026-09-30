import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4
from io import BytesIO
import pandas as pd

from app.services.export_service import (
    export_to_excel,
    export_to_contpaqi,
    export_generic,
    _transform_to_contpaqi
)
from app.models.ticket import TicketModel
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.accounting_mapping import AccountingMappingModel


class TestExportService:
    @pytest.fixture
    async def sample_reconciliation_data(self, db_session, test_company):
        """Crear datos de conciliación para tests de exportación"""
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="WALMART",
            provider_tax_id="WAL910101XXX",
            total_amount=Decimal("249.86"),
            tax_amount=Decimal("34.46"),
            expense_date=date(2025, 1, 15),
            category="SUPERMERCADO"
        )
        db_session.add(ticket)
        
        bank = BankTransactionModel(
            company_id=test_company.id,
            transaction_date=date(2025, 1, 15),
            amount=Decimal("-249.86"),
            description="PAGO WALMART SUPERCENTER",
            reference="REF123456"
        )
        db_session.add(bank)
        await db_session.commit()
        await db_session.refresh(ticket)
        await db_session.refresh(bank)
        
        recon = ReconciliationModel(
            ticket_id=ticket.id,
            bank_transaction_id=bank.id,
            match_status="PERFECT"
        )
        db_session.add(recon)
        await db_session.commit()
        
        return ticket, bank, recon

    @pytest.mark.asyncio
    async def test_export_to_excel_returns_bytes(self, db_session, test_company, sample_reconciliation_data):
        content = await export_to_excel(db_session, test_company.id)
        
        assert isinstance(content, bytes)
        assert len(content) > 0
        
        # Verificar que es un Excel válido
        df = pd.read_excel(BytesIO(content))
        assert len(df) == 1
        assert "Fecha" in df.columns
        assert "Proveedor" in df.columns
        assert "Total" in df.columns
        assert "Estatus Conciliacion" in df.columns

    @pytest.mark.asyncio
    async def test_export_to_excel_filters_by_date(self, db_session, test_company, sample_reconciliation_data):
        # Exportar con filtro de fecha que no coincida
        content = await export_to_excel(
            db_session, test_company.id,
            date_from=date(2025, 2, 1),
            date_to=date(2025, 2, 28)
        )
        
        df = pd.read_excel(BytesIO(content))
        assert len(df) == 0  # Sin datos en febrero

    @pytest.mark.asyncio
    async def test_export_to_contpaqi_format(self, db_session, test_company, sample_reconciliation_data):
        content = await export_to_contpaqi(db_session, test_company.id)
        
        assert isinstance(content, bytes)
        
        df = pd.read_excel(BytesIO(content))
        assert len(df) == 1
        
        # Verificar columnas CONTPAQI
        expected_cols = ["Fecha", "Concepto", "RFC", "Nombre", "Importe", "IVA", "Total"]
        for col in expected_cols:
            assert col in df.columns
        
        # Verificar valores
        row = df.iloc[0]
        assert row["RFC"] == "WAL910101XXX"
        assert row["Nombre"] == "WALMART"
        assert row["Total"] == 249.86
        assert row["IVA"] == 34.46

    @pytest.mark.asyncio
    async def test_export_to_contpaqi_with_custom_mapping(self, db_session, test_company, sample_reconciliation_data):
        # Crear mapping personalizado
        mapping = AccountingMappingModel(
            company_id=test_company.id,
            software_name="CONTPAQI_CUSTOM",
            column_mappings={
                "Fecha": "Fecha",
                "Concepto": "Concepto",
                "RFC": "RFC",
                "Nombre": "Nombre",
                "Importe": "Importe",
                "IVA": "IVA",
                "Total": "Total",
                "Cuenta": "6000",  # Cuenta fija
                "Referencia": "Referencia Banco",
                "TipoComprobante": "I",
                "Serie": "A",
                "Folio": "001",
                "Moneda": "MXN",
                "TipoCambio": "1.0000",
                "MetodoPago": "PUE",
                "UsoCFDI": "G03"
            }
        )
        db_session.add(mapping)
        await db_session.commit()
        
        content = await export_to_contpaqi(db_session, test_company.id, mapping_id=mapping.id)
        
        df = pd.read_excel(BytesIO(content))
        row = df.iloc[0]
        assert str(row["Cuenta"]) == "6000"
        assert str(row["Serie"]) == "A"
        # Folio "001" gets parsed as int by pandas, check numeric value
        assert str(row["Folio"]) in ("001", "1")

    @pytest.mark.asyncio
    async def test_export_generic_custom_columns(self, db_session, test_company, sample_reconciliation_data):
        content = await export_generic(
            db_session, test_company.id,
            columns=["Fecha", "Proveedor", "Total", "Estatus Conciliacion"]
        )
        
        df = pd.read_excel(BytesIO(content))
        assert list(df.columns) == ["Fecha", "Proveedor", "Total", "Estatus Conciliacion"]
        assert len(df) == 1

    @pytest.mark.asyncio
    async def test_export_generic_column_rename(self, db_session, test_company, sample_reconciliation_data):
        content = await export_generic(
            db_session, test_company.id,
            column_mapping={
                "Fecha": "Fecha Operacion",
                "Proveedor": "Nombre Proveedor",
                "Total": "Importe Total"
            }
        )
        
        df = pd.read_excel(BytesIO(content))
        assert "Fecha Operacion" in df.columns
        assert "Nombre Proveedor" in df.columns
        assert "Importe Total" in df.columns

    def test_transform_to_contpaqi_applies_mapping(self):
        sample_data = [
            ["15/01/2025", "WALMART", "WAL910101XXX", "SUPERMERCADO", 215.40, 34.46, 249.86,
             "15/01/2025", "PAGO WALMART", "REF123", "PERFECT", "0.00", 0]
        ]

        # Sin mapping - usa defaults
        result = _transform_to_contpaqi(sample_data, None)
        assert len(result) == 1
        assert len(result[0]) == 16  # 16 columnas CONTPAQI
        assert result[0][0] == "15/01/2025"  # Fecha
        assert result[0][1] == "SUPERMERCADO"  # Concepto (categoría)
        assert result[0][2] == "WAL910101XXX"  # RFC
        assert result[0][3] == "WALMART"  # Nombre

    @pytest.mark.parametrize(
        "columna",
        [
            "TipoComprobante",
            "Serie",
            "Folio",
            "Moneda",
            "TipoCambio",
            "MetodoPago",
            "UsoCFDI",
        ],
    )
    def test_sin_mapping_no_se_inventa_ninguna_celda(self, columna):
        """Sin mapping configurado, estas columnas van VACIAS. No inventadas.

        El defecto que esto cierra: cada una traia un literal fijo
        (`Moneda="MXN"`, `TipoCambio="1.0000"`, `TipoComprobante="I"`) que se
        escribia en todas las filas. Un comprobante en USD salia con tipo de
        cambio 1.0000 y sin marca de error, y el contador lo subia a CONTPAQI
        creyendolo.

        Para un contador una celda vacia dice "no lo se" y se corrige a mano.
        Una celda con 1.0000 dice "lo se", y eso no se ve ni se corrige.

        El indice sale de `CONTPAQI_COLUMNS`, no de un numero escrito aqui: la
        primera version de este test los llevo escritos a mano, uno corrido, y
        el parametro se comio el valor de `Referencia` (`"REF123"`) sin que se
        notara. Un indice fijo en un test sobre columnas ordenadas es una
        bomba de reloj.
        """
        from app.services.export_service import CONTPAQI_COLUMNS

        indice = CONTPAQI_COLUMNS.index(columna)
        fila = ["15/01/2025", "WALMART", "WAL910101XXX", "SUPERMERCADO",
                215.40, 34.46, 249.86, "15/01/2025", "PAGO WALMART", "REF123",
                "PERFECT", "0.00", 0]
        result = _transform_to_contpaqi([fila], None)
        assert result[0][indice] == "", (
            f"{columna} salio como {result[0][indice]!r} sin que nadie lo haya "
            "configurado. El sistema esta afirmando algo que no lee de "
            "ningun lado."
        )

    def test_con_mapping_las_columnas_siguen_aceptando_valores(self):
        """Quitar el default NO quita la capacidad de llenarlas.

        El camino para poner TipoCambio o Serie es `AccountingMapping`, que es
        una decision del contador y queda registrada en el mapeo. Lo que se
        quito es que el sistema lo decidiera por el.
        """
        from app.services.export_service import CONTPAQI_COLUMNS

        fila = ["15/01/2025", "WALMART", "WAL910101XXX", "SUPERMERCADO",
                215.40, 34.46, 249.86, "15/01/2025", "PAGO WALMART", "REF123",
                "PERFECT", "0.00", 0]
        mapping = AccountingMappingModel(
            company_id=uuid4(),
            software_name="CONTPAQI",
            column_mappings={
                "TipoComprobante": "E",
                "Moneda": "USD",
                "TipoCambio": "17.5230",
                "Cuenta": "6000",
            },
        )
        result = _transform_to_contpaqi([fila], mapping)
        assert result[0][CONTPAQI_COLUMNS.index("Cuenta")] == "6000"
        assert result[0][CONTPAQI_COLUMNS.index("TipoComprobante")] == "E"
        assert result[0][CONTPAQI_COLUMNS.index("Moneda")] == "USD"
        assert result[0][CONTPAQI_COLUMNS.index("TipoCambio")] == "17.5230"

class TestLoQueSeExporta:
    """`only_reconciled` decide que entra al reporte contable. No estaba
    probado en ninguna parte, y es justo el filtro que impide que una
    conciliacion que no cuadra llegue al archivo que se manda a contabilidad.
    """

    @pytest.fixture
    async def una_conciliada_y_una_no(self, db_session, test_company):
        hechas = []
        for proveedor, estado in (("WALMART", "PERFECT"), ("RENTA", "DISCREPANCY")):
            ticket = TicketModel(
                company_id=test_company.id,
                provider_name=proveedor,
                total_amount=Decimal("100.00"),
                expense_date=date(2025, 1, 15),
            )
            db_session.add(ticket)
            await db_session.flush()

            banco = BankTransactionModel(
                company_id=test_company.id,
                transaction_date=date(2025, 1, 15),
                amount=Decimal("-100.00"),
                description=f"PAGO {proveedor}",
            )
            db_session.add(banco)
            await db_session.flush()

            db_session.add(
                ReconciliationModel(
                    ticket_id=ticket.id,
                    bank_transaction_id=banco.id,
                    match_status=estado,
                )
            )
            hechas.append((proveedor, estado))
        await db_session.commit()
        return hechas

    @pytest.mark.asyncio
    async def test_por_defecto_solo_sale_lo_conciliado(self, db_session, test_company, una_conciliada_y_una_no):
        content = await export_to_excel(db_session, test_company.id)

        df = pd.read_excel(BytesIO(content))

        assert len(df) == 1
        assert df.iloc[0]["Proveedor"] == "WALMART"
        assert "DISCREPANCY" not in df["Estatus Conciliacion"].tolist()

    @pytest.mark.asyncio
    async def test_sin_el_filtro_salen_las_dos(self, db_session, test_company, una_conciliada_y_una_no):
        """Con `only_reconciled=False` la discrepancia si aparece. El filtro es
        una decision de quien exporta, no un forgets de que existe."""
        content = await export_to_excel(
            db_session, test_company.id, only_reconciled=False
        )

        df = pd.read_excel(BytesIO(content))

        assert len(df) == 2
        assert "DISCREPANCY" in df["Estatus Conciliacion"].tolist()

    @pytest.mark.asyncio
    async def test_contpaqi_tampoco_se_lleva_una_discrepancia(self, db_session, test_company, una_conciliada_y_una_no):
        """CONTPAQI es el archivo que se carga al sistema contable. Ahi una
        fila sin cuadrar es un descuadre invisible hasta que alguien descubre
        el mes cerr mal."""
        content = await export_to_contpaqi(db_session, test_company.id)

        assert isinstance(content, bytes)
        assert b"DISCREPANCY" not in content
        assert b"RENTA" not in content
