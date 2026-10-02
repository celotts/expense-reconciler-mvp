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
        with pytest.raises(ValueError, match="Error en el formato del CSV"):
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


class TestElImporteNoSeCorrompe:
    """El importe que entra es el importe que se guarda.

    Estos tests existen por un bug que era peor que un fallo: no daba error, se
    guardaba el numero equivocado. Delegar la conversion de la columna en los
    argumentos `thousands`/`decimal` de `read_csv` funciona solo cuando coinciden
    con lo que pandas asume por omision, y los bancos usan las dos convenciones.
    Con la mexicana (coma decimal, punto de miles) un importe de 890.00 se
    guardaba como 89000.00: cien veces mas caro, sin un 400, sin un aviso, y
    con una conciliacion que parecia correcta porque todos los tickets aparecian
    en discrepancia y no habia forma de saber por que.
    """

    def test_importe_con_punto_no_se_toma_como_miles(self):
        """El caso que corrompia importes. Sin miles, el punto es decimal."""
        csv_content = b"""fecha,importe,concepto,referencia
20/09/2026,890.00,GASOLINA,REF1
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
        )
        assert txs[0].amount == Decimal("890.00")

    def test_importe_con_miles_se_lee_como_importe(self):
        """Con miles declarados, el grupo se quita en vez de_DECIMALIZARSE."""
        csv_content = b"""fecha,importe,concepto,referencia
20/09/2026,"1,234.56",MATERIAL,REF1
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
        )
        assert txs[0].amount == Decimal("1234.56")

    def test_importe_mexicano_con_puntos_de_miles(self):
        csv_content = b"""fecha;importe;concepto
20/09/2026;1.234,56;MATERIAL
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
            separator=";",
        )
        assert txs[0].amount == Decimal("1234.56")

    def test_importe_con_simbolo_de_moneda(self):
        csv_content = b"""fecha,importe,concepto,referencia
20/09/2026,"$1,234.56",MATERIAL,REF1
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
        )
        assert txs[0].amount == Decimal("1234.56")

    def test_solo_miles_sin_decimal(self):
        """Tres digitos exactos son grupo de miles, no tres decimales."""
        csv_content = b"""fecha;importe;concepto
20/09/2026;1,234;RENTA
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
            separator=";",
        )
        assert txs[0].amount == Decimal("1234")

    def test_importe_con_menos_de_dos_digitos_es_decimal(self):
        csv_content = b"""fecha;importe;concepto
20/09/2026;1,23;IMPUESTO
"""
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
            separator=";",
        )
        assert txs[0].amount == Decimal("1.23")

    def test_importe_no_numerico_rechaza_la_fila(self):
        csv_content = b"""fecha,importe,concepto,referencia
20/09/2026,N/D,PAGO,REF1
"""
        with pytest.raises(ValueError, match="Error en el formato del CSV"):
            parse_bank_csv(
                csv_content,
                date_format="%d/%m/%Y",
                decimal_separator=",",
                thousands_separator=".",
            )

    def test_importe_vacio_rechaza_la_fila(self):
        csv_content = b"""fecha,importe,concepto,referencia
