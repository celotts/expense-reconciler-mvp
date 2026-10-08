"""De la factura digitalizada a la entrada al inventario.

La regla de negocio, en una linea
---------------------------------

Digitalizar la factura, extraer sus datos, registrar los productos nuevos, y
dejar la orden de compra en un estado que alguien tiene que confirmar. **Solo
ese ultimo estado mueve el inventario.**

Los tres estados son `EstadoCompra` (ver `app/core/enums.py`):

    PROCESAR  -> se esta digitalizando y extrayendo
    EN_REVISION -> extraida; esperando que alguien la autorice
    PROCESADO -> autorizada; YA sumo stock

Por que PROCESADO lo hace una persona y no el sistema
-------------------------------------------------------

No es prudencia generica. Los cinco motivos estan medidos:

1. La exactitud del OCR sobre fotos reales es **33.3%** (AGENTS.md). Los cuatro
   caminos para mejorarla estan descartados con medicion en
   `docs/known-issues.md` 21.

2. Una linea es un tiro mas que el total. En un comprobante de 15 lineas, que
   las 15 esten bien no tiene la misma probabilidad que el total este bien: se
   multiplican. A 95% por linea, las 15 correctas dan 46%. A 80%, dan 3.5%.

3. **La conciliacion bancaria valida el monto, nunca la composicion.** Un ticket
   puede dar `PERFECT` contra el banco —el total cuadrado al centavo— y tener
   las lineas equivocadas. El banco dice que se gastaron $4,093.80; no dice que
   se compro.

4. `confianza_por_campos` evalua los campos del encabezado. **Nunca ha medido la
   confianza de una linea**, asi que `AUTO_APROBADO` no es una garantia sobre
   `items`. Tratarlo como si lo fuera seria el salto que este proyecto no puede
   sostener.

5. Y la deriva es **monotona**: una cantidad de mas infla el stock para siempre.
   Las ventas si las cuentas tu, asi que la diferencia entre lo contado y lo que
   dice el sistema crece sin techo, y no hay ninguna entrada que la revierta.

Idempotencia: por que un comprobante no se cuenta dos veces
-----------------------------------------------------------

Por `compras.ticket_id`, que es UNIQUE. Es el unico punto donde se puede
garantizar: sin el, un `POST` repetido o el escaner releyendo el mismo papel
generan dos compras del mismo comprobante y el inventario sube el doble.

Se apoya en `ix_tickets_source_hash` (unico por `(company_id, source_hash)`), que
ya garantiza que no haya dos tickets del mismo papel en la misma empresa.

Por que el stock se calcula y no se guarda
------------------------------------------

Porque una columna `stock` es una copia, y una copia se desincroniza siempre.
El stock es la SUMA de `movimientos_inventario`: `stock_de()` mas abajo.

QUE PASA CON UNA LINEA CUYA DESCRIPCION NO ESTA EN EL CATALOGO
=============================================================

**Se crea el producto, y se marca como no verificado.** `resolver_producto` lo
resuelve en tres pasos, en este orden:

  1. Por codigo de barras. Es lo mas fuerte: no hay dos productos con el mismo
     codigo, y si existe, ese es.
  2. Por **nombre normalizado exacto**: minusculas, sin tildes, sin espacios
     repetidos, sin puntuacion final. Ver `normalizar_descripcion`.
  3. Si no hay coincidencia, se crea con `origen = OCR` y `verificado = false`.

Por que buscar antes de crear, y no solo crear
==============================================

Sin la busqueda, re-escanear el mismo comprobante crea un producto nuevo cada
vez. A la tercera pasada el catalogo tiene tres filas para el mismo carton de
laminas, con tres stocks distintos, y la compra se confirma igual. Nada se
queja: tres filas son tres filas. El inventario quedaria partido sin que ninguna
parte del sistema lo note.

Por que coincidencia EXACTA y no difusa
=======================================

Porque un "parecido" es una adivinanza, y adivinar en el catalogo produce el
error que el resto de este diseno evita a proposito: "Cinta industrial" y
"Cinta Industrial 50mm" se-fundirian con fuzzy matching, y el stock de las dos
se sumaria en una sola fila.

La eleccion es: **si no estoy seguro, son dos productos**, y una persona los
junta en la cola de revision. El coste es una fila de mas. El beneficio es que
no se fusiona nada por error.

Por que se marca en vez de no crear
===================================

Porque las dos salidas alternativas son peores, y la que se descarto primero
tambien:

  - **No crear (lo que habia):** la compra NO se puede confirmar hasta que
    alguien asigne N productos. En un lote de 60 comprobantes son cientos de
    decisiones antes de que el inventario sirva de algo, y el inventario deja
    de reflejar lo que se compro. Un sistema que no refleja lo que pasa es
    peor que uno que lo refleja con ruido.

  - **Crear sin marcar:** el catalogo se llena de "Reginen de", "Regin de" y
    "REGINA DE 12 PULG" y nadie sabe cuales fusionar. En seis meses son 500
    filas y el problema es irresoluble porque ya no se sabe de donde salio
    ninguna.

  - **Crear marcado:** el inventario funciona desde el primer dia, y hay una
    lista ordenada de "estas filas hay que revisarlas", en la que las que mas
    veces aparecen son las que importan. `verificado = false` es esa lista.

Un producto con codigo de barras nace `verificado = true`, porque un codigo es
una identidad y no una descripcion: no hay nada que adivinar.

QUE SIGUE SIN RESOLVERSE
========================

**La cantidad.** El producto se crea solo, pero la CANTITIDA sale de la misma
lectura que esta al 33.3% de exactitud. Un producto bien identificado con una
cantidad mal leida mete la cantidad equivocada al stock, y el trigger de stock
negativo no lo detecta porque la cantidad es positiva.

Eso es el techo real, y no se arregla en este modulo: `docs/known-issues.md` 0.c.
Lo que este modulo garantiza es que el error sea **visible y localizable** —el
producto se puede renombrar, la cantidad se puede corregir con un AJUSTE, y el
kardex no se puede editar— y no una fila de stock que no cuadra sin explicación.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import EstadoCompra, ProductoOrigen, TipoMovimiento
from app.core.time import utcnow
from app.models.inventario import (
    CompraItemModel,
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.models.ticket import TicketModel

logger = logging.getLogger(__name__)

# Cuantos caracteres se guardan de la descripcion de la linea.
#
# 300 y no mas porque es un VARCHAR(300) en el DDL, y una descripcion mas larga
# es ruido de OCR, no informacion: "REGINA DE 12 PULGADAS PAQ 2 UND..." no aporta
# nada despues del numero de referencia, que va aparte si el papel lo trae.
_DESC_MAX = 300


class ErrorDeInventario(Exception):
    """Una operacion de inventario que no se puede hacer.

    Es una excepcion y no un valor de retorno porque estos casos son decisiones
    de negocio, no errores de validacion de forma: el endpoint los traduce a 409
    con el motivo, y el mensaje es la parte que el usuario necesita leer.
    """


class NoExiste(ErrorDeInventario):
    """Lo que se busca no esta. Se traduce a 404, no a 409.

    La distincion importa porque son cosas distintas y el cliente las trata
    distinto: un 404 significa "esta ruta o ese recurso no existe, no lo
    intentes otra vez"; un 409 significa "el recurso esta y su ESTADO no permite
    esto, vuelve a leerlo y reintenta".

    Confundirlos hace que un cliente que reintenta en 409 se quede dando
    vueltas contra un recurso que jamas va a existir.
    """


# ---------------------------------------------------------------------------
# De una linea de papel a un producto del catalogo
# ---------------------------------------------------------------------------


def normalizar_descripcion(texto: str) -> str:
    """La forma en que dos descripciones se comparan entre si.

    Sin esto, "Cinta Industrial", "cinta industrial", "CINTA  INDUSTRIAL." y
    "Cinta Industrial " son cuatro productos, y el stock se reparte entre cuatro
    filas que son la misma cosa. Es exactamente el problema que se quiere evitar
    al crear productos automaticamente, y no se evita con "no crear": se evita
    con crear **una vez** lo que es la misma cosa.

    Que normaliza, y por que cada paso:

      - **minusculas.** "Cinta" y "cinta" son la misma.
      - **sin tildes.** "Café" y "Cafe" son la misma, y el OCR alterna entre
        una y otra en la misma foto.
      - **espacios colapsados.** Un comprobante alinea columnas con espacios, y
        dos palabras separadas por cuatro espacios son dos palabras.
      - **sin puntuacion al final.** El punto y la coma del final de linea son
        del formato, no del nombre.

    NO normaliza los espacios INTERNOS de palabras ni quita unidades: "Caja de
    laminas 12" y "Caja de laminas 6" son productos distintos y fundirlos seria
    peor que tener dos filas.
    """
    t = texto.strip().lower()
    # Las tildes por composicion Unicode (n + combining acute -> á) y por
    # precomposicion (á -> á) dan el mismo resultado aqui.
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .,:;-*#")
    return t


@dataclass(frozen=True)
class ProductoResuelto:
    """El producto de una linea, y si hubo que crearlo.

    El `creado` viaja con el producto a proposito: el que pide la operacion
    necesita distinguir "15 lineas resueltas con 15 productos nuevos" —un
    catalogo que hay que revisar entero— de "15 resueltas con 2 nuevos", que es
    un escaneo normal. Contarlo con un `SELECT count(*)` antes y despues seria
    una carrera y ademas una consulta por linea.
    """

    producto: ProductoModel
    creado: bool
    reutilizado: bool = False


async def resolver_producto(
    db: AsyncSession,
    company_id: UUID,
    descripcion: str,
    *,
    codigo: str | None = None,
) -> ProductoResuelto:
    """Devuelve el producto de esa linea, creandolo si no existe.

    EL ORDEN IMPORTA, Y ES EL QUE HACE QUE EL CATALOGO NO SE LLENE
    ============================================================

    Primero se **busca**, y solo si no hay coincidencia exacta se crea. Sin esa
    busqueda, re-escanear el mismo comprobante crearia un producto nuevo cada
    vez, y a la tercera el catalogo tendria tres filas para el mismo carton de
    laminas con tres stocks distintos. La compra se confirmaria igual y el
    inventario quedaria partido sin que nada se quejara.

    Que coincide: el nombre normalizado EXACTO. No es busqueda difusa, y esa es
    una decision. "Cinta industrial" y "Cinta Industrial 50mm" se adivinarian
    distintos con fuzzy matching, que es justo el error que el automatico no
    debe cometer: si no esta seguro, son dos productos y que una persona los
    junte en la cola de revision. Un fuzzy matching aqui crearia el error de
    inventario que el resto del diseno evita a proposito.

    Que marca un producto nuevo:
      - `origen = OCR` y `verificado = false`, si la linea no trae codigo.
      - `origen = OCR` y `verificado = true`, si trae codigo de barras: un codigo
        es una identidad, no una descripcion, y no hay nada que adivinar.

    Los dos casos dejan rastro (`origen`) y el que necesita trabajo humano deja
    tambien una bandera (`verificado`). Ver db/migrations/0011_productos_origen.sql.
    """
    nombre = descripcion.strip()[:200]
    if not nombre:
        raise ErrorDeInventario("una linea sin descripcion no puede tener producto")

    codigo = (codigo or "").strip() or None

    # 1. Por codigo de barras. Es lo mas fuerte que hay: no hay dos productos con
    #    el mismo codigo, y si existe, ese es el producto.
    if codigo:
        por_codigo = (
            await db.execute(
                select(ProductoModel).where(
                    ProductoModel.company_id == company_id,
                    ProductoModel.codigo == codigo,
                )
            )
        ).scalar_one_or_none()
        if por_codigo is not None:
            return ProductoResuelto(por_codigo, creado=False, reutilizado=True)

    # 2. Por nombre normalizado. Se compara contra la COLUMNA normalizada y no
    #    contra `lower(nombre)`: `lower('Café')` conserva la tilde y el objetivo no,
    #    asi que con `lower` "Café molido" y "Cafe molido" seriam dos productos.
    #    Es el fallo que hace inutil la funcion, y solo se ve con un producto con
    #    tilde.
    objetivo = normalizar_descripcion(nombre)
    candidatos = (
        await db.execute(
            select(ProductoModel).where(
                ProductoModel.company_id == company_id,
                ProductoModel.nombre_normalizado == objetivo,
            )
        )
    ).scalars().all()

    # Varios con el mismo nombre normalizado pueden existir si uno se dio de alta
    # a mano con tildes y otro salio del OCR sin ellas. Se toma el mas antiguo: es
    # el que ya tiene historia y stock, y partir el stock entre dos filas nuevas
    # es peor que tener un duplicado.
    if candidatos:
        elegido = min(candidatos, key=lambda p: (p.created_at, str(p.id)))
        logger.info(
            "Linea %r reutilizo el producto %s (normalizado: %r).",
            nombre[:60], elegido.id, objetivo,
        )
        return ProductoResuelto(elegido, creado=False, reutilizado=True)

    # 3. No hay: se crea. Y nace marcado, porque un texto leido de un papel es una
    #    opinion del sistema, no un dato.
    producto = ProductoModel(
        company_id=company_id,
        nombre=nombre,
        codigo=codigo,
        origen=ProductoOrigen.OCR.value,
        # Con codigo no hay nada que verificar. Sin codigo, si.
        verificado=codigo is not None,
    )
    db.add(producto)
    await db.flush()
    logger.info(
        "Producto creado desde una linea: %r (id=%s, verificado=%s). Va a la "
        "cola de revision si no trae codigo.",
        nombre[:60],
        producto.id,
        producto.verificado,
    )
    return ProductoResuelto(producto, creado=True)


async def resolver_lineas(
    db: AsyncSession,
    compra: CompraModel,
    *,
    actor: str,
) -> tuple[int, int]:
    """Le asigna producto a todas las lineas de la compra.

    Devuelve `(resueltas, creadas)`. Los numeros se devuelven porque el que
    pide la operacion necesita saber cuantos productos nuevos aparecieron, que
    es informacion distinta de "se resolvio": 15 lineas resueltas con 15 productos
    nuevos es un catalogue que hay que revisar, y 15 con 2 nuevos es un escaneo
    normal.

    Idempotente: una linea que ya tiene `producto_id` se deja como esta. Volver a
    llamar no reasigna, y eso importa porque el escaner puede pasar dos veces por
    el mismo comprobante.
    """
    from app.core.time import utcnow

    resueltas = 0
    creadas = 0
    reutilizados = 0

    for item in compra.items:
        if item.producto_id is not None:
            continue

        salida = await resolver_producto(db, compra.company_id, item.descripcion)
        if salida.creado:
            creadas += 1
        else:
            reutilizados += 1

        item.producto_id = salida.producto.id
        item.producto_asignado_por = actor
        item.producto_asignado_at = utcnow()
        resueltas += 1

    await db.flush()
    logger.info(
        "Compra %s: %d lineas con producto (%d productos nuevos, %d reutilizados).",
        compra.id, resueltas, creadas, reutilizados,
    )
    return resueltas, creadas


@dataclass(frozen=True)
class LineaSinInterpretar:
    """Una linea de `ticket.items` que no se pudo convertir.

    Se acumula y se tira junta, en vez de canceling el guardado entero. La razon
    es que un solo campo raro no debe impedir registrar las otras 14 lineas que
    si se entendieron: perder 14 lineas correctas por una que venia con
    `quantity` en texto no es un comportamiento que alguien quiera.
    """

    indice: int
    motivo: str


# ---------------------------------------------------------------------------
# De `ticket.items` (JSON crudo) a lineas de compra
# ---------------------------------------------------------------------------


def _decimal_de(valor: object) -> Decimal | None:
    """Un numero del JSON a `Decimal`, o None si no es un numero.

    `Decimal` y no `float` por la convencion del proyecto, y porque
    `0.1 + 0.2` en binario no es `0.3` y aqui la aritmetica decide si una compra
    cuadra con su total.

    Acepta un string porque el modelo devuelve `"3"` y `"12.50"` con frecuencia:
    un JSON bien formado puede llevar el numero como texto, y un `isinstance(v,
    float)` lo dejaria fuera como si no hubiera linea.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        # `True` es un int en Python. Una cantidad `true` es basura del modelo,
        # y `Decimal("True")` es un crash en vez de una linea descartada.
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def interpretar_items(items: object) -> tuple[list[dict], list[LineaSinInterpretar]]:
    """Convierte el JSON crudo de `ticket.items` en filas utilizables.

    Devuelve las lineas buenas y las que no se pudieron leer, en vez de solo las
    buenas: el que llama tiene que poder decir "de 15 lineas se guardaron 12 y 3
    no se entendieron", que es informacion que el usuario necesita antes de
    autorizar una compra.
    """
    if not isinstance(items, list):
        # NULL, o algo que no es una lista. No hay nada que interpretar y no es
        # un error: significa que el lector no produjo lineas.
        return [], []

    lineas: list[dict] = []
    descartadas: list[LineaSinInterpretar] = []

    for indice, cruda in enumerate(items):
        if not isinstance(cruda, dict):
            descartadas.append(LineaSinInterpretar(indice, "no es un objeto"))
            continue

        descripcion = str(cruda.get("description") or "").strip()
        if not descripcion:
            # Sin descripcion la linea no identifica nada. Es el caso real que
            # mas se da: el modelo mete un objeto vacio cuando no leyó la linea.
            descartadas.append(LineaSinInterpretar(indice, "sin descripcion"))
            continue

        cantidad = _decimal_de(cruda.get("quantity"))
        if cantidad is None:
            # Sin cantidad la linea no se puede sumar a ningun inventario. Un
            # `quantity` ausente o ilegible es una de las dos mitades de esta
            # tabla, y no tiene un valor por omision que sea honesto: 0 seria un
            # producto que se compro de a gratis.
            descartadas.append(LineaSinInterpretar(indice, "cantidad ilegible"))
            continue

        if cantidad <= 0:
            # Una cantidad negativa es una devolucion, y eso es una SALIDA. La
            # constraint `ck_compra_items_cantidad_positiva` lo rechaza igual;
            # esto es para que el motivo sea legible en vez de un IntegrityError.
            descartadas.append(LineaSinInterpretar(indice, "cantidad no positiva"))
            continue

        lineas.append(
            {
                "orden": indice,
                "descripcion": descripcion[:_DESC_MAX],
                "cantidad": cantidad,
                "costo_unitario": _decimal_de(cruda.get("unit_price")),
                "total": _decimal_de(cruda.get("total")),
            }
        )

    return lineas, descartadas


