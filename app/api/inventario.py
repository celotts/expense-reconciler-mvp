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
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.core.deps import Sesion, UsuarioActual
from app.core.enums import EstadoCompra, ProductoOrigen
from app.models.inventario import (
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.schemas.inventario import (
    STOCK_SIN_MOVIMIENTOS,
    AsignarProductoRequest,
    CompraItemResponse,
    CompraResponse,
    ConfirmarCompraRequest,
    LineaSinProductoResponse,
    MovimientoCreate,
    MovimientoResponse,
    ProductoConStock,
    ProductoCreate,
    ProductoResponse,
    ProductoUpdate,
    RechazarCompraRequest,
    _item_a_dict,
)
from app.services import inventario_service as inv

# ---------------------------------------------------------------------------
# POR QUE HAY `cast()` EN ESTE ARCHIVO
# ---------------------------------------------------------------------------
#
# Los modelos se declaran con `Column(...)`, que en SQLAlchemy 2.0 se anota a
# nivel de CLASE: `email` es `Column[str]`, no `str`. Al leer `usuario.email`
# sobre una INSTANCIA el descriptor devuelve el valor de la fila, que es un
# `str`, y el type checker ve la anotacion de la clase y no el descriptor.
#
# `cast()` dice eso sin mentir: no convierte nada en ejecucion, solo le dice al
# checker lo que el ORM ya resolvio. Los cuatro sitios de este archivo son las
# cuatro unicas cosas que aqui se USA una columna COMO VALOR y no como columna.
#
# ## POR QUE NO SE ARREGLA EN EL MODELO
#
# La forma correcta es `Mapped[str]` en vez de `Column(String)`, que es
# justamente lo que SQLAlchemy 2.0 ofrece para esto. Se midio el alcance: son
# **126 de los 173 errores de `app/`**, y las columnas a migrar son **143 en 11
# modelos** (`app/models/`). Es un arreglo de raiz y merece su propio commit con
# la suite entera en verde, no cuatro `cast` colados mientras se toca otra cosa.
#
# Con estos cuatro, `inventario.py` queda en cero errores de pyright; los otros
# 122 viven en los sitios donde una columna se USA como columna, que no se
# pueden arreglar sin tocar el modelo.


router = APIRouter()


def _actor(usuario: UsuarioActual) -> str:
    """Quien firma. Del token, nunca del body.

    Ver `ConfirmarCompraRequest`: un `confirmada_por` en el request seria una
    puerta para firmar la entrada al inventario de otra empresa.

    El `cast` es por el modelo, no por este codigo. Ver `_COLUMNA_COMO_VALOR`.
    """
    return cast(str, usuario.email)


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


def _compra_a_response(compra: CompraModel) -> CompraResponse:
    """La compra con sus lineas aplanadas.

    Vive aqui y no en el servicio porque el aplanado de `producto.nombre` es de
    presentacion: `CompraItemResponse` es plano y la relacion `producto` no. Y
    estaba copiado en `listar_compras`, `obtener_compra` y `confirmar_compra`, con
    el riesgo de que las tres se desincronicen al anadir un campo a la linea.
    """
    respuesta = CompraResponse.model_validate(compra)
    respuesta.items = [
        CompraItemResponse.model_validate(
            _item_a_dict(i, i.producto.nombre if i.producto else None)
        )
        for i in compra.items
    ]
    return respuesta


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
        # El defecto es `STOCK_SIN_MOVIMIENTOS`, la MISMA constante que declara el
        # default del campo. Antes era
        # `ProductoConStock.model_fields["stock"].default`, que hacia lo correcto
        # pero rompia el type checker: `FieldInfo.default` esta tipado como
        # `PydanticUndefined | Any`, y con ese tipo pyright no puede elegir el
        # overload de `dict.get` — el error era "No overloads for get match the
        # provided arguments". El problema no era el `.get`, era el centinela de
        # pydantic en la posicion de defecto.
        #
        # Y el default NO es un centinela en ejecucion — se comprobo:
        # `model_fields["stock"].default` vale `Decimal('0')`. O sea que el valor
        # era correcto y solo la anotacion mentia.
        respuesta.stock = stocks.get(cast(UUID, producto.id), STOCK_SIN_MOVIMIENTOS)
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
    #
    # Y es la MISMA constante que usa el listado y que declara el default del
    # campo. Estaba escrito a mano aqui, y mover el otro sitio a una constante
    # mientras este se quedaba con un `Decimal("0")` literal no habria quitado
    # la duplicacion: la habria movido. Con tres sitios (el campo del schema, el
    # `.get()` del listado y este alta) son tres valores que pueden divergir en
    # silencio, y el type checker no dice nada porque los tres estan bien
    # tipados.
    respuesta.stock = STOCK_SIN_MOVIMIENTOS
    return respuesta


@router.get(
    "/productos/{producto_id}",
    response_model=ProductoConStock,
    tags=["Inventario"],
    summary="Un producto con su stock",
)
async def obtener_producto(
    producto_id: UUID,
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
) -> ProductoConStock:
    """Un producto del catalogo, con el stock que le da el kardex.

    El `company_id` es obligatorio y se comprueba contra el producto. No es
    decorativo: `AGENTS.md` advierte que no hay multi-tenancy, asi que sin esta
    comparacion un `producto_id` de otra empresa se leeria igual y el stock que
    saliera en la respuesta seria el de la empresa equivocada —un 200 con el
    numero de otro, que es peor que un 403 porque no parece un error.

    Por que responde `ProductoConStock` y no `ProductoResponse`: el listado
    devuelve stock, y que el detalle no lo devuelva obliga al cliente a hacer una
    segunda llamada para saber el mismo numero.
    """
    producto = (
        await db.execute(
            select(ProductoModel).where(ProductoModel.id == producto_id)
        )
    ).scalar_one_or_none()
    if producto is None:
        raise HTTPException(status_code=404, detail="El producto no existe")
    if cast(UUID, producto.company_id) != company_id:
        # 404 y no 403: el recurso no existe PARA esta empresa, y un 403
        # confirmaria que hay un producto con ese id en otra. Ver
        # `inventario_service.NoExiste`.
        raise HTTPException(status_code=404, detail="El producto no existe")

    respuesta = ProductoConStock.model_validate(producto)
    respuesta.stock = (await inv.stock_de_una(db, producto.id))
    return respuesta


@router.patch(
    "/productos/{producto_id}",
    response_model=ProductoConStock,
    tags=["Inventario"],
    summary="Renombrar, verificar, dar de baja o corregir el precio",
)
async def actualizar_producto(
    producto_id: UUID,
    datos: ProductoUpdate,
    db: Sesion,
    _: UsuarioActual,
    company_id: UUID = Query(...),
) -> ProductoConStock:
    """Corrige el catalogo. Es lo que hace que la cola se pueda vaciar.

    `GET /inventario/productos?solo_sin_verificar=true` devuelve la cola de
    productos que salieron de leer un papel, y `AGENTS.md` le atribuye la tarea de
    "revisar, renombrar o fusionar". Sin este endpoint la cola se podia mirar pero
    no limpiar: el `verificado` de un producto de OCR no tenia ninguna puerta por
    la que cambiarlo.

    Lo que el servicio NO deja cambiar, y por que, esta en
    `inventario_service.actualizar_producto`: `company_id`, `origen`, y poner
    `verificado` a un producto de OCR sin codigo.

    `exclude_unset=True` y no `exclude_none`: es la diferencia entre "no lo
    mandaron" y "lo mandaron en null". Ver `ProductoUpdate`.
    """
    producto = (
        await db.execute(
            select(ProductoModel).where(ProductoModel.id == producto_id)
        )
    ).scalar_one_or_none()
    if producto is None or cast(UUID, producto.company_id) != company_id:
        raise HTTPException(status_code=404, detail="El producto no existe")

    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        # 422 y no un 200 con el producto sin cambios: un PATCH vacio no es una
        # operacion, y responder 200 haria que un cliente que reenvia el objeto
        # entero creyera que guardo algo.
        raise HTTPException(
            status_code=422,
            detail="No mandaste ningún campo que se pueda cambiar",
        )

    try:
        await inv.actualizar_producto(db, producto_id, cambios)
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc
    await db.commit()

    await db.refresh(producto)
    respuesta = ProductoConStock.model_validate(producto)
    respuesta.stock = await inv.stock_de_una(db, producto.id)
    return respuesta


# ---------------------------------------------------------------------------
# La cola, y las rutas con placeholder
#
# `cola` va primero: ver el docstring del modulo. Lo mismo para
# `/productos/{producto_id}`: son dos segmentos y no colisionarian con `/cola`, pero
# las literales declaradas antes cuestan menos que demostrar que no hace falta.
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
    return [_compra_a_response(compra) for compra in compras]


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
    return _compra_a_response(compra)


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

    return _compra_a_response(compra)


@router.post(
    "/compras/{compra_id}/rechazar",
    response_model=CompraResponse,
    tags=["Inventario"],
    summary="EN_REVISION -> RECHAZADO. Este comprobante no es una compra.",
)
async def rechazar_compra(
    compra_id: UUID,
    cuerpo: RechazarCompraRequest,
    db: Sesion,
    usuario: UsuarioActual,
) -> CompraResponse:
    """Descarta una compra sin borrarla.

    Por que no se borra: `compras.ticket_id` es UNIQUE, asi que borrar deja el
    ticket libre y el proximo reescaneo vuelve a crear la compra desde
    `registrar_compra`. El bucle aparece cada vez que se toca el comprobante y no
    recuerda que alguien ya la habia mirado. Con RECHAZADO el veredicto queda
    escrito.

    Una compra PROCESADA no se puede rechazar: ya sumo stock y el kardex es
    append-only, no hay forma de deshacerlo. El 409 dice que la via es el AJUSTE.

    Quien rechaza sale del token, nunca del body. Ver `RechazarCompraRequest`.
    """
    try:
        compra = await inv.rechazar_compra(
            db, compra_id, actor=_actor(usuario), motivo=cuerpo.motivo
        )
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc

    await db.commit()
    await db.refresh(compra, ["items"])
    return _compra_a_response(compra)


@router.post(
    "/compras/{compra_id}/reabrir",
    response_model=CompraResponse,
    tags=["Inventario"],
    summary="RECHAZADO -> EN_REVISION. Deshace el rechazo, no la confirmacion.",
)
async def reabrir_compra(
    compra_id: UUID,
    db: Sesion,
    usuario: UsuarioActual,
) -> CompraResponse:
    """Devuelve una compra rechazada a la cola de autorizacion.

    No reabre una compra PROCESADA, y hay un endpoint distinto para cada cosa a
    proposito: uno que aceptara las dos necesitaria adivinar cual es cual, y el
    error —dar por reversible una compra que ya movio stock— es el que no tiene
    salida. Ver `inventario_service.reabrir_compra`.
    """
    try:
        compra = await inv.reabrir_compra(db, compra_id, actor=_actor(usuario))
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc

    await db.commit()
    await db.refresh(compra, ["items"])
    return _compra_a_response(compra)


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


@router.post(
    "/movimientos",
    response_model=MovimientoResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Inventario"],
    summary="Registra una salida (venta) o un ajuste de conteo",
)
async def registrar_movimiento(
    cuerpo: MovimientoCreate,
    db: Sesion,
    usuario: UsuarioActual,
    company_id: UUID = Query(...),
) -> MovimientoResponse:
    """Escribe una fila del kardex a mano.

    ES LA RESPUESTA A "el conteo fisico no cuadra, que movio este producto". El
    kardex es append-only por trigger, asi que corregir un movimiento viejo es
    agregar uno nuevo que diga lo contrario, y este endpoint es el unico camino
    para hacerlo.

    `es_ajuste=true` pone `referencia_tipo='AJUSTE'` y es lo que permite una
    `ENTRADA`: sin el, una entrada seria indistinguible de una compra y el servicio
    la rechaza con 409, porque `confirmar_compra` es la unica via que puede sumar
    stock. Ver `inventario_service.registrar_movimiento`.

    El cliente no manda `referencia_tipo`: sale de `es_ajuste`. Pedirlo seria
    abrir la puerta a que alguien escriba `referencia_tipo='COMPRA'` a mano, y una
    fila con esa etiqueta es lo que `export_service` y las auditorias toman como
    "esto vino de una compra".
    """
    referencia_tipo = "AJUSTE" if cuerpo.es_ajuste else "VENTA"

    try:
        movimiento = await inv.registrar_movimiento(
            db,
            company_id=company_id,
            producto_id=cuerpo.producto_id,
            tipo=cuerpo.tipo,
            cantidad=cuerpo.cantidad,
            actor=_actor(usuario),
            referencia_tipo=referencia_tipo,
            referencia_id=cuerpo.referencia_id,
            descripcion_origen=cuerpo.descripcion_origen,
        )
    except inv.ErrorDeInventario as exc:
        raise _conflictos(exc) from exc

    await db.commit()
    await db.refresh(movimiento)
    return MovimientoResponse.model_validate(movimiento)


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