"""La lista de categorias de gasto, expuesta a la UI.

Por que es un endpoint y no una constante en el front
-----------------------------------------------------
La taxonomia vive en `app.core.categorias` porque es ella la que decide como se
agrupa el gasto en el tablero. Si el desplegable de la pantalla de Tickets
tuviera su propia copia, bastaria con anadir una categoria a un lado para que el
desplegable la ofreciera y el tablero no la reconociera (o al reves), y el
sintoma seria desconcertante: se elige una categoria, se guarda, y el ticket no
aparece en ninguna barra del reparto.

No se fuerza: la lista es la que se ofrece, pero el backend acepta cualquier
texto. Meter una categoria nueva en un ticket tiene que poder hacerse sin tocar
codigo, porque los rubros de un negocio se inventan segun el negocio. Lo que no
se puede es que el cliente ofrezca una lista distinta a la que el servidor
agrupa.
"""

from fastapi import APIRouter

from app.core import categorias as cat

router = APIRouter(prefix="/categorias", tags=["Categorias"])


@router.get("")
async def listar_categorias() -> list[dict]:
    """Las categorias, en el orden canonico y con su etiqueta legible.

    El orden es el mismo con el que el tablero las dibuja, para que la persona
    elija viendo la misma secuencia en los dos lados. Y cada una trae su tipo
    (fijo o variable) porque clasificar tambien dice si un gasto se mueve con la
    actividad, y eso se decide al clasificar, no despues.
    """
    return [
        {
            "clave": c.clave,
            "etiqueta": c.etiqueta,
            "variable": c.variable,
        }
        for c in cat.CATEGORIAS
    ]


@router.get("/sin-clasificar")
async def sin_clasificar() -> dict:
    """El pseudo-valor con el que se saca un ticket de la barra gris.

    Existe para poder **desclasificar**. Un ticket mal clasificado es peor que
    uno sin clasificar: el primero se cuenta en un rubro que no es el suyo y
    nadie lo ve, el segundo al menos esta en la barra que avisa. Sin una manera
    de quitar la categoria, corregir un error de captura es imposible, y la
    clasificacion se vuelve irreversible.
    """
    return {"clave": cat.SIN_CLASIFICAR, "etiqueta": cat.ETIQUETA_SIN_CLASIFICAR}
