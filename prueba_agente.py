"""Funcion de prueba: la suma de importes.

La validacion de tipos va implementada, no pospuesta: aqui un `float` no es un
adorno, es la forma exacta de perder centavos (`0.1 + 0.2 != Decimal("0.3")`),
y en este proyecto todo importe es `Decimal` (convencion de `AGENTS.md`).
"""

from decimal import Decimal


def sumar(a: Decimal, b: Decimal) -> Decimal:
    """Suma dos importes; rechaza cualquier tipo que no sea `Decimal`.

    Se comprueba en tiempo de ejecucion ademas de la anotacion: las anotaciones
    no se ejecutan, y un `float` que entrara por una frontera mal tipada
    produciria un total con centavos inventados sin que nadie lo notara.
    """
    if not isinstance(a, Decimal) or not isinstance(b, Decimal):
        raise TypeError("sumar solo acepta Decimal; un float pierde centavos")
    return a + b


if __name__ == "__main__":
    print(sumar(Decimal("0.10"), Decimal("0.20")))
