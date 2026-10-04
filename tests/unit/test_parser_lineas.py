"""El parser de lineas por geometria y aritmetica.

QUE SE COMPRUEBA
================

La extraccion se **auto-valida**: se prueban las lecturas de los numeros y se
queda con la que hace que `cantidad x precio == importe`, y solo pasa a
inventario si la suma de las lineas cuadra con el subtotal que declara el ticket.

Los casos de aqui son reales:

  test_el_1_028_se_lee_como_cantidad   el caso de la base, con el numero ambiguo
  test_no_pasa_sin_reconciliar        sin subtotal declarado, no hay juez
  test_una_linea_que_no_cuadra_no_entra
  test_el_iva_no_es_una_linea_de_producto
  test_la_descripcion_no_arrastra_el_precio

POR QUE ESTO Y NO "LEER EL TEXTO"
=================================

Porque en el caso real (`IMG_4220.jpeg`) el texto sale asi:

    G 1.028 HILANESA DE PECHU 94.90 4

La descripcion es basura y ningun parser de texto la arregla. Los tres numeros
**si son los correctos**, y `1.028 x 94.90 == 97.56` al centavo. Un `l` leido
donde iba un `1` no mueve la caja: por eso se parsea la geometria y no el texto.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.parser_lineas import (
    _a_decimal,
    _todos_los_valores,
    como_items,
    leer_lineas,
    resumen_legible,
)


def _linea(y: int, palabras: list[tuple[int, str]]) -> dict:
    """Una linea geometrica, como la devuelve `ocr.py`.

    Las palabras van como `(x0, texto)` y `x1` se calcula como `x0 + ancho`, que
    es lo que hace el OCR real. El ancho importa poco y se fija aqui para que las
    pruebas sean legibles.
    """
    return {
        "y0": y,
        "y1": y + 12,
        "clave": [1, 1, y],
        "palabras": [
            {"x0": x0, "x1": x0 + 60, "texto": texto} for x0, texto in palabras
        ],
    }


# El caso real de `IMG_4220.jpeg`, con la geometria que tendria.
GEOMETRIA_REAL = [
    _linea(10, [(30, "ARTICULOS")]),
    _linea(40, [(300, "P.UNIT"), (400, "P.TOTAL")]),
    _linea(
        80,
        [
            (30, "G"),
            (65, "1.028"),
            (115, "HILANESA"),
            (255, "DE"),
            (305, "PECHU"),
            (360, "94.90"),
            (410, "97.56"),
        ],
    ),
    _linea(110, [(30, "IVA"), (400, "0.00")]),
    _linea(140, [(30, "TOTAL"), (410, "97.56")]),
]


class TestLaAritmeticaDecide:
    def test_el_1_028_se_lee_como_cantidad(self):
        """EL CASO REAL. `1.028` es ambiguo y solo la aritmetica lo resuelve.

        Mil veintiocho —`1028 x 94.90 = 97,563`, que no es el importe— o uno coma
        cero dos ocho: `1.028 x 94.90 = 97.56`, exacto.

        Con una regla de formato, el `1.028` se habria leido como 1028 y la linea
        correcta se habria descartado. Con la aritmetica, gana sola.
        """
        lectura = leer_lineas(GEOMETRIA_REAL, subtotal_declarado=Decimal("97.56"))

        assert lectura.veredicto == "CONCILIA"
        linea = lectura.lineas[0]
        assert linea.cantidad == Decimal("1.028")
        assert linea.precio_unitario == Decimal("94.90")
        assert linea.importe == Decimal("97.56")
        assert linea.aviso is None

    def test_el_1_028_tiene_dos_lecturas_posibles(self):
        """Y el parser no elige: ofrece las dos. La eleccion es de la aritmetica."""
        lecturas = _todos_los_valores("1.028")

        assert Decimal("1.028") in lecturas      # cantidad
        assert Decimal("1028") in lecturas       # con miles

    def test_un_numero_sin_ambiguedad_tiene_una_sola_lectura(self):
        assert _todos_los_valores("97.56") == [Decimal("97.56")]

    def test_una_coma_de_miles(self):
        """`1,250.50` es mil doscientos cincuenta con cincuenta, no uno con dos."""
        lecturas = _todos_los_valores("1,250.50")

        assert Decimal("1250.50") in lecturas
        assert Decimal("1.25050") not in lecturas


class TestSoloPasaLoQueSeSostiene:
    def test_no_pasa_sin_reconciliar(self):
        """Sin el subtotal del ticket NO HAY JUEZ, asi que no se afirma nada.

        Este es el punto donde el sistema se puede callar. Un parseo puede tener
        lineas y ser razonablemente correcto, pero sin un numero contra el cual
        comprobarlo, decirlo seria afirmar.
        """
        lectura = leer_lineas(GEOMETRIA_REAL)

        assert lectura.veredicto == "PARCIAL"
        assert "no declara un subtotal" in (lectura.motivo or "")
        # Y sobre todo: no produce items. Un PARCIAL no mueve inventario.
        assert como_items(lectura) == []

    def test_una_linea_que_no_cuadra_no_entra(self):
        """Una linea descartada no se guarda, ni como item ni como movimiento."""
        geometria = [
            # Cantidad y precio que no dan el importe: no es una linea de producto.
            _linea(80, [(30, "ALGO"), (200, "3"), (300, "17.00"), (400, "99.99")]),
            _linea(120, [(30, "SUBTOTAL"), (400, "99.99")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("99.99"))

        assert lectura.veredicto in ("PARCIAL", "NADA")
        assert como_items(lectura) == []

    def test_una_suma_que_no_cuadra_es_parcial(self):
        """Las lineas suman mas que el subtotal: algo se leyo mal."""
        geometria = [
            _linea(80, [(30, "PRODUCTO UNO"), (200, "2"), (300, "50.00"), (400, "100.00")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("250.00"))

        assert lectura.veredicto == "PARCIAL"
        assert "diferencia" in (lectura.motivo or "")
        assert como_items(lectura) == []


class TestElRuidoNoEntra:
    def test_el_iva_no_es_una_linea_de_producto(self):
        """`IVA 0.00` parseado como producto seria un producto de 0 pesos.

        Y en un ticket con IVA real, `IVA 540.00` seria un producto de 540.00 —
        que es justo la clase de error que mueve inventario de mas.
        """
        geometria = [
            _linea(80, [(30, "HILANESA"), (200, "1.028"), (300, "94.90"), (400, "97.56")]),
            _linea(110, [(30, "IVA"), (400, "16.32")]),
            _linea(140, [(30, "TOTAL"), (400, "113.88")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("97.56"))

        assert len(lectura.lineas) == 1
        assert "IVA" not in lectura.lineas[0].descripcion.upper()
        assert lectura.veredicto == "CONCILIA"

    def test_la_descripcion_no_arrastra_el_precio(self):
        """Un nombre de producto no termina en la columna de precio.

        Sin esto la descripcion sale "HILANESA DE PECHU 94.90", que como nombre de
        catalogo no sirve y se compara mal contra cualquier otro producto.

        Y lo que NO se hace: limpiar el `1.028` que el OCR dejo en medio de la
        linea. Es geometria honesta —esa palabra cae a la izquierda del importe— y
        arrancarla exigiria adivinar cuales de las palabras a la izquierda son
        producto y cuales basura, que es el problema que todavia no esta resuelto
        y que el emparejamiento por embedding cubre mejor: "1.028 HILANESA DE
        PECHU" sigue apareciendo cerca de "HILANESA DE PECHUGA" en el catalogo, y
        una persona renombra una vez.
        """
        lectura = leer_lineas(GEOMETRIA_REAL, subtotal_declarado=Decimal("97.56"))

        assert lectura.lineas[0].descripcion == "G 1.028 HILANESA DE PECHU"

    def test_una_linea_sin_descripcion_no_entra(self):
        """Un producto sin nombre no es un producto."""
        geometria = [
            _linea(80, [(200, "3"), (300, "17.00"), (400, "51.00")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("51.00"))

        assert lectura.lineas == []
        assert lectura.veredicto == "NADA"

    def test_sin_lineas_no_inventa_nada(self):
        lectura = leer_lineas([], subtotal_declarado=Decimal("100"))

        assert lectura.lineas == []
        assert lectura.veredicto == "NADA"
        assert lectura.motivo


class TestVariasLineas:
    def test_varias_lineas_que_cuadran(self):
        geometria = [
            _linea(80, [(30, "LLAVE"), (200, "2"), (300, "25.00"), (400, "50.00")]),
            _linea(110, [(30, "CINTA"), (200, "3"), (300, "15.50"), (400, "46.50")]),
            _linea(140, [(30, "SUBTOTAL"), (400, "96.50")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("96.50"))

        assert lectura.veredicto == "CONCILIA"
        assert len(lectura.lineas) == 2
        assert lectura.suma_de_lineas == Decimal("96.50")
        assert len(como_items(lectura)) == 2

    def test_una_de_dos_malas_baja_el_veredicto(self):
        """La suma global es un juez fuerte: una linea mala se nota.

        Las dos lineas son reales pero la segunda tiene la cantidad mal leida, y
        la suma ya no da el subtotal. El veredicto pasa a PARCIAL y **no** entra
        ninguna: el todo no se sostiene, asi que no se toma la parte buena.
        """
        geometria = [
            _linea(80, [(30, "LLAVE"), (200, "2"), (300, "25.00"), (400, "50.00")]),
            _linea(110, [(30, "CINTA"), (200, "9"), (300, "15.50"), (400, "46.50")]),
            _linea(140, [(30, "SUBTOTAL"), (400, "96.50")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("96.50"))

        assert lectura.veredicto == "PARCIAL"
        assert como_items(lectura) == []


class TestElResumenLegible:
    def test_explica_el_veredicto(self):
        """El motivo va al ticket: un PENDIENTE con esto dice "se intento".

        Es la diferencia entre "el sistema no pudo" y "nadie lo abrio".
        """
        texto = resumen_legible(leer_lineas(GEOMETRIA_REAL, subtotal_declarado=Decimal("97.56")))
        assert "CONCILIA" in texto

        texto_nada = resumen_legible(leer_lineas([], subtotal_declarado=None))
        assert "sin lineas" in texto_nada

    def test_una_lectura_sin_geometria_no_revienta(self):
        assert leer_lineas(None).veredicto == "NADA"
        assert leer_lineas([{}]).veredicto == "NADA"
        assert leer_lineas([{"palabras": []}]).veredicto == "NADA"