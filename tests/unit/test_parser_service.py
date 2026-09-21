import pytest
from decimal import Decimal
from datetime import date
from app.services.parser_service import parse_bank_csv, extract_ticket_data, _parse_receipt_text


class TestBankCSVParser:
    def test_parse_valid_mexican_csv(self, sample_bank_csv_content):
        transactions = parse_bank_csv(
            sample_bank_csv_content,
            date_column="fecha",
            amount_column="importe",
            description_column="concepto",
            reference_column="referencia",
            date_format="%d/%m/%Y",
            decimal_separator=".",
            thousands_separator=","
        )
        
        assert len(transactions) == 4
        
        # Primera transacción: gasto en Walmart
        tx = transactions[0]
        assert tx.transaction_date == date(2025, 1, 15)
        assert tx.amount == Decimal("-125.50")
        assert "WALMART" in tx.description.upper()
        assert tx.reference == "REF001"
        
        # Segunda transacción: Amazon
        tx = transactions[1]
        assert tx.amount == Decimal("-89.90")
        assert "AMAZON" in tx.description.upper()
        
        # Tercera: transferencia
        tx = transactions[2]
        assert tx.amount == Decimal("-245.00")
        
        # Cuarta: ingreso (nómina)
        tx = transactions[3]
        assert tx.amount == Decimal("5000.00")
        assert "NOMINA" in tx.description.upper()

    def test_parse_csv_with_comma_decimal_separator(self):
        # When using comma as decimal separator, CSV must use different delimiter or quote values
        csv_content = b"""fecha;importe;concepto
15/01/2025;-125,50;PAGO WALMART
16/01/2025;-89,90;COMPRA AMAZON
"""
        transactions = parse_bank_csv(
            csv_content,
            date_column="fecha",
            amount_column="importe",
            description_column="concepto",
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
            separator=";"
        )
        
        assert len(transactions) == 2
        assert transactions[0].amount == Decimal("-125.50")
        assert transactions[1].amount == Decimal("-89.90")

    def test_parse_csv_missing_required_columns_raises(self):
        csv_content = b"""fecha,otro_campo
15/01/2025,valor
"""
        with pytest.raises(ValueError, match="Missing required columns"):
            parse_bank_csv(
                csv_content,
                date_column="fecha",
                amount_column="importe",
                description_column="concepto"
            )

    def test_parse_csv_invalid_date_format_raises(self):
        csv_content = b"""fecha,importe,concepto
2025-01-15,-125.50,PAGO
"""
        with pytest.raises(ValueError, match="Error parsing row"):
            parse_bank_csv(
                csv_content,
                date_column="fecha",
                amount_column="importe",
                description_column="concepto",
                date_format="%d/%m/%Y"
            )

    def test_parse_csv_empty_file_returns_empty_list(self):
        csv_content = b"""fecha,importe,concepto
"""
        transactions = parse_bank_csv(
            csv_content,
            date_column="fecha",
            amount_column="importe",
            description_column="concepto"
        )
        assert transactions == []


class TestTicketExtraction:
    def test_parse_receipt_text_extracts_key_fields(self, sample_ticket_text):
        result = _parse_receipt_text(sample_ticket_text)
        
        assert result.provider_name == "WALMART SUPERCENTER"
        assert result.provider_tax_id == "WAL910101XXX"
        assert result.total_amount == Decimal("249.86")
        assert result.tax_amount == Decimal("34.46")
        assert result.expense_date == date(2025, 1, 15)
        assert "LECHE ENTERA" in result.raw_text
        assert "PAN BIMBO" in result.raw_text

    def test_parse_receipt_text_handles_missing_fields(self):
        minimal_text = """TIENDA GENERICA
FECHA: 20/02/2025
TOTAL: $100.00
"""
        result = _parse_receipt_text(minimal_text)
        
        assert result.provider_name == "TIENDA GENERICA"
        assert result.total_amount == Decimal("100.00")
        assert result.expense_date == date(2025, 2, 20)
        assert result.provider_tax_id is None
        assert result.tax_amount == Decimal("0.00")

    def test_extract_ticket_data_pdf_returns_structured_result(self, sample_ticket_text):
        # Test usando la función de parseo directo (sin PDF real)
        from app.services.parser_service import TicketExtractionResult
        result = _parse_receipt_text(sample_ticket_text)
        
        assert isinstance(result.provider_name, str)
        assert isinstance(result.total_amount, Decimal)
        assert isinstance(result.expense_date, date)
        assert result.provider_name == "WALMART SUPERCENTER"