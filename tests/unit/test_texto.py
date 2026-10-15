"""`neutralizar_formula` existe, pero hoy no esta en produccion.

Por que este archivo existe
---------------------------

`app/core/texto.py` tiene DOS defensas y confundirlas es el error:

- `forzar_texto` -> XLSX. Pone `data_type = "s"`, y openpyxl no puede escribir
  una formula en una celda de texto. Es la que sostiene la seguridad de verdad,
  y la prueban `tests/integration/test_export_injection.py`.
- `neutralizar_formula` -> CSV. Antepone un apostrofo, que el lector del CSV se
  come. En un XLSX ese apostrofo SE VE, asi que no debe usarse ahi.

**Los tres exportadores del proyecto son XLSX.** No hay ninguno de CSV. Asi que
`neutralizar_formula` y `neutralizar_filas` no las llama nadie: son codigo de
una necesidad futura, y el propio modulo lo dice en su docstring ("queda para
cuando exista una exportacion a CSV de verdad, que hoy no la hay").

Lo que seBIO al escribir `scripts/verify_export_mutations.py`: mutar
`neutralizar_formula` no rompia NINGUN test, porque no hay test que la llame. Tres
mutaciones "sobrevivieron" y la razon era esa, no que la defensa fuera fuerte.

Este archivo las prueba directamente. No es hypocrita: son ocho lineas que ya
estan escritas, y cuando alguien agregue el export CSV van a ser la primera
linea de defensa. Lo que no se puede es que esten ahi sin probar.

La diferencia con `forzar_texto` queda escrita aqui a proposito, porque aplicarlas
al reves es un fallo de los dos lados: apostrofo en XLSX mete basura visible en
los datos, y `data_type` en CSV no hace nada porque el formato no tiene tipos.
"""

from __future__ import annotations

import pytest

from app.core.texto import (
    _PREFIJO_TEXTO,
    es_formula_peligrosa,
    forzar_texto,
    neutralizar_filas,
    neutralizar_formula,
)


class TestQueSeConsideraPeligroso:
    @pytest.mark.parametrize(
        "valor",
        [
            "=cmd|'/c calc'!A1",
            "@SUM(A1:A9)",
            "+1+1",
            "-1+1",
            "\t=1+1",
            "\r=1+1",
            " =1+1",
        ],
    )
    def test_los_siete_payloads_clasicos(self, valor):
        assert es_formula_peligrosa(valor), f"{valor!r} deberia ser peligroso"

    @pytest.mark.parametrize("valor", ["PROVEEDOR SA DE CV", "OXXO", "150.00", "", "3M"])
    def test_lo_normal_no_es_peligroso(self, valor):
        assert not es_formula_peligrosa(valor)

    def test_solo_textos(self):
        """Un numero no tiene Formula de injection. Convertirlo seria romper la columna."""
        from decimal import Decimal

        assert not es_formula_peligrosa(Decimal("150.00"))
        assert not es_formula_peligrosa(150.0)
        assert not es_formula_peligrosa(None)


class TestNeutralizarParaCsv:
    """La defensa de CSV. Aprobada hoy; se activa sola si aparece el export."""

    def test_le_ponemos_el_apostrofo(self):
        salida = neutralizar_formula("=1+1")
        assert salida == _PREFIJO_TEXTO + "=1+1"
        assert salida != "=1+1", "La formula sigue sin neutralizar"

    def test_lo_normal_no_se_toca(self):
        """Un apostrofo en un proveedor normal ensuciaria el dato sin ningun beneficio."""
        assert neutralizar_formula("OXXO") == "OXXO"
        assert neutralizar_formula("") == ""

    def test_los_no_textos_pasan_intactos(self):
        """Un Decimal convertido a texto haria que la columna izquierda dejara de ser numerica."""
        from decimal import Decimal

        importe = Decimal("150.00")
        assert neutralizar_formula(importe) is importe

    def test_sobre_filas(self):
        filas = [{"Proveedor": "=cmd", "Total": "10", "Nota": "normal"}]
        salida = neutralizar_filas(filas)
        assert salida[0]["Proveedor"] == _PREFIJO_TEXTO + "=cmd"
        assert salida[0]["Total"] == "10"
        assert salida[0]["Nota"] == "normal"

    def test_sobre_filas_no_muta_la_entrada(self):
        filas = [{"Proveedor": "=cmd"}]
        neutralizar_filas(filas)
        assert filas[0]["Proveedor"] == "=cmd", "El llamador ve el input mutado"


class TestLasDosDefensasNoSeConfunden:
    """La confusion cuesta datos, no solo seguridad."""

    def test_el_apostrofo_no_es_lo_mismo_que_forzar_texto(self):
        """El motivo de que existan dos funciones, escrito como test.

        Si alguien unifica las dos, este es el test que se muere, y el mensaje
        explica por que no se unifican.
        """
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws["A1"] = "=1+1"

        # Camino correcto en XLSX: cambia el tipo, el valor queda intacto.
        forzar_texto(ws)
        buf = io.BytesIO()
        wb.save(buf)

        from openpyxl import load_workbook

        leido = load_workbook(io.BytesIO(buf.getvalue())).active
        assert leido["A1"].value == "=1+1", "El valor se altero al guardarlo"
        assert leido["A1"].data_type != "f", "La celda sigue siendo formula"

    def test_puto_lo_que_pasa_si_se_confunden(self):
        """Con el apostrofo en XLSX el contador VE la comilla pegada.

        Es el fallo que la regla 7 de `AGENTS.md` previene, escrito para que no
        sea una regla que alguien razonó sin ver el efecto.
        """
        import io

        from openpyxl import Workbook, load_workbook

        wb = Workbook()
        ws = wb.active
        ws["A1"] = _PREFIJO_TEXTO + "=1+1"  # lo que haria el camino equivocado
        buf = io.BytesIO()
        wb.save(buf)

        leido = load_workbook(io.BytesIO(buf.getvalue())).active
        assert leido["A1"].value.startswith("'"), (
            "El valor de la celda ya no es el dato; el contador abriria el "
            "archivo y veria un apostrofo pegado."
        )


class TestLoNoTextosNoSeTocan:
    def test_forzar_texto_ignora_lo_que_no_es_texto(self):
        """Un numero no necesita ser texto y una celda vacia tampoco."""
        import io

        from openpyxl import Workbook, load_workbook

        wb = Workbook()
        ws = wb.active
        ws["A1"] = 150.5
        ws["B1"] = None
        ws["C1"] = "OXXO"
        forzar_texto(ws)

        buf = io.BytesIO()
        wb.save(buf)
        leido = load_workbook(io.BytesIO(buf.getvalue())).active
        assert leido["A1"].value == 150.5
        assert leido["C1"].value == "OXXO"
