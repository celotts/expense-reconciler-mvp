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


class TestProviderNameDetection:
    """El nombre del proveedor es el campo mas importante y el mas fragile:
    los recibos reales ponen metadata (RFC, fecha, headers) alrededor del nombre."""

    def test_provider_name_is_first_line(self):
        # Regresion: un `i > 0` descartaba la primera linea, que es el nombre.
        text = """SUPERMERCADO DEL BARRIO
RFC: SMB010203AB4
FECHA: 10/10/2025
TOTAL: $350.00
"""
        assert _parse_receipt_text(text).provider_name == "SUPERMERCADO DEL BARRIO"

    def test_cfdi_emisor_label_yields_name_not_the_label(self):
        # Layout CFDI: el emisor viene etiquetado. Debe extraerse el VALOR.
        text = """FACTURA ELECTRONICA
CFDI F412
EMISOR: FARMACIAS DEL SUR SA DE CV
RFC: FAR920315AB3
FECHA EXPEDICION: 15/12/2024
SUBTOTAL: $1,000.00
IVA: $160.00
TOTAL: $1,160.00
"""
        result = _parse_receipt_text(text)
        assert result.provider_name == "FARMACIAS DEL SUR SA DE CV"
        assert result.provider_tax_id == "FAR920315AB3"
        assert result.total_amount == Decimal("1160.00")
        assert result.tax_amount == Decimal("160.00")
        assert result.expense_date == date(2024, 12, 15)

    def test_razon_social_label_yields_name(self):
        text = """RAZON SOCIAL: DISTRIBUIDORA NACIONAL DEL NORTE
RFC: DNN110405RT9
TOTAL: $7,899.00
"""
        assert _parse_receipt_text(text).provider_name == "DISTRIBUIDORA NACIONAL DEL NORTE"

    def test_business_name_containing_common_spanish_words_is_kept(self):
        # Regresion: una lista de labels demasiado amplia rechazaba "TIENDA"
        # como nombre de negocio, que es una denominacion comun en Mexico.
        for name in ("TIENDA GENERICA", "LA CASA DE LAS CAJAS", "ABARROTES EL CONSUMIDOR"):
            text = f"{name}\nFECHA: 20/02/2025\nTOTAL: $100.00\n"
            assert _parse_receipt_text(text).provider_name == name

    def test_metadata_line_is_never_used_as_provider_name(self):
        # Sin un nombre plausible, no inventar uno a partir de un campo.
        text = """1234567890
RFC: ABC010101ABC
FECHA: 20/02/2025
TOTAL: $50.00
"""
        assert _parse_receipt_text(text).provider_name == "Unknown Provider"

    def test_name_with_digits_and_punctuation(self):
        text = """OXXO S.A. DE C.V. #4521
RFC: OXX010101XXX
TOTAL: $89.50
"""
        assert _parse_receipt_text(text).provider_name == "OXXO S.A. DE C.V. #4521"

    def test_trailing_boilerplate_does_not_win_over_real_name(self):
        text = """RESTAURANTE LA CATEDRAL
RFC: RST010203XXX
TOTAL: $580.00
GRACIAS POR SU COMPRA
"""
        assert _parse_receipt_text(text).provider_name == "RESTAURANTE LA CATEDRAL"


class TestRfcExtraction:
    """RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos, 3 alfanumericos."""

    @pytest.mark.parametrize("rfc", ["WAL910101XXX", "CAPL800101HDF", "FAR920315AB3", "AAA000000XXX"])
    def test_rfc_preserved_exactly(self, rfc):
        text = f"EMPRESA DE PRUEBA\nRFC: {rfc}\nTOTAL: $10.00\n"
        assert _parse_receipt_text(text).provider_tax_id == rfc

    def test_rfc_must_not_swallow_extra_letter(self):
        # Regresion: `[A-Z]{3,4}` greedy devolvia WALM910101XXX (13 chars).
        text = "WALMART SUPERCENTER\nRFC: WAL910101XXX\nTOTAL: $249.86\n"
        rfc = _parse_receipt_text(text).provider_tax_id
        assert rfc == "WAL910101XXX"
        assert len(rfc) == 12

    def test_missing_rfc_stays_none(self):
        text = "TIENDA SIN RFC\nFECHA: 20/02/2025\nTOTAL: $100.00\n"
        assert _parse_receipt_text(text).provider_tax_id is None


