"""El veredicto de las lineas de un comprobante, y nada mas.

QUE LE QUEDO, Y POR QUE ESTE ARCHIVO ERA EL DOBLE
=================================================

Este archivo tenia 1,118 lineas. **808 se fueron el 2026-10-10**: eran el camino
completo de "geometria del OCR -> lineas de producto" (`leer_lineas`, `como_items`,
`LineaProducto`, `LecturaDeLineas`, `resumen_legible` y sus ayudantes), y **ningun
caller en `app/` los invocaba jams**. MEDIDO: las tres referencias a `leer_lineas`
dentro de `app/` estaban en `parser_service.py` y las tres eran COMENTARIOS
(`:1215`, `:1223`, `:1250`). El propio `parser_service.py` lo admitia en su
docstring: *"el campo se llena de verdad cuando el LLM trae el detalle de partidas
o cuando el OCR aporta geometria (`parser_lineas.leer_lineas`)"*. La segunda mitad
de esa frase describia un cable que no estaba conectado.

POR QUE BORDARLO CUANDO ESTA ESCRITO Y TESTEADO
===============================================

Porque era **verificacion teatro**, que es lo peor que puede tener un codigo que
nunca corre. `scripts/verify_ocr_mutations.py` reportaba **10 de 16 mutaciones
sobre este archivo**, y **3 de esas 10 mutateaban codigo inalcanzable**:
`como_items` volviendo a `float`, el log de "linea descartada" y el conteo de
descartadas. Las tres "pasaban" porque los tests las ejecutan. Un verificador que
reporta proteccion sobre una rama que el producto no ejecuta hace creer que hay una
defensa donde no hay nada.

Ese fue el criterio del corte: **lo que `app/` importa se queda, lo que solo los
tests tocan se va.** Se midio con un recorrido de alcance sobre el AST desde los
tres simbolos que `parser_service.py:11` importa, no a ojo.

LO QUE SE PERDIO, DICHO CLARO
=============================

La capacidad de extraer partidas **de la foto** por geometria. No estaba perdida:
**nunca estuvo.** Si algun dia se quiere inventario desde fotografia, hay que
cablear `ocr.ResultadoOCR.cajas -> leer_lineas`, y ese trabajo vuelve a
escribirse. Lo que se borro fue el borrador.

Y con esto, `verificacion_items` **siempre sale `NADA` en la ruta de reglas**,
porque la ruta por reglas no produce lineas. Solo el LLM las trae, y ahi
`verificar_items` si hace su trabajo: comprobar que `cantidad x precio == importe`
y que la suma cuadre con el subtotal. Eso **sigue igual que antes del corte**.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

TOLERANCIA_SUBTOTAL = Decimal("0.01")

class LineaDeTicket(BaseModel):
    """Una linea de producto YA VALIDADA, en la forma de `tickets.items`.

    ## POR QUE PYDANTIC Y NO UN DICT

    Es la puerta entre "lo que el OCR leyo" y "lo que suma stock". Del otro lado
    hay `compra_items` y una entrada al kardex, y ahi los tipos dejan de ser una
    comodidad y pasan a ser una garantia: `quantity=1.028` como `float` son dos
    cosas distintas segun quien lo lea —`1.028` mexicano o `1028` europeo— y
    ese es exactamente el caso que `LineaProducto` ya documenta que existe. Un
    `float` no puede cargar esa duda: `1.028` en binario es `1.0279999999...`, y
    la duda desaparece SOLO porque el tipo la tira al convertir.

    Que lo haga Pydantic y no el codigo a mano es lo que evita el otro extremo:
    si la validacion fuera una cadena de `if` repartida, la linea nueva se
    saltaria la mitad de las reglas el dia que se agregue.

    ## QUE VALIDA, Y POR QUE CADA REGLA ESTA AQUI

    - `extra="forbid"`: una clave que no exista es un dato que el sistema no
      sabe leer, y aceptarla en silencio la deja pasar al inventario.
    - `cantidad > 0` y `importe > 0`: `ck_movimientos_cantidad_positiva` ya lo
      exige en Postgres, pero el trigger no corre bajo SQLite y la comprobacion
      en Python es la que da el 409 con los dos numeros. Aqui se corta antes.
    - `Decimal` siempre, nunca `float`: la convencion del repo y la razon por la
      que `JSONConDecimal` existe. Un importe que llega a la base como
      `1.0279999` es un centavo que se perdio en silencio.

    ## LO QUE NO SE COMPRUEBA, Y POR QUE

    `NaN` e `infinito` **no** tienen una comprobacion aqui, y es deliberado.

    Se intento escribirla (`if not v.is_finite()`) y resulto ser INALCANZABLE:
    Pydantic v2 rechaza `Decimal("NaN")` y `Decimal("Infinity")` al construir el
    campo, con el error `finite_number`, ANTES de que corra cualquier
    `field_validator`. Medido:

        LineaDeTicket(total=Decimal("NaN"))       -> finite_number
        LineaDeTicket(total=Decimal("Infinity"))  -> finite_number

    As que un validador propio para eso es decoracion: nunca se ejecuta, asi que
    nunca puede morir con el, y un test que lo cubra estaria probando que el
    codigo ejecuta una rama que no ejecuta. Se dejo el que dice el manual de
    Pydantic y se aclaro el nombre del error en vez de agregar el segundo filtro.

    El caso que hace que esto NO sea accidental: `Decimal("Infinity") > 0` es
    `True`, asi que si Pydantic no lo rechazara, la comprobacion de signo —que si
    corre— dejaria pasar el infinito. La defensa existe, pero vive en la
    biblioteca, no aqui. Por eso `test_infinito_pasaria_el_signo` esta en el
    archivo: deja escrito de quien es la responsabilidad.

    Lo que el modelo NO hace: no corrige ni completa. Un campo que el OCR no leyo
    es `None` y se queda `None`, que es lo que `interpretar_items` ya sabe
    recibir.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal
    unit_price: Decimal | None = None
    total: Decimal

    @field_validator("description")
    @classmethod
    def _descripcion_util(cls, v: str) -> str:
        """Una descripcion con espacios es ruido de OCR, no una descripcion.

        El recorte es a proposito y pequeno: `HILANESA DE PECHU` —que es como el
        OCR lee "PECHUGA"— sobrevive, y `A A A` no. Lo que se descarta es la
        cadena sin ningun caracter util, que es lo que un threshold de foto
        oscurecida deja cuando la tinta se perdio del todo.
        """
        limpio = " ".join(v.split())
        if not limpio.strip(" -_."):
            raise ValueError("descripcion sin caracteres utiles")
        return limpio

    @field_validator("quantity")
    @classmethod
    def _cantidad_positiva(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"cantidad debe ser positiva, leida {v}")
        return v

    @field_validator("unit_price")
    @classmethod
    def _precio_positivo(cls, v: Decimal | None) -> Decimal | None:
        # `None` es legitimo: una linea con importe y sin precio unitario ya
        # paso la aritmetica con la cantidad, y descartar la linea entera por
        # un campo opcional perderia una compra real.
        if v is None:
            return None
        if v <= 0:
            raise ValueError(f"precio unitario debe ser positivo, leido {v}")
        return v

    @field_validator("total")
    @classmethod
    def _importe_util(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError(f"importe debe ser positivo, leido {v}")
        return v

class VerificacionDeItems(BaseModel):
    """Si las lineas del comprobante suman lo que el ticket DECLARA.

    ## POR QUE ESTE CHEQUEO Y NO EL OTRO

    El gate ya comprueba la aritmetica del ENCABEZADO:
    `subtotal + IVA + IEPS == total`. Y esa comprobacion tiene un punto ciego
    medido, que es el error COHERENTE: dos campos mal leidos que se compensan.

        el papel:  217.27 + 8.14 + 8.59 = 234.00
        leido:    207.27 + 8.14 + 18.59 = 234.00   -> ok=True, todo pasa

    El total es el unico numero correcto de los cuatro y el sistema no tiene
    forma de saberlo: la suma cuadra porque el error la cuadra. Un check que
    compara el documento consigo mismo no puede encontrar eso.

    Este check usa una fuente **independiente**: las lineas de producto vienen de
    la GEOMETRIA de la foto, no del encabezado. Si el encabezado dice 234.00 y las
    doce lineas de partida suman 217.27, se contradicen y algo se leyo mal.

    ## POR QUE `cuadra` ES DERIVADA Y NO UN CAMPO QUE SE RELLENA

    `suma_items` y los declarados son hechos. `cuadra` es su comparacion, y si
    fuera un campo de entrada, un cliente podria mandar `cuadra=True` y el
    sistema lo creeria — es el bug de `puede_cerrarse` en `reporte_cierre.py`,
    donde el PDF imprimia `None` como si fuera un hecho. Con `PrivateAttr` +
    `@computed_field` no hay entrada: pasarlos da `extra_forbidden`, y aun asi
    salen en el JSON de salida.

    Que se muten los privados en vez de devolverse un `model_copy` es porque
    Pydantic **avisa con un `UserWarning`** que un validador de primer nivel que
    no devuelve `self` no se aplica al validar con `__init__`. Medido con la
    version que devolvia la copia: todos los campos salian en `None` y
    `cuadra=True` pasado a mano **ganaba**. El aviso es un warning, no un error:
    el modelo se construia sin fallar y el codigo parecia funcionar.

    ## CUANDO ES `None` Y NO `False`

    `cuadra=None` cuando **no hay con que comprobar**: no hay lineas, o el ticket
    no trae subtotal. Eso NO es un fallo. `False` si hay datos y no cuadran.
    Confundir los dos haria que "no lo sabemos" se mostrara como "esta mal", que
    es la clase de error que hace que la gente deje de mirar los avisos.
    """

    model_config = ConfigDict(extra="forbid")

    # --- LO QUE SE PASA. Solo tres cosas, y las tres son hechos de entrada. ---
    items: list[LineaDeTicket] = Field(default_factory=list)
    # Lo que declara el comprobante. Se prueban LOS DOS contra las lineas.
    subtotal_declarado: Decimal | None = None
    total_declarado: Decimal | None = None

    # --- LO QUE SE DERIVA. Nada de esto se puede pasar. --------------------
    _suma: Decimal | None = PrivateAttr(default=None)
    _diferencia: Decimal | None = PrivateAttr(default=None)
    _cuadra: bool | None = PrivateAttr(default=None)
    # Uno de: CONCILIA, PARCIAL, NADA. El mismo vocabulario de `LecturaDeLineas`,
    # para que las dos cosas se lean igual.
    _veredicto: str = PrivateAttr(default="NADA")
    _motivo: str | None = PrivateAttr(default=None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def suma_items(self) -> Decimal | None:
        """Lo que suman las lineas validadas. `None` si no hay lineas."""
        return self._suma

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diferencia(self) -> Decimal | None:
        """Cuanto se van las lineas de lo declarado. `None` si no hay con que comparar."""
        return self._diferencia

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cuadra(self) -> bool | None:
        """`True` cuadra, `False` no cuadra, `None` **no se pudo comprobar**.

        El `None` no es un fallo: es "no hay lineas" o "el ticket no trae
        subtotal". `False` es "se comprobo y no cuadra", que pide otra accion.
        """
        return self._cuadra

    @computed_field  # type: ignore[prop-decorator]
    @property
    def veredicto(self) -> str:
        """CONCILIA, PARCIAL o NADA."""
        return self._veredicto

    @computed_field  # type: ignore[prop-decorator]
    @property
    def motivo(self) -> str | None:
        """Por que llego donde llego, con los numeros dentro."""
        return self._motivo

    @model_validator(mode="after")
    def _derivar_el_cuadre(self) -> "VerificacionDeItems":
        """Calcula suma, diferencia, cuadra y veredicto. No los deja rellenar."""
        # `suma` suma los `total` de las lineas YA VALIDADAS, no los `importe` en
        # crudo. La diferencia importa: una linea con `aviso` no llega al modelo,
        # asi que una suma que las incluyera estaria contando renglones que el
        # sistema decidio no creer.
        suma = (
            sum((l.total for l in self.items), Decimal("0"))
            if self.items
            else None
        )
        self._suma = suma

        # Contra el subtotal primero: es el numero que la suma de partida tiene
        # que reproducir. El total es el fallback para el ticket que no imprime
        # linea de SUBTOTAL, que es la mayoria de los tickets de autocomercio.
        for nombre, declarado in (
            ("subtotal", self.subtotal_declarado),
            ("total", self.total_declarado),
        ):
            if suma is None or declarado is None:
                continue
            self._diferencia = suma - declarado
            cerca = abs(self._diferencia) <= TOLERANCIA_SUBTOTAL
            self._cuadra = cerca
            self._veredicto = "CONCILIA" if cerca else "PARCIAL"
            self._motivo = (
                f"las {len(self.items)} lineas suman {suma} y el {nombre} del "
                f"ticket es {declarado}"
                if cerca
                else f"las lineas suman {suma} y el {nombre} del ticket es "
                     f"{declarado}: difieren en {self._diferencia}"
            )
            return self

        # No hay contra que comparar: no se afirma nada.
        self._diferencia = None
        self._cuadra = None
        self._veredicto = "PARCIAL" if self.items else "NADA"
        self._motivo = (
            "el ticket no trae subtotal ni total, o no se leyeron lineas: "
            "no hay con que comprobar la suma"
        )
        return self

def verificar_items(
    items: object,
    *,
    subtotal: Decimal | None = None,
    total: Decimal | None = None,
) -> VerificacionDeItems:
    """Arma la `VerificacionDeItems` desde los items crudos que da el lector.

    Es la frontera: entra lo que el lector produjo (`list[dict]` sin validar, o
    `None`) y sale un modelo que ya sabe si la suma cuadra. Las lineas que no
    pasan `LineaDeTicket` se quedan fuera y **se cuentan**, porque un ticket que
    se guardo con 14 lineas y devuelvo 11 tiene que poder decirlo.

    `None` y `[]` se distinguen a proposito, con la misma razon que en
    `TicketExtractionResult.items`: `None` = el lector no produjo lineas (la ruta
    OCR no las extrae, solo el LLM), `[]` = produjo y no eran ninguna.
    """
    if not isinstance(items, list) or not items:
        return VerificacionDeItems(items=[], subtotal_declarado=subtotal,
                                   total_declarado=total)

    validas: list[LineaDeTicket] = []
    for cruda in items:
        if not isinstance(cruda, dict):
            continue
        try:
            validas.append(
                LineaDeTicket(
                    description=str(cruda.get("description") or ""),
                    quantity=cruda.get("quantity"),
                    unit_price=cruda.get("unit_price"),
                    total=cruda.get("total"),
                )
            )
        except ValidationError as exc:
            logger.info("linea fuera de la verificacion de suma: %s",
                        exc.errors()[0].get("msg", "sin motivo"))

    if len(validas) != len(items):
        logger.info(
            "verificacion de suma: %d de %d linea(s) pasaron el modelo",
            len(validas), len(items),
        )
    return VerificacionDeItems(items=validas, subtotal_declarado=subtotal,
                               total_declarado=total)
