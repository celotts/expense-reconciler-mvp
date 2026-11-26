"""El mantenedor de cuentas de gasto, y la autorizacion de los gastos.

POR QUE ESTE ARCHIVO NO ES UN CRUD MAS
=====================================

Tres decisiones que no son de "como se hace un CRUD":

1. **El que autoriza sale del token, nunca del cuerpo** (regla 23). No hay
   `autorizado_por` en ninguno de los dos schemas de escritura: aceptarlo del
   body seria una puerta para autorizar en nombre de otra persona. La empresa
   tampoco sale del cuerpo ni del token: `EmpresaActiva` la lee de las
   membresias en la BD en cada peticion.

2. **La cuenta y su texto se escriben en la misma operacion.** El router solo
   pasa el `tipo_gasto_id`; de `tickets.category` se ocupa el servicio, en el
   mismo `UPDATE`. Si cada uno escribiera lo suyo, un dia queda un gasto con el
   id puesto y el texto viejo, y el cierre lo reporta en la cuenta anterior.

3. **Rechazar exige motivo, y lo dice el schema.** El 422 sale antes de tocar la
   base, y la constraint `ck_tickets_autorizacion_firmada` lo prohibe igual, para
   que un `UPDATE` a mano tampoco lo evite.

Y lo que NO hay: un `DELETE`. Una cuenta se da de baja con `activo=false`
(regla 24), porque un gasto ya contabilizado apunta a ella para siempre y borrarla
deja el reporte de un mes ya cerrado sin poder decir en que cuenta estaba el
gasto.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import EmpresaActiva, UsuarioActual
from app.models.inventario import ProductoModel
from app.models.ticket import TicketModel
from app.models.tipo_gasto import TipoGastoModel
from app.schemas.tipo_gasto import (
    AsignarLoteRequest,
    AsignarLoteResponse,
    AsignarTipoGastoRequest,
    AutorizacionResponse,
    AutorizarRequest,
    ConteoAutorizacion,
    RechazarRequest,
    TipoGastoCreate,
    TipoGastoResponse,
    TipoGastoUpdate,
)
from app.services import tipos_gasto_service as svc

router = APIRouter(prefix="/tipos-gasto", tags=["Tipos de gasto"])


def _como_error(exc: svc.ErrorDeTipoGasto) -> HTTPException:
    """El error del servicio es de operacion, no de programacion: es 422.

    400 seria "la peticion esta mal formada", que no es el caso: el JSON esta bien
    y lo que no existe es la cuenta que se pidio. 404 seria "no hay ruta", que
    tampoco. Y 409 es para un conflicto de estado, que aqui no hay todavia.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


async def _a_response(db: AsyncSession, tipo: TipoGastoModel) -> TipoGastoResponse:
    """Arma la respuesta con los conteos de uso.

    Los cuenta con dos `count()` agrupados en vez de tres consultas por tipo: son
    las mismas cifras que la pantalla necesita para decidir si dar de baja una
    cuenta es sensato, y sacarlas de otra parte es la forma de que la pantalla y
    la base discrepen.
    """
    gastos = await db.execute(
        select(func.count())
        .select_from(TicketModel)
        .where(TicketModel.tipo_gasto_id == tipo.id)
    )
    productos = await db.execute(
        select(func.count())
        .select_from(_Producto())
        .where(_Producto().id.isnot(None), _Producto().tipo_gasto_id == tipo.id)
    )
    return TipoGastoResponse(
        id=tipo.id,
        company_id=tipo.company_id,
        clave=tipo.clave,
        nombre=tipo.nombre,
        variable=tipo.variable,
        activo=tipo.activo,
        gastos=gastos.scalar_one(),
        productos=productos.scalar_one(),
    )


def _Producto():  # noqa: N802 - es un alias, no una clase
    from app.models.inventario import ProductoModel

    return ProductoModel


# ---------------------------------------------------------------------------
# El catalogo
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TipoGastoResponse])
async def listar_tipos_gasto(
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
    incluir_inactivos: bool = Query(False, description="Trae tambien las dadas de baja"),
) -> list[TipoGastoResponse]:
    """Las cuentas de la empresa activa.

    No se acepta `company_id` del cliente: `EmpresaActiva` lo resuelve contra las
    membresias de la BD en esta misma peticion, y la guardia de `api_router`
    rechaza ademas cualquier `company_id` declarado que no concuerde. Aceptarlo
    aqui seria escribir en la cuenta de otra.
    """
    tipos = await svc.listar(
        db, empresa, incluir_inactivos=incluir_inactivos
    )
    return [await _a_response(db, t) for t in tipos]


