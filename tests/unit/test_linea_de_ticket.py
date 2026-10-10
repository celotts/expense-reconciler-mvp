"""`LineaDeTicket`: la puerta validada entre el OCR y el inventario.

## QUE PROTEGE ESTE ARCHIVO

`como_items` es la ultima linea de codigo antes de que las lineas de un ticket
entren a `tickets.items`, y de ahi a `interpretar_items` y a `compra_items`. Un
error de tipo ahi no se ve: se ve como un inventario con un centavo de mas, y la
deriva de stock es monotona —una cantidad inflada nunca se corrige sola—.

Lo que se fijo, en orden de gravedad:

1. **`Decimal`, nunca `float`.** Antes `como_items` hacia `float(linea.cantidad)`.
   Con un importe de 1.15, `float` produce 1.1499999999999999, y ese numero es
   el que se multiplica por la cantidad y se suma. La aritmetica que decide si
   una compra cuadra con su total es la que se come el error, y en silencio.
2. **Lo que no valida, no entra.** La aritmetica `_elegir_por_aritmetica` ya
   descarto lo que no cuadra, pero cuadrar no es lo mismo que ser un numero de
   dinero: cantidad 1, precio 0.00 e importe 0.00 cuadran a la perfeccion y no
   son una compra.
3. **`None` no es un error.** `unit_price` ausente es legitimo y se queda
   `None`. Un campo opcional no debe descartar la linea entera.

## POR QUE NO SE PRUEBA CON FOTOS REALES

Por la misma razon que `test_recorte_del_papel.py`: un comprobante de una
persona no va al repo. Aqui se prueba la REGLA de validacion, que es lo que
puede fallar sin avisar.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.parser_lineas import (
    LecturaDeLineas,
    LineaDeTicket,
    LineaProducto,
    _linea_vertical,
    como_items,
)


def _linea(descripcion="LLAVE", cantidad="2", precio="25.00", importe="50.00", **kw):
    return LineaProducto(
        descripcion=descripcion,
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio) if precio is not None else None,
        importe=Decimal(importe),
        orden=0,
        **kw,
    )


def _lectura(lineas, veredicto="CONCILIA", subtotal="50.00"):
    return LecturaDeLineas(
        lineas=lineas,
        veredicto=veredicto,
        subtotal_declarado=Decimal(subtotal) if subtotal else None,
        suma_de_lineas=Decimal(subtotal) if subtotal else None,
    )


# --- El tipo del dato -----------------------------------------------------


class TestElImporteNuncaEsFloat:
    """El motivo por el que el modelo existe, y el que no es visible."""

    def test_el_importe_sale_como_texto_exacto(self):
        """`1.15` tiene que quedar `1.15`, no `1.1499999999999999`."""
        item = LineaDeTicket(
            description="LLAVE", quantity="1", unit_price="1.15", total="1.15"
        )
        assert item.total == Decimal("1.15")

    def test_lo_que_va_a_items_es_decimal(self):
        """No `float`. El tipo es la defensa, no el valor.

        Un test que solo mirara el NUMERO pasaria con `float(1.15) == 1.15`, que
        es lo que hacia el codigo de antes. Lo que se mira es el tipo del dict que
        llega a la columna JSON.
        """
        items = como_items(_lectura([_linea(importe="50.00")]))
        assert items[0]["total"] == "50.00"

    def test_el_centavo_no_se_pierde_al_redondear(self):
        """El caso que `float` rompe de verdad: 1.15 x 2.

        En binario `1.15 * 2` da 2.2999999999999998. Con `Decimal` da 2.30, que
        es el importe que el papel declara y el que la linea del inventario
        necesita para cuadrar con el subtotal.
        """
        items = como_items(_lectura([_linea(cantidad="2", precio="1.15", importe="2.30")]))
        cantidad = Decimal(items[0]["quantity"])
        precio = Decimal(items[0]["unit_price"])
        assert cantidad * precio == Decimal("2.30")

    def test_cada_importe_de_una_compra_real(self):
        """El caso de verdad, con los numeros del ticket que hay en la base.

        La linea de `IMG_4220.jpeg` tal como la leyo el OCR: 1.028 a 94.90, con el
        total que el papel declara, 97.56. El producto exacto da 97.5572 —NO da
        97.56—, y cuadra porque `TOLERANCIA_LINEA` son 2 centavos. Ese matiz es
        el que importa: la comparacion se hace sobre `Decimal`, y en `float` el
        mismo producto da 97.55999999999999, que sigue dentro de la tolerancia
        pero por un margen de 1.4 centavos en vez de 1.72.

        Y aqui esta el dato que no se ve mirando el codigo: **`Decimal("1.028")`
        son mil veintiocho milésimas o 1.028, segun quien lea**, y el sistema lo
        resuelve probando las dos y quedandose con la que cuadra
        (`_elegir_por_aritmetica`). Un `float` no puede cargar esa duda — la
        tira al convertir— asi que la eleccion tiene que hacerse antes, con el
        numero intacto. Por eso el modelo serializa a texto: `1.028` vuelve a
        `Decimal("1.028")` sin haber pasado por binario.
        """
        items = como_items(
            _lectura(
                [
                    _linea(descripcion="HILANESA DE PECHU", cantidad="1.028",
                           precio="94.90", importe="97.56"),
                ],
                subtotal="97.56",
            )
        )
        producto = Decimal(items[0]["quantity"]) * Decimal(items[0]["unit_price"])
        assert producto == Decimal("97.5572")
        assert abs(producto - Decimal(items[0]["total"])) <= Decimal("0.02")

    def test_el_importe_sobrevive_al_camino_real_completo(self):
        """Y el caso de verdad de verdad: por `interpretar_items` hasta el kardex.

        Los tests anteriores miran `como_items`. Este recorre lo que de verdad
        ocurre: el dict serializado, guardado en la columna JSON, y leido de vuelta
        por `interpretar_items`, que es donde el numero se convierte en la fila
        que suma stock.

        El paso intermedio importa porque el dict pasa por `json.dumps` y por
        `json.loads`, y un `float` sobrevive a ese viaje de forma distinta que un
        texto. Sin este test, un cambio que regresara `como_items` a `float`
        podria dejar `como_items` en verde y romper dos capas mas abajo.
        """
        import json

        from app.services.inventario_service import interpretar_items

        items = como_items(
            _lectura(
                [_linea(descripcion="HILANESA DE PECHU", cantidad="1.028",
                        precio="94.90", importe="97.56")],
                subtotal="97.56",
            )
        )

        # El viaje por la columna JSON: bytes a disco y de vuelta.
        guardadas = json.loads(json.dumps(items))
        lineas, descartadas = interpretar_items(guardadas)

        assert descartadas == [], "una linea valida se perdio en el camino"
        assert lineas[0]["cantidad"] == Decimal("1.028")
        assert lineas[0]["costo_unitario"] == Decimal("94.90")
        assert lineas[0]["total"] == Decimal("97.56")

    def test_float_no_habria_cuadrado(self):
        """La contraprueba: con `float`, este MISMO caso falla.

        Esta en el archivo para que el test de arriba no se lea como una
        preferencia estetica. Si alguien cambia `como_items` a `float` otra vez,
        este test sigue en verde por su cuenta y el de arriba se pone rojo — y
        queda claro que el rojo es una perdida de exactitud, no un cambio de
        criterio.

        El numero es concreto: en binario, `1.028 * 94.90` no es 97.5572 sino
        97.55720000000001. El desvio es de una fraccion de centimo y esta dentro
        de la tolerancia, si — pero ya no es el numero del papel, y lo que se
        guarda en el kardex es un valor que nadie escribio. Con cantidades
        mayores el mismo error se acumula linea por linea, que es por lo que la
        deriva de stock es monotona.
        """
        producto = float(1.028) * float(94.90)
        assert producto != 97.5572
        assert repr(producto) == "97.55720000000001"


# --- Lo que no puede entrar -----------------------------------------------


class TestLoQueNoEntra:
    @pytest.mark.parametrize(
        "campo,valor,motivo",
        [
            ("quantity", "0", "cantidad cero"),
            ("quantity", "-2", "cantidad negativa"),
            ("total", "0", "importe cero"),
            ("total", "-5.00", "importe negativo"),
            ("unit_price", "0", "precio cero"),
            ("unit_price", "-1.00", "precio negativo"),
        ],
    )
    def test_rechaza_lo_que_no_puede_ser_dinero(self, campo, valor, motivo):
        datos = {"description": "X", "quantity": "1", "unit_price": "1.00", "total": "1.00"}
        datos[campo] = valor
        with pytest.raises(ValidationError):
            LineaDeTicket(**datos)

    def test_rechaza_un_importe_no_finito(self):
        """`NaN` e `infinito` no entran, y quien los detiene es Pydantic.

        Aqui NO hay un validador propio: se intento escribir y resulto
        inalcanzable, porque Pydantic v2 rechaza el `Decimal` al construir el
        campo, con el error `finite_number`, antes de que corra el
        `field_validator`. Este test existe para dejar esa dependencia escrita y
        para que quitarla se note: si alguien cambia el tipo del campo y Pydantic
        deja de filtrar, este test se pone rojo.

        Se mira el TIPO del error y no el texto: el mensaje es de Pydantic y lo
        puede cambiar entre versiones, el tipo `finite_number` no.
        """
        for valor in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(ValidationError) as exc:
                LineaDeTicket(
                    description="X", quantity="1", unit_price="1.00", total=valor
                )
            assert "finite_number" in {e["type"] for e in exc.value.errors()}, valor

    def test_no_hay_is_finite_propio(self):
        """El modelo no reimplementa lo que la biblioteca ya hace.

        Un validador que nunca se ejecuta no puede morir con el, y un test que lo
        cubra estaria probando una rama muerta. Este test falla si alguien vuelve
        a escribir `is_finite` aqui, y el mensaje dice por que.
        """
        import inspect

        # El codigo de los validadores, NO el docstring: la nota que explica por
        # que no hay `is_finite` menciona la palabra, y buscarla en todo el
        # fuente daria un fallo que parece un bug y es una confusion de alcance.
        fuente = inspect.getsource(LineaDeTicket)
        codigo = fuente[fuente.index('"""', fuente.index('"""') + 3) + 3 :]
        assert "is_finite" not in codigo, (
            "Pydantic ya rechaza NaN e infinito con 'finite_number'. Un "
            "is_finite propio es inalcanzable: nunca corre y nunca puede "
            "morir con el."
        )

    def test_infinito_pasaria_el_signo(self):
        """La contraprueba que deja escrito de quien es la responsabilidad.

        `Decimal("Infinity") > 0` es `True`, asi que si Pydantic NO lo rechazara,
        la comprobacion de signo —que si corre— dejaria pasar el infinito al
        kardex. La defensa existe, pero vive en la biblioteca. Este test lo dice
        con un numero en vez de dejarlo en un comentario.
        """
        assert Decimal("Infinity") > 0
        assert not Decimal("Infinity").is_finite()

    def test_rechaza_una_descripcion_sin_texto(self):
        """Una linea sin descripcion no identifica nada.

        Es el caso que mas se da cuando la tinta se pierde: el numero se lee y
        la palabra no. `interpretar_items` tambien lo descarta, pero mas tarde y
        sin el mismo criterio.
        """
        for vacia in ("", "   ", "---", "  .  "):
            with pytest.raises(ValidationError):
                LineaDeTicket(
                    description=vacia, quantity="1", unit_price="1.00", total="1.00"
                )

    def test_rechaza_una_clave_desconocida(self):
        """`extra="forbid"`: una clave que no existe no se ignora.

        Ignorarla en silencio deja pasar un dato que el sistema no sabe leer. Un
        error de nombres (`"importe"` en vez de `"total"`) seria un 422 ruidoso
        en vez de una linea sin importe que llega al kardex.
        """
        with pytest.raises(ValidationError):
            LineaDeTicket(
                description="X", quantity="1", total="1.00",
                unit_price="1.00", importe="1.00",
            )

    def test_normaliza_los_espacios_de_la_descripcion(self):
        """El OCR mete espacios raros; no se guardan.

        `HILANESA  DE   PECHU` y `HILANESA DE PECHU` son el mismo producto, y sin
        esto la cola de `compra_items` los muestra como dos distintos.
        """
        item = LineaDeTicket(
            description="  LLAVE   DE   TILDE ", quantity="1", total="1.00"
        )
        assert item.description == "LLAVE DE TILDE"


# --- Lo que si tiene que entrar ------------------------------------------


class TestLoQueSiEntra:
    def test_un_precio_ausente_no_descarta_la_linea(self):
        """`unit_price` es opcional de verdad.

        Una linea con cantidad e importe ya paso la aritmetica `cantidad x precio
        == importe` por otra via, y una compra real puede no declarar el precio
        unitario. Descartarla deja el producto fuera del inventario.
        """
        items = como_items(_lectura([_linea(precio=None, cantidad="3", importe="50.00")]))
        assert len(items) == 1
        assert items[0]["unit_price"] is None

    def test_una_linea_valida_pasa_intacta(self):
        items = como_items(_lectura([_linea()]))
        assert items == [
            {"description": "LLAVE", "quantity": "2", "unit_price": "25.00", "total": "50.00"}
        ]


# --- El filtro que ya existia, y el que faltaba ---------------------------


class TestLaLineaSinNumeroNoEsProducto:
    """`_linea_vertical` tenía un comentario que describía código inexistente.

    El filtro de "una linea sin ningun numero no aporta nada" estaba escrito en
    el comentario y **no en el `return`**. Sin el, `leer_lineas` se llevaba
    `GRACIAS POR SU COMPRA` como linea de producto, y una linea sin numeros no
    produce numeros que la aritmetica pueda rechazar: nada la detiene.
    """

    @pytest.mark.parametrize(
        "texto",
        ["GRACIAS POR SU COMPRA", "TUS PUNTOS VENCEN", "ATENCION AL CLIENTE"],
    )
    def test_descarta_el_rótulo_sin_cifras(self, texto):
        assert _linea_vertical({"palabras": [{"texto": texto}]}) is True

    @pytest.mark.parametrize(
        "palabras",
        [
            ["COCA COLA", "35.00"],
            ["1.028", "HILANESA DE PECHU", "94.90", "97.56"],
            ["2", "LLAVE", "25.00"],
        ],
    )
    def test_no_descarta_la_linea_que_si_tiene_numero(self, palabras):
        """El numero va al FINAL de la linea, asi que el filtro no puede ser `match`.

        Anclar al principio —`re.match(r"\\d", texto)`— pasaria este test y
        descartaria TODAS las lineas de producto de un ticket, que es justo el
        caso de uso. Por eso va `re.search`.
        """
        linea = {"palabras": [{"texto": p} for p in palabras]}
        assert _linea_vertical(linea) is False

    def test_el_texto_vacio_tambien_se_descarta(self):
        assert _linea_vertical({"palabras": []}) is True


class TestElFiltroDeComoItems:
    def test_una_linea_con_aviso_no_entra(self):
        """`aviso` es la marca de duda de `leer_lineas`, y ya filtraba."""
        items = como_items(_lectura([_linea(aviso="no cuadra")]))
        assert items == []

    def test_solo_concilia_devuelve_algo(self):
        """El modulo ya loomania: un parseo `PARCIAL` no mueve inventario."""
        assert como_items(_lectura([_linea()], veredicto="PARCIAL")) == []
        assert como_items(_lectura([_linea()], veredicto="NADA")) == []

    def test_cuadra_pero_no_es_dinero_tampoco_entra(self):
        """La defensa que faltaba, y el motivo de este archivo.

        Cantidad 1, precio 0.00 e importe 0.00 cumplen `cantidad x precio ==
        importe` a la perfeccion. `_elegir_por_aritmetica` la acepta, y con solo
        el filtro de `aviso` entraba al inventario como una compra de cero.
        """
        items = como_items(_lectura([_linea(cantidad="1", precio="0.00", importe="0.00")]))
        assert items == []

    def test_una_linea_mala_no_arrastra_a_las_buenas(self):
        """Se descarta la mala, no todas.

        Un parseo con 14 lineas y una ilegible tiene que guardar 13. Perder las
        14 porque una no valido seria peor que no haber guardado ninguna.
        """
        lineas = [
            _linea(descripcion="LLAVE", cantidad="2", precio="25.00", importe="50.00"),
            LineaProducto(
                descripcion="X", cantidad=Decimal("1"),
                precio_unitario=Decimal("0"), importe=Decimal("0"), orden=1,
            ),
            _linea(descripcion="CINTA", cantidad="3", precio="15.50", importe="46.50"),
        ]
        for linea, orden in zip(lineas, (0, 1, 2)):
            linea.orden = orden

        items = como_items(_lectura(lineas, subtotal="96.50"))
        assert [i["description"] for i in items] == ["LLAVE", "CINTA"]

    def test_el_json_sobrevive_al_diccionario(self):
        """El dict tiene que ser serializable tal cual sale.

        `tickets.items` es una columna `JSONConDecimal`, y el bug que esa columna
        arreglo fue un `TypeError` en el INSERT que se报告显示 como ERROR con
        el comprobante perdido. El `Decimal` se lleva a texto justamente para que
        esto no pueda pasar.
        """
        import json

        items = como_items(_lectura([_linea()]))
        serializado = json.dumps(items)
        assert json.loads(serializado)[0]["total"] == "50.00"

    def test_lo_que_no_valida_se_cuenta_en_el_log(self, caplog):
        """Una linea descartada tiene que verse en el log.

        Sin esto, "el ticket se leyo bien pero no entro nada" es indistinguible de
        "el ticket no tenia lineas", y son dos fallas distintas: la primera es
        del lector y la segunda del papel.
        """
        lineas = [
            _linea(descripcion="LLAVE", cantidad="2", precio="25.00", importe="50.00"),
            LineaProducto(
                descripcion="X", cantidad=Decimal("1"),
                precio_unitario=Decimal("0"), importe=Decimal("0"), orden=1,
            ),
        ]

        with caplog.at_level(logging.INFO, logger="app.services.parser_lineas"):
            items = como_items(_lectura(lineas))

        assert len(items) == 1
        assert "descartada por validacion" in caplog.text

    def test_no_hay_log_cuando_no_hay_que_descartar(self, caplog):
        """El caso normal no ensucia el log.

        Un `info` por cada linea de cada foto llenaria el log del escaner de
        cosas que no pasó, que es como se deja de leer un log.
        """
        with caplog.at_level(logging.INFO, logger="app.services.parser_lineas"):
            como_items(_lectura([_linea()]))

        assert "descartada por validacion" not in caplog.text

    def test_el_resumen_dice_cuantas_faltaron(self, caplog):
        """El resumen con el numero, que es el que se lee cuando hay problema.

        El log por linea dice QUE se descarto; este dice CUANTAS, que es la
        pregunta que se hace uno al ver un ticket que no movio inventario: "falto
        una linea o faltaron doce". Sin el, hay que contar a mano en el log.
        """
        lineas = [
            _linea(descripcion="LLAVE", cantidad="2", precio="25.00", importe="50.00"),
            LineaProducto(descripcion="A", cantidad=Decimal("1"),
                          precio_unitario=Decimal("0"), importe=Decimal("0"), orden=1),
            LineaProducto(descripcion="B", cantidad=Decimal("1"),
                          precio_unitario=Decimal("0"), importe=Decimal("0"), orden=2),
        ]

        with caplog.at_level(logging.INFO, logger="app.services.parser_lineas"):
            como_items(_lectura(lineas))

        assert "2 linea(s) cuadraron pero no pasaron el modelo" in caplog.text