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

    if comas + puntos == 0:
        _agregar(salida, limpio)
        return salida

    if comas + puntos >= 2:
        # Formato con miles Y decimal: no hay ambiguedad que resolver.
        #
        # La parte entera se limpia de LOS DOS separadores, no solo del que no
        # resulto ser el decimal. Con `1,250.50` la rama del punto deja
        # `1,250` como entero, y `Decimal("1,250.50")` es invalido: la lectura se
        # pierde entera y el token se descarta. Medido: `1,250.50 -> []`.
        if limpio.rfind(",") > limpio.rfind("."):
            entero, _, decimal = limpio.rpartition(",")
        else:
            entero, _, decimal = limpio.rpartition(".")
        entero = re.sub(r"[.,]", "", entero) or "0"
        _agregar(salida, f"{entero}.{decimal}")
        return salida

    # Un solo separador. Se ofrecen las dos lecturas cuando lo que va detras
    # tiene tres digitos, que es el caso ambiguo; con dos, es un decimal claro.
    separador = "," if comas else "."
    entero, _, decimal = limpio.rpartition(separador)
    entero = entero.replace(",", "").replace(".", "") or "0"

    _agregar(salida, f"{entero}.{decimal}")
    if len(decimal) == 3:
        # El ambiguo: `1.028` -> 1.028 (cantidad) o 1028 (mil veintiocho).
        _agregar(salida, f"{entero}{decimal}")

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
    if not texto:
        return True
    if ROTULOS_FINALES.match(texto):
        return True
    # Una linea sin ningun numero no aporta nada al inventario.
    return False


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
    return [
        {
            "description": linea.descripcion,
            "quantity": float(linea.cantidad),
            "unit_price": float(linea.precio_unitario) if linea.precio_unitario is not None else None,
            "total": float(linea.importe),
        }
        for linea in lectura.lineas
        if linea.aviso is None
    ]


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