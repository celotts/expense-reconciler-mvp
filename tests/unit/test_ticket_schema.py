"""Validacion del schema de tickets.

La validacion del frontend (front/src/utils/validation.ts) es la primera linea
de defensa, pero no puede ser la unica: la API es consumible por curl, imports
de CSV, scripts y el propio endpoint /extract-and-create, ninguno de los cuales
pasa por el form.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.ticket import TicketCreate, TicketUpdate


def make_create(**overrides) -> dict:
    data = {
        "company_id": str(uuid4()),
        "provider_name": "WALMART SUPERCENTER",
        "provider_tax_id": "WAL910101XXX",
        "total_amount": Decimal("249.86"),
        "tax_amount": Decimal("34.46"),
        "expense_date": date(2025, 1, 15),
    }
    data.update(overrides)
    return data


class TestValidPayloads:
    def test_full_ticket_accepted(self):
        ticket = TicketCreate(**make_create())
        assert ticket.provider_name == "WALMART SUPERCENTER"
        assert ticket.tax_amount == Decimal("34.46")

    def test_tax_id_optional(self):
        ticket = TicketCreate(**make_create(provider_tax_id=None))
        assert ticket.provider_tax_id is None

    def test_empty_tax_id_treated_as_absent(self):
        # El form manda "" cuando el campo opcional queda en blanco.
        assert TicketCreate(**make_create(provider_tax_id="")).provider_tax_id is None

    def test_rfc_lowercase_normalized(self):
        assert TicketCreate(**make_create(provider_tax_id="wal910101xxx")).provider_tax_id == "WAL910101XXX"

    def test_provider_name_trimmed(self):
        assert TicketCreate(**make_create(provider_name="  OXXO  ")).provider_name == "OXXO"

    @pytest.mark.parametrize("rfc", ["WAL910101XXX", "CAPL800101HDF", "FAR920315AB3"])
    def test_valid_rfc_formats(self, rfc):
        assert TicketCreate(**make_create(provider_tax_id=rfc)).provider_tax_id == rfc

    def test_tax_omitted_defaults_to_zero(self):
        data = make_create()
        del data["tax_amount"]
        assert TicketCreate(**data).tax_amount == Decimal("0.00")

    def test_tax_equal_to_total_is_allowed(self):
        # Un bien acquired sin IVA da tax == total (monederoelectronico).
        assert TicketCreate(**make_create(total_amount=Decimal("100"), tax_amount=Decimal("100")))

    def test_very_small_amount_accepted(self):
        # No todo gasto es >= 1.00; el pan de la esquina sale 0.01 menos IVA.
        assert TicketCreate(
            **make_create(total_amount=Decimal("0.01"), tax_amount=Decimal("0.00"))
        )


class TestUnknownProviderRejected:
    """El parser devuelve "Unknown Provider" cuando no lee el emisor. Dejarlo
    pasar guarda un ticket inservible para la conciliacion."""

    def test_unknown_provider_rejected_on_create(self):
        with pytest.raises(ValidationError, match="no identifico al proveedor"):
            TicketCreate(**make_create(provider_name="Unknown Provider"))

    def test_unknown_provider_rejected_on_update(self):
        with pytest.raises(ValidationError, match="no identifico al proveedor"):
            TicketUpdate(provider_name="Unknown Provider")

    def test_whitespace_provider_rejected(self):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(provider_name="   "))

    def test_similar_but_valid_names_accepted(self):
        # Evitar un falso positivo con nombres que contienen la palabra.
        for name in ("UNKNOWN PROVIDER SAC", "TIENDA UNKNOWN", "PROVEEDOR UNICO"):
            assert TicketCreate(**make_create(provider_name=name)).provider_name == name


class TestRfcValidation:
    @pytest.mark.parametrize("rfc", [
        "12345678901",        # solo digitos
        "WALMART",            # sin digitos
        "WAL91010XXX",        # 5 digitos en vez de 6
        "WAL910101XXXX",      # 4 alfanumericos en vez de 3
        "WA910101XXX",        # 2 letras
        "WAL910101X",         # 2 alfanumericos
        "WAL910101XX!",
    ])
    def test_malformed_rfc_rejected(self, rfc):
        with pytest.raises(ValidationError, match="RFC invalido"):
            TicketCreate(**make_create(provider_tax_id=rfc))

    def test_malformed_rfc_rejected_on_update(self):
        with pytest.raises(ValidationError, match="RFC invalido"):
            TicketUpdate(provider_tax_id="NOT-AN-RFC")

    def test_rfc_too_long_for_column(self):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(provider_tax_id="A" * 60))


class TestAmountValidation:
    @pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1.00")])
    def test_non_positive_total_rejected(self, amount):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(total_amount=amount))

    def test_negative_tax_rejected(self):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(tax_amount=Decimal("-5.00")))

    def test_tax_greater_than_total_rejected(self):
        with pytest.raises(ValidationError, match="no puede ser mayor"):
            TicketCreate(**make_create(total_amount=Decimal("100"), tax_amount=Decimal("150")))

    def test_too_many_decimal_places_rejected(self):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(total_amount=Decimal("100.005")))

    def test_amount_overflowing_numeric_12_2_rejected(self):
        with pytest.raises(ValidationError):
            TicketCreate(**make_create(total_amount=Decimal("100000000000.00")))


class TestUpdatePartial:
    def test_all_fields_optional(self):
        assert TicketUpdate().model_dump(exclude_unset=True) == {}

    def test_single_field_update(self):
        assert TicketUpdate(total_amount=Decimal("50")).total_amount == Decimal("50")

    def test_tax_validated_against_total_only_when_both_present(self):
        # PATCH parcial: sin total no se puede juzgar el IVA.
        assert TicketUpdate(tax_amount=Decimal("999.99")).tax_amount == Decimal("999.99")

    def test_tax_rejected_when_both_present_and_invalid(self):
        with pytest.raises(ValidationError, match="no puede ser mayor"):
            TicketUpdate(total_amount=Decimal("10"), tax_amount=Decimal("50"))


class TestAritmeticaFiscal:
    """subtotal + IVA + IEPS - retenciones == total, tolerancia maxima 0.05.

    La tolerancia del validator (5 centavos) existe por el redondeo del OCR, y es
    deliberadamente MAS floja que MONEY_TOLERANCE del gate (1 centimo): el gate
    decide quien aprueba, el validator solo abre la puerta de la API.
    """

    def test_cuadra_exacto(self):
        ticket = TicketCreate(**make_create(
            subtotal=Decimal("215.40"),
            total_amount=Decimal("249.86"),
            tax_amount=Decimal("34.46"),
        ))
        assert ticket.subtotal == Decimal("215.40")

    def test_diferencia_de_cinco_centavos_pasa(self):
        # Redondeo del papel: la tolerancia maxima es inclusive.
        TicketCreate(**make_create(
            subtotal=Decimal("215.35"),
            total_amount=Decimal("249.86"),
            tax_amount=Decimal("34.46"),
        ))

    def test_diferencia_mayor_a_cinco_centavos_falla(self):
        with pytest.raises(ValidationError, match="aritmetica fiscal"):
            TicketCreate(**make_create(
                subtotal=Decimal("215.00"),
                total_amount=Decimal("249.86"),
                tax_amount=Decimal("34.46"),
            ))

    def test_ieps_se_suma_al_total(self):
        # El caso real: Walmart con IVA e IEPS. 217.27 + 8.14 + 8.59 = 234.00.
        ticket = TicketCreate(**make_create(
            provider_name="WALMART",
            subtotal=Decimal("217.27"),
            tax_amount=Decimal("8.14"),
            ieps_amount=Decimal("8.59"),
            total_amount=Decimal("234.00"),
        ))
        assert ticket.ieps_amount == Decimal("8.59")

    def test_ieps_que_no_se_declara_hace_fallar_cuando_cuadra_solo_aparente(self):
        # 217.27 + 8.14 = 225.41 != 234.00: sin el IEPS no cuadra, y debe ir a revision.
        with pytest.raises(ValidationError, match="aritmetica fiscal"):
            TicketCreate(**make_create(
                provider_name="WALMART",
                subtotal=Decimal("217.27"),
                tax_amount=Decimal("8.14"),
                total_amount=Decimal("234.00"),
            ))

    def test_retenciones_se_restan_del_total(self):
        # Retencion de ISR 10% sobre 1000.00: 1000 + 160 - 100 = 1060.
        ticket = TicketCreate(**make_create(
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("160.00"),
            retention_amount=Decimal("100.00"),
            total_amount=Decimal("1060.00"),
        ))
        assert ticket.retention_amount == Decimal("100.00")

    def test_sin_subtotal_no_se_comprueba(self):
        # Sin subtotal no hay con que comparar: no es un error, es un dato ausente.
        ticket = TicketCreate(**make_create())
        assert ticket.subtotal is None

    def test_el_error_lleva_los_numeros_dentro(self):
        with pytest.raises(ValidationError) as excinfo:
            TicketCreate(**make_create(
                subtotal=Decimal("100.00"),
                total_amount=Decimal("249.86"),
                tax_amount=Decimal("34.46"),
            ))
        texto = str(excinfo.value)
        assert "100.00" in texto and "249.86" in texto and "34.46" in texto
