"""La puerta de validacion de una linea de producto.

QUE SE BORRO DE AQUI, Y POR QUE
==============================

El 2026-10-10 se borro de `parser_lineas.py` el camino de "geometria del OCR ->
lineas de producto" (`leer_lineas`, `como_items`, `LineaProducto`,
`LecturaDeLineas` y ayudantes), que **ningun caller en `app/` invocaba**. Aqui
fueron sus 19 tests: `TestElImporteNuncaEsFloat`, `TestLoQueSiEntra`,
`TestLaLineaSinNumeroNoEsProducto` y `TestElFiltroDeComoItems`.

Lo que se queda prueba lo que sigue corriendo: que `LineaDeTicket` **no deja pasar**
una linea con cantidad, precio o importe no positivo, y que no acepta claves
desconocidas. Eso lo invoca `verificar_items`, que es lo unico de este archivo que
`app/` usa.

Ver el criterio del corte en el docstring de `app/services/parser_lineas.py`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.parser_lineas import LineaDeTicket


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
