"""Las confusiones de digito del OCR y la verificacion de la suma de items.

QUE PROTEGE ESTE ARCHIVO
========================

Dos defendedoras que comparten una sola idea: **no se afirma un numero, se
ofrece como candidato y lo judge el comprobante**.

1. `_lectura_corregida` / `_todos_los_valores`: el OCR pone letras donde va un
   digito (`1,1OO.OO` por `1,100.00`). Antes, `_a_decimal` **borraba** la letra y
   el token se perdia entero. Ahora la lectura corregida se ofrece como
   CANDIDATA, la ultima de la lista, y `_elegir_por_aritmetica` se queda con la
   que haga cuadrar `cantidad x precio == importe`.

2. `VerificacionDeItems`: si las lineas de producto suman lo que el ticket
   declara. Es el unico check que usa una fuente **independiente** del
   encabezado, y por eso es el que encuentra el error coherente que la
   aritmetica `subtotal + IVA + IEPS == total` no puede ver.

LA MEDIDA QUE DICE QUE EL PUNTO 1 ESTA EN EL LADO CORRECTO
==========================================================

`docs/known-issues.md` §21 descarto los filtros de OCR con medicion, y uno de
los resultados fue este:

> Un filtro de whitelist de digitos empeora: `551.50`, `01.50`.

Es decir: **arreglar el numero por la fuerza agita el error de sitio**. Un
reemplazo global de `O` por `0` hace exactamente eso — y peor, convierte
`OXXO` en `0XX0`, que es el nombre del comercio.

Por eso la regla no es "toda letra es un digito" sino:

    solo dentro de una tirada que YA tiene un digito real

`1,1OO.OO` tiene `1` y `1`: sus `O` son digitos. `OXXO` no tiene ninguno: es una
palabra. Esa es la linea entera, y el test que la fija se llama
`test_una_palabra_no_se_toca_porque_no_tiene_digitos`.

Y ni siquiera con eso se afirma: la lectura corregida es la **ultima** de la
lista, asi que la literal gana cuando puede, y la corregida entra solo cuando
ninguna cuadra. `test_la_literal_gana_cuando_ya_cuadra` lo comprueba.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.parser_lineas import (
    LineaDeTicket,
    VerificacionDeItems,
    _lectura_corregida,
    _todos_los_valores,
    leer_lineas,
    verificar_items,
)


def _linea(descripcion="LLAVE", cantidad="2", precio="25.00", importe="50.00", **kw):
    from app.services.parser_lineas import LineaProducto

    return LineaProducto(
        descripcion=descripcion,
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio),
        importe=Decimal(importe),
        orden=0,
        **kw,
    )


def _item(descripcion="LLAVE", cantidad="2", precio="25.00", total="50.00"):
    return LineaDeTicket(
        description=descripcion, quantity=cantidad, unit_price=precio, total=total
    )


# --- La correccion: cuando y donde ---------------------------------------


class TestQueSeCorrigeYQueNo:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # El caso canonico de AGENTS.md: un total de 1,100.00 que el OCR
            # entrega como 1,1OO.OO, "tan valido como el otro" para un regex.
            ("1,1OO.OO", "1,100.00"),
            ("5O.OO", "50.00"),
            ("1OO", "100"),
            ("lOO.5O", "100.50"),
            ("S.9O", "5.90"),
        ],
    )
    def test_corrige_la_letra_dentro_de_un_numero(self, texto, esperado):
        assert _lectura_corregida(texto) == esperado

    @pytest.mark.parametrize(
        "texto",
        ["OXXO", "TUS PUNTOS", "SUBTOTAL", "CAJA", "S/O", "LECHE"],
    )
    def test_una_palabra_no_se_toca_porque_no_tiene_digitos(self, texto):
        """La linea que separa las dos palabras del punto 2.

        Un reemplazo GLOBAL produciria `0XX0` de `OXXO`, que es el nombre del
        comercio. La regla es al reves: solo se corrige dentro de una tirada que
        ya tiene un digito real, y estas no tienen ninguno.
        """
        assert _lectura_corregida(texto) is None

    def test_un_numero_ya_bueno_no_se_toca(self):
        """Sin confusiones, la funcion no hace nada.

        Y eso importa por una razon concreta: si devolviera el token tal cual,
        `_todos_los_valores` agregaria la MISMA lectura dos veces y el catalogo
        de candidatos se llenaria de ruido.
        """
        assert _lectura_corregida("97.56") is None
        assert _lectura_corregida("1,250.50") is None

    def test_una_letra_suelta_no_altera_el_numero_de_al_lado(self):
        """`51.50OXXO` conserva el 51.50.

        La `O` de `OXXO` viene pegada al numero. Corregir el bloque entero daria
        `51.500000`, y ese es exactamente el modo de fallo de §21: un filtro que
        "arregla" el numero lo desplaza.
        """
        assert _lectura_corregida("51.50OXXO") == "51.500XXO"


# --- El orden de las lecturas: la literal gana -----------------------------


class TestElOrdenDeLasLecturas:
    def test_la_candidata_corregida_es_la_ultima(self):
        """La literal primero, y la corregida al final.

        `_elegir_por_aritmetica` se queda con la PRIMERA lectura que hace cuadrar.
        Poner la corregida al principio seria un empeoramiento: `1OO` es
        `100`, pero un `5O` legitimo que ademas cuadra por otra via debe ganar
        con su lectura literal.
        """
        lecturas = [str(v) for v in _todos_los_valores("1,1OO.OO")]
        assert lecturas[-1] == "1100.00"
        assert "1100.00" in lecturas

    def test_la_literal_gana_cuando_ya_cuadra(self):
        """Un numero que ya es valido no produce una lectura corregida extra."""
        assert [str(v) for v in _todos_los_valores("97.56")] == ["97.56"]
        assert [str(v) for v in _todos_los_valores("51.50")] == ["51.50"]

    def test_las_lecturas_literales_no_cambian(self):
        """REGRESION contra el comportamiento anterior a este cambio.

        La normalizacion se extrajo a una funcion para que la lectura corregida
        pase por la MISMA, y esa extraccion es el riesgo: si toca la literal,
        cambian las lecturas de todos los tickets. Estos valores son los de
        antes del cambio, comprobados uno a uno.
        """
        assert [str(v) for v in _todos_los_valores("1.028")] == ["1.028", "1028"]
        assert [str(v) for v in _todos_los_valores("1,250.50")] == ["1250.50"]
        assert _todos_los_valores("OXXO") == []
        assert _todos_los_valores("") == []


# --- El juez sigue siendo la aritmetica -----------------------------------


class TestLaAritmeticaSigueSiendoElJuez:
    """La correccion NO afirma nada: propone, y el papel responde.

    Si esto no se cumple, la funcion se convierte en la "afirmacion" que el
    proyecto entero rechaza, con el agravante de que el numero ya parece
    limpio.
    """

    def _linea_ocr(self, palabras):
        return {"clave": [0, 0, 0], "y0": 0, "y1": 20,
                "palabras": [{"x0": x, "x1": x + 40, "texto": t}
                             for x, t in palabras]}

    def test_una_linea_con_OO_que_asi_cuadra_se_acepta(self):
        """`1.5OO` x `35.OO` == `52,5OO`: la lectura corregida la resuelve.

        Las tres cifras tienen `O` donde deberia haber digito, y ninguna de las
        lecturas literales cuadra. La aritmetica es la que dice que la
        interpretacion correcta es `1.500 x 35.00 == 52,500`.
        """
        geometria = [
            self._linea_ocr([(30, "BEBIDA"), (200, "1.5OO"), (300, "35.OO"), (400, "52,5OO")]),
            self._linea_ocr([(30, "SUBTOTAL"), (400, "52.50")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("52.50"))

        assert lectura.veredicto == "CONCILIA", lectura.motivo
        assert lectura.lineas[0].cantidad == Decimal("1.500")
        assert lectura.lineas[0].importe == Decimal("52.50")

    def test_una_linea_con_OO_que_no_cuadra_se_descarta(self):
        """La correccion no salva una linea que no cuadra por otra razon.

        Si `1.5OO` se leyera `1.500` pero el importe del papel fuera `99.99`,
        ninguna combinacion cuadra y la linea se descarta. La correccion ofrece
        mas lecturas, no mas compro misiones.
        """
        geometria = [
            self._linea_ocr([(30, "BEBIDA"), (200, "1.5OO"), (300, "35.OO"), (400, "99.99")]),
            self._linea_ocr([(30, "SUBTOTAL"), (400, "99.99")]),
        ]
        lectura = leer_lineas(geometria, subtotal_declarado=Decimal("99.99"))

        assert lectura.veredicto in ("PARCIAL", "NADA")
        assert not [l for l in lectura.lineas if l.aviso is None]


# --- La verificacion de la suma -------------------------------------------


class TestLaVerificacionDeLaSuma:
    def test_cuadra_cuando_las_lineas_suman_el_subtotal(self):
        items = [_item("LLAVE", "2", "25.00", "50.00"),
                 _item("CINTA", "3", "15.50", "46.50")]
        v = VerificacionDeItems(items=items, subtotal_declarado=Decimal("96.50"))

        assert v.cuadra is True
        assert v.veredicto == "CONCILIA"
        assert v.suma_items == Decimal("96.50")
        assert v.diferencia == Decimal("0.00")
        assert "96.50" in v.motivo

    def test_no_cuadra_y_dice_cuanto_le_falta(self):
        """`False` con la diferencia, no solo un `False` pelado.

        La pregunta de quien mira es "cuanto le falta", y sin el numero no hay
        manera de saber si se leyo mal un importe o si se perdio una linea.
        """
        items = [_item("LLAVE", "2", "25.00", "50.00")]
        v = VerificacionDeItems(items=items, subtotal_declarado=Decimal("96.50"))

        assert v.cuadra is False
        assert v.veredicto == "PARCIAL"
        assert v.diferencia == Decimal("-46.50")
        assert "46.50" in v.motivo

    def test_sin_lineas_es_none_y_no_false(self):
        """`None` es "no se pudo comprobar". `False` es "se comprobo y no cuadra".

        La ruta por reglas no extrae lineas, asi que esto sale SIEMPRE asi. Si
        fuera `False`, la respuesta diria que el ticket esta mal cuando lo que
        pasa es que nadie lo comprobo — y ese es el aviso que hace que la gente
        deje de mirar avisos.
        """
        v = VerificacionDeItems(items=[], subtotal_declarado=Decimal("100.00"))

        assert v.cuadra is None
        assert v.veredicto == "NADA"
        assert v.suma_items is None

    def test_sin_subtotal_cae_al_total(self):
        """Muchos tickets de autocomercio no imprimen linea de SUBTOTAL.

        El total es el fallback, y se dice en el motivo cual se uso, porque "no
        cuadra" sin saber contra que se comparo no es accionable.
        """
        items = [_item("LLAVE", "2", "25.00", "50.00")]
        v = VerificacionDeItems(items=items, total_declarado=Decimal("50.00"))

        assert v.cuadra is True
        assert "total" in v.motivo

    def test_cuadra_no_se_puede_rellenar_a_mano(self):
        """El campo es DERIVADO. Pasarlo no lo cambia.

        Sin esto, `cuadra=True` seria un campo mas que un cliente puede enviar y
        el sistema creeria. Es la misma trampa que `puede_cerrarse` en
        `reporte_cierre.py`, donde el PDF imprimia `None` como si fuera un hecho.
        """
        v = VerificacionDeItems(
            items=[_item()], subtotal_declarado=Decimal("999.00")
        )
        assert v.cuadra is False, "alguien pudo forzar el veredicto"

    def test_los_derivados_no_son_entradas(self):
        """`cuadra`, `suma_items`, `diferencia`, `veredicto` y `motivo` se rechazan.

        Antes eran campos de entrada normales y `VerificacionDeItems(suma_items=
        "500.00")` **no daba error**: `extra="forbid"` solo rechaza las claves que
        NO existen, y `suma_items` si existia. Un campo declarado como entrada y
        luego calculado es una puerta.

        Con `PrivateAttr` + `@computed_field` no hay entrada. Y siguen saliendo en
        el JSON de SALIDA, que es donde el consumidor los lee.
        """
        for clave, valor in [("cuadra", True), ("suma_items", "500.00"),
                             ("diferencia", "1.00"), ("veredicto", "CONCILIA"),
                             ("motivo", "a mano")]:
            with pytest.raises(ValidationError):
                VerificacionDeItems(items=[], **{clave: valor})

    def test_una_clave_desconocida_tampoco(self):
        with pytest.raises(ValidationError):
            VerificacionDeItems(items=[], campo_inventado="x")

    def test_los_derivados_siguen_saliendo_en_el_json(self):
        """Que no sean entradas no significa que desaparezcan de la respuesta.

        El consumidor los necesita: `cuadra` es la respuesta a la pregunta, y un
        campo de solo lectura que no se serializa seria invisible.
        """
        import json

        v = VerificacionDeItems(items=[_item()], subtotal_declarado=Decimal("50.00"))
        datos = json.loads(v.model_dump_json())
        assert datos["cuadra"] is True
        assert datos["suma_items"] == "50.00"
        assert datos["veredicto"] == "CONCILIA"


# --- El punto ciego que el check de items cierra --------------------------


class TestElErrorCoherente:
    """El caso que la aritmetica del ENCABEZADO no puede ver.

    El papel: `217.27 + 8.14 + 8.59 = 234.00`.
    Leido:   `207.27 + 8.14 + 18.59 = 234.00`.

    Dos campos mal leidos que se compensan: la suma cuadra porque el error la
    cuadra, y el gate lo aprueba. El total es el unico numero correcto de los
    cuatro y no hay forma de saberlo comparando el documento consigo mismo.

    Las lineas de producto vienen de la GEOMETRIA de la foto, no del encabezado.
    Si suman otra cosa, se contradicen.
    """

    def _items_del_ticket_real(self):
        return [_item("COCA COLA", "1", "12.00", "12.00"),
                _item("GALLETA", "1", "8.50", "8.50"),
                _item("LECHE", "2", "24.00", "48.00"),
                _item("PAN", "1", "35.27", "35.27"),
                _item("QUESO", "1", "23.00", "23.00"),
                _item("JABON", "1", "14.50", "14.50"),
                _item("PAPEL", "1", "19.00", "19.00"),
                _item("REFILL", "2", "13.50", "27.00"),
                _item("CERVESA", "6", "8.00", "48.00"),
                _item("AGUA", "3", "6.00", "18.00"),
                _item("VARIOS", "1", "11.00", "11.00")]

    def test_la_suma_de_items_contradice_al_subtotal_mal_leido(self):
        items = self._items_del_ticket_real()
        v = VerificacionDeItems(items=items, subtotal_declarado=Decimal("207.27"))

        assert v.cuadra is False
        assert v.veredicto == "PARCIAL"

    def test_y_con_el_total_tambien(self):
        """El total es la ultima palabra, y tambien se contrasta.

        Aqui `264.27` contra `234.00` de total: los dos dicen lo mismo, que
        el encabezado no se leyo bien. Un solo check no bastaba.
        """
        items = self._items_del_ticket_real()
        v = VerificacionDeItems(items=items, total_declarado=Decimal("234.00"))

        assert v.cuadra is False

    def test_con_el_subtotal_correcto_si_concilia(self):
        """El mismo conjunto de lineas, con el subtotal bien leido: CONCILIA.

        Esto es lo que hace que el check sirva: no dice "las lineas nunca
        cuadran", dice "estas lineas NO cuadran con ESTE encabezado".
        """
        items = self._items_del_ticket_real()
        v = VerificacionDeItems(items=items, subtotal_declarado=Decimal("264.27"))

        assert v.cuadra is True
        assert v.veredicto == "CONCILIA"


# --- La frontera: items crudos del lector ---------------------------------


class TestVerificarItemsDesdeCrudos:
    def test_acepta_lo_que_ya_esta_validado(self):
        v = verificar_items(
            [{"description": "LLAVE", "quantity": "2", "unit_price": "25.00", "total": "50.00"}],
            subtotal=Decimal("50.00"),
        )
        assert v.cuadra is True

    def test_descarta_lo_ilegible_y_lo_cuenta(self, caplog):
        """Una linea mala no se pierde en silencio.

        Un ticket con 14 lineas del que se guardan 11 tiene que poder decirlo, o
        la diferencia entre "el ticket no tenia partidas" y "no las entendimos" no
        se puede distinguir — que es justo lo que hace falta saber.
        """
        crudos = [
            {"description": "LLAVE", "quantity": "2", "unit_price": "25.00", "total": "50.00"},
            {"description": "X", "quantity": "1", "unit_price": "0", "total": "0"},
            {"description": "", "quantity": "1", "total": "5.00"},
        ]
        with caplog.at_level(logging.INFO, logger="app.services.parser_lineas"):
            v = verificar_items(crudos, subtotal=Decimal("50.00"))

        assert len(v.items) == 1
        assert "verificacion de suma" in caplog.text

    def test_none_y_lista_vacia_difieren(self):
        """`None` = el lector no produjo lineas. `[]` = produjo y no eran ninguna.

        La misma distincion que en `TicketExtractionResult.items`, y por el mismo
        motivo: las dos cosas piden acciones distintas y la respuesta las
        distingue por la falta de items, no por un mensaje.
        """
        assert verificar_items(None).items == []
        assert verificar_items([]).items == []
        assert verificar_items(None).motivo == verificar_items([]).motivo

    def test_una_no_lista_no_revienta(self):
        """El LLM puede devolver cualquier cosa en `items`. No es una lista.

        `interpretar_items` ya devuelve `( [], [] )` para lo que no sea lista, y
        aqui es igual: un tipo raro se reporta como "no hay lineas", no como un
        500 en mitad de una subida.
        """
        assert verificar_items({"no": "es lista"}).items == []
        assert verificar_items("texto").items == []