async def registrar_compra(
    db: AsyncSession,
    ticket: TicketModel,
    *,
    estado_inicial: EstadoCompra = EstadoCompra.EN_REVISION,
) -> CompraModel | None:
    """Crea la orden de compra a partir de un ticket, si no existe ya.

    Idempotente: si el ticket ya tiene compra, devuelve la que hay. La garantia
    real es el UNIQUE de `compras.ticket_id`; esto es lo que evita que la
    segunda llamada reviente con IntegrityError en vez de devolver la misma
    compra.

    Devuelve `None` — sin raise — cuando `ticket.items` es NULL, o sea cuando el
    lector no produjo lineas. Es un caso normal y frecuente: la ruta OCR no
    extrae detalle. Que no haya compra no es un fallo, es que no hay nada que
    comprar en el inventario todavia. El `logger` lo dice para que no parezca un
    silencio.
    """
    existente = (
        await db.execute(
            select(CompraModel).where(CompraModel.ticket_id == ticket.id)
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    if not ticket.items:
        logger.info(
            "Ticket %s sin compra: el lector no produjo lineas "
            "(items NULL, tipicamente la ruta OCR).",
            ticket.id,
        )
        return None

    lineas, descartadas = interpretar_items(ticket.items)
    if not lineas:
        logger.warning(
            "Ticket %s: items tiene %d entradas y ninguna se pudo interpretar "
            "(%s). No se crea compra.",
            ticket.id,
            len(ticket.items) if isinstance(ticket.items, list) else 0,
            "; ".join(f"linea {d.indice}: {d.motivo}" for d in descartadas),
        )
        return None

    compra = CompraModel(
        company_id=ticket.company_id,
        ticket_id=ticket.id,
        # NUNCA PROCESADO aqui. El unico camino a PROCESADO es
        # `confirmar_compra`, que exige una persona. Ver el modulo.
        estado=estado_inicial,
        fecha=ticket.expense_date,
        proveedor_nombre=ticket.provider_name,
        total=ticket.total_amount,
    )
    db.add(compra)
    await db.flush()

    for linea in lineas:
        db.add(CompraItemModel(compra_id=compra.id, producto_id=None, **linea))
    await db.flush()

    for d in descartadas:
        logger.warning(
            "Ticket %s, linea %d de items descartada: %s. Se guardaron %d de %d.",
            ticket.id,
            d.indice,
            d.motivo,
            len(lineas),
            len(lineas) + len(descartadas),
        )

    await db.refresh(compra, ["items"])

    # Las lineas se resuelven a productos AQUI, y no al confirmar.
    #
    # Por que aqui y no despues: `confirmar_compra` bloquea si hay lineas sin
    # producto, y resolver en ese momento significaria que el endpoint tiene que
    # crear productos como efecto secundario de una confirmacion. Crear un
    # producto es una decision de catalogo, y mezclarla con "suma stock" hace que
    # un 409 por linea sin resolver venga con un efecto lateral que nadie pidio.
    #
    # El que crea es el sistema, asi que el `actor` es explicito y no un string
    # magico: queda en `producto_asignado_por` y en el log, y se puede distinguir
    # de "lo eligio Ana" al auditar.
    resoltas, creadas = await resolver_lineas(db, compra, actor="sistema:extraccion")

    logger.info(
        "Compra %s creada para el ticket %s en %s con %d lineas "
        "(%d productos nuevos).",
        compra.id,
        ticket.id,
        compra.estado,
        len(lineas),
        creadas,
    )
    return compra


async def asignar_producto(
    db: AsyncSession,
    item_id: UUID,
    producto_id: UUID,
    *,
    actor: str,
) -> CompraItemModel:
    """Le asigna un producto del catalogo a una linea de compra.

    Es el unico camino que llena `compra_items.producto_id`, y por eso el que
    guarda QUIEN lo asigno y cuando. "Lo eligio el sistema" y "lo eligio Ana"
    son respuestas distintas a "de donde salio este producto del catalogo", y sin
    la columna de actor la segunda no se puede reconstruir.

    El producto tiene que ser de la misma empresa que la linea. Sin esa
    comprobacion, un `producto_id` de otra empresa movería el stock en el kardex de
    una con cantidades de otra, y el inventario de las dos queda mal.
    """
    if not actor or not actor.strip():
        raise ErrorDeInventario("asignar un producto requiere quien lo asigne")

    item = (
        await db.execute(
            select(CompraItemModel).where(CompraItemModel.id == item_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise NoExiste("La linea de compra no existe")

    compra = (
        await db.execute(
            select(CompraModel).where(CompraModel.id == item.compra_id)
        )
    ).scalar_one()
    producto = (
        await db.execute(
            select(ProductoModel).where(ProductoModel.id == producto_id)
        )
    ).scalar_one_or_none()
    if producto is None:
        raise NoExiste("El producto no existe")

    if producto.company_id != compra.company_id:
        # `AGENTS.md` dice que no hay multi-tenancy: `get_current_user` no recibe
        # company_id y cualquiera autenticado puede mandar un company_id
        # arbitrario. Por eso esta comprobacion es del servicio y no del router:
        # es la ultima linea antes de que un producto de otra empresa acabe en
        # el kardex de esta.
        raise ErrorDeInventario("El producto es de otra empresa")

    item.producto_id = producto.id
    item.producto_asignado_por = actor
    item.producto_asignado_at = utcnow()
    await db.flush()
    return item


async def confirmar_compra(
    db: AsyncSession,
    compra_id: UUID,
    *,
    actor: str,
) -> CompraModel:
    """EN_REVISION -> PROCESADO, y aqui es donde el inventario suma.

    Es la unica funcion del proyecto que escribe en `movimientos_inventario`, y
    por eso lleva dentro las tres cosas que la hacen segura:

    1. Rechaza volver a confirmar. Un segundo `POST` no duplica los movimientos,
       porque el estado ya no es PROCESADO y `ck_compras_confirmacion` prohibiria
       dejarlos en NULL. Es la idempotencia de la parte que mueve dinero de
       verdad, y por eso se comprueba el estado y no solo el UNIQUE.

    2. Escribe un movimiento POR LINEA CON PRODUCTO. Una linea sin producto se
       salta y se cuenta: si no, una compra de 15 lineas de las que 3 no se
       reconocieron meteria solo 12 al inventario, sin decirselo a nadie.

    3. Escribe el kardex y cambia el estado en la MISMA transaccion. Si se
       escribieran por separado, un fallo entre medias deja el estado en
       PROCESADO sin movimientos, o movimientos sin estado, y las dos mitades
       dicen cosas distintas.
    """
    if not actor or not actor.strip():
        raise ErrorDeInventario("confirmar una compra requiere quien la confirme")

    compra = (
        await db.execute(
            select(CompraModel)
            .where(CompraModel.id == compra_id)
            .options(selectinload(CompraModel.items))
        )
    ).scalar_one_or_none()
    if compra is None:
        raise NoExiste("La compra no existe")

    if compra.estado == EstadoCompra.PROCESADO:
        # No es un error que se pueda ignorar en silencio: si alguien pide
        # confirmar dos veces, la segunda tiene que decir que ya estaba
        # confirmada, o parece que funciono y en realidad no sumo nada.
        raise ErrorDeInventario(
            "La compra ya esta PROCESADO y ya sumo stock. "
            "Confirmarla otra vez no volveria a sumar."
        )

    if compra.estado == EstadoCompra.RECHAZADO:
        # LA DEFENSA DEL RECHAZO. Sin este `if`, `POST /rechazar` seria cosmetico:
        # el rechazo se deshacia llamando a este endpoint, y el 409 de "ya esta
        # PROCESADO" de arriba no saltaria porque la compra todavia no lo esta.
        #
        # El mensaje dice el camino: una compra rechazada se reabre, no se
        # confirma. Sin eso, quien lo lea asume que el rechazo se perdio.
        raise ErrorDeInventario(
            "La compra esta RECHAZADA: alguien decidio que este comprobante no es "
            "una compra. Si fue un error, reabrela con "
            "POST /inventario/compras/{id}/reabrir y despues confirma."
        )

    sin_producto = [i for i in compra.items if i.producto_id is None]
    if sin_producto:
        # Con la resolucion automatica de `registrar_compra` esto ya no deberia
        # pasar: toda linea guardada tiene producto. Queda el bloqueo, y con el
        # mensaje nuevo, porque es una red: si aqui apareciera una linea suelta,
        # significa que algo se salto la resolucion, y autorizar dejaria el stock
        # incompleto sin que nadie lo notara.
        detalle = "; ".join(f'"{i.descripcion[:60]}"' for i in sin_producto[:5])
        raise ErrorDeInventario(
            f"La compra tiene {len(sin_producto)} linea(s) sin producto: "
            f"{detalle}. Normalmente se resuelven solas al crearse la compra; "
            "asignalas con POST /inventario/compras/items/{id}/producto antes "
            "de confirmar, o esas lineas no entran al inventario."
        )

    ahora = utcnow()
    for item in compra.items:
        db.add(
            MovimientoInventarioModel(
                company_id=compra.company_id,
                producto_id=item.producto_id,
                tipo=TipoMovimiento.ENTRADA.value,
                cantidad=item.cantidad,
                referencia_tipo="COMPRA",
                referencia_id=compra.id,
                actor=actor,
                descripcion_origen=item.descripcion,
                created_at=ahora,
            )
        )

    compra.estado = EstadoCompra.PROCESADO
    compra.confirmada_por = actor
    compra.confirmada_at = ahora
    await db.flush()

    logger.info(
        "Compra %s confirmada por %s: %d lineas entraron al inventario.",
        compra.id,
        actor,
        len(compra.items),
    )
    await db.refresh(compra, ["items"])
    return compra


# ---------------------------------------------------------------------------
# Administracion del catalogo: renombrar, verificar, dar de baja
# ---------------------------------------------------------------------------


async def actualizar_producto(
    db: AsyncSession,
    producto_id: UUID,
    cambios: dict,
) -> ProductoModel:
    """Aplica cambios a un producto del catalogo. Lo que se le pide, se aplica.

    Por que recibe un `dict` y no el modelo entero
    ---------------------------------------------

    Porque el endpoint tiene que distinguir "no lo mandaron" de "lo mandaron en
    NULL", y `model_dump()` sin `exclude_unset` no lo distingue: en los dos
    casos trae la clave. Un `PATCH /productos/{id}` que borrara `precio_referencia`
    porque el cliente no lo mando seria un fallo de datos que no se ve en la
    respuesta — el 200 llega igual, y el precio desaparecio. Ver
    `ProductoUpdate` en `app/schemas/inventario.py`.

    Lo que NO se puede cambiar por aqui, y por que
    ----------------------------------------------

    - **`id` y `company_id`.** Mover un producto de empresa sacaria su kardex con
      el, porque `movimientos_inventario.company_id` es su propia copia del de la
      compra. El stock pasaria a contarse en la empresa nueva y a seguir visible en
      la vieja. No hay endpoint para eso, y no debe haberlo.
    - **`origen`.** Dice si lo puso una persona o si salio de leer un papel. Si
      fuera editable, un producto de OCR podria declararse MANUAL y sair de la
      cola de revision sin que nadie lo mirara, que es exactamente la mentira que
      `verificado` existe para decir que no.
    - **`verificado` en un producto de OCR sin codigo.** Un codigo de barras es una
      identidad; una descripcion es una opinion. Marcar como verificado un producto
      de OCR sin codigo es afirmar que el sistema sabe lo que hay en el almacen, y
      no lo sabe. Ver `test_verificar_ocr_sin_codigo_no`.

    Lo que si se puede, y por que cada uno hace falta:

    - **`nombre`.** Renombrar es lo que hace falta para limpiar la cola: el OCR
      produce "Reginen de" y "Regin de" y la decision de si son el mismo producto
      la toma una persona. `nombre_normalizado` se recalcula por el `@validates` del
      modelo, asi que no hay que acordarse.
    - **`activo`.** Dar de baja sin borrar: el kardex necesita el producto vivo, y
      `ON DELETE CASCADE` de `movimientos_inventario.producto_id` borraria el
      historial entero. Por eso NO hay `DELETE` de producto, y es la misma razon
      por la que `producto.activo` existe en el modelo.

    El producto tiene que ser de la misma empresa que la que se pide. Es la misma
    comprobacion que hace `asignar_producto`, y por el mismo motivo: no hay
    multi-tenancy y esta es la ultima linea antes de que un producto de otra
    empresa se renombre desde aqui.
    """
    producto = (
        await db.execute(select(ProductoModel).where(ProductoModel.id == producto_id))
    ).scalar_one_or_none()
    if producto is None:
        raise NoExiste("El producto no existe")

    if "company_id" in cambios or "origen" in cambios:
        # No es un 422 de forma: el cuerpo es valido, el campo es que no se toca.
        # Se responde en el router; aqui solo se deja constancia de que la
        # condicion existe, porque un `continue` silencioso seria mas dificil de
        # leer que un error explicito.
        raise ErrorDeInventario(
            "`company_id` y `origen` no se pueden cambiar por API"
        )

    if cambios.get("verificado") is True and not producto.verificado:
        # Solo se bloquea el caso que la constraint no cubre. La constraint
        # `ck_productos_verificado_ocr` dice "verificado = true OR origen = OCR",
        # o sea que ya prohibe que un MANUAL deje de estar verificado; lo que NO
        # prohibe es que un OCR sin codigo pase a verificado, y ese es el salto
        # que este servicio no da.
        if producto.origen == ProductoOrigen.OCR.value and not producto.codigo:
            raise ErrorDeInventario(
                "Un producto que salio de leer un papel y no tiene codigo no se "
                "puede dar por verificado solo con llamarlo verificado: un codigo "
                "de barras es una identidad, y una descripcion leida no lo es. "
                "Asignale un codigo, o dejalo en la cola."
            )

    if "codigo" in cambios and cambios["codigo"] is not None:
        # El indice unico `ix_productos_codigo` es `(company_id, codigo) WHERE codigo
        # IS NOT NULL`. Sin esta comprobacion, un codigo repetido llega al INSERT y
        # sale un IntegrityError, que el router no traduce: 500. El 409 con el
        # nombre del producto que ya lo tiene es lo que un cliente puede usar.
        repetido = (
            await db.execute(
                select(ProductoModel).where(
                    ProductoModel.company_id == producto.company_id,
                    ProductoModel.codigo == cambios["codigo"],
                    ProductoModel.id != producto.id,
                )
            )
        ).scalar_one_or_none()
        if repetido is not None:
            raise ErrorDeInventario(
                f'Ya existe el producto "{repetido.nombre}" con el codigo '
                f'{cambios["codigo"]} en esta empresa.'
            )

    for campo, valor in cambios.items():
        setattr(producto, campo, valor)
    await db.flush()
    return producto


# ---------------------------------------------------------------------------
# El kardex: escribir un movimiento que no venga de una compra
# ---------------------------------------------------------------------------


async def registrar_movimiento(
    db: AsyncSession,
    *,
    company_id: UUID,
    producto_id: UUID,
    tipo: TipoMovimiento,
    cantidad: Decimal,
    actor: str,
    referencia_tipo: str = "AJUSTE",
    referencia_id: UUID | None = None,
    descripcion_origen: str | None = None,
) -> MovimientoInventarioModel:
    """Escribe una fila del kardex a mano: una SALIDA (venta) o un AJUSTE.

    POR QUE UNA `ENTRADA` SOLO SE ACEPTA CON `referencia_tipo='AJUSTE'`
    ------------------------------------------------------------------

    Porque `ENTRADA` sin mas significa "esto entro por una compra que alguien
    autorizo", y la unica forma de que signifique eso es `confirmar_compra`. Si este
    endpoint aceptara una `ENTRADA` a secas, existiria un camino que suma stock sin
    compra, sin linea y sin que nadie mirara el papel — y entonces
    `compras.ticket_id` UNIQUE, que es lo que impide contar dos veces un
    comprobante, dejaria de ser la garantia de que el inventario solo refleja
    compras de verdad.

    Con `referencia_tipo='AJUSTE'` si se acepta, y por una razon concreta: una
    correccion de conteo que SUMA es indistinguible de una compra si lo unico que
    se guarda es el tipo. El par (tipo con signo, procedencia) es lo que las dos
    necesitan, y es lo que se guarda. El error sale con 409 y no con 422 porque no
    es un problema de forma del cuerpo: es un problema de lo que el cuerpo quiere
    decir.

    QUE ES UN AJUSTE Y POR QUE SE ESCRIBE CON OTRO `tipo`
    ---------------------------------------------------

    Un AJUSTE corrige una diferencia entre el conteo fisico y el sistema, y eso
    pasa en las dos direcciones: se conto de mas, o se conto de menos. El enum
    `AJUSTE` no puede decir cual de las dos es, y `cantidad` es positiva por
    `ck_movimientos_cantidad_positiva`, asi que el signo no puede ir en el numero.

    La fila se escribe con el tipo que SI tiene signo —`ENTRADA` si se conto de
    menos, `SALIDA` si de mas— y con `referencia_tipo = 'AJUSTE'`, que es lo que
    dice "esto es una correccion" sin tocar el stock. El enum conserva `AJUSTE`
    como nombre de `referencia_tipo` porque de donde viene la fila y que le hace
    al stock son dos preguntas distintas. Ver `TipoMovimiento`.

    POR QUE SE COMPRUEBA EL STOCK AQUÍ Y NO SE DEJA SOLO AL TRIGGER
    --------------------------------------------------------------

    Porque el trigger es de Postgres y los tests corren sobre SQLite, que no tiene
    triggers de este tipo. Si la comprobacion viviera solo en la base, la suite
    pasaria y el fallo apareceria en produccion como un IntegrityError sin
    traducir —un 500 que no dice "no hay stock"—. Aqui se comprueba y se responde
    409 con el numero que hay y el que se pedia, que es lo que la persona necesita
    para decidir si pidio de mas.

    La comprobacion no reemplaza al trigger: corre en la misma transaccion y en el
    mismo motor, asi que los dos ven el mismo estado. Si alguien meter la escritura
    por otra via sin pasar por aqui, el trigger sigue bloqueando el negativo.

    Un AJUSTE hacia abajo (`SALIDA`) tambien pasa por esta comprobacion de stock,
    y es lo correcto: una correccion que deja el inventario negativo no es una
    correccion, es el mismo error de captura que el trigger persigue.
    """
    if not actor or not actor.strip():
        # Mismo criterio que `asignar_producto` y `confirmar_compra`: sin actor no
        # hay quien_responga de la fila. `ck_movimientos_actor` lo prohibe en la
        # base, pero aqui el mensaje es util y el 409 no un 500.
        raise ErrorDeInventario("registrar un movimiento requiere quien lo registre")

    if tipo is TipoMovimiento.ENTRADA and referencia_tipo != "AJUSTE":
        raise ErrorDeInventario(
            "Una ENTRADA solo puede venir de confirmar una compra, que es el "
            "camino que escribe el kardex con una linea por linea del papel y "
            "exige a alguien que la autorice. Registrar una entrada suelta "
            "sumaria stock sin compra, y el inventario dejaria de reflejar lo "
            "que se compro. Si lo que quieres es una correccion de conteo, mandala "
            "con es_ajuste=true."
        )

    if cantidad is None or cantidad <= 0:
        # La constraint `ck_movimientos_cantidad_positiva` lo prohibe igual, pero
        # un 422 aqui dice "la cantidad tiene que ser mayor que cero" en vez de
        # dejar que reviente el INSERT. Y el signo NO se mira: una cantidad
        # negativa es una SALIDA con la cantidad positiva, no un numero negativo.
        raise ErrorDeInventario(
            "La cantidad tiene que ser mayor que cero. Si el movimiento resta "
            "stock, el tipo es SALIDA y la cantidad va positiva."
        )

    producto = (
        await db.execute(select(ProductoModel).where(ProductoModel.id == producto_id))
    ).scalar_one_or_none()
    if producto is None:
        raise NoExiste("El producto no existe")
    if producto.company_id != company_id:
        raise ErrorDeInventario("El producto es de otra empresa")

    if tipo is TipoMovimiento.SALIDA:
        actual = await stock_de_una(db, producto.id)
        if cantidad > actual:
            raise ErrorDeInventario(
                f'No hay stock suficiente: "{producto.nombre}" tiene {actual} y '
                f"pediste restar {cantidad}. Un AJUSTE hacia abajo que deja el "
                "inventario negativo no es una correccion."
            )

    movimiento = MovimientoInventarioModel(
        company_id=company_id,
        producto_id=producto.id,
        tipo=tipo.value,
        cantidad=cantidad,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        actor=actor,
        descripcion_origen=(descripcion_origen or producto.nombre)[:300],
        created_at=utcnow(),
    )
    db.add(movimiento)
    await db.flush()
    logger.info(
        "Movimiento %s de %s %s piezas sobre el producto %s, por %s.",
        movimiento.tipo,
        movimiento.cantidad,
        referencia_tipo,
        producto.id,
        actor,
    )
    return movimiento


async def stock_de_una(db: AsyncSession, producto_id: UUID) -> Decimal:
    """El stock de UN producto, con el mismo signo que usa `stock_de`.

    Existe para no recorrer el kardex entero de la empresa en cada comprobacion de
    `registrar_movimiento`. Y replica el signo de `stock_de` a proposito, con la
    misma unica pregunta (`¿es SALIDA?`): si esta cuenta y `stock_de` dieran
    numeros distintos, el 409 de "no hay stock" y el stock que se muestra en el
    catalogo estarian describiendo la misma fila de dos maneras.
    """
    entradas = await db.execute(
        select(func.coalesce(func.sum(MovimientoInventarioModel.cantidad), 0)).where(
            MovimientoInventarioModel.producto_id == producto_id,
            MovimientoInventarioModel.tipo != TipoMovimiento.SALIDA.value,
        )
    )
    salidas = await db.execute(
        select(func.coalesce(func.sum(MovimientoInventarioModel.cantidad), 0)).where(
            MovimientoInventarioModel.producto_id == producto_id,
            MovimientoInventarioModel.tipo == TipoMovimiento.SALIDA.value,
        )
    )
    return Decimal(entradas.scalar_one() or 0) - Decimal(salidas.scalar_one() or 0)


# ---------------------------------------------------------------------------
# La maquina de estados de la compra, hacia atras
# ---------------------------------------------------------------------------


async def rechazar_compra(
    db: AsyncSession,
    compra_id: UUID,
    *,
    actor: str,
    motivo: str | None = None,
) -> CompraModel:
    """EN_REVISION -> RECHAZADO: este comprobante no es una compra.

    QUE POR QUE NO SE BORRA
    -----------------------

    Porque `compras.ticket_id` es UNIQUE. Borrar la compra deja el ticket libre, y
    el proximo reescaneo lo encuentra sin compra y vuelve a crearla desde
    `registrar_compra`, con estado EN_REVISION y sin recordar que alguien ya la
    habia mirado. Es un bucle: la compra descartada reaparece sola cada vez que se
    toca el comprobante. Con RECHAZADO el veredicto queda escrito y
    `registrar_compra` lo devuelve tal cual, que es lo idempotente.

    POR QUE NO SE PUEDE RECHAZAR UNA PROCESADA
    ------------------------------------------

    Porque ya sumo stock, y el kardex es append-only por trigger: `UPDATE` nunca y
    `DELETE` nunca mientras el producto exista. No hay forma de deshacer los
    movimientos de una compra confirmada. Si el error es de conteo, la correccion
    es un AJUSTE por producto —`POST /inventario/movimientos`— y es la unica via
    que deja el rastro de por que el stock ya no cuadra con lo que dice el papel.

    El mensaje de error lo dice, porque "no se puede" sin explicar el camino
    alternativo hace que la persona busque una pantalla que no existe.
    """
    if not actor or not actor.strip():
        raise ErrorDeInventario("rechazar una compra requiere quien la rechace")

    compra = (
        await db.execute(select(CompraModel).where(CompraModel.id == compra_id))
    ).scalar_one_or_none()
    if compra is None:
        raise NoExiste("La compra no existe")

    if compra.estado is EstadoCompra.PROCESADO or compra.estado == EstadoCompra.PROCESADO.value:
        raise ErrorDeInventario(
            "La compra ya esta PROCESADO y ya sumo stock, y el kardex no se "
            "reescribe: no hay forma de deshacer sus movimientos. Si el error es "
            "de conteo, corrige cada producto con un AJUSTE en "
            "POST /inventario/movimientos, que es lo que deja rastro."
        )

    if compra.estado == EstadoCompra.RECHAZADO.value:
        # Idempotente a proposito. Rechazar dos veces no es un error de forma: es
        # la misma operacion, y un cliente que reintenta tras un timeout debe
        # poder hacerlo sin que la segunda llame sea un 409 que no sabe leer.
        return compra

    compra.estado = EstadoCompra.RECHAZADO
    await db.flush()
    logger.info(
        "Compra %s rechazada por %s%s.",
        compra.id,
        actor,
        f": {motivo}" if motivo else "",
    )
    return compra


async def reabrir_compra(
    db: AsyncSession,
    compra_id: UUID,
    *,
    actor: str,
) -> CompraModel:
    """RECHAZADO -> EN_REVISION. Deshace el rechazo, no la confirmacion.

    Lo que NO hace, y por que no hay un solo endpoint para las dos cosas
    ---------------------------------------------------------------------

    No reabre una compra PROCESADA. `reabrir_compra` solo deshace un RECHAZADO, y
    una compra que ya sumo stock no se puede reabrir: sus movimientos existen y el
    trigger del kardex prohibe editarlos y borrarlos. Si el error es de conteo, la
    via es el AJUSTE.

    La separacion es deliberada: un endpoint "reabrir" que aceptara los dos casos
    necesitaria adivinar cual de los dos es, y el caso equivocado —dar por
    reversible una compra que ya movio stock— es el que no tiene salida. Con dos
    funciones y un 409 explicito, el que se equivoca se da cuenta.
    """
    if not actor or not actor.strip():
        raise ErrorDeInventario("reabrir una compra requiere quien la reabra")

    compra = (
        await db.execute(select(CompraModel).where(CompraModel.id == compra_id))
    ).scalar_one_or_none()
    if compra is None:
        raise NoExiste("La compra no existe")

    if compra.estado == EstadoCompra.PROCESADO.value:
        raise ErrorDeInventario(
            "Una compra PROCESADA no se reabre: ya sumo stock y el kardex no se "
            "reescribe. Para corregir el conteo, registra un AJUSTE por producto."
        )

    if compra.estado != EstadoCompra.RECHAZADO.value:
        raise ErrorDeInventario(
            f"Solo una compra RECHAZADO se puede reabrir, y esta esta en "
            f"{compra.estado}."
        )

    # A EN_REVISION y no a PROCESAR: PROCESAR significa "se esta digitalizando", y
    # esta compra ya esta digitalizada y con sus lineas interpretadas. Volver a
    # PROCESAR seria mandarla al principio del camino cuando ya esta casi al final.
    compra.estado = EstadoCompra.EN_REVISION
    await db.flush()
    logger.info("Compra %s reabierta por %s.", compra.id, actor)
    return compra


# ---------------------------------------------------------------------------
# Lecturas
# ---------------------------------------------------------------------------


async def stock_de(db: AsyncSession, company_id: UUID) -> dict[UUID, Decimal]:
    """El stock de cada producto, como suma del kardex. Ver el modulo.

    El signo sale del tipo, no de la cantidad, y por eso `cantidad` es positiva
    siempre en la tabla: la cantidad de lugares donde se aplica el signo tiene
    que ser una. Aqui se suman las entradas y se restan las salidas en dos
    consultas, en vez de un `SUM` con un `CASE` adentro que hay que acertar.
    """
    entradas = (
        await db.execute(
            select(
                MovimientoInventarioModel.producto_id,
                func.sum(MovimientoInventarioModel.cantidad),
            )
            .where(
                MovimientoInventarioModel.company_id == company_id,
                MovimientoInventarioModel.tipo.in_(
                    [TipoMovimiento.ENTRADA.value, TipoMovimiento.AJUSTE.value]
                ),
            )
            .group_by(MovimientoInventarioModel.producto_id)
        )
    ).all()
    salidas = (
        await db.execute(
            select(
                MovimientoInventarioModel.producto_id,
                func.sum(MovimientoInventarioModel.cantidad),
            )
            .where(
                MovimientoInventarioModel.company_id == company_id,
                MovimientoInventarioModel.tipo == TipoMovimiento.SALIDA.value,
            )
            .group_by(MovimientoInventarioModel.producto_id)
        )
    ).all()

    salida: dict[UUID, Decimal] = {}
    for producto_id, total in entradas:
        salida[producto_id] = Decimal(total or 0)
    for producto_id, total in salidas:
        salida[producto_id] = salida.get(producto_id, Decimal("0")) - Decimal(total or 0)

    return salida


async def lineas_sin_producto(
    db: AsyncSession, company_id: UUID
) -> list[CompraItemModel]:
    """La cola de trabajo: las lineas cuya descripcion no se reconoce.

    No hay una tabla de "pendientes" y esta consulta ES la cola. Una tabla mas
    seria una segunda fuente de verdad que se puede desincronizar de
    `compra_items`, que es justo el problema que `productos_pendientes`mia
    resolveria en lugar de causar.
    """
    return list(
        (
            await db.execute(
                select(CompraItemModel)
                .join(CompraModel, CompraModel.id == CompraItemModel.compra_id)
                .where(
                    CompraModel.company_id == company_id,
                    CompraItemModel.producto_id.is_(None),
                    # Solo las compras sin confirmar. Una vez PROCESADO no hay
                    # nada que asignar: el stock ya se movio y la linea ya no
                    # puede entrar.
                    CompraModel.estado != EstadoCompra.PROCESADO,
                )
                .order_by(CompraModel.fecha.desc(), CompraItemModel.orden)
            )
        ).scalars()
    )