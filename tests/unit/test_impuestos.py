"""El impuesto es de la partida, y el gate lo tiene que saber.

EL BUG QUE ESTOS TESTS CUBREN
-----------------------------

El gate comprobaba `subtotal + IVA == total`. Eso asume que un comprobante tiene
UNA tasa de impuesto, y en Mexico es falso.

Medido sobre comprobantes reales de esta maquina:

    Walmart, un solo ticket:
      SUBTOTAL      217.27
      IVA  16.0%      8.14
      IEPS  8.0%      8.59   <- el sistema no tenia donde meterlo
      TOTAL         234.00

    217.27 + 8.14 = 225.41, y el total es 234.00.

Con `MONEY_TOLERANCE` de un centimo, ese comprobante NO PODIA pasar el check,
aunque los tres numeros se leyeran perfectamente. El gate rechazaba una lectura
correcta, y eso es peor que un check flojo: hace que `subtotal_plus_tax_mismatch`
deje de significar "leiste mal".

Y EL PROBLEMA DE FONDO NO ES EL IEPS

----------------------------

El impuesto depende de la PARTIDA, no del comprobante. En ese mismo Walmart:

    BOLILLO    33.00  T   tasa 0
    ACTII ESQ  21.00  C   tasa 0
    ARTIELLIQ  35.00  A   tasa 16
    ZOTE BARRA 24.00  A   tasa 16
    PAKETAXO   30.00  C   tasa 0 + IEPS

Un solo comprobante con tres tratamientos fiscales. Por eso `ieps_amount` esta en
el ticket (para que el gate pueda cuadrar) Y `iva_linea`/`ieps_linea` estan en
`compra_items` (que es donde vive la tasa).

LO QUE NO SE HIZO, Y POR QUE
----------------------------

Se probo un heuristico: "si `subtotal + IVA` no da el total, busca una tasa
fiscal conocida que explique la diferencia". **Es falso y se descarta.**

    diferencia = 234.00 - 225.41 = 8.59
    8.59 / 217.27 = 4.0%

El IEPS de 8% sobre un subtotal de 217.27 NO es 8% de 217.27: el impuesto se
aplica a cada partida, y aqui hubo lineas a 0%. El heuristico habria marcado
como "un impuesto que no modelamos" a cualquier total mal leido que se desviara
alrededor del 4%, que es justo el caso que tiene que seguir fallando.

Un heuristico que hace pasar errores de lectura es peor que no tener ninguno.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.confidence_gate import gate_ticket, validate_extraction

# Los numeros del Walmart real, no inventados.
WALMART_SUBTOTAL = Decimal("217.27")
WALMART_IVA = Decimal("8.14")
WALMART_IEPS = Decimal("8.59")
WALMART_TOTAL = Decimal("234.00")


class TestElIeps:

    def test_con_ieps_leido_el_comprobante_cuadra(self):
        """El caso que antes era IMPOSIBLE que pasara.

        `217.27 + 8.14 + 8.59 = 234.00` exacto sobre un ticket real.
        """
        v = validate_extraction(
            "NUEVA WAL MART DE MEXICO S DE RL DE CV",
            WALMART_TOTAL, WALMART_IVA, None, None,
            WALMART_SUBTOTAL, WALMART_IEPS,
        )

        assert "arithmetic_consistent_with_ieps" in v.passed
        assert not [f for f in v.failures if "mismatch" in f], (
            f"un comprobante con IVA e IEPS bien leidos no puede fallar la "
            f"aritmetica: {v.failures}"
        )

    def test_sin_ieps_seguia_fallando_como_antes(self):
        """Sin el campo, el comportamiento es el de siempre.

        No se relaja nada: lo que cambia es que ahora hay una forma de que el
        lector reporte el dato y el check lo use.
        """
        v = validate_extraction(
            "NUEVA WAL MART DE MEXICO S DE RL DE CV",
            WALMART_TOTAL, WALMART_IVA, None, None,
            WALMART_SUBTOTAL, None,
        )

        assert any("mismatch" in f for f in v.failures)

    def test_un_ieps_incorrecto_no_hace_pasar_el_comprobante(self):
        """El campo nuevo no es una puerta trasera.

        Si el `ieps` que se manda no es el del papel, el check sigue fallando: es
        el unico modo de comprobar que se leyo bien, y no puede depender de que
        el campo este lleno.
        """
        v = validate_extraction(
            "NUEVA WAL MART DE MEXICO S DE RL DE CV",
            WALMART_TOTAL, WALMART_IVA, None, None,
            WALMART_SUBTOTAL, Decimal("99.00"),
        )

        assert any("mismatch" in f for f in v.failures)
        assert "arithmetic_consistent_with_ieps" not in v.passed


class TestElHeuristicoQueSeDescarto:
    """Por que NO hay un "detecta la tasa que falta".

    Estos tests no comprueban una funcion que exista: comprueban que el
    comportamiento DESCARTADO no se pueda reintroducir por accidente. Si alguien
    anade el heuristico thinking que es buena idea, estos lo delatan.
    """

    def test_un_total_mal_leido_no_pasa_como_si_fuera_un_impuesto(self):
        """La foto de DSW: el OCR leyo el precio YA descontado.

        `279.93` es el total real; `119.97` es el precio con el 30% de descuento
        ya aplicado. Este es el caso que el sistema tiene que seguir detectando, y
        es el que un heuristico de "tasa rara" dejaria pasar.
        """
        v = validate_extraction(
            "GRUPO COMERCIAL DSW S.A. DE C.V.",
            Decimal("119.97"), Decimal("0.00"), None, None,
            Decimal("279.33"), None,
        )

        assert any("mismatch" in f for f in v.failures)

    def test_un_desvio_del_4_por_ciento_no_se_explica_con_una_tasa(self):
        """El numero que el heuristico habria capturado.

        El IEPS del Walmart es 8.59 sobre 217.27, que es 4.0% del subtotal — y aun
        asi NO es una tasa del subtotal, porque el impuesto se aplica por
        partida. Por eso "4% de diferencia" NO puede.significar "hay un impuesto
        que no modelamos".
        """
        v = validate_extraction(
            "X SA DE CV", Decimal("108.35"), Decimal("0.00"), None, None,
            Decimal("104.00"), None,
        )
        # 104.00 + 0.00 = 104.00, y el total dice 108.35: 4.35 de diferencia.
        assert any("mismatch" in f for f in v.failures)

    def test_un_total_menor_nunca_se_explica_con_un_impuesto(self):
        """Un impuesto SUMA. Si lo leido es menor, el problema no es el impuesto.

        Sin esta regla, un total leido de mas pasaria como "un impuesto raro" y
        seria un gasto inflado aceptando el sistema.
        """
        v = validate_extraction(
            "X SA DE CV", Decimal("90.00"), Decimal("10.00"), None, None,
            Decimal("100.00"), None,
        )

        assert any("mismatch" in f for f in v.failures)


class TestElGateRecibeElIeps:

    def test_gate_ticket_lo_acepta_y_lo_usa(self):
        """El camino del lector automatico tambien lo recibe.

        `gate_ticket` es el que corre el escaneo. Si solo `validate_extraction` lo
        aceptara, el escaneo seguiria rechazando el comprobante y el cambio no
        serviria de nada en produccion.
        """
        d = gate_ticket(
            provider_name="NUEVA WAL MART DE MEXICO S DE RL DE CV",
            total_amount=WALMART_TOTAL,
            tax_amount=WALMART_IVA,
            expense_date=None,
            subtotal=WALMART_SUBTOTAL,
            ieps_amount=WALMART_IEPS,
        )

        assert not any("mismatch" in f for f in d.validation.failures)

    def test_gate_manual_ticket_lo_acepta(self):
        """Y el camino de la correccion manual, que es donde se escribe a mano.

        El IEPS de un comprobante con IVA+IEPS lo escribe una persona mirando el
        papel, porque el OCR no lo lee. Si `gate_manual_ticket` no lo aceptara, el
        PATCH devolveria un `mismatch` que la persona no puede resolver.
        """
        from app.services.confidence_gate import gate_manual_ticket

        d = gate_manual_ticket(
            provider_name="NUEVA WAL MART DE MEXICO S DE RL DE CV",
            total_amount=WALMART_TOTAL,
            tax_amount=WALMART_IVA,
            expense_date=date(2026, 9, 23),
            subtotal=WALMART_SUBTOTAL,
            ieps_amount=WALMART_IEPS,
        )

        assert not any("mismatch" in f for f in d.validation.failures)


class TestElWarningNoEsUnFallo:

    """El gate se relaja lo JUSTO: ni un check menos, ni una puerta mas."""

    def test_un_comprobante_correcto_sigue_siendo_aprobable(self):
        """El cambio del IEPS no tocò `ok`: nada se relaxo globalmente.

        Si esto se cumpliera para mas comprobantes seria por el campo nuevo, que
        es el unico caminoAdded. Este es el control: el caso de siempre.
        """
        v = validate_extraction(
            "X SA DE CV", Decimal("10.00"), Decimal("1.00"), date(2026, 9, 23)
        )

        assert v.ok is True
        assert v.as_text() is None

    def test_el_ieps_no_aparece_como_error_de_validacion(self):
        """`validation_errors` dice "esto esta roto", y el IEPS no es un error.

        Con el dato bien leido el ticket cuadra y no genera ninguna nota. Lo que
        se comprueba aqui es lo contrario de lo que se temia: que el campo nuevo
        no vaya a ensuciar la columna que cualquier pantalla muestra como
        diagnostico.
        """
        v = validate_extraction(
            "NUEVA WAL MART DE MEXICO S DE RL DE CV",
            WALMART_TOTAL, WALMART_IVA, date(2026, 9, 23), None,
            WALMART_SUBTOTAL, WALMART_IEPS,
        )

        assert v.ok is True
        assert v.as_text() is None