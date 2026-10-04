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


class ProductoConStock(ProductoResponse):
    """Un producto y cuanto hay de el.

    `stock` viene del kardex y por eso es `Decimal`, no `int`: las cantidades son
    fraccionables (se compra 1.5 kg, no 2), y un `int` seria un `round()` callado
    que no sabe si sube o baja.
    """

    stock: Decimal = Decimal("0")


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