@router.post("/sembrar", response_model=dict)
async def sembrar_catalogo(
    usuario: UsuarioActual, empresa: EmpresaActiva, db: AsyncSession = Depends(get_db)
) -> dict:
    """Crea las cuentas basicas que falten. Idempotente.

    Existe porque el catalogo depende de la empresa y una migracion no puede
    sembrarlo (ver la nota de la migracion 0018). Para una empresa nueva se llama
    sola al crearla; este endpoint es el botón de la pantalla para las que ya
    existen, y se puede volver a pulsar sin que rompa nada.

    **POST y no GET.** Si fuera GET la guardia global lo clasificaria como lectura
    y un `viewer` podria escribir el catalogo con solo mirar una pantalla. Una
    siembra es una escritura, aunque sea idempotente y no borre nada.
    """
    anadidas = await svc.asegurar_catalogo(db, empresa)
    return {"anadidas": anadidas, "mensaje": f"Se agregaron {anadidas} cuentas." if anadidas else "El catalogo ya estaba completo."}


@router.post("", response_model=TipoGastoResponse, status_code=status.HTTP_201_CREATED)
async def crear_tipo_gasto(
    datos: TipoGastoCreate,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> TipoGastoResponse:
    """Da de alta una cuenta.

    La clave se normaliza a mayusculas en el servicio, no aqui: `Renta` y `renta`
    son la misma cuenta y si se guardaran distintas el cierre las contaria dos
    veces.
    """
    try:
        tipo = await svc.crear(
            db,
            empresa,
            clave=datos.clave,
            nombre=datos.nombre,
            variable=datos.variable,
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return await _a_response(db, tipo)


@router.patch("/{tipo_id}", response_model=TipoGastoResponse)
async def actualizar_tipo_gasto(
    tipo_id: UUID,
    datos: TipoGastoUpdate,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> TipoGastoResponse:
    """Edita una cuenta.

    El `exclude_unset` lo aplica FastAPI antes de llegar al servicio, asi que
    `nombre=None` llego como "no lo mandaste" y no como "ponlo en null" (regla 22).
    """
    try:
        tipo = await svc.actualizar(
            db,
            empresa,
            tipo_id,
            nombre=datos.nombre,
            variable=datos.variable,
            activo=datos.activo,
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return await _a_response(db, tipo)


# ---------------------------------------------------------------------------
# La cuenta de un gasto y de un producto
# ---------------------------------------------------------------------------


@router.put("/tickets/cuenta", response_model=AsignarLoteResponse)
async def asignar_cuenta_a_lote_de_tickets(
    datos: AsignarLoteRequest,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> AsignarLoteResponse:
    """Clasifica la seleccion de la barra con una sola operacion.

    La ruta literal va antes que `/tickets/{ticket_id}/cuenta` por la regla de
    que el primer match gana. No son del mismo largo, pero la convencion evita
    que un dia se anada `/tickets/{algo}` y la nueva ruta muera sin respuesta.
    """
    try:
        resultado = await svc.asignar_lote_a_tickets(
            db, empresa, datos.ticket_ids, datos.tipo_gasto_id
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return AsignarLoteResponse(**resultado)


@router.put("/tickets/{ticket_id}/cuenta", status_code=status.HTTP_204_NO_CONTENT)
async def asignar_cuenta_a_ticket(
    ticket_id: UUID,
    datos: AsignarTipoGastoRequest,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Le pone (o le quita) la cuenta a un gasto.

    Mandar `tipo_gasto_id: null` lo deja SIN CLASIFICAR. Es una respuesta valida y
    no un error: significa "todavia no lo se", y es la unica forma de corregir
    una cuenta mal puesta.
    """
    try:
        await svc.asignar_a_ticket(db, empresa, ticket_id, datos.tipo_gasto_id)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc


@router.put("/productos/{producto_id}/cuenta", status_code=status.HTTP_204_NO_CONTENT)
async def asignar_cuenta_a_producto(
    producto_id: UUID,
    datos: AsignarTipoGastoRequest,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Le pone la cuenta por omision a un producto.

    NO copia nada a ningun gasto. Lo que se compre de ese producto puede ir a otra
    cuenta, y heredarla seria decidir por la persona en que se imputa un gasto.
    """
    try:
        await svc.asignar_a_producto(db, empresa, producto_id, datos.tipo_gasto_id)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc


# ---------------------------------------------------------------------------
# La autorizacion
# ---------------------------------------------------------------------------


@router.get("/autorizacion", response_model=ConteoAutorizacion)
async def conteo_de_autorizacion(
    usuario: UsuarioActual, empresa: EmpresaActiva, db: AsyncSession = Depends(get_db)
) -> ConteoAutorizacion:
    """Cuantos gastos hay de cada estado. Para la cabecera de la pantalla."""
    c = await svc.conteo_por_estado(db, empresa)
    return ConteoAutorizacion(
        pendientes=c.get("PENDIENTE", 0),
        aprobadas=c.get("APROBADA", 0),
        rechazadas=c.get("RECHAZADA", 0),
    )


@router.get("/autorizacion/pendientes")
async def pendientes_de_autorizar(
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
    limite: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    """La cola: los gastos que todavia nadie ha decidido.

    Devuelve datos planos y NO los `TicketResponse` completos, a proposito: esta
    pantalla necesita commerce, monto, fecha y cuenta, y nada mas. Mandar el
    `raw_text` de 20 000 caracteres por cada pendiente es lo que hace que una
    pantalla con 200 filas se sienta lenta.
    """
    tickets = await svc.pendientes_de_autorizar(db, empresa, limite=limite)
    if not tickets:
        return []
    ids = [t.id for t in tickets]
    cuentas = await db.execute(
        select(TipoGastoModel.id, TipoGastoModel.clave, TipoGastoModel.nombre).where(
            TipoGastoModel.id.in_(
                select(TicketModel.tipo_gasto_id).where(TicketModel.id.in_(ids))
            )
        )
    )
    por_id = {c[0]: {"id": c[0], "clave": c[1], "nombre": c[2]} for c in cuentas.all()}

    return [
        {
            "id": str(t.id),
            "provider_name": t.provider_name,
            "total_amount": str(t.total_amount) if t.total_amount is not None else None,
            "expense_date": t.expense_date.isoformat() if t.expense_date else None,
            "fecha_provisional": t.fecha_provisional,
            "extraction_status": t.extraction_status,
            "validacion": t.validation_errors,
            "tipo_gasto": por_id.get(t.tipo_gasto_id),
            "puede_autorizarse": t.extraction_status not in ("PENDIENTE", "RECHAZADO"),
        }
        for t in tickets
    ]


@router.post("/tickets/{ticket_id}/autorizar", response_model=AutorizacionResponse)
async def autorizar_ticket(
    ticket_id: UUID,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
    _cuerpo: AutorizarRequest = AutorizarRequest(),
) -> AutorizacionResponse:
    """Aprueba un gasto.

    ## POR QUE EL CUERPO ESTA VACIO Y SIN embargo SE ACEPTA

    Para que el cliente pueda mandar `{}` y no un 422, y para que quede
    explicito en la firma que aqui no se acepta ninguna decision: ni quien
    autoriza (sale del token) ni el motivo (aprobar no lleva motivo). Un
    endpoint de "haz esto" con un cuerpo que no dice nada es raro, pero un
    endpoint donde el cuerpo PODRIA decir quien autorizo es peor.
    """
    try:
        estado = await svc.autorizar(db, empresa, ticket_id, usuario=usuario.email)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return AutorizacionResponse(
        ticket_id=ticket_id,
        estado=estado.value,
        autorizado_por=usuario.email,
    )


@router.post("/tickets/{ticket_id}/rechazar", response_model=AutorizacionResponse)
async def rechazar_ticket(
    ticket_id: UUID,
    datos: RechazarRequest,
    usuario: UsuarioActual,
    empresa: EmpresaActiva,
    db: AsyncSession = Depends(get_db),
) -> AutorizacionResponse:
    """Rechaza un gasto. El motivo va en el cuerpo y es obligatorio."""
    try:
        estado = await svc.rechazar(
            db, empresa, ticket_id, usuario=usuario.email, motivo=datos.motivo
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return AutorizacionResponse(
        ticket_id=ticket_id,
        estado=estado.value,
        autorizado_por=usuario.email,
        motivo_rechazo=datos.motivo,
    )