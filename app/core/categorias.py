"""Las categorias de gasto, en un solo lugar.

Existe este archivo por una razon que se ve en cuanto se mira un dashboard: si
la categoria se escribe en cinco sitios distintos, la agrupacion por monto deja
de ser una agrupacion y pasa a ser una lista de cadenas parecidas. `SUPERMERCADO`
y `supermercado` y `Supermercado` son tres categorias para el motor de busqueda
y son el mismo gasto para una persona. Una barra de gastos por categoria con
esa duplication no miente, pero tampoco informa: informa de la falta de
convencion.

**Lo que este archivo NO hace, a proposito:** no impone la lista. No hay
constraint en la base, no se rechaza un ticket por tener una categoria rara y no
se reescribe lo que el usuario escribio. Imponerla aqui solo obligaria a que la
primera version se lie con los datos que ya estan dentro.

Lo que si hace:

  - **Normalizar** antes de agrupar, para que el agrupamiento sea por concept y
    no por como se escribio. El valor guardado no se toca.
  - **Un nombre legible** para cada categoria, porque `SERVICIOS_PROFESIONALES`
    no es algo que uno quiera leer en una grafica.
  - **Un orden canonico**, para que las barras de un periodo y del siguiente
    salgan igual y se puedan comparar.
  - **Una lista de semantica** para la UI: cuales son gastos variables (que
    suben y bajan con la actividad) y cuales son fijos (una renta sube o baja,
    no "varia"). Sin esa distincion, ver un gasto grande en TRANSPORTE y otro
    igual en RENTA se lee igual, y significan cosas distintas para un cierre.

Por que la lista es de contabilidad mexicana y no una libre: es un sistema que
exporta a CONTPAQI, y el agrupar por categoria es lo que contesta "en que se va
la platita" antes de que exista un estado de resultados. La lista es opinion
del que la escribe y se cambia aqui, en una linea por categoria.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Categoria:
    """Una categoria de gasto."""

    clave: str
    etiqueta: str
    #: Gasto variable: depende del volumen de operacion (consumo, viaticos,
    #: comisiones). Sube y baja con la actividad del mes.
    variable: bool = True


# El nombre en la base es la clave. La etiqueta es lo que se ve.
#
# El orden es a proposito, y no alfabetico: van primero las que se revisan todos
# los meses porque son las que se van de las manos cuando se descontrola el
# gasto, y al final las que nadie mira. En una barra de barras, lo de arriba es
# lo que se lee primero.
CATEGORIAS: tuple[Categoria, ...] = (
    Categoria("RENTA", "Renta", variable=False),
    Categoria("SERVICIOS_PROFESIONALES", "Servicios profesionales", variable=False),
    Categoria("SERVICIOS", "Servicios (internet, luz, tel)", variable=False),
    Categoria("SOFTWARE", "Software y suscripciones", variable=False),
    Categoria("IMPUESTOS", "Impuestos y retenciones", variable=False),
    Categoria("SUELDOS", "Sueldos y prestaciones", variable=False),
    Categoria("EQUIPO", "Equipo de computo", variable=False),
    Categoria("ALIMENTACION", "Alimentacion", variable=True),
    Categoria("BEBIDAS", "Bebidas y despensa", variable=True),
    Categoria("COMBUSTIBLE", "Combustible", variable=True),
    Categoria("TRANSPORTE", "Transporte y casetas", variable=True),
    Categoria("VIATICOS", "Viaticos", variable=True),
    Categoria("MATERIALES", "Materiales de construccion", variable=True),
    Categoria("HERRAMIENTAS", "Herramientas", variable=True),
    Categoria("MANTENIMIENTO", "Mantenimiento", variable=True),
    Categoria("PAPELERIA", "Papeleria", variable=True),
    Categoria("OTROS", "Otros", variable=True),
)

# La categoria que se muestra cuando el ticket no tiene ninguna. NO es una
# categoria mas: es la ausencia de una, y por eso tiene su propio tratamiento en
# la UI. Un gasto sin clasificar no se puede atribuir a un rubro, asi que un
# "Sin clasificar" es un dato sobre la disciplina de captura, no sobre el
# negocio, y por eso se dibuja aparte y al final, con su propio color.
SIN_CLASIFICAR = "SIN_CLASIFICAR"
ETIQUETA_SIN_CLASIFICAR = "Sin clasificar"

_POR_CLAVE: dict[str, Categoria] = {c.clave: c for c in CATEGORIAS}

# Un valor desconocido se trata como variable por omision: se asume que se mueve
# con la actividad, que es la lectura conservadora (no se afirma que algo es
# estable sin saberlo).
_VARIABLE_POR_OMISION = True


def normaliza(valor: object) -> str:
    """La clave con la que se agrupa. No cambia lo que esta guardado.

    Se comparan los nombres "en mayusculas y sin acentos" porque es como se
    escriben a mano y como los ponen los exportadores de contabilidad. Un
    `normalize` de unicode deja `MATERIÁLES` y `MATERIALES` en la misma bolsa.

    Lo que sale aqui NO se escribe en la base: es solo la clave del `GROUP BY`.
    Guardar la version normalizada perderia como se escribio, y eso importa
    cuando alguien tiene que corregir la clasificacion a mano.
    """
    if valor is None:
        return SIN_CLASIFICAR

    texto = str(valor).strip()
    if not texto:
        return SIN_CLASIFICAR

    return _sin_acentos(texto.upper())


def _sin_acentos(texto: str) -> str:
    import unicodedata

    # NFKD separa la "a" acentuada en "a" + acento combinante; el filtro se
    # queda con lo que no es un acento combinante.
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def etiqueta(clave: str) -> str:
    """El nombre que se muestra."""
    if clave == SIN_CLASIFICAR:
        return ETIQUETA_SIN_CLASIFICAR
    categoria = _POR_CLAVE.get(clave)
    if categoria is not None:
        return categoria.etiqueta
    # Una categoria que no esta en la lista se muestra tal cual, con la primera
    # letra en mayuscula. Inventar una etiqueta para un nombre desconocido
    # obligaria a mantener un diccionario de traducciones que nadie va a
    # llenar entero.
    return clave.capitalize().replace("_", " ")


def es_variable(clave: str) -> bool:
    """Si el gasto de esta categoria se mueve con la actividad."""
    categoria = _POR_CLAVE.get(clave)
    if categoria is None:
        return _VARIABLE_POR_OMISION
    return categoria.variable


def orden_canonico() -> list[str]:
    """El orden en que se pintan las categorias: el de la lista, y al final
    `SIN_CLASIFICAR`, que siempre queda al fondo.

    Que las barras de dos periodos distintos salgan en el mismo orden es lo que
    las hace comparables. Ordenadas por monto, cada periodo las reacomoda y la
    comparacion es imposible.
    """
    return [c.clave for c in CATEGORIAS] + [SIN_CLASIFICAR]
