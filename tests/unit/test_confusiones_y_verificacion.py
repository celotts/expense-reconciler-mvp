"""La aritmetica como juez: `cantidad x precio == importe`, y la suma contra el subtotal.

QUE SE BORRO DE AQUI, Y POR QUE
==============================

El 2026-10-10 se borro de `parser_lineas.py` el camino de geometria del OCR, sin
callers en `app/`. Aqui fueron sus 9 tests: `TestQueSeCorrigeYQueNo` (cubria
`_lectura_corregida`), `TestElOrdenDeLasLecturas` (`_todos_los_valores`) y
`TestLaAritmeticaSigueSiendoElJuez` (`leer_lineas`).

**Lo que se queda es lo que decide si una compra entra al inventario**, y es lo
unico que corre: si los importes de linea suman el subtotal que declara el
comprobante. Un caracter mal leido rompe la suma; una suma que cuadra al centavo
sobre muchos renglones casi no puede ser casualidad.

Y aqui va la consecuencia del corte, escrita para que no se lea como un bug:
**en la ruta por reglas `verificacion_items` siempre sale `NADA`**, porque las
reglas no producen lineas. Solo el LLM las trae. Eso no cambio con el borrado.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.parser_lineas import (
    LineaDeTicket,
    VerificacionDeItems,
    verificar_items,
)


def _item(descripcion="LLAVE", cantidad="2", precio="25.00", total="50.00"):
    return LineaDeTicket(
        description=descripcion, quantity=cantidad, unit_price=precio, total=total
    )



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
