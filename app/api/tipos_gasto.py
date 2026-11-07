"""El mantenedor de cuentas de gasto, y la autorizacion de los gastos.

POR QUE ESTE ARCHIVO NO ES UN CRUD MAS
=====================================

Tres decisiones que no son de "como se hace un CRUD":

1. **El que autoriza sale del token, nunca del cuerpo** (regla 23). No hay
   `autorizado_por` en ninguno de los dos schemas de escritura: aceptarlo del
   body seria una puerta para autorizar en nombre de otra persona. Y como los
   roles existen desde P0-1 (`app/core/deps.py`), autorizar y rechazar piden
   el permiso "aprobar" (admin, contador o aprobador): un digitalizador lee y
   clasifica, pero no firma veredictos.

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

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import Escribir, UsuarioActual, exige_rol, resolver_empresa
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.models.tipo_gasto import TipoGastoModel
from app.schemas.tipo_gasto import (
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


async def _empresa(
    usuario: UsuarioActual,
    db: AsyncSession,
    company_id: UUID | None = None,
) -> UUID:
    # La empresa de este router (P0-1; correccion del bug P0-6). P0-6: este
    # archivo leia `usuario.company_id` en 10 sitios, pero `UserModel` no
    # tenia esa columna: cada llamada reventaba con `AttributeError` y el
    # router entero devolvia 500. La columna existe desde P0-1, pero leerla
    # directo no basta: un usuario sin empresa pasaria `None` al servicio y
    # el servicio filtraria `company_id IS NULL`, que no es "nada" sino
    # "todo lo sin empresa". `resolver_empresa` convierte ese caso en 404
    # ("sin empresa no ve nada"). El `company_id` opcional del query solo
    # lo usa el operador admin sin empresa; un usuario con empresa que pida
    # otra recibe 404.
    return await resolver_empresa(usuario, company_id, db)


def _como_error(exc: svc.ErrorDeTipoGasto) -> HTTPException:
    """El error del servicio es de operacion, no de programacion: es 422.

    400 seria "la peticion esta mal formada", que no es el caso: el JSON esta bien
    y lo que no existe es la cuenta que se pidio. 404 seria "no hay ruta", que
    tampoco. Y 409 es para un conflicto de estado, que aqui no hay todavia.
    """
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


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
    db: AsyncSession = Depends(get_db),
    incluir_inactivos: bool = Query(False, description="Trae tambien las dadas de baja"),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> list[TipoGastoResponse]:
    """Las cuentas de esta empresa.

    La empresa sale del usuario autenticado (`_empresa`), nunca del cliente:
    aceptar un `company_id` del cuerpo o del query sin contrastarlo seria
    leer el catalogo de otra empresa. Ver P0-6 en `_empresa`.
    """
    empresa = await _empresa(usuario, db, company_id)
    tipos = await svc.listar(
        db, empresa, incluir_inactivos=incluir_inactivos
    )
    return [await _a_response(db, t) for t in tipos]


@router.get("/sembrar", response_model=dict)
async def sembrar_catalogo(
    _: Escribir,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> dict:
    """Crea las cuentas basicas que falten. Idempotente.

    Existe porque el catalogo depende de la empresa y una migracion no puede
    sembrarlo (ver la nota de la migracion 0018). Para una empresa nueva se llama
    sola al crearla; este endpoint es el botón de la pantalla para las que ya
    existen, y se puede volver a pulsar sin que rompa nada.
    """
    anadidas = await svc.asegurar_catalogo(db, await _empresa(usuario, db, company_id))
    return {"anadidas": anadidas, "mensaje": f"Se agregaron {anadidas} cuentas." if anadidas else "El catalogo ya estaba completo."}


@router.post("", response_model=TipoGastoResponse, status_code=status.HTTP_201_CREATED)
async def crear_tipo_gasto(
    _: Escribir,
    datos: TipoGastoCreate,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> TipoGastoResponse:
    """Da de alta una cuenta.

    La clave se normaliza a mayusculas en el servicio, no aqui: `Renta` y `renta`
    son la misma cuenta y si se guardaran distintas el cierre las contaria dos
    veces.
    """
    try:
        tipo = await svc.crear(
            db,
            await _empresa(usuario, db, company_id),
            clave=datos.clave,
            nombre=datos.nombre,
            variable=datos.variable,
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return await _a_response(db, tipo)


@router.patch("/{tipo_id}", response_model=TipoGastoResponse)
async def actualizar_tipo_gasto(
    _: Escribir,
    tipo_id: UUID,
    datos: TipoGastoUpdate,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> TipoGastoResponse:
    """Edita una cuenta.

    El `exclude_unset` lo aplica FastAPI antes de llegar al servicio, asi que
    `nombre=None` llego como "no lo mandaste" y no como "ponlo en null" (regla 22).
    """
    try:
        tipo = await svc.actualizar(
            db,
            await _empresa(usuario, db, company_id),
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


@router.put("/tickets/{ticket_id}/cuenta", status_code=status.HTTP_204_NO_CONTENT)
async def asignar_cuenta_a_ticket(
    _: Escribir,
    ticket_id: UUID,
    datos: AsignarTipoGastoRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> None:
    """Le pone (o le quita) la cuenta a un gasto.

    Mandar `tipo_gasto_id: null` lo deja SIN CLASIFICAR. Es una respuesta valida y
    no un error: significa "todavia no lo se", y es la unica forma de corregir
    una cuenta mal puesta.
    """
    try:
        await svc.asignar_a_ticket(db, await _empresa(usuario, db, company_id), ticket_id, datos.tipo_gasto_id)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc


@router.put("/productos/{producto_id}/cuenta", status_code=status.HTTP_204_NO_CONTENT)
async def asignar_cuenta_a_producto(
    _: Escribir,
    producto_id: UUID,
    datos: AsignarTipoGastoRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> None:
    """Le pone la cuenta por omision a un producto.

    NO copia nada a ningun gasto. Lo que se compre de ese producto puede ir a otra
    cuenta, y heredarla seria decidir por la persona en que se imputa un gasto.
    """
    try:
        await svc.asignar_a_producto(db, await _empresa(usuario, db, company_id), producto_id, datos.tipo_gasto_id)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc


# ---------------------------------------------------------------------------
# La autorizacion
# ---------------------------------------------------------------------------


@router.get("/autorizacion", response_model=ConteoAutorizacion)
async def conteo_de_autorizacion(
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> ConteoAutorizacion:
    """Cuantos gastos hay de cada estado. Para la cabecera de la pantalla."""
    c = await svc.conteo_por_estado(db, await _empresa(usuario, db, company_id))
    return ConteoAutorizacion(
        pendientes=c.get("PENDIENTE", 0),
        aprobadas=c.get("APROBADA", 0),
        rechazadas=c.get("RECHAZADA", 0),
    )


@router.get("/autorizacion/pendientes")
async def pendientes_de_autorizar(
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    limite: int = Query(200, ge=1, le=1000),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> list[dict]:
    """La cola: los gastos que todavia nadie ha decidido.

    Devuelve datos planos y NO los `TicketResponse` completos, a proposito: esta
    pantalla necesita commerce, monto, fecha y cuenta, y nada mas. Mandar el
    `raw_text` de 20 000 caracteres por cada pendiente es lo que hace que una
    pantalla con 200 filas se sienta lenta.
    """
    tickets = await svc.pendientes_de_autorizar(db, await _empresa(usuario, db, company_id), limite=limite)
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
    _aprueba: Annotated[UserModel, Depends(exige_rol("aprobar"))],
    ticket_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
    _cuerpo: AutorizarRequest | None = None,
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
        estado = await svc.autorizar(db, await _empresa(usuario, db, company_id), ticket_id, usuario=usuario.email)
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return AutorizacionResponse(
        ticket_id=ticket_id,
        estado=estado.value,
        autorizado_por=usuario.email,
    )


@router.post("/tickets/{ticket_id}/rechazar", response_model=AutorizacionResponse)
async def rechazar_ticket(
    _aprueba: Annotated[UserModel, Depends(exige_rol("aprobar"))],
    ticket_id: UUID,
    datos: RechazarRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None, description="Solo para el operador admin sin empresa"),
) -> AutorizacionResponse:
    """Rechaza un gasto. El motivo va en el cuerpo y es obligatorio."""
    try:
        estado = await svc.rechazar(
            db, await _empresa(usuario, db, company_id), ticket_id, usuario=usuario.email, motivo=datos.motivo
        )
    except svc.ErrorDeTipoGasto as exc:
        raise _como_error(exc) from exc
    return AutorizacionResponse(
        ticket_id=ticket_id,
        estado=estado.value,
        autorizado_por=usuario.email,
        motivo_rechazo=datos.motivo,
    )