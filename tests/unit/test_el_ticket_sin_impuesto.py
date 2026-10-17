"""El documento dice la respuesta, y hay que escucharlo.

MEDIDO el 2026-10-16 con `IMG_4409.heic` (DSW) sobre el papel. Dos reglas, y las
dos nacen de lo mismo: **la aritmetica interna del comprobante manda**, no el
modelo y no el regex.

Los dos casos que fijan el diseno, y los dos son el CONTRASTE:

1. `IMG_4409`: el OCR se comio un glifo (`7` -> `1`) en la linea `TOTAL`, pero
   el mismo OCR leyo `279.93` dos veces mas (linea de partida y `SUBTOTAL`). El
   sistema guardaba `219.93` y pagaba 100 s de modelo para volver al numero que
   ya tenia en la mano.
2. El cupon de descuento: `SUBTOTAL 279.93` / `TOTAL 159.96`, sin impuesto. La
   MISMA forma, y aqui el total es el que esta bien. Por eso la regla exige
   corroboracion, no basta con que difieran.

Y el tercero, que no es un parser sino un modelo: `qwen2.5vl:3b` **inventa el
IVA** en tickets que no lo traen.
"""

from decimal import Decimal

import pytest

from app.services.capture import invoice_to_result
from app.services.parser_service import (
    _impuesto_incoherente,
    _parse_receipt_text,
)

# Texto REAL que devolvio Tesseract sobre `IMG_4409.heic`. No esta escrito a
# mano: es el que salio, con el ruido de caja y el glifo cambiados. Un texto
# limpio seria un test que no puede ver el bug.
OCR_DE_DSW = """GRUPO COMERCIAL DSW'S.A.*0E.C3U. o
PADRE ‘HIER. PTE 167. MTY CENTRO .C.P.64000 |
HT HAL RFCS GCDIFO101656 |
BELSÓL 143 “AU REVOLUCION 99. 7
| U.P. 76113 SÁNTIAGO DE ORG... ORO,
10/01/26 11:39:50. AN 1143 202 48-9 :
PANT24034 PANTALON SAB 279.93 1
-30.08 Usted “ahorra. $119.97 de $399.90 .
| SUBTOTAL 279.93 ES
TOTAL. 219.93 OS
‘Usted ahorra en Total: $119.97 > E
le Atendio:. Auta cobra 3G. | a
10/01/26:11:35:34 AY . |
"""


class TestElTotalQueElDocumentoSostiene:
    """`_total_mas_corroborado`: el numero que el papel imprime mas veces."""

    def test_el_glifo_mal_leido_no_manda(self):
        """MEDIDO: el papel imprime 279.93 tres veces y el OCR leyo 219.93 una."""
        r = _parse_receipt_text(OCR_DE_DSW)
        assert r.total_amount == Decimal("279.93")

    def test_el_subtotal_no_se_toca(self):
        """La regla decide el TOTAL. El subtotal ya venia bien y se queda."""
        r = _parse_receipt_text(OCR_DE_DSW)
        assert r.subtotal == Decimal("279.93")

    def test_la_linea_de_ahorro_no_cuenta_como_corroboracion(self):
        """`$119.97` aparece DOS veces y es un ahorro: si contara, el filtro de
        `AHORRO_EN_LINEA_RE` estaria de mas y la regla aprenderia de numeros que
        no son importes. Este test es el que dice que no cuenta."""
        assert _parse_receipt_text(OCR_DE_DSW).total_amount != Decimal("119.97")

    def test_el_cupon_de_descuento_no_se_toca(self):
        """EL CONTRASTE, y es el que hace que la regla sea una regla.

        Misma forma que DSW: sin impuesto y los dos importes difieren. Y el
        subtotal tambien sale DOS veces (linea de partida y `SUBTOTAL`), asi que
        contar impresiones NO los separa — eso fue lo que rompio la primera
        version de la regla, y este test es el que lo caczo.

        Lo que los separa es que el papel **explica** la diferencia:
        `279.93 - 159.96 = 119.97`, y `119.97` es el ahorro que imprime.
        """
        texto = (
            "TIENDA X\n"
            "PANTALON GAB 279.93\n"
            "Usted ahorra $119.97 de $399.90\n"
            "SUBTOTAL 279.93\n"
            "TOTAL 159.96\n"
        )
        assert _parse_receipt_text(texto).total_amount == Decimal("159.96")

    def test_el_cupon_solo_en_porcentaje_es_el_limite_que_se_sabe(self):
        """EL LIMITE, fijado para que no sea sorpresa.

        Si el papel NO escribe la diferencia en pesos, nadie la explica y la
        regla la toma por un error de lectura. Aqui el total del papel se
        cambia. Se documenta en vez de esconderse: es un caso que se puede dar,
        y lo que se promise es que va a estar en un test, no que no ocurra.
        """
        texto = (
            "TIENDA X\n"
            "PANTALON GAB 279.93\n"
            "-30% DESCUENTO\n"
            "SUBTOTAL 279.93\n"
            "TOTAL 159.96\n"
        )
        # El comportamiento ACTUAL, no el deseado. Si algun dia se corrige, este
        # test es el que tiene que cambiar con la decision, no al reves.
        assert _parse_receipt_text(texto).total_amount == Decimal("279.93")

    def test_con_iva_la_regla_no_se_aplica(self):
        """EL SEGUNDO CONTRASTE, y es el que protege los impuestos.

        El ticket de Aurrera: `217.27 + 8.14 + 8.59 = 234.00`. Aqui
        `subtotal != total` es lo NORMAL, y tomar el subtotal perderia 16.73 de
        impuesto del archivo que ve el contador. Un `if` de menos y el export
        sale con el IVA equivocado.
        """
        texto = (
            "BODEGA AURRERA\n"
            "SUBTOTAL 217.27\n"
            "IVA 8.14\n"
            "IEPS 8.59\n"
            "TOTAL 234.00\n"
        )
        r = _parse_receipt_text(texto)
        assert r.total_amount == Decimal("234.00")
        assert r.tax_amount == Decimal("8.14")

    def test_sin_subtotal_no_hay_que_decidir(self):
        """Sin subtotal no hay contradiccion que resolver: se respeta el total."""
        texto = "TIENDA X\nTOTAL 159.96\n"
        assert _parse_receipt_text(texto).total_amount == Decimal("159.96")