20/09/2026,,PAGO,REF1
"""
        with pytest.raises(ValueError, match="Error en el formato del CSV"):
            parse_bank_csv(
                csv_content,
                date_format="%d/%m/%Y",
                decimal_separator=",",
                thousands_separator=".",
            )

    def test_la_suma_de_los_importes_no_deriva(self):
        """La razon de usar `Decimal`: cien tickets de 0.10 deben sumar 10.00.

        Con `float`, esta asercion falla. Con `Decimal` pasa, y es la que
        protege la conciliacion: si el total de la columna de importes tiene un
        peso de diferencia, todos los tickets de ese lote caen en discrepancia.
        """
        filas = "\n".join(
            f"20/09/2026,0.10,PAGO {i},R{i}" for i in range(100)
        )
        csv_content = f"fecha,importe,concepto,referencia\n{filas}\n".encode()
        txs = parse_bank_csv(
            csv_content,
            date_format="%d/%m/%Y",
            decimal_separator=",",
            thousands_separator=".",
        )
        assert len(txs) == 100
        assert sum(t.amount for t in txs) == Decimal("10.00")


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

    def test_missing_date_stays_missing(self):
        """Un documento sin fecha NO se fecha con la de hoy.

        Este test antes afirmaba lo contrario (`defaults_to_today`) y el
        comportamiento era el que acabo de cambiar. Es un cambio de requisito
        explicito, no un arreglo para hacer pasar un test: la especificacion
        anterior era el bug.

        Poner la fecha de hoy cuando el documento no trae fecha no es un valor
        por defecto comodo, es fabricar un dato. El ticket resultante parece un
        gasto de hoy, indistinguible de uno real, y el cierre mensual lo cuenta
        en el mes equivocado: un gasto de marzo aparece en septiembre. El
        producto que se va a usar para reportar cierres no puede hacer eso sin
        avisar.

        Lo que se guarda en su lugar es la fecha con la que se registro el
        ticket, y el ticket queda en la cola con `date_missing` a la vista. La
        diferencia con antes no es el valor guardado, es que ahora se sabe que
        el valor no es real.
        """
        text = "TIENDA\nTOTAL: $100.00\n"
        assert _parse_receipt_text(text).expense_date is None


# Texto tal cual lo devolvio Tesseract sobre la foto real `IMG_4220.jpeg`
# (carneMart, CMT QUERETARO REVOLUCION), recortado a las lineas que deciden la
# fecha. La fecha real es 28/09/26 y la dice el papel en la linea del folio.
#
# Se pega el texto REAL y no uno inventado porque el caso que dio origen a esta
# regla es ese texto: las tres fotos de la carpeta. En las tres, la fecha del
# gasto no tiene la palabra "fecha" delante, y en las tres el parser la perdia.
TEXTO_OCR_CON_FECHA_EN_EL_FOLIO = """CMT QUERETARO REVOLUCION
AU. REVOLUCION NO. 403 COL. PLUTARCO ELIAS CALLES
TUS PUNTOS VENCEN: 31/10/2026
#0436110 28/09/26 09:52 POS #01 TDA 4452
TOTAL AN $97.56
"""


class TestLaFechaQueVaPegadaAlFolio:
    """La fecha sin etiqueta, en la linea del identificador del comprobante."""

    def test_la_fecha_del_folio_se_lee(self):
        """La regresion que motivo la regla.

        El texto OCR de la foto real trae "#0436110 28/09/26 09:52" y ningun
        patron con etiqueta la encuentra: no hay "fecha" delante. Antes de esta
        regla el ticket iba a la cola con `date_missing` habiendole leido bien
        el proveedor, el total y el IVA.
        """
        resultado = _parse_receipt_text(TEXTO_OCR_CON_FECHA_EN_EL_FOLIO)
        assert resultado.expense_date == date(2026, 9, 28)

    def test_un_anio_de_dos_digitos_se_agrupa_en_el_siglo_que_toca(self):
        """`28/09/26` es 2026, no 1926.

        El corte en 68 es el de POSIX. Un comprobante de 1926 no existe en este
        producto, y aceptarlo mandaria el gasto a un periodo que el gate marca
        como `date_too_old`: mejor que la regla lo rechace con un motivo claro.
        """
        texto = "TIENDA\nFOLIO 5521 28/09/26 09:52\nTOTAL: $10.00\n"
        assert _parse_receipt_text(texto).expense_date == date(2026, 9, 28)

    def test_el_vencimiento_no_gana_aunque_venga_primero(self):
        """`TUS PUNTOS VENCEN: 31/10/2026` es el vencimiento de unos puntos.

        Es la unica fecha de la foto antes de la del folio, asi que una regla
        de "primera fecha que encuentres" mete el comprobante de una compra de
        septiembre en el mes de octubre. Y `date_in_future` no lo frena: una
        fecha de octubre es un futuro creible para un ticket de septiembre.
        """
        resultado = _parse_receipt_text(TEXTO_OCR_CON_FECHA_EN_EL_FOLIO)
        assert resultado.expense_date != date(2026, 10, 31)

    def test_la_etiqueta_manda_sobre_la_fecha_del_folio(self):
        """Una fecha con la palabra "fecha" delante gana, este o mas abajo.

        La regla del folio es un RESPALDO, no una preferencia: si el documento
        dice "FECHA:", lo que dice el documento es la fecha.
        """
        texto = "TIENDA\nFECHA: 15/01/2025\nFOLIO 5521 28/09/26 09:52\nTOTAL: $10.00\n"
        assert _parse_receipt_text(texto).expense_date == date(2025, 1, 15)

    def test_la_linea_sin_marca_no_se_acepta(self):
        """Sin folio, nota ni "#", la fecha no se toca.

        Este es el caso MEDIDO de la foto `IMG_4222.jpeg`: el OCR leyo
        `o: 261001000017) 01-10-2028`, con la "N" de "No:" perdida y el 6 del
        ano leido como 8. Sin marca de comprobante la fecha no se lee, el ticket
        va a la cola con `date_missing`, y no queda un gasto fechado en 2028.
        """
        texto = "MERCASTAR\no: 261001000017) 01-10-2028 1Ój5T:19\nTOTAL: $48.00\n"
        assert _parse_receipt_text(texto).expense_date is None

    def test_un_ano_imposible_llega_al_gate_como_date_in_future(self):
        """Con la marca leida bien, la fecha se acepta y el gate la detiene.

        El caso de al lado del anterior, y delimita donde acaba esta regla.
        Cuando el OCR lee "No:" completo, la fecha SI se toma: el parser no
        puede saber que el 8 del ano era un 6. Lo que detiene el gasto dentro
        de dos anos no es esta regla, es el check `date_in_future` del gate, que
        ya existe (`confidence_gate.py:146`). Por eso el test de este caso
        comprueba el veredicto del gate y no solo el del parser.
        """
        from app.core.enums import ExtractionStatus
        from app.services.confidence_gate import gate_ticket

        resultado = _parse_receipt_text(
            "MERCASTAR\nNo: 261001000017 01-10-2028\nTOTAL: $48.00\n"
        )
        assert resultado.expense_date == date(2028, 10, 1)

        veredicto = gate_ticket(
            provider_name=resultado.provider_name,
            total_amount=resultado.total_amount,
            tax_amount=resultado.tax_amount,
            expense_date=resultado.expense_date,
            provider_tax_id=resultado.provider_tax_id,
            subtotal=resultado.subtotal,
            confidence=resultado.confidence,
            source=resultado.confidence_source,
        )
        assert any(
            fallo.startswith("date_in_future") for fallo in veredicto.validation.failures
        ), veredicto.validation.failures
        assert veredicto.status is not ExtractionStatus.AUTO_APROBADO

    def test_un_importe_no_se_toma_como_fecha(self):
        """`1.028 HILANESA DE PECHU 94.90` es un codigo de producto.

        El punto no es separador de fecha a proposito: en un ticket mexicano el
        punto separa miles y decimales, y "51.50" es un importe.
        """
        texto = "TIENDA\nFOLIO 452\n1.028 HILANESA DE PECHU 94.90\nTOTAL: $97.56\n"
        assert _parse_receipt_text(texto).expense_date is None

    def test_una_fecha_que_no_existe_se_descarta(self):
        """`31/02/2026` no es una fecha, y no se "corrige" a marzo.

        A proposito se usa `date()` y no `pd.to_datetime`: la libreria resuelve
        el fuera de rango con un aviso y un valor inventado en vez de fallar, y
        un comprobante del 31 de febrero es un dato que no se sabe.
        """
        texto = "TIENDA\nFOLIO 5521 31/02/2026\nTOTAL: $10.00\n"
        assert _parse_receipt_text(texto).expense_date is None

    def test_un_importe_en_la_misma_linea_no_confunde_la_fecha(self):
        """La linea del folio tambien trae importes; la fecha es la primera.

        `FOLIO 5521 28/09/26 09:52` tiene un numero de cuatro digitos antes de
        la fecha y una hora detras. El dia tiene que salir del primer grupo que
        sigue a una barra, no de "5521".
        """
        texto = "TIENDA\nFOLIO 5521 28/09/26 09:52 POS #01\nTOTAL: $10.00\n"
        assert _parse_receipt_text(texto).expense_date == date(2026, 9, 28)


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

class TestProveedorEnMinusculasSostenidas:
    """El parser perdia el proveedor en el formato mas comun que hay.

    La heuristica de proveedor exigia tres mayusculas consecutivas. "Tiendas
    Ramirez SA de CV" solo tiene "SA" y "CV", asi que no califica: el nombre
    estaba en la primera linea, a la vista, y el ticket iba a la cola con
    `provider_missing`.

    No es un caso exotico. Cualquier sistema de facturacion que no arme el
    ticket en un formato de 80 columnas en mayusculas emite el nombre asi. Con
    la heuristica vieja, casi todo PDF impreso caia a la cola.
    """

    @pytest.mark.parametrize("nombre", [
        "Tiendas Ramirez SA de CV",
        "Ferreteria del Sur",
        "Cafe La Esquina",
        "Panaderia El Trigal",
        "Hotel Ambos Mundos",
    ])
    def test_reconoce_nombre_con_mayuscula_inicial(self, nombre):
        texto = f"{nombre}\nRFC: TRAM910101XXX\nFECHA: 15/03/2025\nTOTAL: 500.00\n"
        assert _parse_receipt_text(texto).provider_name == nombre

    def test_no_se_confunde_con_una_linea_de_domicilio(self):
        """El domicilio va justo debajo del nombre y parece un nombre.

        Acaba en numero, que es la senal de que es una linea de importes o una
        direccion y no un comercio. Antes podia ganar como proveedor.
        """
        texto = (
            "Tiendas Ramirez SA de CV\n"
            "Av. Insurgentes Sur 1234\n"
            "RFC: TRAM910101XXX\n"
            "TOTAL: 1,000.00\n"
        )
        assert _parse_receipt_text(texto).provider_name == "Tiendas Ramirez SA de CV"

    def test_no_toma_una_linea_de_articulo_como_proveedor(self):
        texto = (
            "Tiendas Ramirez SA de CV\n"
            "Cafe en grano 1kg          250.00\n"
            "Refresco 600ml              35.50\n"
            "TOTAL: 1,100.00\n"
        )
        assert _parse_receipt_text(texto).provider_name == "Tiendas Ramirez SA de CV"

    def test_no_toma_el_encabezado_de_la_tabla_si_es_lo_primero(self):
        """El encabezado de columnas va antes que el nombre en algunos layouts."""
        texto = (
            "DESCRIPCION                IMPORTE\n"
            "Cafe en grano 1kg          250.00\n"
            "GRUPO ACME SA DE CV\n"
            "TOTAL: 1,000.00\n"
        )
        assert _parse_receipt_text(texto).provider_name == "GRUPO ACME SA DE CV"

    def test_acepta_nombres_cortos_de_comercios_reales(self):
        """Cuatro caracteres, no cinco: "OXXO" es de los mas comunes del pais.

        Todo lo demas tiene que pasar antes por los filtros de label, numero y
        dos puntos, que son los que de verdad descartan el ruido.
        """
        texto = "OXXO\nRFC: OXX010101XXX\nTOTAL: 85.50\n"
        assert _parse_receipt_text(texto).provider_name == "OXXO"

    def test_seguDescartando_lineas_muy_cortas(self):
        """El corte de longitud no se elimina, se mueve.

        Con cuatro, "AB" (dos) y "1234" (numero) siguen sin calificar. Un corte
        en cuatro aqui no relaja el filtro: relaja el ultimo nombre legitimo que
        quedaba fuera.
        """
        assert _parse_receipt_text("AB\nTOTAL: 100.00\n").provider_name == "Unknown Provider"
        assert _parse_receipt_text("1234\nTOTAL: 100.00\n").provider_name == "Unknown Provider"


class TestExtraccionDeSubtotal:
    """El subtotal nunca se extracia, y el campo estaba declarado y documentado.

    `subtotal` se definiio como "el check mas barato que hay" porque
    `subtotal + IVA == total` se verifica contra la aritmetica interna del
    propio documento: no necesita comparar contra nada externo. Al no llenarse,
    `subtotal_plus_tax_mismatch` no podia activarse nunca en la ruta de reglas.
    Solo el modelo podia activarlo.

    O sea: el camino mas barato y mas exacto era el unico sin verificacion, y
    sus tickets no tenian manera de comprobarse.
    """

    def test_extrae_subtotal_y_iva(self):
        texto = (
            "TIENDAS RAMIREZ SA DE CV\n"
            "RFC: TRAM910101XXX\n"
            "SUBTOTAL 964.00\n"
            "IVA (16%) 136.00\n"
            "TOTAL 1,100.00\n"
        )
        resultado = _parse_receipt_text(texto)
        assert resultado.subtotal == Decimal("964.00")
        assert resultado.tax_amount == Decimal("136.00")
        assert resultado.total_amount == Decimal("1100.00")

    def test_acepta_subtotal_con_guion(self):
        texto = "FERRETERIA DEL SUR\nSub-Total: 500.00\nIVA: 80.00\nTotal: 580.00\n"
        resultado = _parse_receipt_text(texto)
        assert resultado.subtotal == Decimal("500.00")
        assert resultado.total_amount == Decimal("580.00")

    def test_subtotal_no_contamina_el_total(self):
        """El riesgo real de extraer el subtotal es que se confunda con el total."""
        texto = "TIENDA\nSUBTOTAL: 215.40\nIVA (16%): 34.46\nTOTAL: 249.86\n"
        resultado = _parse_receipt_text(texto)
        assert resultado.subtotal == Decimal("215.40")
        assert resultado.total_amount == Decimal("249.86")

    def test_sin_subtotal_queda_en_none_y_no_en_cero(self):
        """None y cero no significan lo mismo.

        Con cero, `subtotal + iva == total` daria falso negativo (nunca cuadra)
        y el ticket caeria a la cola sin motivo. None es la ausencia real.
        """
        resultado = _parse_receipt_text("OXXO S.A. DE C.V.\nRFC: OXX010101XXX\nTOTAL: 85.50\n")
        assert resultado.subtotal is None

    def test_activa_el_check_de_aritmetica(self):
        """El fin de todo esto: el check ahora puede dispararse."""
        from app.services.confidence_gate import validate_extraction

        # Con fecha, para que el unico fallo que puede aparecer sea el
        # aritmetico y el test se lea limpio.
        coherente = _parse_receipt_text(
            "TIENDA\nRFC: TRAM910101XXX\nFECHA: 15/03/2025\n"
            "SUBTOTAL 964.00\nIVA 136.00\nTOTAL 1,100.00\n"
        )
        incoherente = _parse_receipt_text(
            "TIENDA\nRFC: TRAM910101XXX\nFECHA: 15/03/2025\n"
            "SUBTOTAL 964.00\nIVA 136.00\nTOTAL 1,160.00\n"
        )

        ok = validate_extraction(
            coherente.provider_name, coherente.total_amount, coherente.tax_amount,
            coherente.expense_date, coherente.provider_tax_id, coherente.subtotal,
        )
        mal = validate_extraction(
            incoherente.provider_name, incoherente.total_amount, incoherente.tax_amount,
            incoherente.expense_date, incoherente.provider_tax_id, incoherente.subtotal,
        )

        assert ok.failures == []
        assert any(f.startswith("subtotal_plus_tax_mismatch") for f in mal.failures)
