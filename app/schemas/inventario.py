"""Lo que entra y sale de la API de inventario.

Por que los schemas no exponen `TicketExtractionResult.items`
------------------------------------------------------------

Porque esa forma es la del LLM, y la API habla con personas. `items` es un
`list[dict]` libre que el modelo produce: las claves son `description`,
`quantity`, `unit_price`, `total`, y ninguna esta garantizada. Un schema que lo
reproduce tal cual declara un contrato que el modelo puede romper en cualquier
momento sin que nadie se entere.

Lo que se expone son las LINEAS YA INTERPRETADAS, con sus campos obligatorios y
su `producto_id` nullable. La conversion ocurre en
`inventario_service.interpretar_items`, que es el unico lugar que sabe que hacer
con una cantidad en texto, ausente o negativa, y las lineas que no pudo
interpretar quedan contadas y reportadas en la respuesta.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import EstadoCompra, ProductoOrigen, TipoMovimiento


class ProductoCreate(BaseModel):
    """Alta de producto a mano.

    El alta automatica desde una linea de OCR la hace
    `inventario_service.resolver_producto`, no este endpoint: nace con
    `origen=OCR` y `verificado=false`, y por eso no se pide aqui. Lo que se pide
    es un producto de verdad, y nace de verdad: `MANUAL` y verificado.
    """

    nombre: str = Field(..., min_length=1, max_length=200)
    codigo: str | None = Field(default=None, max_length=64)
    unidad_medida: str = Field(default="PZA", max_length=20)
    precio_referencia: Decimal | None = Field(default=None, ge=0)


class ProductoResponse(ProductoCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    activo: bool
    # Quien lo puso y si alguien lo confirmo. Ver `ProductoOrigen`.
    #
    # Van en la respuesta y no solo en la base a proposito: quien mira el
    # catalogo tiene que poder distinguir de un vistazo un producto que dio de
    # alta de un texto que salio de leer un papel.
    origen: ProductoOrigen
    verificado: bool
    created_at: datetime


# El stock de un producto que no tiene movimientos.
#
# Existe como constante y no solo dentro de la clase porque hay DOS lugares que
# necesitan el valor: la anotacion del campo y el `.get(..., <defecto>)` de
# `app/api/inventario.py`, que lista los productos y rellena el stock que no
# esta en el kardex. Con el default escrito dos veces, cambiar uno y no el otro
# deja el listado mocking un valor que el schema no declara — y el type checker
# lo detecta, porque `model_fields["stock"].default` esta tipado como el centinela
# `PydanticUndefined | Any` y por eso `dict.get` no resuelve su overload.
#
# Es `Decimal` y no `int` por la misma razon que el campo: las cantidades son
# fraccionables.
STOCK_SIN_MOVIMIENTOS = Decimal("0")


class ProductoConStock(ProductoResponse):
    """Un producto y cuanto hay de el.

    `stock` viene del kardex y por eso es `Decimal`, no `int`: las cantidades son
    fraccionables (se compra 1.5 kg, no 2), y un `int` seria un `round()` callado
    que no sabe si sube o baja.
    """

    stock: Decimal = STOCK_SIN_MOVIMIENTOS


class ProductoUpdate(BaseModel):
    """Lo que se puede corregir de un producto. Todo opcional.

    POR QUE `exclude_unset` Y NO `exclude_none`
    ------------------------------------------

    Por la diferencia entre "no lo mandaron" y "lo mandaron en null". Un
    `model_dump()` sin `exclude_unset` trae las dos igual, asi que un
    `PATCH {"precio_referencia": null}` y un `PATCH {}` se verian iguales en el
    servicio — y el primero borraria un precio que el segundo no toca. Por eso el
    router pasa `model_dump(exclude_unset=True)`: solo llegan las claves que
   ombo en la peticion.

    `verificado` a `false` esta permitido solo para productos de OCR, y la
    constraint `ck_productos_verificado_ocr` lo prohibe para los demas. Aqui se
    documenta en vez de silenciarse: el 422/409 lo dice el servicio, con el motivo.

    `extra="forbid"` y no `ignore`: un cliente que manda `stock` —porque lo leyó
    en la respuesta del listado— recibe un 422 con el nombre del campo en vez de
    un 200 que finge haberlo guardado. `stock` es la suma del kardex y no se
    escribe; aceptarlo en silencio seria prometer una escritura que no ocurre.

    NO aparecen `company_id` ni `origen`, y no por descuido:

    - `company_id` moveria el producto con su kardex y el stock pasaria a contarse
      en la empresa nueva. No hay forma de hacerlo bien sin reescribir el
      historial, y el kardex es append-only.
    - `origen` declara si lo puso una persona o si salio de leer un papel. Si
      fuera editable, un producto de OCR podria declararse MANUAL y salir de la
    cola de revision sin que nadie lo mirara.

    Que no haya `id` tampoco es casualidad: la PK la pone la base, no el cliente.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    codigo: str | None = Field(default=None, max_length=64)
    unidad_medida: str | None = Field(default=None, max_length=20)
    precio_referencia: Decimal | None = Field(default=None, ge=0)
    verificado: bool | None = None
    # Dar de baja sin borrar: el kardex necesita el producto vivo. No hay DELETE.
    activo: bool | None = None