class TestElImpuestoQueNoEsta:
    """`_impuesto_incoherente`: total == subtotal significa impuesto cero."""

    def test_el_iva_inventado_por_el_modelo_se_cae(self):
        """MEDIDO el 2026-10-16: el modelo devolvio `tax 27.93` sobre un ticket
        que no imprime linea de impuesto. Con eso el gate marcaba
        `subtotal_plus_tax_mismatch(leido=279.93,esperado=307.86)`: un check
        falso,-causing un numero que nadie leyo."""
        r = _impuesto_incoherente(
            _resultado(total="279.93", subtotal="279.93", tax="27.93")
        )
        assert r.tax_amount == Decimal("0.00")
        assert r.total_amount == Decimal("279.93")

    def test_el_total_no_se_mueve(self):
        """La regla toca el impuesto, NUNCA el monto que se concilia contra el
        banco. Si algum dia este test muere, se rompio el campo que no se
        negocia."""
        r = _impuesto_incoherente(
            _resultado(total="279.93", subtotal="279.93", tax="27.93")
        )
        assert r.total_amount == Decimal("279.93")

    def test_un_iva_real_no_se_toca(self):
        """`234.00 = 217.27 + 8.14 + 8.59`: aqui los tres importes son distintos
        y TODOS son correctos. La regla solo aplica cuando total == subtotal."""
        r = _impuesto_incoherente(
            _resultado(total="234.00", subtotal="217.27", tax="8.14")
        )
        assert r.tax_amount == Decimal("8.14")

    def test_el_modelo_pasa_por_la_misma_regla(self):
        """Si la correccion viviera solo en el parser, el lector que MAS falla
        —el modelo, que es el que mejor lee los totales— se quedaria con el IVA
        inventado. Las dos rutas se sujetan al mismo criterio."""
        from app.services.ai_extractor import ExtractedInvoice

        invoice = ExtractedInvoice(
            provider_name="GRUPO COMERCIAL DSW S.A. DE C.V.",
            total=Decimal("279.93"),
            subtotal=Decimal("279.93"),
            tax_amount=Decimal("27.93"),
            raw_text="...",
        )
        r = invoice_to_result(invoice)
        assert r.tax_amount == Decimal("0.00")


def _resultado(total: str, subtotal: str | None, tax: str | None):
    from app.services.parser_service import TicketExtractionResult

    return TicketExtractionResult(
        provider_name="X",
        total_amount=Decimal(total),
        subtotal=Decimal(subtotal) if subtotal else None,
        tax_amount=Decimal(tax) if tax is not None else Decimal("0.00"),
        raw_text="texto del papel",
    )


@pytest.mark.parametrize(
    "total,subtotal,impuesto,espera_impuesto",
    [
        ("279.93", "279.93", "27.93", "0.00"),   # inventado
        ("234.00", "217.27", "8.14", "8.14"),     # real
        ("279.93", "279.93", "0.00", "0.00"),     # ya estaba bien
        ("279.93", None, "27.93", "27.93"),        # sin subtotal no hay contradiccion
    ],
)
def test_tabla(total, subtotal, impuesto, espera_impuesto):
    """La regla en una tabla, para que agregar un caso sea barato."""
    r = _impuesto_incoherente(_resultado(total, subtotal, impuesto))
    assert r.tax_amount == Decimal(espera_impuesto)