"""Columnas JSON que admiten `Decimal` sin perder exactitud.

El problema
-----------

`tickets.items` es una columna JSON, y `ai_extractor.py:346-348` convierte a
`Decimal` las cantidades y los importes de cada linea. `Decimal` no existe en
JSON, asi que el serializador de SQLAlchemy —`json.dumps`— falla:

    TypeError: Object of type Decimal is not JSON serializable

Medido, no supuesto: un `ticket_mercado_simple.pdf` leido por la ruta LLM
produce lineas, y el INSERT del ticket revienta. El escaneo lo reporta como
`accion=ERROR` con `datos=null`, y el comprobante se pierde sin que nada diga
por que. No es un caso raro: es cualquier factura con detalle de partidas, que
es exactamente el caso de uso del inventario.

Y el fallo es de los que no se ven en la prueba: los comprobantes SIN lineas
(`items=[]`) se guardan bien, asi que el camino del LLM se puede dar por bueno
mientras no se le pase uno con detalle.

Por que `str` y no `float`
--------------------------

Porque la convencion del proyecto es que todo importe es `Decimal` y nunca
`float` (`inventario_service._decimal_de` lo dice con razon: `0.1 + 0.2` en
binario no es `0.3`, y aqui la aritmetica decide si una compra cuadra con su
total). Convertir a `float` para que `json.dumps` pase seria cambiar la
exactitud justo en la capa que decide si el inventario cuadra, y el redondeo se
comeria centavos de forma silenciosa.

Un `Decimal` serializado como texto no pierde nada: `"12.50"` vuelve a
`Decimal("12.50")` sin perdida. Y quien lo lee ya lo aceptaba:
`interpretar_items`->_decimal_de` acepta strings a proposito, porque "un JSON
bien formado puede llevar el numero como texto" —el modelo devuelve `"3"` y
`"12.50"` con frecuencia. Este fix no le agrega un caso nuevo, lo completa.

Lo que NO se hace
-----------------

No se castea a `float` "por compatibilidad", y no se define un `json_encoder`
global. Un encoder global cambiaria el comportamiento de TODAS las columnas
JSON de la aplicacion sin que nadie lo pidiera, y el bug reapareceria en
cualquier sitio nuevo sin que nadie lo estuviera mirando. El arreglo es local a
la columna que lo tiene.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator


def _sin_decimal(valor: Any) -> Any:
    """`Decimal` a texto, en profundidad y sin tocar el resto.

    Solo se convierte `Decimal`. `float`, `int`, `str`, `bool`, `None`, `date` y
    `datetime` se dejan como estan: los ultimos los sabe serializar `json`
    (ISO 8601) y convertirlos aqui seria reescribir el formato de una columna que
    ya funciona.

    La recursion por dict y por lista es la que hace falta: las lineas del
    comprobante son `list[dict]`, asi que un `Decimal` vive dos niveles dentro y
    un recorrido de un solo nivel no lo encontraria.
    """
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _sin_decimal(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sin_decimal(v) for v in valor]
    return valor


class JSONConDecimal(TypeDecorator):
    """`JSON` que serializa `Decimal` como texto en vez de reventar.

    `none_as_null=True` va en el `JSON` de adentro y no aqui, por el mismo
    motivo que en `models/ticket.py:158`: es una opcion del tipo `JSON`, y en el
    constructor de `TypeDecorator` se perderia en silencio.
    """

    impl = JSON(none_as_null=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return _sin_decimal(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        # No se reconstruye el `Decimal` al leer. El valor vuelve como texto, que
        # es lo que la columna guarda, y `interpretar_items`->_decimal_de` ya lo
        # convierte donde hace falta y con la validacion que hace falta.
        #
        # Reconstruir aqui seria peorar: cualquier columna JSON leida pasaria por
        # este tipo, y un consumidor que compare contra `str` —o que serie a un
        # JSON para otra maquina— veria un tipo que no esta en la base.
        return value