class MovimientoCreate(BaseModel):
    """Una fila del kardex escrita a mano: una venta, o una correccion de conteo.

    LOS TRES CAMPOS QUE DICEN QUE PASA, Y POR QUE NO SE ADIVINAN
    ------------------------------------------------------------

    `tipo` + `es_ajuste` + `cantidad` positiva. Los tres son necesarios porque el
    signo del stock no cabe en un solo campo, y esta es la razon por la que
    existen:

      - `tipo` es el signo. `SALIDA` resta, `ENTRADA` suma. Lo dice
        `TipoMovimiento.suma_stock`.
      - `es_ajuste` es la procedencia. Sin el, una `ENTRADA` seria indistinguible de
        una compra, y una compra es lo unico que puede sumar stock por la via de
        `confirmar_compra`. Con el, la fila queda con `referencia_tipo='AJUSTE'` y
        se puede leer despues como "esto lo escribio una persona, no una compra".
      - `cantidad` es el numero. Positiva siempre: una cantidad negativa seria una
        devolucion, que es una SALIDA con la cantidad positiva. La constraint
        `ck_movimientos_cantidad_positiva` lo prohibe en la base igual.

    LAS CUATRO COMBINACIONES QUE TIENEN SENTIDO

        es_ajuste=false, tipo=SALIDA   -> una venta. Resta stock.
        es_ajuste=false, tipo=ENTRADA  -> RECHAZADO con 409. Sumaria stock sin
                                         compra y sin que nadie mirara el papel.
        es_ajuste=true,  tipo=SALIDA   -> "conto de mas". Resta stock.
        es_ajuste=true,  tipo=ENTRADA  -> "conto de menos". Suma stock.

    La cuarta es la que hace necesario `es_ajuste`: una correccion que SUMA es
    indistinguible de una compra si lo unico que se guarda es el tipo. Por eso el
    servicio rechaza la `ENTRADA` sin `es_ajuste` y la acepta con el — la
    distincion esta ahi, no en un nombre de enum nuevo.

    `extra="forbid"` para lo mismo que en `ProductoUpdate`: un cliente que reenvia
    el `actor` que vio en la respuesta recibe un 422 con el nombre del campo, en
    vez de un 200 que finge haber guardado quien autorizo. El autor sale del token.
    """

    model_config = ConfigDict(extra="forbid")

    producto_id: UUID
    tipo: TipoMovimiento
    cantidad: Decimal = Field(..., gt=0)
    # True = correccion de conteo. Es lo que pone `referencia_tipo='AJUSTE'` y lo
    # que permite una ENTRADA: sin el, la entrada seria una compra disfrazada.
    es_ajuste: bool = False
    # La venta viene de un comprobante de venta, si lo hay. NULL es lo normal.
    referencia_id: UUID | None = None
    descripcion_origen: str | None = Field(default=None, max_length=300)


