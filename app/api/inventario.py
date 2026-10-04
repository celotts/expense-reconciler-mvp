"""El router de inventario: productos, compras, y la confirmacion que suma stock.

Este router es un adaptador. No decide nada: toda la logica esta en
`app/services/inventario_service.py`. Lo unico que hace aqui y no alla es traducir
`ErrorDeInventario` a un 409, porque el codigo HTTP no le corresponde al servicio.

ORDEN DE LAS RUTAS, Y POR QUE IMPORTA

`GET /inventario/cola` va ANTES que cualquier ruta con `{...}`. Es el mismo
criterio que `tickets.py` y `reconciliations.py` documentan: en FastAPI el
primer match gana, y una ruta literal declarada despues de un placeholder es una
ruta a la que nunca se llega. Con dos segmentos (`/cola`) no habria problema, pero
cuesta menos ser explicitos que demostrar que no hace falta.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.core.deps import Sesion, UsuarioActual
from app.core.enums import EstadoCompra, ProductoOrigen
from app.models.inventario import (
    CompraItemModel,
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.schemas.inventario import (
    AsignarProductoRequest,
    CompraItemResponse,
    CompraResponse,
    ConfirmarCompraRequest,
    LineaSinProductoResponse,
    MovimientoResponse,
    ProductoConStock,
    ProductoCreate,
    ProductoResponse,
    _item_a_dict,
)
from app.services import inventario_service as inv

router = APIRouter()


def _actor(usuario: UsuarioActual) -> str:
    """Quien firma. Del token, nunca del body.

    Ver `ConfirmarCompraRequest`: un `confirmada_por` en el request seria una
    puerta para firmar la entrada al inventario de otra empresa.
    """
    return usuario.email


def _conflictos(exc: inv.ErrorDeInventario) -> HTTPException:
    """El error del inventario, con su codigo.

    404 y no 409 para lo que no existe: un 404 dice "no lo intentes otra vez", y
    un cliente que reintenta en 409 se queda golpeando un recurso que jamas va a
    aparecer. Ver `inventario_service.NoExiste`.

    409 y no 400 para lo que existe pero no puede: "ya esta confirmada" no es un
    error de forma, es un choque con el estado actual. Un 400 diria "arregla el
    cuerpo", y el cuerpo esta perfecto.
    """
    codigo = (
        status.HTTP_404_NOT_FOUND
        if isinstance(exc, inv.NoExiste)
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=codigo, detail=str(exc))


async def _archivar_el_comprobante(db: Sesion, ticket_id: UUID, usuario) -> None:
    """Mueve el papel a la carpeta de escaneados, si el escaner lo tiene registrado.

    Se busca por el `scan_files` del ticket, y no por el archivo del ticket: el
    escaner es quien sabe el `relative_path` real en la carpeta, y un ticket
    subido por la API no tiene ninguno (no viene de un archivo que escanear).

    No propaga errores. La compra ya esta confirmada y el stock ya sumo; que el
    papel no se haya movido es un annoyance que se arregla en la siguiente
    corrida, no un motivo para devolver un error que hace dudar de una
    confirmacion que si ocurrio. Es el mismo criterio que `_registrar` en
    `scan_service`: la auditoria y la organizacion no tumban el trabajo.
    """
    from app.models.scan_file import ScanFileModel
    from app.services.scan_service import archivar_si_ya_resuelto

    fila = (
        await db.execute(
            select(ScanFileModel).where(ScanFileModel.ticket_id == ticket_id)
        )
    ).scalar_one_or_none()
    if fila is None:
        return

    try:
        await archivar_si_ya_resuelto(db, fila, actor=_actor(usuario))
    except Exception as exc:  # noqa: BLE001 - aqui el fallo no se propaga
        logger.warning(
            "La compra se confirmo pero el comprobante %s no se pudo archivar: %s",
            ticket_id,
            exc,
        )
    else:
        await db.commit()


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------


@router.get("/productos", response_model=list[ProductoConStock], tags=["Inventario"])
async def listar_productos(
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
    incluir_inactivos: bool = Query(False),
    solo_sin_verificar: bool = Query(
        False,
        description=(
            "Solo los productos que salio de una linea de comprobante y que "
            "nadie ha confirmado. Es la cola de trabajo del catalogo: lo que "
            "hay que revisar, renombrar o fusionar."
        ),
    ),
) -> list[ProductoConStock]:
    """El catalogo, con el stock de cada uno.

    El stock sale del kardex (`stock_de`), no de una columna: ver el modulo de
    `inventario_service`. Por eso la consulta del catalogo y la del stock son dos
    y despues se pegan: una columna `stock` seria una copia que se desincroniza.

    `solo_sin_verificar=true` es la cola de revision de productos. Es la parte que
    hace manejable el alta automatica: el inventario funciona desde el primer dia
    y a la vez hay una lista de lo que hay que limpiar. Ver
    db/migrations/0011_productos_origen.sql.
    """
    consulta = select(ProductoModel).where(ProductoModel.company_id == company_id)
    if not incluir_inactivos:
        consulta = consulta.where(ProductoModel.activo.is_(True))
    if solo_sin_verificar:
        consulta = consulta.where(ProductoModel.verificado.is_(False))
    productos = list((await db.execute(consulta.order_by(ProductoModel.nombre))).scalars())

    stocks = await inv.stock_de(db, company_id)
    salida = []
    for producto in productos:
        respuesta = ProductoConStock.model_validate(producto)
        respuesta.stock = stocks.get(producto.id, ProductoConStock.model_fields["stock"].default)
        salida.append(respuesta)
    return salida


@router.post(
    "/productos",
    # `ProductoConStock` y no `ProductoResponse`: el listado devuelve stock, y
    # que el alta no lo devuelva obliga al cliente a hacer una segunda llamada
    # para saber un 0 que ya se sabe. Nace en 0 porque un producto recien creado
    # no tiene movimientos — que no es lo mismo que "no aparece en el listado".
    response_model=ProductoConStock,
    status_code=status.HTTP_201_CREATED,
    tags=["Inventario"],
)
async def crear_producto(
    datos: ProductoCreate,
    db: Sesion,
    usuario: UsuarioActual,
    company_id: UUID = Query(...),
) -> ProductoResponse:
    """Da de alta un producto. A mano, no desde una linea de OCR."""
    existe = (
        await db.execute(
            select(ProductoModel).where(
                ProductoModel.company_id == company_id,
                ProductoModel.codigo == datos.codigo,
            )
        )
    ).scalar_one_or_none() if datos.codigo else None
    if existe is not None:
        # 409 y no 422: el cuerpo es valido, el producto ya esta. Ademas el error
        # del indice unico seria un 500 sin contexto.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Ya existe un producto con el codigo {datos.codigo} en esta "
                "empresa."
            ),
        )

    producto = ProductoModel(
        company_id=company_id,
        # Alta a mano: nace verificada y `MANUAL`, sin excepcion. Un producto que
        # puso una persona mirando el producto no necesita pasar por la cola de
        # revision. Ver `ProductoCreate`.
        origen=ProductoOrigen.MANUAL.value,
        verificado=True,
        **datos.model_dump(),
    )
    db.add(producto)
    await db.commit()
    await db.refresh(producto)

    respuesta = ProductoConStock.model_validate(producto)
    # Cero explicito, no ausente: ver el comentario del decorador.
    respuesta.stock = Decimal("0")
    return respuesta


# ---------------------------------------------------------------------------
# La cola, y las rutas con placeholder
#
# `cola` va primero: ver el docstring del modulo.
# ---------------------------------------------------------------------------


@router.get(
    "/cola",
    response_model=list[LineaSinProductoResponse],
    tags=["Inventario"],
    summary="Lineas cuya descripcion no se reconoce (la cola de trabajo)",
)
async def cola_de_productos(
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
) -> list[LineaSinProductoResponse]:
    """Las lineas de compra sin producto asignado.

    Es la cola de trabajo del inventario. No hay una tabla de "pendientes": esta
    consulta ES la cola, y es `compra_items WHERE producto_id IS NULL`. Una tabla
    mas seria una segunda fuente de verdad que se puede desincronizar de
    `compra_items`.
    """
    items = await inv.lineas_sin_producto(db, company_id)
    compras = {
        c.id: c
        for c in (
            await db.execute(
                select(CompraModel).where(CompraModel.company_id == company_id)
            )
        ).scalars()
    }

    salida = []
    for item in items:
        compra = compras.get(item.compra_id)
        fila = _item_a_dict(item, item.producto.nombre if item.producto else None)
        fila["compra_id"] = item.compra_id
        fila["compra_fecha"] = compra.fecha if compra else item.created_at.date()
        fila["compra_estado"] = compra.estado if compra else EstadoCompra.PROCESAR
        fila["motivo"] = "sin producto asignado"
        salida.append(LineaSinProductoResponse.model_validate(fila))
    return salida


# ---------------------------------------------------------------------------
# Compras
# ---------------------------------------------------------------------------


@router.get("/compras", response_model=list[CompraResponse], tags=["Inventario"])
async def listar_compras(
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
    estado: EstadoCompra | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[CompraResponse]:
    consulta = (
        select(CompraModel)
        .where(CompraModel.company_id == company_id)
        .options(selectinload(CompraModel.items))
    )
    if estado is not None:
        consulta = consulta.where(CompraModel.estado == estado.value)

    compras = list(
        (
            await db.execute(
                consulta.order_by(CompraModel.fecha.desc()).offset(skip).limit(limit)
            )
        ).scalars()
    )

    salida = []
    for compra in compras:
        respuesta = CompraResponse.model_validate(compra)
        respuesta.items = [
            CompraItemResponse.model_validate(
                _item_a_dict(i, i.producto.nombre if i.producto else None)
            )
            for i in compra.items
        ]
        salida.append(respuesta)
    return salida


@router.get(
    "/compras/{compra_id}", response_model=CompraResponse, tags=["Inventario"]
)
async def obtener_compra(
    compra_id: UUID, db: Sesion, _: UsuarioActual
) -> CompraResponse:
    compra = (
        await db.execute(
            select(CompraModel)
            .where(CompraModel.id == compra_id)
            .options(selectinload(CompraModel.items))
        )
    ).scalar_one_or_none()
    if compra is None:
        raise HTTPException(status_code=404, detail="La compra no existe")

    respuesta = CompraResponse.model_validate(compra)
    respuesta.items = [
        CompraItemResponse.model_validate(
            _item_a_dict(i, i.producto.nombre if i.producto else None)
        )
        for i in compra.items
    ]
    return respuesta


@router.post(
    "/compras/{compra_id}/confirmar",
    response_model=CompraResponse,
    tags=["Inventario"],
    summary="EN_REVISION -> PROCESADO. Aqui es donde el inventario suma.",
)
async def confirmar_compra(
    compra_id: UUID,
    cuerpo: ConfirmarCompraRequest,
    db: Sesion,
    usuario: UsuarioActual,
) -> CompraResponse:
    """Autoriza la compra y hace que sus lineas entren al inventario.

    Es el unico endpoint del proyecto que escribe en `movimientos_inventario`, y
    por eso devuelve 409 y no 200 en los tres casos que importan: ya confirmada
    (no se vuelve a sumar), con lineas sin producto (el inventario quedaria
    incompleto sin rastro), o sin quien firmar.
    """
    try:
        compra = await inv.confirmar_compra(db, compra_id, actor=_actor(usuario))
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc

    await db.commit()

    # El comprobante se archiva DESPUES del commit, y solo ahora.
    #
    # El orden importa: si se moviera el archivo antes, un fallo en la
    # confirmacion dejaria el comprobante en la carpeta de escaneados sin que
    # nunca hubiera entrado al inventario — la senal visible diria lo contrario
    # de lo que paso. Y si se moviera antes del commit y el commit fallara, el
    # archivo ya no estaria donde el proximo escaneo lo va a buscar.
    #
    # Que no tumbe la respuesta: la compra ya esta confirmada y el stock ya sumo,
    # que es lo importante. El archivo sin archivar se puede volver a mover con
    # `POST /scan` y no se pierde nada — por eso se avisa con un log y no con un
    # 500 que hiciera dudar de una confirmacion que si ocurrio.
    await _archivar_el_comprobante(db, compra.ticket_id, usuario)

    respuesta = CompraResponse.model_validate(compra)
    respuesta.items = [
        CompraItemResponse.model_validate(
            _item_a_dict(i, i.producto.nombre if i.producto else None)
        )
        for i in compra.items
    ]
    return respuesta


@router.post(
    "/compras/items/{item_id}/producto",
    response_model=CompraItemResponse,
    tags=["Inventario"],
    summary="Le asigna un producto del catalogo a una linea",
)
async def asignar_producto_a_linea(
    item_id: UUID,
    datos: AsignarProductoRequest,
    db: Sesion,
    usuario: UsuarioActual,
) -> CompraItemResponse:
    """Resuelve una linea de la cola.

    Guarda QUIEN asigno y cuando: "lo eligio el sistema" y "lo eligio Ana" son
    respuestas distintas a "de donde salio este producto del catalogo", y sin la
    columna de actor la segunda no se puede reconstruir.
    """
    try:
        item = await inv.asignar_producto(
            db, item_id, datos.producto_id, actor=_actor(usuario)
        )
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc

    await db.commit()
    await db.refresh(item)
    return CompraItemResponse.model_validate(
        _item_a_dict(item, item.producto.nombre if item.producto else None)
    )


# ---------------------------------------------------------------------------
# El kardex
# ---------------------------------------------------------------------------


@router.get(
    "/movimientos",
    response_model=list[MovimientoResponse],
    tags=["Inventario"],
    summary="El kardex: que movio cada producto y cuando",
)
async def listar_movimientos(
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
    producto_id: UUID | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> list[MovimientoResponse]:
    """La linea de tiempo del inventario.

    Es la respuesta a "el conteo fisico no cuadra, que movio este producto".
    """
    consulta = select(MovimientoInventarioModel).where(
        MovimientoInventarioModel.company_id == company_id
    )
    if producto_id is not None:
        consulta = consulta.where(MovimientoInventarioModel.producto_id == producto_id)

    movimientos = list(
        (
            await db.execute(
                consulta.order_by(MovimientoInventarioModel.created_at.desc()).limit(limit)
            )
        ).scalars()
    )
    return [MovimientoResponse.model_validate(m) for m in movimientos]