"""Reglas de negocio de la caja chica.

El saldo se mueve con una actualizacion condicional en la MISMA transaccion que
el movimiento. `SELECT ... FOR UPDATE` se usa en Postgres; en SQLite se confia
en la condicion `WHERE ... AND saldo_actual + delta >= 0`, que es la que decide
si la operacion ocurre. Las dos variantes comparten el criterio: si la fila no
se actualizo, no se escribe el movimiento y se devuelve un conflicto.

Eso evita el fallo caro: dos gastos que leen el mismo saldo y escriben dos filas
dejando el saldo por debajo de cero, con cada movimiento "valido" por separado.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EstadoCaja, RolEmpresa, TipoMovimientoCaja
from app.core.time import utcnow
from app.models.caja_chica import CajaChicaModel, MovimientoCajaModel
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.schemas.caja_chica import (
    CajaChicaCreate,
    CajaChicaUpdate,
    MovimientoCajaCreate,
)

_CENTAVOS = Decimal("0.01")


class ErrorCaja(Exception):
    """Un conflicto de negocio, con el codigo HTTP que le corresponde."""

    def __init__(self, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _centavos(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


async def _existe(db: AsyncSession, modelo, identificador: UUID) -> bool:
    return (
        await db.execute(select(modelo.id).where(modelo.id == identificador))
    ).scalar_one_or_none() is not None


async def crear_caja(
    db: AsyncSession,
    datos: CajaChicaCreate,
    usuario_id: UUID,
    *,
    company_id: UUID,
    rol: RolEmpresa,
) -> CajaChicaModel:
    # La empresa activa NO se toma del cuerpo: el cuerpo lo escribe el cliente y
    # por tanto puede mentir. La dependencia `empresa_activa` la resolvio en BD.
    if datos.company_id != company_id:
        raise ErrorCaja("La empresa de la caja no coincide con la empresa activa")
    if rol.es_operador and datos.responsable_id != usuario_id:
        raise ErrorCaja("Un operador solo puede abrir cajas a su cargo", 403)
    if not await _existe(db, CompanyModel, datos.company_id):
        raise ErrorCaja("La empresa no existe", 404)
    if not await _existe(db, UserModel, datos.responsable_id):
        raise ErrorCaja("El responsable no existe", 404)

    caja = CajaChicaModel(
        company_id=company_id,
        responsable_id=datos.responsable_id,
        nombre=datos.nombre,
        monto_inicial=_centavos(datos.monto_inicial),
        # El saldo nace igual al monto inicial: una caja recien abierta no puede
        # mostrar un saldo que nadie puso.
        saldo_actual=_centavos(datos.monto_inicial),
        umbral_alerta=_centavos(datos.umbral_alerta),
        estado=EstadoCaja.ABIERTA.value,
        opened_at=utcnow(),
    )
    db.add(caja)
    await db.commit()
    await db.refresh(caja)
    return caja


async def listar_cajas(
    db: AsyncSession,
    company_id: UUID,
    *,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> list[CajaChicaModel]:
    stmt = select(CajaChicaModel).where(CajaChicaModel.company_id == company_id)
    if rol is not None and rol.es_operador and usuario_id is not None:
        stmt = stmt.where(CajaChicaModel.responsable_id == usuario_id)
    stmt = stmt.order_by(CajaChicaModel.opened_at)
    return list((await db.execute(stmt)).scalars().all())


async def obtener_caja(
    db: AsyncSession,
    caja_id: UUID,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> CajaChicaModel:
    caja = (
        await db.execute(select(CajaChicaModel).where(CajaChicaModel.id == caja_id))
    ).scalar_one_or_none()
    # 404 y no 403: un 403 confirmaria que la caja existe en otra empresa.
    if caja is None or caja.company_id != company_id:
        raise ErrorCaja("La caja chica no existe", 404)
    if rol is not None and rol.es_operador and caja.responsable_id != usuario_id:
        raise ErrorCaja("Solo puedes operar las cajas a tu cargo", 403)
    return caja


async def actualizar_caja(
    db: AsyncSession,
    caja_id: UUID,
    datos: CajaChicaUpdate,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> CajaChicaModel:
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    cambios = datos.model_dump(exclude_unset=True)
    if caja.estado == EstadoCaja.CERRADA.value and cambios:
        raise ErrorCaja("Una caja cerrada no se puede modificar")
    if (
        rol is not None
        and rol.es_operador
        and "responsable_id" in cambios
        and cambios["responsable_id"] != usuario_id
    ):
        raise ErrorCaja("Un operador no puede traspasar la caja a otra persona", 403)
    for campo, valor in cambios.items():
        if campo == "responsable_id" and not await _existe(db, UserModel, valor):
            raise ErrorCaja("El responsable no existe", 404)
        setattr(caja, campo, valor)
    await db.commit()
    await db.refresh(caja)
    return caja


async def listar_movimientos(
    db: AsyncSession,
    caja_id: UUID,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> list[MovimientoCajaModel]:
    await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    stmt = (
        select(MovimientoCajaModel)
        .where(MovimientoCajaModel.caja_chica_id == caja_id)
        .order_by(MovimientoCajaModel.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def cambiar_estado(
    db: AsyncSession,
    caja_id: UUID,
    estado: EstadoCaja,
    *,
    closed_at: datetime | None = None,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> CajaChicaModel:
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    actual = EstadoCaja(caja.estado)
    if actual is EstadoCaja.CERRADA:
        raise ErrorCaja("Una caja cerrada no se puede reabrir")

    if estado is EstadoCaja.CERRADA:
        if actual is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("Solo se puede cerrar una caja en revision")
        caja.closed_at = closed_at or utcnow()
        caja.estado = estado.value
    elif estado is EstadoCaja.EN_REVISION:
        if actual is not EstadoCaja.ABIERTA:
            raise ErrorCaja("Solo se puede enviar a revision una caja abierta")
        caja.estado = estado.value
    elif estado is EstadoCaja.ABIERTA:
        if actual is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("Solo se puede reabrir una caja en revision")
        caja.estado = estado.value
    else:
        raise ErrorCaja("Estado de caja no reconocido", 422)

    await db.commit()
    await db.refresh(caja)
    return caja


async def registrar_movimiento(
    db: AsyncSession,
    caja_id: UUID,
    datos: MovimientoCajaCreate,
    usuario_id: UUID,
    *,
    company_id: UUID,
    rol: RolEmpresa,
) -> MovimientoCajaModel:
    """Escribe un movimiento y mueve el saldo de forma atomica.

    El orden importa: primero se decide si el saldo puede cambiar (UPDATE
    condicional) y solo si la fila cambio se INSERTA el movimiento. Al reves,
    un movimiento sin saldo seria un kardex que cuenta dinero que no hay.
    """
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    if datos.company_id is not None and datos.company_id != company_id:
        raise ErrorCaja("La empresa del movimiento no coincide con la de la caja")

    estado = EstadoCaja(caja.estado)
    if estado is EstadoCaja.CERRADA:
        raise ErrorCaja("La caja esta cerrada; no acepta movimientos")
    if datos.tipo is TipoMovimientoCaja.CIERRE:
        if estado is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("El cierre solo se registra con la caja en revision")
    elif estado is not EstadoCaja.ABIERTA:
        raise ErrorCaja("La caja no esta abierta; no acepta movimientos")

    ticket = None
    if datos.ticket_id is not None:
        ticket = (
            await db.execute(select(TicketModel).where(TicketModel.id == datos.ticket_id))
        ).scalar_one_or_none()
        if ticket is None:
            raise ErrorCaja("El ticket no existe", 404)
        if ticket.company_id != caja.company_id:
            raise ErrorCaja("El ticket pertenece a otra empresa")

    saldo_antes = caja.saldo_actual
    monto = _centavos(datos.monto)
    if datos.tipo.suma:
        delta = monto
    elif datos.tipo in (TipoMovimientoCaja.GASTO_TICKET, TipoMovimientoCaja.CIERRE, TipoMovimientoCaja.AJUSTE):
        delta = -monto if (datos.resta or datos.tipo is not TipoMovimientoCaja.AJUSTE) else monto
    else:
        raise ErrorCaja("Tipo de movimiento no reconocido", 422)

    # `FOR UPDATE` solo donde existe. SQLite no lo soporta y ahi la condicion
    # del UPDATE es la que hace de cerrojo.
    try:
        es_postgres = db.get_bind().dialect.name == "postgresql"
    except Exception:
        es_postgres = False
    if es_postgres:
        caja = (
            await db.execute(
                select(CajaChicaModel).where(CajaChicaModel.id == caja_id).with_for_update()
            )
        ).scalar_one()

    stmt = (
        update(CajaChicaModel)
        .where(
            and_(
                CajaChicaModel.id == caja_id,
                CajaChicaModel.saldo_actual + delta >= 0,
            )
        )
        .values(saldo_actual=CajaChicaModel.saldo_actual + delta)
        .execution_options(synchronize_session="fetch")
    )
    resultado = await db.execute(stmt)
    if resultado.rowcount != 1:
        # La condicion no se cumplio: otro movimiento gano la carrera o el saldo
        # no alcanza. `rowcount=0` significa que el UPDATE no toco ninguna fila,
        # asi que no hay nada que deshacer. No se llama a `rollback()` aqui:
        # eso expiraria TODOS los objetos de la sesion a mitad de la peticion,
        # incluido el usuario que acaba de resolver la empresa activa, y la
        # respuesta 409 se caeria con un `MissingGreenlet`. El cierre de la
        # sesion al terminar el request hace el rollback que haga falta.
        raise ErrorCaja(
            f"Saldo insuficiente en la caja: hay {saldo_antes} y se pidio "
            f"{'restar' if delta < 0 else 'sumar'} {monto}"
        )

    movimiento = MovimientoCajaModel(
        company_id=caja.company_id,
        caja_chica_id=caja.id,
        ticket_id=datos.ticket_id,
        tipo=datos.tipo.value,
        origen_fondo=datos.origen_fondo.value if datos.origen_fondo else None,
        banco_nombre=datos.banco_nombre,
        referencia_bancaria=datos.referencia_bancaria,
        monto=monto,
        notas=datos.notas,
        created_by=usuario_id,
        created_at=utcnow(),
    )
    db.add(movimiento)
    # Vincular el ticket a la caja es parte del mismo hecho que el gasto: si se
    # hiciera despues, un ticket podria quedar contado en dos cajas.
    if ticket is not None and datos.tipo is TipoMovimientoCaja.GASTO_TICKET:
        ticket.caja_chica_id = caja.id
    await db.commit()
    await db.refresh(movimiento)
    await db.refresh(caja)
    return movimiento