class RechazarCompraRequest(BaseModel):
    """El cuerpo de rechazar. El motivo es opcional y va al log, no al estado.

    Quien rechaza sale del token, nunca del body: un `rechazada_por` en la
    peticion seria una puerta para descartar la compra de otra empresa. Ver
    `ConfirmarCompraRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    motivo: str | None = Field(default=None, max_length=500)


class CompraItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    producto_id: UUID | None
    descripcion: str
    cantidad: Decimal
    costo_unitario: Decimal | None
    total: Decimal | None
    orden: int
    producto_asignado_por: str | None
    producto_asignado_at: datetime | None
    producto_nombre: str | None = None


class CompraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    ticket_id: UUID
    estado: EstadoCompra
    fecha: date
    proveedor_nombre: str | None
    total: Decimal
    confirmada_por: str | None
    confirmada_at: datetime | None
    created_at: datetime
    items: list[CompraItemResponse] = Field(default_factory=list)

    # Cuantas lineas se guardaron y cuantas no se pudieron interpretar. Va en la
    # respuesta porque es la informacion que el usuario necesita ANTES de
    # autorizar: "de 15 lineas se guardaron 12" es informacion; un 422 en el
    # confirm sin decir cuales se perdieron, no.
    lineas_descartadas: int = 0


class AsignarProductoRequest(BaseModel):
    producto_id: UUID


class ConfirmarCompraRequest(BaseModel):
    """El cuerpo va vacio a proposito.

    Quien confirma sale del token, nunca del body. Un campo `confirmada_por` en el
    request seria una puerta para que alguien firme la entrada al inventario de
    otra empresa —y `AGENTS.md` ya advierte que no hay multi-tenancy, cualquiera
    autenticado puede mandar un `company_id` arbitrario.
    """

    nota: str | None = Field(default=None, max_length=500)


class MovimientoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    producto_id: UUID
    tipo: TipoMovimiento
    cantidad: Decimal
    referencia_tipo: str
    referencia_id: UUID | None
    actor: str
    descripcion_origen: str | None
    created_at: datetime


class LineaSinProductoResponse(CompraItemResponse):
    """Una linea esperando que alguien le asigne un producto.

    Es la cola. La respuesta trae el `compra_id` y la fecha del comprobante para
    que la pantalla pueda decir de que papel salio la linea sin otra consulta.
    """

    compra_id: UUID
    compra_fecha: date
    compra_estado: EstadoCompra
    # Que NO se esta proponiendo un producto. Es un dato, no una sugerencia: la
    # eleccion es de una persona y por eso no hay `candidato`.
    motivo: str = "sin producto asignado"


class ErrorDeInventarioResponse(BaseModel):
    """El error del servicio, traduciendolo.

    El mensaje del servicio esta escrito para que lo lea quien lo va a ver, y no
    es un `detail` generico de 422: "la compra tiene 3 lineas sin producto" es la
    informacion que hace falta para arreglarlo.
    """

    detail: str
    # Que falta y como arreglarlo. Machine-readable para que el cliente no tenga
    # que parsear el mensaje.
    codigo: str
    lineas_pendientes: int = 0


def _item_a_dict(item: Any, producto_nombre: str | None) -> dict[str, Any]:
    """El helper que el router usa para aplanar la relacion `producto`.

    Vive aqui y no en el router porque el router es un adaptador sin logica, y
    porque hace falta el mismo aplanado en dos respuestas distintas
    (`CompraResponse` y `LineaSinProductoResponse`).
    """
    return {
        "id": item.id,
        "producto_id": item.producto_id,
        "descripcion": item.descripcion,
        "cantidad": item.cantidad,
        "costo_unitario": item.costo_unitario,
        "total": item.total,
        "orden": item.orden,
        "producto_asignado_por": item.producto_asignado_por,
        "producto_asignado_at": item.producto_asignado_at,
        "producto_nombre": producto_nombre,
    }