class TestDateExtraction:
    def test_iso_date_with_time(self):
        # Regresion: `line.split("T")[0]` devolvia "FECHA: 2025-06-15" y
        # pd.to_datetime lo rechazaba -> caia silenciosamente a date.today().
        text = """OXXO S.A. DE C.V. #4521
RFC: OXX010101XXX
FECHA: 2025-06-15T10:20:30
TOTAL: $89.50
"""
        assert _parse_receipt_text(text).expense_date == date(2025, 6, 15)

    def test_iso_date_without_time(self):
        text = "OXXO S.A. DE C.V.\nRFC: OXX010101XXX\n2025-06-15\nTOTAL: $89.50\n"
        assert _parse_receipt_text(text).expense_date == date(2025, 6, 15)

    def test_slash_date_is_day_first(self):
        text = "TIENDA\nFECHA: 15/01/2025\nTOTAL: $10.00\n"
        assert _parse_receipt_text(text).expense_date == date(2025, 1, 15)

    def test_dmy_not_mdy_confusion(self):
        # Si 15/01 se leyera como mes=15 fallaria: el dia 15 nunca es mes.
        text = "TIENDA\nFECHA: 15/01/2025\nTOTAL: $10.00\n"
        assert _parse_receipt_text(text).expense_date.month == 1
        assert _parse_receipt_text(text).expense_date.day == 15

    def test_missing_date_defaults_to_today(self):
        text = "TIENDA\nTOTAL: $100.00\n"
        assert _parse_receipt_text(text).expense_date == date.today()


class TestAmountExtraction:
    def test_subtotal_is_not_mistaken_for_total(self):
        text = "TIENDA\nSUBTOTAL: $215.40\nIVA (16%): $34.46\nTOTAL: $249.86\n"
        assert _parse_receipt_text(text).total_amount == Decimal("249.86")

    def test_fallback_does_not_use_subtotal(self):
        # Sin linea TOTAL, el fallback no debe quedarse con el SUBTOTAL.
        text = "TIENDA\nFECHA: 20/02/2025\nSUBTOTAL: $215.40\n"
        assert _parse_receipt_text(text).total_amount != Decimal("215.40")

    @pytest.mark.parametrize("raw,expected", [
        ("$1,234.56", Decimal("1234.56")),   # formato mexicano: coma=millares
        ("$1.234,56", Decimal("1234.56")),   # formato europeo
        ("$249.86", Decimal("249.86")),
        ("$1,234,567.89", Decimal("1234567.89")),
    ])
    def test_number_formats(self, raw, expected):
        text = f"TIENDA\nTOTAL: {raw}\n"
        assert _parse_receipt_text(text).total_amount == expected

    def test_tax_default_is_zero(self):
        text = "TIENDA\nTOTAL: $100.00\n"
        assert _parse_receipt_text(text).tax_amount == Decimal("0.00")

    def test_iva_with_percentage(self):
        text = "TIENDA\nIVA (16%): $80.00\nTOTAL: $580.00\n"
        assert _parse_receipt_text(text).tax_amount == Decimal("80.00")

    def test_iva_word_boundary_does_not_match_product_name(self):
        # Sin \b, el patron matchearia "iva" dentro del nombre de un producto.
        text = "TIENDA\nARTICULOS:\nVITAMINA C 500MG   $45.00\nTOTAL: $45.00\n"
        assert _parse_receipt_text(text).tax_amount == Decimal("0.00")