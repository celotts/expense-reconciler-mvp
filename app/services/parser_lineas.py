"""De la geometria de un ticket a lineas de producto, y a un veredicto.

QUE RESUELVE, Y POR QUE NO ES "LEER EL TEXTO"
============================================

El problema de las fotos no es que el OCR lea mal el texto: es que el texto no
alcanza. En el caso real que hay en la base (`IMG_4220.jpeg`) la lectura sale:

    Aves | 94.90 97,56
    G 1.028 HILANESA DE PECHU 94.90 4

La descripcion es basura —"HILANESA DE PECHU" en vez de "HILANESA DE PECHUGA"— y
un LLM no la arregla. Pero los tres numeros **si son los correctos**:

    1.028 x 94.90 == 97.56

al centavo, y ese 97.56 es el TOTAL que el propio ticket declara. Eso no es
casualidad: es la unica senal fuerte que hay en un ticket ilegible, y es la que
decide si la extraccion entra al inventario.

ASI QUE EL ORDEN ES ESTE
========================

1. **Geometria, no texto.** Las palabras llegan con `x0`/`x1` de
   `image_to_data`. En un ticket, la descripcion esta a la izquierda y los
   numeros a la derecha, alineados. Una `l` leida donde iba un `1` no mueve la
   caja: las coordenadas son fiables aunque el texto este roto.

2. **De derecha a izquierda.** El ultimo numero de la linea es el importe de la
   linea. El anterior suele ser el precio unitario, y el anterior a ese la
   cantidad. Se leen por posicion, no por nombre.

3. **La aritmetica como juez.** `cantidad x precio == importe`. Si no cuadra, la
   linea no es de producto: es de impuesto, de cobro, de un cero suelto. Se
   descarta SIN entrar al inventario.

4. **La suma global como verificador.** Si la suma de los importes de linea
   coincide con el SUBTOTAL que declara el ticket, la extraccion es **muy
   probablemente correcta** aunque el texto sea basura. Un caracter mal leido
   rompe la suma; una suma que cuadra al centavo sobre muchos renglones casi no
   puede ser casualidad.

EL VEREDICTO, Y POR QUE NO ES UN SI/NO
======================================

Un parseo puede estar bien y aun asi no ser confiable, y al reves. Se devuelven
tres niveles, y cada uno decide algo distinto:

- `CONCILIA`: los importes de linea suman el subtotal del ticket. **Es la unica
  señal que da permiso para mover inventario.**
- `PARCIAL`: hay lineas que no cuadran entre si, pero el total del ticket se
  conoce. Se guardan, marcadas, y van a revision humana.
- `NADA`: no se interpretaron lineas. No se inventa nada.

Y el ultimo detalle, que es el que hace esto mantenible: **la confianza de la
extraccion no se pide al OCR**. Se pide a la aritmetica. Un OCR con 60% de
confianza por palabra puede dar una extraccion CONCILIA, y uno con 95% puede dar
NADA. Medir la exactitud por la confianza del motor es medir la cosa que no
importa.

LO QUE NO HACE
==============

No inventa una linea. Si el papel no dice que compro, no se pone que compro. Un
producto inventado es peor que un producto faltante: uno se puede leer, el otro
miente.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

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

# Que tan lejos puede estar `cantidad x precio` del importe de la linea y
# seguir siendo la misma linea.
#
# 0.02 en pesos es dos centavos: mas que eso y ya no es redondeo de una linea de
# ticket, es un numero leido mal. Se compara sobre el importe, asi que una linea
# de 97.56 tolera 2 centavos y una de 4,000 tolera 80 — que es proporcional y
# es lo correcto para un ticket real, donde los centavos se pierden en el
# redondeo por unidad.
TOLERANCIA_LINEA = Decimal("0.02")

# Cuando la suma de lineas tiene que coincidir con el subtotal del ticket.
#
# Es mas amplia que la de una linea porque acumula el error de todas: con 15 lineas
# cada una con 2 centavos de diferencia, la suma se va hasta 30 centavos. Un 1% es
# un tope generoso que aun asi descarta el caso real de una lectura mala —porque
# una lectura mala se equivoca en decimales o en digitos, no en centavos.
TOLERANCIA_SUBTOTAL = Decimal("0.01")

# Palabras que indican que una linea NO es de producto.
#
# No es una lista de palabras prohibidas: es la lista de los rótulos que un
# ticket mexicano pone junto a sus totales y que tambien traen numeros. Si no se
# filtran, "IVA 540.00" se parsea como una linea de producto con cantidad 540 y
# precio 1.00, y eso si entra al inventario.
ROTULOS_FINALES = re.compile(
    r"^\s*(sub\s*total|total|iva|i\.?v\.?a|impuesto|descuento|dto|"
    r"cambio|efectivo|tarjeta|credito|debito|pts|puntos|ahorro|"
    r"consumo|cuenta|folio|factura|no\.?\s*de|telefono|tel\.?|domicilio)\b",
    re.IGNORECASE,
)

# Un numero con separador de miles, decimal, o los dos.
#
# Se acepta que el separador de miles falte y que el decimal sea coma: los dos
# aparecen en tickets mexicanos y decidir por el contexto es la unica forma
# fiable. Ver `_a_decimal`.
_NUMERO = re.compile(r"^[\$€]?\s*-?\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{1,3})?$|"
                     r"^[\$€]?\s*-?\d+(?:[.,]\d{1,3})?$")


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


@dataclass
class LineaProducto:
    """Una linea de producto ya interpretada."""

    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal | None
    importe: Decimal
    orden: int
    # De donde salio cada numero, para poder auditar la decision. Sin esto, un
    # `None` en `precio_unitario` no se distingue de "no lo lei" de "lo calcule".
    fuente: dict[str, str] = field(default_factory=dict)
    # La linea NO cuadro y por eso. Se guarda para que la cola diga que arreglar.
    aviso: str | None = None


@dataclass
class LecturaDeLineas:
    """Lo que el parser concludes, con su nivel de confianza."""

    lineas: list[LineaProducto] = field(default_factory=list)
    # Uno de: CONCILIA, PARCIAL, NADA
    veredicto: str = "NADA"
    subtotal_declarado: Decimal | None = None
    suma_de_lineas: Decimal | None = None
    # Por que no se llego a CONCILIA, cuando no se llego. Sin esto la cola dice
    # "revisar" sin decir que revisar.
    motivo: str | None = None
    # Cuantas lineas se vieron y cuantas se descartaron. Un parseo que descarta 12
    # de 14 y concilia igual tiene un problema que hay que ver.
    vistas: int = 0
    descartadas: int = 0


# Las letras que el OCR pone donde va un digito, y el digito que queria.
#
# ## POR QUE ESTA TABLA Y NO UN REEMPLAZO GLOBAL
# ==============================================
#
# La peticion original era "reemplazar letras 'O' por ceros '0' en bloques
# numericos". Un reemplazo GLOBAL produce dano de verdad, y el caso esta medido:
# `OXXO` se convierte en `0XX0`, y el nombre del comercio es el dato que mas se
# usa de un ticket.
#
# Por eso la regla no es "todo lo que parezca letra es un digito", sino la
# inversa y mas estrecha: **solo dentro de una tirada que YA tiene un digito**.
# En `1,1OO.OO` la tirada ya es numerica (tiene `1` y `1`) y las `O` son digitos;
# en `OXXO` la tirada **no tiene ningun digito**, asi que no se toca. Eso es lo
# que separa las dos palabras.
#
# Y ni siquiera eso es la regla final: esto **no afirma** que `1,1OO.OO` era
# `1,100.00`. Solo **ofrece esa lectura como candidata**, y la aritmetica del
# propio comprobante decide si esa lectura cuadra con el resto de la linea
# (`_elegir_por_aritmetica`). Un numero "arreglado" que no multiplica por su
# cantidad y da su importe no entra: se descarta como cualquier otra lectura que
# no cuadra.
#
# Por eso esto va en `_todos_los_valores` y no como una pasada de limpieza sobre
# el texto. Limpiar el texto antes de parsear seria **afirmar**: el `1,1OO.OO`
# pasaria a ser `1,100.00` sin que nadie lo comprobara. Ofrecerlo como
# candidato es **preguntar**, y la respuesta la da el papel.
#
# `docs/known-issues.md` §21 ya midio lo que pasa con estos filtros:
# "Un filtro de whitelist de digitos empeora: `551.50`, `01.50`". Por eso la
# correccion es condicional y va de ultima.
#
# ## LA LISTA ES CORTA A PROPOSITO
# ===============================
#
# Solo los glifos que Tesseract confunde de verdad con digitos en letra de
# ticket. No se incluyen `D` ni `U` (->0): `X` e `Y` salen mucho en palabras y
# anadirlos multiplica las lecturas candidatas sin aportar casos reales.
_CONFUSIONES_A_DIGITO: dict[str, str] = {
    "O": "0", "o": "0",
    "I": "1", "l": "1", "|": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
}

# Un caracter que puede vivir dentro de un numero segun el OCR. La tirada se
# arma con esto, y solo se corrige si la tirada contiene AL MENOS un digito real.
#
# Las letras van DENTRO de la clase de caracteres, no concatenadas despues: la
# primera version era `r"[\d,.\-_]" + re.escape(...)`, que produce el patron
# `[\d,.\-_]OoIl\|SBZ` — la clase se cierra en el `]` y las letras quedan como
# texto literal. El resultado era que `_EN_NUMERO` **no reconociba ni al `1`**,
# `_lectura_corregida` devolvia `None` siempre, y la funcion entera era decoracion
# que parecia funcionar porque no fallaba: no hacia nada.
_EN_NUMERO = re.compile(
    r"[\d,.\-_"
    + re.escape("".join(_CONFUSIONES_A_DIGITO))
    + r"]"
)


def _lectura_corregida(texto: str) -> str | None:
    """El token con las confusiones de digito resueltas, o `None`.

    `None` cuando no hay nada que corregir: sin confusiones, o sin ninguna
    tirada que contenga un digito — que es el caso de `OXXO`, `TUS`, `CAJA`.

    ## POR QUE CORRIGE LA LETRA EN LUGAR DE BORRARLA
    ==================================================

    Antes, `_a_decimal` hacia `re.sub(r"[^\\d,.]", "", texto)`: **borraba** la
    `O`. O sea que `1,1OO.OO` no producia `1,1.` — produce una tirada truncada que
    no es ningun numero, y el token se pierde entero. Borrar no es interpretar:
    el digito estaba ahi, mal reconocido, y descartar la letra entero tira tambien
    la cifra que el OCR si habia leido bien.
    """
    if not any(ch in _CONFUSIONES_A_DIGITO for ch in texto):
        return None

    salida: list[str] = []
    hubo_correccion = False
    # Se recorre de izquierda a derecha agrupando tiradas. Una tirada son los
    # caracteres que PUEDEN ser parte de un numero; si la tirada completa tiene
    # un digito, se corrige; si no, se deja igual.
    i = 0
    while i < len(texto):
        if not _EN_NUMERO.match(texto[i]):
            salida.append(texto[i])
            i += 1
            continue
        fin = i
        while fin < len(texto) and _EN_NUMERO.match(texto[fin]):
            fin += 1
        tirada = texto[i:fin]
        if any(ch.isdigit() for ch in tirada):
            corregida = "".join(_CONFUSIONES_A_DIGITO.get(ch, ch) for ch in tirada)
            if corregida != tirada:
                hubo_correccion = True
            salida.append(corregida)
        else:
            # Sin digito real: esto es una palabra (`OXXO`), no un numero roto.
            salida.append(tirada)
        i = fin

    if not hubo_correccion:
        return None
    return "".join(salida)


def _a_decimal(texto: str) -> Decimal | None:
    """Un token a `Decimal`, o None si no es un numero.

    POR QUE DEVUELVE UNA LISTA Y NO UN NUMERO
    ==========================================

    `1.028` es ambigüo: mil veintiocho en formato europeo, uno coma cero dos
    ocho en formato mexicano. Con el caso real de la base la ambiguedad es
    real y no academica:

        G 1.028 HILANESA DE PECHU 94.90 97.56
        1.028 x 94.90 == 97.56   <- si 1.028 es la CANTIDAD
        1028  x 94.90 == 97,563  <- si es un numero con miles

    Solo una de las dos lecturas hace que la linea cuadre. Por eso aqui NO se
    decide: se devuelven LAS DOS y es `leer_lineas` la que se queda con la que
    reconcilia.

    Decidir aqui por heuristica (la del manual de separadores) habriaMetadata
    colgado el `1.028` a 1028 y descartado una linea correcta. La aritmetica del
    propio comprobante es mejor juez que cualquier regla de formato, y ya esta
    disponible.
    """
    limpio = re.sub(r"[^\d,.]", "", texto)
    if not limpio or not any(ch.isdigit() for ch in limpio):
        return None

    candidatos: list[Decimal] = []

    def _intentar(valor: str) -> None:
        try:
            d = Decimal(valor)
        except InvalidOperation:
            return
        if d not in candidatos:
            candidatos.append(d)

    # Lectura literal: sin interpretacion de separadores.
    _intentar(limpio)

    if "," in limpio or "." in limpio:
        # El separador DECIMAL es el ULTIMO; lo anterior es de miles.
        if limpio.rfind(",") > limpio.rfind("."):
            entero, _, decimal = limpio.rpartition(",")
            _intentar(f"{entero.replace(',', '') or '0'}.{decimal}")
        else:
            entero, _, decimal = limpio.rpartition(".")
            _intentar(f"{entero.replace('.', '') or '0'}.{decimal}")

        # Y la lectura en la que el separador final es de miles: util para
        # `97,563` leido de un `97.56` con un 3 pegado.
        _intentar(limpio.replace(",", "").replace(".", ""))

    # La lectura con las confusiones de digito resueltas, y **la ultima**.
    #
    # El orden importa: `_elegir_por_aritmetica` se queda con la PRIMERA lectura
    # que hace cuadrar la multiplicacion. Poniendo la corregida al final, la
    # literal gana siempre que pueda, y la corregida solo entra cuando la literal
    # no cuadra — que es exactamente cuando no hay nada que perder.
    corregido = _lectura_corregida(texto)
    if corregido is not None:
        solo = re.sub(r"[^\d,.]", "", corregido)
        _intentar(solo)
        if "," in solo or "." in solo:
            if solo.rfind(",") > solo.rfind("."):
                entero, _, dec = solo.rpartition(",")
                _intentar(f"{entero.replace(',', '') or '0'}.{dec}")
            else:
                entero, _, dec = solo.rpartition(".")
                _intentar(f"{entero.replace('.', '') or '0'}.{dec}")

    return candidatos[0] if candidatos else None


def _todos_los_valores(texto: str) -> list[Decimal]:
    """TODAS las lecturas plausibles de un token.

    El caso ambiguo de verdad es UN separador seguido de tres digitos: `1.028` es
    uno coma cero dos ocho en un ticket mexicano y mil veintiocho en uno europeo.
    Ese caso ofrece las dos lecturas. Con dos separadores no hay duda (`1,250.50`
    es mil doscientos cincuenta con cincuenta) y se ofrece una sola.

    No se ofrece "quitar todos los separadores" como lectura alternativa: para
    `97.56` produciria `9756`, que casi nunca reconcilia y solo ensucia el
    catalogo de lecturas.
    """
    limpio = re.sub(r"[^\d,.]", "", texto)
    if not limpio or not any(ch.isdigit() for ch in limpio):
        return []

    def _agregar(destino: list[Decimal], valor: str) -> None:
        try:
            d = Decimal(valor)
        except InvalidOperation:
            return
        if d not in destino:
            destino.append(d)

    salida: list[Decimal] = []
    comas = limpio.count(",")
    puntos = limpio.count(".")

    def _normalizar(bruto: str) -> None:
        """Agrega las lecturas plausibles de un token YA sin letras.

        Se extrajo como funcion porque la lectura corregida necesita exactamente
        la misma normalizacion que la literal, y duplicarla es como aparecio el
        bug que esto arregla: la copia pasaba `1,100.00` tal cual a `Decimal`, que
        **rechaza la coma** (`InvalidOperation`), asi que la lectura se perdia en
        silencio y `_todos_los_valores("1,1OO.OO")` devolvia `['11']` — el numero
        que AGENTS.md documenta como el caso canonico, descartado por el camino
        que se suponia que lo arreglaba.

        Una normalizacion en un sitio, y las dos entradas pasan por ella.
        """
        comas_ = bruto.count(",")
        puntos_ = bruto.count(".")

        if comas_ + puntos_ == 0:
            _agregar(salida, bruto)

        elif comas_ + puntos_ >= 2:
            # Formato con miles Y decimal: no hay ambiguedad que resolver.
            #
            # La parte entera se limpia de LOS DOS separadores, no solo del que no
            # resulto ser el decimal. Con `1,250.50` la rama del punto deja
            # `1,250` como entero, y `Decimal("1,250.50")` es invalido: la lectura
            # se pierde entera y el token se descarta. Medido: `1,250.50 -> []`.
            if bruto.rfind(",") > bruto.rfind("."):
                entero, _, decimal = bruto.rpartition(",")
            else:
                entero, _, decimal = bruto.rpartition(".")
            entero = re.sub(r"[.,]", "", entero) or "0"
            _agregar(salida, f"{entero}.{decimal}")

        else:
            # Un solo separador. Se ofrecen las dos lecturas cuando lo que va
            # detras tiene tres digitos, que es el caso ambiguo; con dos, es un
            # decimal claro.
            separador = "," if comas_ else "."
            entero, _, decimal = bruto.rpartition(separador)
            entero = entero.replace(",", "").replace(".", "") or "0"

            _agregar(salida, f"{entero}.{decimal}")
            if len(decimal) == 3:
                # El ambiguo: `1.028` -> 1.028 (cantidad) o 1028 (mil veintiocho).
                _agregar(salida, f"{entero}{decimal}")

    if comas + puntos == 0:
        _agregar(salida, limpio)
    elif comas + puntos >= 2:
        _normalizar(limpio)
    else:
        _normalizar(limpio)

    # Y AL FINAL la lectura con las confusiones de digito resueltas.
    #
    # Va FUERA de las ramas del `if/elif/else` y no dentro de la ultima, y eso es
    # lo que importa. Con un `return` en cada rama —que es como estaba— la lectura
    # corregida solo se ofrecia con UN separador, y el caso que la motivo,
    # `1,1OO.OO`, cae en el de DOS: al borrar las `O` el token queda `1,1.`.
    #
    # Ultima a proposito: `_elegir_por_aritmetica` se queda con la PRIMERA lectura
    # que hace cuadrar la multiplicacion, asi que la corregida solo entra cuando
    # ninguna de las literales cuadra.
    corregido = _lectura_corregida(texto)
    if corregido is not None:
        _normalizar(re.sub(r"[^\d,.]", "", corregido))

    return salida


def _es_numero(texto: str) -> bool:
    """¿El token es un numero que puede participar en una linea de producto?

    ANTES FILTRABA POR CANTIDAD DE DIGITOS ("3 a 9"), y era un bug: una cantidad
    de 1 o 2 digitos —"LLAVE 2 25.00 50.00"— quedaba fuera, la linea se
    reinterpretaba como cantidad 1, y `1 x 25.00 = 25.00 != 50.00` la echaba.
    Medido: `test_varias_lineas_que_cuadran` fallaba por esto.

    Ahora solo se exige que tenga digitos. Lo que hace de juez es la
    multiplicacion, y es mejor juez que un filtro de forma: un folio de 8
    digitos tampoco sobrevive a `cantidad x precio == importe`, mientras que una
    cantidad legitima de un digito si.
    """
    limpio = texto.strip()
    if not limpio:
        return False
    return bool(re.search(r"\d", limpio))


def _numeros_de(linea: dict) -> list[tuple[list[Decimal], str]]:
    """Los numeros de la linea: `(lecturas_posibles, texto)`, de izquierda a derecha.

    Cada elemento trae todas las lecturas plausibles del token, no una sola. Ver
    `_todos_los_valores` para por que `1.028` son dos numeros y no uno.

    El texto original se conserva porque es lo que va a `LineaProducto.fuente`:
    la auditoria necesita ver lo que el OCR leyo, no lo que el sistema concluyo
    que queria decir.
    """
    salida = []
    for palabra in linea.get("palabras", []):
        texto = str(palabra.get("texto") or "").strip()
        if not _es_numero(texto):
            continue
        lecturas = _todos_los_valores(texto)
        if lecturas:
            salida.append((lecturas, texto))
    return salida


def _descripcion_de(linea: dict, hasta_x: float) -> str:
    """El texto a la IZQUIERDA del ultimo numero: la descripcion del producto.

    Se corta por coordenada y no por posicion en la lista, porque el OCR puede
    devolver las palabras desordenadas y lo que define "la descripcion" es donde
    caen en el papel.

    Y se recorta la COLUMNA DE PRECIO del final. Un renglon de ticket es
    `descripcion | cantidad | precio | importe`, asi que cortar solo en el importe
    deja el precio pegado a la descripcion: "HILANESA DE PECHU 94.90", que como
    nombre de producto no sirve para nada y además se compara mal contra el
    catálogo.

    Se quitan **solo los tokens numericos del final**, nunca los del principio:
    "7up 600ml" es un producto y "Caja 12" puede serlo. Un producto no termina en
    un numero de columna, pero sí puede empezar por uno.
    """
    partes = [
        str(p.get("texto") or "").strip()
        for p in linea.get("palabras", [])
        if p.get("x0", 0) < hasta_x and str(p.get("texto") or "").strip()
    ]
    while partes and _es_numero(partes[-1]):
        partes.pop()
    return " ".join(partes).strip()


def _elegir_por_aritmetica(
    lecturas_importe: list[Decimal],
    previos: list[tuple[list[Decimal], str]],
) -> tuple[Decimal, Decimal, Decimal | None, dict[str, str]] | None:
    """La combinacion de numeros en la que la multiplicacion da el importe.

    Devuelve `(importe, cantidad, precio, textos)` o `None` si ninguna combinacion
    cuadra — y en ese caso la linea se descarta, que es la decision segura.

    POR QUE NO UNA REGLA DE FORMATO
    ==============================

    Porque el formato no alcanza. `1.028` es mil veintiocho en un papel europeo y
    uno coma cero dos ocho en uno mexicano, y el sistema no sabe en que pais se
    imprimio. Con el caso real de la base, `1.028 x 94.90 == 97.56` al centavo:
    la aritmetica del comprobante decide, y decide bien.

    Esto es lo que hace que la extraccion sea **auto-validante**: no se afirma
    que el numero sea `1.028`, se comprueba que multiplicado por lo que hay a su
    izquierda da lo que hay a su derecha. Si no da, el numero no era ese.
    """
    # El importe: se prueban sus lecturas, de de la mas simple a la mas compuesta.
    for importe in lecturas_importe:
        if importe <= 0:
            continue

        # Un solo numero en la linea: es el importe de una unidad.
        if not previos:
            return (importe, Decimal("1"), importe, {"precio": ""})

        if len(previos) == 1:
            for precio in previos[0][0]:
                if precio > 0 and _cerca(precio, importe):
                    return (importe, Decimal("1"), precio, {"precio": previos[0][1]})
            # Un numero previo que no da el importe: puede ser un descuento o un
            # factor. Se usa como precio y se marca despues como dudoso.
            for precio in previos[0][0]:
                if precio > 0:
                    return (importe, Decimal("1"), precio, {"precio": previos[0][1]})
            continue

        # Dos o mas: los dos últimos son cantidad y precio, en algun orden.
        izq, der = previos[-2], previos[-1]
        for a in izq[0]:
            if a <= 0:
                continue
            for b in der[0]:
                if b <= 0:
                    continue
                if _cerca(a * b, importe):
                    return (
                        importe,
                        a,
                        b,
                        {"cantidad": izq[1], "precio": der[1]},
                    )
                if _cerca(b * a, importe):
                    # Algunos tickets imprimen "precio cantidad importe".
                    return (
                        importe,
                        b,
                        a,
                        {"cantidad": der[1], "precio": izq[1]},
                    )

    return None


def _linea_vertical(linea: dict) -> bool:
    """Cosas que son lineas pero no de producto.

    Un encabezado, un pie, un aviso de puntos. Sin este filtro, "TUS PUNTOS
    VENCEN: 31/10/2026" es una linea de producto con cantidad 31.
    """
    texto = " ".join(
        str(p.get("texto") or "") for p in linea.get("palabras", [])
    ).strip()
    # Una sola expresion, y no tres `return` en cadena. La diferencia practica:
    # cada rama del `if` era un sitio donde anadir una condicion nueva hacia
    # falta acordarse de a cual de los tres `return` tocar, y el que se
    #olvidaba no fallaba — devolvia `False` como los demas.
    #
    # LA TERCERA CONDICION ESTABA Y SE PERDIO
    # ========================================
    #
    # El comentario de abajo —"Una linea sin ningun numero no aporta nada al
    # inventario"— estaba, pero la condicion que describia **no**. El codigo
    # quedo con dos ramas y tres lineas de comentario que describen una tercera.
    # Medido antes de arreglarlo:
    #
    #     _linea_vertical("GRACIAS POR SU COMPRA")   -> False   (no descarta)
    #
    # y `leer_lineas` se la llevaba como linea de producto.
    #
    # Con el filtro puesto, "GRACIAS POR SU COMPRA", "TUS PUNTOS VENCEN" y el
    # membrete del ticket salen antes de que `_elegir_por_aritmetica` los mire.
    # Sin el dependen de que la aritmetica los rechace, y una linea de texto sin
    # ningun numero no produce ningun numero que rechazar: no hay nada que la
    # detiene.
    return (
        not texto
        or bool(ROTULOS_FINALES.match(texto))
        # Ni un solo numero. `re.search` y no `re.match` porque en un ticket el
        # numero va al FINAL de la linea (`COCA COLA 1.5L 35.00`) y el texto al
        # principio: anclar al principio habria descartado las lineas de producto.
        or not re.search(r"\d", texto)
    )


def leer_lineas(
    lineas_ocr: list[dict] | tuple[dict, ...],
    *,
    subtotal_declarado: Decimal | None = None,
) -> LecturaDeLineas:
    """Interpreta las lineas geometricas de un ticket como lineas de producto.

    `subtotal_declarado` es el SUBTOTAL que el propio ticket imprime. Es
    **opcional pero es lo que da la confianza**: sin el, el resultado nunca pasa
    de `PARCIAL`, porque no hay contra que reconciliar. Con el, la suma de las
    lineas tiene que coincidir y eso es un juez fuerte.
    """
    resultado = LecturaDeLineas(subtotal_declarado=subtotal_declarado)
    if not lineas_ocr:
        resultado.motivo = "el OCR no devolvio lineas con geometria"
        return resultado

    orden = 0
    suma = Decimal("0")

    for linea in lineas_ocr:
        if _linea_vertical(linea):
            continue
        resultado.vistas += 1

        numeros = _numeros_de(linea)
        if not numeros:
            resultado.descartadas += 1
            continue

        # De derecha a izquierda: el ultimo numero es el importe de la linea.
        lecturas_importe, texto_importe = numeros[-1]
        x_del_importe = 0.0
        for p in linea.get("palabras", []):
            if str(p.get("texto") or "").strip() == texto_importe:
                x_del_importe = float(p.get("x0") or 0)
                break

        descripcion = _descripcion_de(linea, x_del_importe)
        if not descripcion:
            # Sin descripcion la linea no identifica nada. Se descarta: un
            # producto sin nombre en el catalogo no es un producto.
            resultado.descartadas += 1
            continue

        previos = numeros[:-1]

        # --- SELECCION POR ARITMETICA --------------------------------------
        #
        # Se prueban las lecturas de los numeros y se queda con la primera
        # combinacion en la que `cantidad x precio == importe`. Es lo que
        # resuelve el `1.028` del caso real: con `1028` no cuadra nada y con
        # `1.028` cuadra exacto, asi que gana la lectura correcta SIN que nadie
        # tenga que saber de formato mexicano.
        #
        # El orden de las combinaciones es "las mas habituales primero", no
        # "todas": 4 productos para 3 numeros es 64 combinaciones y en un ticket
        # de 20 lineas eso son 1280 multiplicaciones por foto. Se prueban las
        # utiles y con eso basta: los ticketsgeries bien, si no serian ilegibles.
        eleccion = _elegir_por_aritmetica(lecturas_importe, previos)
        if eleccion is None:
            resultado.descartadas += 1
            continue

        importe, cantidad, precio, textos = eleccion

        fuente = {"importe": texto_importe}
        if textos.get("cantidad"):
            fuente["cantidad"] = textos["cantidad"]
        if textos.get("precio"):
            fuente["precio"] = textos["precio"]

        aviso = None
        if precio is None:
            aviso = "no se pudo leer el precio unitario"
        elif not _cerca(cantidad * precio, importe):
            aviso = f"la linea no cuadra: {cantidad} x {precio} != {importe}"

        suma += importe
        resultado.lineas.append(
            LineaProducto(
                descripcion=descripcion[:300],
                cantidad=cantidad,
                precio_unitario=precio,
                importe=importe,
                orden=orden,
                fuente=fuente,
                aviso=aviso,
            )
        )
        orden += 1

    resultado.suma_de_lineas = suma if resultado.lineas else None

    if not resultado.lineas:
        resultado.veredicto = "NADA"
        resultado.motivo = (
            f"se vieron {resultado.vistas} lineas con texto y ninguna resulto "
            "interpretable como producto"
        )
        return resultado

    if subtotal_declarado is None:
        resultado.veredicto = "PARCIAL"
        resultado.motivo = (
            "hay lineas pero el ticket no declara un subtotal con el cual "
            "reconciliar; sin ese juez no se puede afirmar que sean correctas"
        )
        return resultado

    diferencia = abs(suma - subtotal_declarado)
    if diferencia <= max(
        TOLERANCIA_SUBTOTAL * max(abs(subtotal_declarado), Decimal("1")),
        Decimal("0.05"),
    ):
        resultado.veredicto = "CONCILIA"
        resultado.motivo = (
            f"las {len(resultado.lineas)} lineas suman {suma}, y el ticket "
            f"declara {subtotal_declarado}"
        )
    else:
        resultado.veredicto = "PARCIAL"
        resultado.motivo = (
            f"las lineas suman {suma} y el ticket declara "
            f"{subtotal_declarado}; diferencia de {diferencia}"
        )

    return resultado


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


def _cerca(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(TOLERANCIA_LINEA * max(abs(b), Decimal("1")), Decimal("0.01"))


def como_items(lectura: LecturaDeLineas) -> list[dict]:
    """La lectura en la forma que `ticket.items` espera.

    **Solo si CONCILIA.** Un parseo `PARCIAL` no se convierte en `items`: las
    lineas dudosas se pueden guardar para que una persona las mire, pero no
    llegan al inventario. Es la traduccion de la regla del modulo —el inventario
    no se mueve con numeros que no se sostienen— al formato de la base.
    """
    if lectura.veredicto != "CONCILIA":
        return []

    # Pasa por `LineaDeTicket` antes de volverse dict. Una linea que no valida
    # NO se descarta en silencio: se cuenta y se dice en el log, porque una linea
    # que se pierde es informacion que el operador necesita ("el ticket se leyo
    # bien pero algo no cuadrado").
    #
    # Por que el filtro no basta. `aviso is None` dice que la linea no tiene
    # marca de duda, y ya paso la aritmetica `cantidad x precio == importe`. Pero
    # la aritmetica usa `TOLERANCIA_LINEA` —2 centavos— y un importe leido como
    # `0.00` con cantidad `1` y precio `0.00` CUADRA a la perfeccion y no es una
    # compra. La validacion del modelo es la que separa "cuadra" de "es un
    # numero de dinero".
    items: list[dict] = []
    descartadas_por_validacion = 0
    for linea in lectura.lineas:
        if linea.aviso is not None:
            continue
        try:
            valida = LineaDeTicket(
                description=linea.descripcion,
                quantity=linea.cantidad,
                unit_price=linea.precio_unitario,
                total=linea.importe,
            )
        except ValidationError as exc:
            descartadas_por_validacion += 1
            logger.info(
                "linea de producto descartada por validacion: %s",
                exc.errors()[0].get("msg", "sin motivo"),
            )
            continue
        # `mode="json"` y NO `float()`: sale el `Decimal` y `JSONConDecimal` lo
        # lleva a la columna `items` como texto, sin perder un centavo.
        items.append(valida.model_dump(mode="json"))

    if descartadas_por_validacion:
        logger.info(
            "%d linea(s) cuadraron pero no pasaron el modelo; el resto se guarda",
            descartadas_por_validacion,
        )
    return items


def resumen_legible(lectura: LecturaDeLineas) -> str:
    """Una linea de texto para el log y para el `validation_errors`.

    Va en el ticket porque es la evidencia de por que se leyo o no: un ticket
    `PENDIENTE` con este motivo dice "el sistema intento y no pudo", que es
    distinto de un ticket que nadie abrio.
    """
    if lectura.veredicto == "NADA":
        return f"sin lineas de producto ({lectura.motivo})"
    partes = [f"{lectura.veredicto}: {lectura.motivo}"]
    if lectura.descartadas:
        partes.append(f"{lectura.descartadas} linea(s) descartada(s)")
    dudosas = [l for l in lectura.lineas if l.aviso]
    if dudosas:
        partes.append(
            f"{len(dudosas)} con aviso: " + "; ".join(l.aviso or "" for l in dudosas[:3])
        )
    return " | ".join(partes)